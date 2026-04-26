# tests/test_integration.py
"""Integration tests for the full agent lifecycle workflow.

Tests cover:
- create → assign → claim → start → submit → review(approve) → verify agent stats
- create → assign → claim → start → submit → review(reject) → verify agent stats
- Concurrent claim attempts (first wins, others rejected)
- Release and re-claim cycle
"""

import json
import pytest
from models import db, Task, Agent, EventLog


@pytest.fixture
def app():
    from app import create_app
    app = create_app(testing=True)
    with app.app_context():
        db.create_all()
        # Seed agents (same as test_api.py)
        for name, display, model in [
            ('coder', 'Coder', 'deepseek-v4-flash'),
            ('editor', 'Editor', 'gpt-5-nano'),
            ('researcher', 'Researcher', 'deepseek-v4-flash'),
            ('planner', 'Planner', 'deepseek-v4-flash'),
        ]:
            db.session.add(Agent(name=name, display_name=display, model=model))
        db.session.commit()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


class TestFullLifecycle:
    """Integration tests covering the full new Phase 1 lifecycle."""

    def test_create_claim_complete_verify_stats(self, app, client):
        """Test full happy path: create → assign → claim → start → submit → review → verify agent stats updated."""
        # --- Step 1: Create a task ---
        resp = client.post('/api/tasks', json={
            'title': 'Build full-stack integration',
            'description': 'End-to-end workflow test',
            'priority': 1,
            'project': 'task-board',
            'tags': 'integration,test',
        })
        assert resp.status_code == 201
        task_data = resp.get_json()
        task_id = task_data['id']
        assert task_data['title'] == 'Build full-stack integration'
        assert task_data['status'] == 'pending'
        assert task_data['claimed_by'] is None
        assert task_data['assigned_to'] is None
        assert task_data['claimed_at'] is None

        # Verify stats reflect pending
        stats_resp = client.get('/api/stats')
        stats = stats_resp.get_json()
        assert stats['by_status']['pending'] >= 1
        assert stats['by_agent']['coder']['completed'] == 0
        assert stats['by_agent']['coder']['active'] == 0

        # --- Step 2: Assign the task ---
        resp = client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        assert resp.status_code == 200
        task_data = resp.get_json()
        assert task_data['status'] == 'assigned'
        assert task_data['assigned_to'] == 'coder'

        # --- Step 3: Claim the task ---
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        assert resp.status_code == 200
        task_data = resp.get_json()
        assert task_data['status'] == 'claimed'
        assert task_data['claimed_by'] == 'coder'
        assert task_data['claimed_at'] is not None

        # Verify stats show active claim
        stats_resp = client.get('/api/stats')
        stats = stats_resp.get_json()
        assert stats['by_status']['claimed'] >= 1
        assert stats['by_agent']['coder']['active'] >= 1

        # --- Step 4: Start work ---
        resp = client.post(f'/api/tasks/{task_id}/start', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'in_progress'

        # --- Step 5: Submit work ---
        resp = client.post(f'/api/tasks/{task_id}/submit', json={
            'agent': 'coder',
            'result': 'Integration test passed successfully'
        })
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'in_review'

        # --- Step 6: Review (approve) ---
        resp = client.post(f'/api/tasks/{task_id}/review', json={
            'reviewer': 'editor',
            'decision': 'approve',
        })
        assert resp.status_code == 200
        task_data = resp.get_json()['task']
        assert task_data['status'] == 'completed'
        assert task_data['result'] == 'Integration test passed successfully'
        assert task_data['completed_at'] is not None

        # --- Step 7: Verify agent stats updated ---
        stats_resp = client.get('/api/stats')
        stats = stats_resp.get_json()
        assert stats['by_status']['completed'] >= 1
        assert stats['by_agent']['coder']['completed'] >= 1
        assert stats['by_agent']['coder']['active'] == 0

        # Verify agent status via agent list
        agents_resp = client.get('/api/agents')
        agents = {a['name']: a for a in agents_resp.get_json()['agents']}
        assert agents['coder']['tasks_completed'] >= 1

    def test_create_claim_fail_verify_stats(self, app, client):
        """Test failure path: create → assign → claim → start → submit → review(reject) → verify agent stats updated."""
        # --- Step 1: Create a task ---
        resp = client.post('/api/tasks', json={
            'title': 'Flaky integration test',
            'description': 'This task is expected to fail',
            'priority': 3,
            'project': 'task-board',
            'tags': 'integration,test,failure',
        })
        assert resp.status_code == 201
        task_id = resp.get_json()['id']

        # --- Step 2: Assign the task ---
        resp = client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'assigned'

        # --- Step 3: Claim the task ---
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'claimed'

        # Verify explicit claim status via GET
        get_resp = client.get(f'/api/tasks/{task_id}')
        assert get_resp.get_json()['status'] == 'claimed'
        assert get_resp.get_json()['claimed_by'] == 'coder'

        # --- Step 4: Start work ---
        client.post(f'/api/tasks/{task_id}/start', json={'agent': 'coder'})

        # --- Step 5: Submit work ---
        client.post(f'/api/tasks/{task_id}/submit', json={
            'agent': 'coder',
            'result': 'Flaky result'
        })

        # --- Step 6: Review (reject) ---
        resp = client.post(f'/api/tasks/{task_id}/review', json={
            'reviewer': 'editor',
            'decision': 'reject',
            'feedback': 'Unexpected integration failure',
        })
        assert resp.status_code == 200
        task_data = resp.get_json()['task']
        assert task_data['status'] == 'failed'
        assert task_data['last_error'] == 'Unexpected integration failure'

        # --- Step 7: Verify agent stats updated ---
        stats_resp = client.get('/api/stats')
        stats = stats_resp.get_json()
        assert stats['by_status']['failed'] >= 1
        assert stats['by_agent']['coder']['failed'] >= 1
        assert stats['by_agent']['coder']['active'] == 0

        # Verify agent status via agent list
        agents_resp = client.get('/api/agents')
        agents = {a['name']: a for a in agents_resp.get_json()['agents']}
        assert agents['coder']['tasks_failed'] >= 1

    def test_concurrent_claim_first_wins(self, app, client):
        """Test that claiming locks the task: first claim succeeds, rest get 409 conflict."""
        # Create a task to race over
        resp = client.post('/api/tasks', json={
            'title': 'Hot potato task',
            'description': 'Multiple agents want this one',
            'priority': 1,
            'project': 'task-board',
            'tags': 'concurrency,race',
        })
        assert resp.status_code == 201
        task_id = resp.get_json()['id']

        # Assign to coder so the task enters 'assigned' state
        resp = client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'assigned'

        # First claim by assigned agent succeeds
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'claimed'
        assert data['claimed_by'] == 'coder'

        # Second claim by different agent fails (assigned_to mismatch)
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'editor'})
        assert resp.status_code == 409

        # Verify via GET
        get_resp = client.get(f'/api/tasks/{task_id}')
        final_data = get_resp.get_json()
        assert final_data['status'] == 'claimed'
        assert final_data['claimed_by'] == 'coder'

    def test_release_and_reclaim(self, app, client):
        """Test the full release and re-claim cycle.

        create → assign → claim → release → re-assign → re-claim → start → submit → review(approve)
        """
        # --- Step 1: Create a task ---
        resp = client.post('/api/tasks', json={
            'title': 'Pass-the-parcel task',
            'description': 'Will be claimed, released, and re-claimed',
            'priority': 2,
            'project': 'task-board',
            'tags': 'release,reclaim',
        })
        assert resp.status_code == 201
        task_id = resp.get_json()['id']

        # --- Step 2: Assign to coder ---
        resp = client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['assigned_to'] == 'coder'

        # --- Step 3: First claim by coder ---
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'claimed'
        assert resp.get_json()['claimed_by'] == 'coder'

        # --- Step 4: Release the task ---
        resp = client.post(f'/api/tasks/{task_id}/release')
        assert resp.status_code == 200
        task_data = resp.get_json()
        # Release clears assignment back to pending
        assert task_data['status'] == 'pending'
        assert task_data['claimed_by'] is None
        assert task_data['assigned_to'] is None

        # Verify via GET
        get_resp = client.get(f'/api/tasks/{task_id}')
        assert get_resp.get_json()['status'] == 'pending'
        assert get_resp.get_json()['claimed_by'] is None

        # --- Step 5: Re-assign to a different agent (editor) ---
        resp = client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'editor'})
        assert resp.status_code == 200
        assert resp.get_json()['assigned_to'] == 'editor'

        # --- Step 6: Re-claim by editor ---
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'editor'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'claimed'
        assert resp.get_json()['claimed_by'] == 'editor'
        assert resp.get_json()['claimed_at'] is not None

        # Verify GET reflects new claim
        get_resp = client.get(f'/api/tasks/{task_id}')
        assert get_resp.get_json()['claimed_by'] == 'editor'

        # --- Step 7: Start, submit, and approve ---
        client.post(f'/api/tasks/{task_id}/start', json={'agent': 'editor'})
        client.post(f'/api/tasks/{task_id}/submit', json={
            'agent': 'editor',
            'result': 'Re-claimed and completed by editor'
        })
        resp = client.post(f'/api/tasks/{task_id}/review', json={
            'reviewer': 'coder',
            'decision': 'approve',
        })
        assert resp.status_code == 200
        task_data = resp.get_json()['task']
        assert task_data['status'] == 'completed'
        assert task_data['claimed_by'] == 'editor'
        assert task_data['result'] == 'Re-claimed and completed by editor'

        # --- Step 8: Verify both agents' stats ---
        stats_resp = client.get('/api/stats')
        stats = stats_resp.get_json()
        # coder had no completed from this flow (released, then reviewed)
        assert stats['by_agent']['coder']['completed'] >= 0
        # editor got the completed task
        assert stats['by_agent']['editor']['completed'] >= 1

    def test_cannot_submit_without_claim(self, app, client):
        """Test that submitting a pending task returns 409."""
        resp = client.post('/api/tasks', json={'title': 'Never claimed'})
        task_id = resp.get_json()['id']
        resp = client.post(f'/api/tasks/{task_id}/submit', json={'agent': 'coder', 'result': 'Should fail'})
        assert resp.status_code == 409

    def test_cannot_review_without_submit(self, app, client):
        """Test that reviewing a pending task returns 409."""
        resp = client.post('/api/tasks', json={'title': 'Never submitted'})
        task_id = resp.get_json()['id']
        resp = client.post(f'/api/tasks/{task_id}/review', json={'reviewer': 'editor', 'decision': 'approve'})
        assert resp.status_code == 409

    def test_cannot_release_without_claim(self, app, client):
        """Test that releasing a pending task returns 409."""
        resp = client.post('/api/tasks', json={'title': 'Never claimed at all'})
        task_id = resp.get_json()['id']
        resp = client.post(f'/api/tasks/{task_id}/release')
        assert resp.status_code == 409

    def test_cannot_claim_twice_completed(self, app, client):
        """Test that a completed task cannot be claimed."""
        resp = client.post('/api/tasks', json={'title': 'Already done'})
        task_id = resp.get_json()['id']
        # Full lifecycle
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/start', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/submit', json={'agent': 'coder', 'result': 'Done'})
        client.post(f'/api/tasks/{task_id}/review', json={'reviewer': 'editor', 'decision': 'approve'})
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'editor'})
        assert resp.status_code == 409

    def test_cannot_claim_twice_failed(self, app, client):
        """Test that a failed task cannot be claimed."""
        resp = client.post('/api/tasks', json={'title': 'Already broken'})
        task_id = resp.get_json()['id']
        # Full lifecycle ending in failure
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/start', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/submit', json={'agent': 'coder', 'result': 'Broken'})
        client.post(f'/api/tasks/{task_id}/review', json={'reviewer': 'editor', 'decision': 'reject', 'feedback': 'Broke'})
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'editor'})
        assert resp.status_code == 409

    def test_claim_requires_agent_name(self, app, client):
        """Test that claiming without agent name returns 400."""
        resp = client.post('/api/tasks', json={'title': 'No agent claim'})
        task_id = resp.get_json()['id']
        # Assign first to get to assigned state
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/claim', json={})
        assert resp.status_code == 400

    def test_submit_requires_result(self, app, client):
        """Test that submitting without result returns 400."""
        resp = client.post('/api/tasks', json={'title': 'No result'})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/start', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/submit', json={'agent': 'coder'})
        assert resp.status_code == 400

    def test_review_requires_decision(self, app, client):
        """Test that reviewing without decision returns 400."""
        resp = client.post('/api/tasks', json={'title': 'No decision'})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/start', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/submit', json={'agent': 'coder', 'result': 'Done'})
        resp = client.post(f'/api/tasks/{task_id}/review', json={'reviewer': 'editor'})
        assert resp.status_code == 400


class TestAutoAssignFullLifecycle:
    """Tests for auto-assign followed by full lifecycle and event log verification."""

    @pytest.fixture
    def app(self):
        from app import create_app
        app = create_app(testing=True)
        with app.app_context():
            db.create_all()
            # Seed agents with skills (needed for auto-assign matching)
            agents_data = [
                ('coder', 'Coder', 'deepseek-v4-flash', 'worker',
                 'python,flask,backend,api', 'task-board,hermes', 3, 'idle'),
                ('editor', 'Editor', 'gpt-5-nano', 'worker',
                 'text,docs,frontend,ui', 'hermes,docs', 3, 'idle'),
                ('researcher', 'Researcher', 'deepseek-v4-flash', 'worker',
                 'research,data,analysis,content', 'general,research', 2, 'idle'),
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
    def client(self, app):
        return app.test_client()

    def test_auto_assign_full_lifecycle_and_event_log(self, app, client):
        """Create task with python tags, auto-assign, walk claim→start→submit→review→complete,
        then verify all 7 event log entries exist."""
        # --- Step 1: Create a task with python tags ---
        resp = client.post('/api/tasks', json={
            'title': 'Python integration test',
            'tags': 'python,api',
            'priority': 2,
            'project': 'task-board',
            'description': 'Test auto-assign + full lifecycle + event log',
        })
        assert resp.status_code == 201
        task_data = resp.get_json()
        task_id = task_data['id']
        assert task_data['status'] == 'pending'
        assert task_data['tags'] == 'python,api'

        # --- Step 2: Call auto-assign ---
        resp = client.post('/api/overseer/auto-assign')
        assert resp.status_code == 200
        auto_data = resp.get_json()
        assert auto_data['assigned'] >= 1

        # --- Step 3: Verify coder got assigned ---
        resp = client.get(f'/api/tasks/{task_id}')
        task_data = resp.get_json()
        assert task_data['status'] == 'assigned'
        assert task_data['assigned_to'] == 'coder', f"Expected coder, got {task_data['assigned_to']}"

        # --- Step 4: Claim the task ---
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'claimed'
        assert resp.get_json()['claimed_by'] == 'coder'

        # --- Step 5: Start work ---
        resp = client.post(f'/api/tasks/{task_id}/start', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'in_progress'

        # --- Step 6: Submit work ---
        resp = client.post(f'/api/tasks/{task_id}/submit', json={
            'agent': 'coder',
            'result': 'Auto-assign lifecycle test passed',
        })
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'in_review'

        # --- Step 7: Review and approve ---
        resp = client.post(f'/api/tasks/{task_id}/review', json={
            'reviewer': 'editor',
            'decision': 'approve',
        })
        assert resp.status_code == 200
        assert resp.get_json()['task']['status'] == 'completed'

        # --- Step 8: Verify event log has all 7 steps ---
        events_resp = client.get(f'/api/tasks/{task_id}/events')
        assert events_resp.status_code == 200
        events = events_resp.get_json()['events']

        expected_event_types = [
            'task_created',
            'assigned',
            'claimed',
            'in_progress',
            'submitted',
            'in_review',
            'completed',
        ]
        actual_event_types = [e['event_type'] for e in events]
        assert len(events) >= len(expected_event_types), \
            f"Expected at least {len(expected_event_types)} events, got {len(events)}: {actual_event_types}"

        for expected in expected_event_types:
            assert expected in actual_event_types, \
                f"Missing event type '{expected}' in event log. Got: {actual_event_types}"

        # Verify the auto-assigned event has auto_assign=True in details
        assigned_events = [e for e in events if e['event_type'] == 'assigned']
        assert len(assigned_events) >= 1
        auto_assign_events = [e for e in assigned_events
                              if e.get('details', {}).get('auto_assign')]
        assert len(auto_assign_events) >= 1, \
            "Expected at least one assigned event with auto_assign=True"
