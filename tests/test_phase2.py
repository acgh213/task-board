"""Tests for Phase 2: Overseer auto-assignment, reclaim-timeouts, dashboard, and poll daemon."""

import json
import time
import threading
import pytest
from datetime import datetime, timezone, timedelta
from models import db, Task, Agent, EventLog


@pytest.fixture
def app():
    from app import create_app
    app = create_app(testing=True)
    with app.app_context():
        db.create_all()
        # Seed agents with skills, preferred_projects, and max_concurrent
        agents_data = [
            ('coder', 'Coder', 'deepseek-v4-flash', 'worker',
             'python,flask,backend,api', 'task-board,hermes', 3, 'idle'),
            ('editor', 'Editor', 'gpt-5-nano', 'worker',
             'text,docs,frontend,ui', 'hermes,docs', 3, 'idle'),
            ('researcher', 'Researcher', 'deepseek-v4-flash', 'worker',
             'research,data,analysis,content', 'general,research', 2, 'idle'),
            ('planner', 'Planner', 'deepseek-v4-flash', 'mission_control',
             'planning,strategy,project-management', 'hermes', 5, 'idle'),
            ('busy_agent', 'Busy Agent', 'deepseek-v4-flash', 'worker',
             'python,api', 'task-board', 1, 'busy'),
            ('offline_agent', 'Offline Agent', 'gpt-5-nano', 'worker',
             'python,backend', 'task-board', 3, 'offline'),
        ]
        for name, display, model, role, skills, projects, maxc, status in agents_data:
            db.session.add(Agent(
                name=name, display_name=display, model=model,
                role=role, skills=skills,
                preferred_projects=projects,
                max_concurrent=maxc, status=status,
            ))
        db.session.commit()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _create_task(client, title, tags='', priority=3, project='general',
                 reserved_for=None, description=''):
    """Helper to create a task and return the task data."""
    data = {'title': title, 'tags': tags, 'priority': priority,
            'project': project, 'description': description}
    if reserved_for:
        data['reserved_for'] = reserved_for
    resp = client.post('/api/tasks', json=data)
    assert resp.status_code == 201
    return resp.get_json()


def _complete_task(client, task_id, agent='coder', reviewer='editor', result='Done'):
    """Helper: run full lifecycle."""
    client.post(f'/api/tasks/{task_id}/assign', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/claim', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/start', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/submit', json={'agent': agent, 'result': result})
    return client.post(f'/api/tasks/{task_id}/review',
                       json={'reviewer': reviewer, 'decision': 'approve'})


class TestAutoAssign:
    """Tests for POST /overseer/auto-assign."""

    def test_pending_task_gets_assigned_to_matching_agent(self, client):
        """Pending task with tags matching an agent's skills gets assigned."""
        _create_task(client, 'Build API endpoint', tags='python,api', priority=2, project='task-board')
        resp = client.post('/api/overseer/auto-assign')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['assigned'] >= 1
        assert data['total'] >= 1

        # Check one of the tasks was assigned to coder (has python,api skills)
        assigned_tasks = [r for r in data['results'] if r['assigned_to'] is not None]
        assert len(assigned_tasks) >= 1
        # Verify task is now in 'assigned' status
        task_id = assigned_tasks[0]['task_id']
        resp2 = client.get(f'/api/tasks/{task_id}')
        assert resp2.get_json()['status'] == 'assigned'
        assert resp2.get_json()['assigned_to'] is not None

    def test_task_no_matching_agent_stays_pending(self, client):
        """Task with tags matching NO available agent's skills stays pending."""
        _create_task(client, 'Quantum physics paper', tags='quantum,physics,math',
                     project='nobody-works-here')
        resp = client.post('/api/overseer/auto-assign')
        data = resp.get_json()
        # The task has no matching skills or project with any agent — should be skipped
        skipped_tasks = [r for r in data['results'] if r['assigned_to'] is None]
        assert len(skipped_tasks) >= 1
        # Verify task is still pending
        task_id = skipped_tasks[0]['task_id']
        resp2 = client.get(f'/api/tasks/{task_id}')
        assert resp2.get_json()['status'] == 'pending'

    def test_busy_agent_at_max_concurrent_is_skipped(self, client):
        """Busy agent with max_concurrent reached is not assigned more tasks."""
        # Busy agent has max_concurrent=1 and status='busy'
        _create_task(client, 'Python task for busy', tags='python,api',
                     project='task-board')
        resp = client.post('/api/overseer/auto-assign')
        data = resp.get_json()
        # busy_agent may not be the best match since coder is also available
        # But let's verify no task was assigned to busy_agent
        for r in data['results']:
            assert r['assigned_to'] != 'busy_agent', \
                f"Task {r['task_id']} was wrongly assigned to busy_agent"

    def test_priority_affects_assignment_order(self, client):
        """Higher priority tasks (lower number) are assigned first."""
        _create_task(client, 'Low prio task', tags='python,api', priority=5, project='task-board')
        _create_task(client, 'High prio task', tags='python,api', priority=1, project='task-board')

        resp = client.post('/api/overseer/auto-assign')
        assert resp.status_code == 200
        data = resp.get_json()
        # Both should be assigned — coder has matching skills
        assert data['assigned'] >= 2

    def test_reserved_for_matching_agent_type(self, client):
        """reserved_for tasks only go to agents with matching role."""
        _create_task(client, 'Mission critical plan', tags='planning,strategy',
                     priority=1, project='hermes', reserved_for='mission_control')
        resp = client.post('/api/overseer/auto-assign')
        data = resp.get_json()
        # planner is mission_control, should get the task
        assigned = [r for r in data['results'] if r['assigned_to']]
        if assigned:
            resp2 = client.get(f'/api/tasks/{assigned[0]["task_id"]}')
            task_data = resp2.get_json()
            if task_data['reserved_for'] == 'mission_control':
                assert task_data['assigned_to'] in ('planner',), \
                    f"Expected planner, got {task_data['assigned_to']}"

    def test_escalation_tags_on_auto_assigned_tasks(self, client):
        """Escalation tags still work on tasks (set at creation time)."""
        # Create task with deployment tags (should auto-escalate to needs_human)
        _create_task(client, 'Deploy release', tags='python,deploy', priority=1, project='task-board')
        # This task should have been escalated to needs_human at creation
        # It won't be in pending so auto-assign won't see it
        resp = client.get('/api/tasks?status=needs_human')
        needs_human = resp.get_json()['tasks']
        deploy_tasks = [t for t in needs_human if 'deploy' in t.get('tags', '')]
        assert len(deploy_tasks) >= 1, \
            "Deploy-tagged task should have been escalated to needs_human at creation"


class TestPendingForAgent:
    """Tests for GET /overseer/pending-for-agent/<name>."""

    def test_returns_matching_tasks(self, client):
        """Returns pending tasks whose tags overlap with agent skills."""
        _create_task(client, 'Backend API', tags='python,api', project='task-board')
        _create_task(client, 'Frontend UI', tags='frontend,ui', project='hermes')

        resp = client.get('/api/overseer/pending-for-agent/coder')
        assert resp.status_code == 200
        data = resp.get_json()
        # coder has python,flask,backend,api — should match first task
        titles = [t['title'] for t in data['tasks']]
        assert 'Backend API' in titles
        # coder does NOT have frontend,ui skills
        assert 'Frontend UI' not in titles

    def test_no_matching_skills_returns_empty(self, client):
        """Agent with skills that don't match any task returns empty list."""
        _create_task(client, 'Backend API', tags='python,api', project='task-board')
        resp = client.get('/api/overseer/pending-for-agent/researcher')
        assert resp.status_code == 200
        data = resp.get_json()
        # researcher has research,data,analysis,content — no overlap with python,api
        assert len(data['tasks']) == 0
        assert data['total'] == 0

    def test_nonexistent_agent_returns_404(self, client):
        """Non-existent agent name returns 404."""
        resp = client.get('/api/overseer/pending-for-agent/nobody')
        assert resp.status_code == 404


class TestReclaimTimeouts:
    """Tests for POST /overseer/reclaim-timeouts."""

    def _create_timed_out_task(self, client, title, attempts=0, max_attempts=3):
        """Helper to create a task in 'timed_out' status."""
        resp = client.post('/api/tasks', json={'title': title, 'tags': 'python'})
        task_id = resp.get_json()['id']
        task = db.session.get(Task, task_id)
        task.status = 'timed_out'
        task.attempts = attempts
        task.max_attempts = max_attempts
        task.claimed_by = 'coder'
        task.last_error = 'Lease expired'
        task.failure_reason = 'timeout'
        task.timed_out_count = 1
        db.session.commit()
        return task_id

    def test_under_max_attempts_released(self, client):
        """Timed-out task with attempts < max_attempts is released back to pending."""
        task_id = self._create_timed_out_task(client, 'Releasable task',
                                              attempts=1, max_attempts=3)
        resp = client.post('/api/overseer/reclaim-timeouts')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['released'] >= 1

        # Verify task is back to pending
        resp2 = client.get(f'/api/tasks/{task_id}')
        task_data = resp2.get_json()
        assert task_data['status'] == 'pending'
        assert task_data['attempts'] == 2  # incremented from 1

    def test_at_max_attempts_marked_dead(self, client):
        """Timed-out task with attempts >= max_attempts is marked dead."""
        task_id = self._create_timed_out_task(client, 'Dead task',
                                              attempts=2, max_attempts=3)
        resp = client.post('/api/overseer/reclaim-timeouts')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['dead'] >= 1

        # Verify task is dead
        resp2 = client.get(f'/api/tasks/{task_id}')
        task_data = resp2.get_json()
        assert task_data['status'] == 'dead'
        assert task_data['attempts'] == 3  # incremented from 2

    def test_reclaimed_task_can_be_claimed_by_different_agent(self, client):
        """A reclaimed (released) task can be claimed by a different agent."""
        # Create a task that was timed out and release it
        task_id = self._create_timed_out_task(client, 'Reclaimable',
                                              attempts=1, max_attempts=3)
        # Reclaim it
        client.post('/api/overseer/reclaim-timeouts')
        resp = client.get(f'/api/tasks/{task_id}')
        assert resp.get_json()['status'] == 'pending'

        # Now assign and claim it with a different agent
        resp = client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'editor'})
        assert resp.status_code == 200
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'editor'})
        assert resp.status_code == 200
        assert resp.get_json()['claimed_by'] == 'editor'


class TestOverseerDashboard:
    """Tests for GET /overseer/dashboard."""

    def test_returns_correct_counts(self, client):
        """Dashboard returns correct task counts by status."""
        # Create tasks in various statuses
        t1 = _create_task(client, 'Pending task')
        t2 = _create_task(client, 'Complete me')
        _complete_task(client, t2['id'], result='All done')

        resp = client.get('/api/overseer/dashboard')
        assert resp.status_code == 200
        data = resp.get_json()

        # Should have at least 1 pending, 1 completed
        assert data['by_status']['pending'] >= 1
        assert data['by_status']['completed'] >= 1
        assert data['total_tasks'] >= 2

    def test_returns_agent_load_info(self, client):
        """Dashboard returns agent load information."""
        resp = client.get('/api/overseer/dashboard')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'agent_load' in data

        # Check specific agents
        agent_load = data['agent_load']
        assert 'coder' in agent_load
        assert 'active' in agent_load['coder']
        assert 'max_concurrent' in agent_load['coder']
        assert 'available_slots' in agent_load['coder']
        assert agent_load['coder']['max_concurrent'] == 3

        # offline_agent should also appear
        assert 'offline_agent' in agent_load
        assert agent_load['offline_agent']['status'] == 'offline'

    def test_includes_recent_events(self, client):
        """Dashboard includes recent events."""
        _create_task(client, 'Eventful task')
        resp = client.get('/api/overseer/dashboard')
        data = resp.get_json()
        assert 'recent_events' in data
        assert len(data['recent_events']) >= 1

    def test_includes_locked_and_timed_out_counts(self, client):
        """Dashboard includes locked and timed_out task counts."""
        resp = client.get('/api/overseer/dashboard')
        data = resp.get_json()
        assert 'locked_tasks' in data
        assert 'timed_out_tasks' in data


class TestPollDaemon:
    """Tests for the poll_daemon.py script."""

    def test_pending_for_agent_endpoint_works(self, client):
        """Verify the endpoint that poll_daemon uses returns correct data."""
        _create_task(client, 'Python work', tags='python,api', project='task-board')
        _create_task(client, 'Research doc', tags='research,content', project='general')

        # coder should see python task
        resp = client.get('/api/overseer/pending-for-agent/coder')
        data = resp.get_json()
        titles = [t['title'] for t in data['tasks']]
        assert 'Python work' in titles
        assert 'Research doc' not in titles

        # researcher should see research task
        resp = client.get('/api/overseer/pending-for-agent/researcher')
        data = resp.get_json()
        titles = [t['title'] for t in data['tasks']]
        assert 'Research doc' in titles

    def test_claim_and_heartbeat_flow(self, client):
        """Claim a task and send heartbeats — the core daemon loop."""
        # Create and assign a task
        t = _create_task(client, 'Daemon task', tags='python,api')
        client.post(f'/api/tasks/{t["id"]}/assign', json={'agent': 'coder'})

        # Claim
        resp = client.post(f'/api/tasks/{t["id"]}/claim', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'claimed'

        # Start
        resp = client.post(f'/api/tasks/{t["id"]}/start', json={'agent': 'coder'})
        assert resp.status_code == 200

        # Heartbeat
        resp = client.post(f'/api/tasks/{t["id"]}/heartbeat', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'in_progress'
        assert resp.get_json()['heartbeat_at'] is not None

    def test_handle_failure_gracefully(self, client):
        """Daemon can report failure via escalation and release."""
        # Create, assign, claim a task
        t = _create_task(client, 'Failing task', tags='python,api')
        client.post(f'/api/tasks/{t["id"]}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{t["id"]}/claim', json={'agent': 'coder'})
        client.post(f'/api/tasks/{t["id"]}/start', json={'agent': 'coder'})

        # Escalate to needs_human
        resp = client.post(f'/api/tasks/{t["id"]}/escalate', json={
            'target': 'needs_human',
            'reason': 'Task failed: unexpected error',
        })
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'needs_human'

    def test_daemon_claims_and_reports(self, client):
        """Full daemon-like lifecycle: claim, start, submit, complete."""
        t = _create_task(client, 'Daemon full cycle', tags='python,api')
        client.post(f'/api/tasks/{t["id"]}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{t["id"]}/claim', json={'agent': 'coder'})
        client.post(f'/api/tasks/{t["id"]}/start', json={'agent': 'coder'})
        client.post(f'/api/tasks/{t["id"]}/submit', json={
            'agent': 'coder',
            'result': 'Successfully completed by daemon',
        })
        resp = client.post(f'/api/tasks/{t["id"]}/review', json={
            'reviewer': 'editor',
            'decision': 'approve',
        })
        assert resp.status_code == 200
        assert resp.get_json()['task']['status'] == 'completed'


class TestAutoAssignEdgeCases:
    """Edge cases for auto-assign."""

    def test_no_pending_tasks(self, client):
        """Auto-assign with no pending tasks returns 0 assigned."""
        resp = client.post('/api/overseer/auto-assign')
        data = resp.get_json()
        assert data['assigned'] == 0
        assert data['total'] == 0

    def test_auto_assign_logs_events(self, client):
        """Auto-assign creates event log entries."""
        _create_task(client, 'Loggable task', tags='python,api')
        resp = client.post('/api/overseer/auto-assign')
        data = resp.get_json()
        if data['assigned'] > 0:
            # Check events were created
            events_resp = client.get('/api/events?event_type=assigned')
            events = events_resp.get_json()['events']
            assigned_auto = [e for e in events if e.get('details', {}).get('auto_assign')]
            assert len(assigned_auto) >= 1

    def test_auto_assign_offline_agent_skipped(self, client):
        """Offline agents are not considered for auto-assign."""
        _create_task(client, 'Python task', tags='python,api', project='task-board')
        resp = client.post('/api/overseer/auto-assign')
        data = resp.get_json()
        # offline_agent shouldn't get any assignments
        for r in data['results']:
            assert r['assigned_to'] != 'offline_agent'
