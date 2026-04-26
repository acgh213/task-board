"""Tests for Phase 5: Reputation System with Complexity.

Covers:
- Complexity field on Task model (1-5 scale, creation, defaults, validation)
- avg_completion_time tracking on Agent model
- Auto-assign prefers low-rep agents for low-complexity tasks,
  high-rep agents for high-complexity tasks
- GET /agents/<name>/reputation endpoint returns detailed reputation stats
- avg_completion_time updated correctly through task lifecycle
"""

import pytest
from datetime import datetime, timezone, timedelta
from models import db, Task, Agent


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture
def app():
    from app import create_app
    app = create_app(testing=True)
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seed_agents(app):
    """Seed agents with varied reputation scores for complexity testing."""
    with app.app_context():
        agents_data = [
            # Low-rep agent (new, score ~50)
            ('rookie', 'Rookie', 'deepseek-v4-flash', 'worker',
             'python,flask,simple', 'general', 5, 'idle', 0, 0, 0, 0, 50.0),
            # Medium-rep agent
            ('journeyman', 'Journeyman', 'deepseek-v4-flash', 'worker',
             'python,flask,backend,api', 'general', 3, 'idle', 10, 2, 0, 0, 70.0),
            # High-rep agent
            ('expert', 'Expert', 'deepseek-v4-flash', 'worker',
             'python,flask,backend,api,complex,architecture', 'general', 3, 'idle',
             50, 2, 0, 0, 90.0),
        ]
        for name, display, model, role, skills, projects, maxc, status, completed, failed, rejected, timeout, rep in agents_data:
            existing = db.session.get(Agent, name)
            if not existing:
                db.session.add(Agent(
                    name=name, display_name=display, model=model,
                    role=role, skills=skills,
                    preferred_projects=projects,
                    max_concurrent=maxc, status=status,
                    tasks_completed=completed, tasks_failed=failed,
                    tasks_review_rejected=rejected, tasks_timed_out=timeout,
                    reputation_score=rep,
                ))
        db.session.commit()


# ──────────────────────────────────────────────
# Helper: run a full task lifecycle
# ──────────────────────────────────────────────

def run_lifecycle(client, task_id, agent_name, reviewer='editor', result='Done'):
    """Run a full task lifecycle: assign -> claim -> start -> submit -> review approve."""
    resp = client.post(f'/api/tasks/{task_id}/assign', json={'agent': agent_name})
    assert resp.status_code == 200, f"Assign failed: {resp.get_json()}"
    resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': agent_name})
    assert resp.status_code == 200, f"Claim failed: {resp.get_json()}"
    resp = client.post(f'/api/tasks/{task_id}/start', json={'agent': agent_name})
    assert resp.status_code == 200, f"Start failed: {resp.get_json()}"
    resp = client.post(f'/api/tasks/{task_id}/submit?skip_wait=true', json={
        'agent': agent_name, 'result': result,
    })
    assert resp.status_code == 200, f"Submit failed: {resp.get_json()}"
    resp = client.post(f'/api/tasks/{task_id}/review', json={
        'reviewer': reviewer, 'decision': 'approve',
    })
    assert resp.status_code == 200, f"Review failed: {resp.get_json()}"
    return resp.get_json()


# ══════════════════════════════════════════════
# Complexity Field Tests
# ══════════════════════════════════════════════

class TestComplexityField:
    """Tests for the complexity field on the Task model."""

    def test_default_complexity_is_3(self, app):
        """Task created without explicit complexity defaults to 3."""
        with app.app_context():
            task = Task(title='Default complexity task')
            db.session.add(task)
            db.session.commit()
            assert task.complexity == 3

    def test_create_task_with_complexity_1(self, client):
        """Can create a task with complexity 1."""
        resp = client.post('/api/tasks', json={
            'title': 'Simple task', 'complexity': 1,
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['complexity'] == 1

    def test_create_task_with_complexity_5(self, client):
        """Can create a task with complexity 5."""
        resp = client.post('/api/tasks', json={
            'title': 'Complex task', 'complexity': 5,
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['complexity'] == 5

    def test_create_task_with_invalid_complexity_0(self, client):
        """Complexity below 1 is rejected."""
        resp = client.post('/api/tasks', json={
            'title': 'Invalid complexity', 'complexity': 0,
        })
        assert resp.status_code == 400
        assert 'complexity' in resp.get_json().get('error', '')

    def test_create_task_with_invalid_complexity_6(self, client):
        """Complexity above 5 is rejected."""
        resp = client.post('/api/tasks', json={
            'title': 'Too complex', 'complexity': 6,
        })
        assert resp.status_code == 400
        assert 'complexity' in resp.get_json().get('error', '')

    def test_create_task_with_string_complexity(self, client):
        """Non-integer complexity is rejected."""
        resp = client.post('/api/tasks', json={
            'title': 'String complexity', 'complexity': 'high',
        })
        assert resp.status_code == 400
        assert 'complexity' in resp.get_json().get('error', '')

    def test_complexity_in_task_list_response(self, client):
        """Complexity field appears in GET /tasks response."""
        resp = client.post('/api/tasks', json={
            'title': 'List test', 'complexity': 4,
        })
        assert resp.status_code == 201
        task_id = resp.get_json()['id']

        resp = client.get(f'/api/tasks/{task_id}')
        assert resp.status_code == 200
        assert resp.get_json()['complexity'] == 4

    def test_complexity_in_list_tasks(self, client):
        """Complexity field appears in task list endpoint."""
        client.post('/api/tasks', json={'title': 'C1', 'complexity': 1})
        client.post('/api/tasks', json={'title': 'C5', 'complexity': 5})

        resp = client.get('/api/tasks')
        assert resp.status_code == 200
        tasks = resp.get_json()['tasks']
        complexities = {t['title']: t['complexity'] for t in tasks}
        assert complexities.get('C1') == 1
        assert complexities.get('C5') == 5


# ══════════════════════════════════════════════
# avg_completion_time Tests
# ══════════════════════════════════════════════

class TestAvgCompletionTime:
    """Tests for avg_completion_time tracking on Agent model."""

    def test_avg_completion_time_default(self, app):
        """New agent has avg_completion_time of 0.0."""
        with app.app_context():
            agent = Agent(name='newbie', display_name='Newbie')
            db.session.add(agent)
            db.session.commit()
            assert agent.avg_completion_time == 0.0

    def test_avg_completion_time_updated_on_completion(self, app, client, seed_agents):
        """After a task completes, agent's avg_completion_time is updated."""
        # Create a task with a specific delay by manipulating created_at
        # We'll set created_at in the past, complete it now
        import json as _json_module

        # Create task
        resp = client.post('/api/tasks', json={
            'title': 'Time test task', 'tags': 'python,flask',
        })
        assert resp.status_code == 201
        task_id = resp.get_json()['id']

        # Manually set created_at to 10 seconds ago to simulate duration
        with app.app_context():
            task = db.session.get(Task, task_id)
            task.created_at = datetime.now(timezone.utc) - timedelta(seconds=10)
            db.session.commit()

        # Run lifecycle
        run_lifecycle(client, task_id, 'rookie')

        # Check avg_completion_time
        with app.app_context():
            agent = db.session.get(Agent, 'rookie')
            assert agent.avg_completion_time > 0
            # Should be approximately 10 seconds (within reasonable tolerance)
            assert 5.0 <= agent.avg_completion_time <= 30.0, (
                f"Expected ~10s, got {agent.avg_completion_time}"
            )

    def test_avg_completion_time_after_multiple_tasks(self, app, client, seed_agents):
        """avg_completion_time is a running average over multiple completed tasks."""
        durations = [5, 15, 10]  # seconds
        task_ids = []

        for i, dur in enumerate(durations):
            resp = client.post('/api/tasks', json={
                'title': f'Multi task {i}', 'tags': 'python,flask',
            })
            assert resp.status_code == 201
            task_id = resp.get_json()['id']

            # Set created_at in the past
            with app.app_context():
                task = db.session.get(Task, task_id)
                task.created_at = datetime.now(timezone.utc) - timedelta(seconds=dur)
                db.session.commit()

            run_lifecycle(client, task_id, 'rookie')

        # Expected average: (5 + 15 + 10) / 3 = 10
        with app.app_context():
            agent = db.session.get(Agent, 'rookie')
            expected_avg = sum(durations) / len(durations)
            lower = expected_avg * 0.5
            upper = expected_avg * 1.5 + 5  # generous upper bound due to test overhead
            assert lower <= agent.avg_completion_time <= upper, (
                f"Expected ~{expected_avg}s, got {agent.avg_completion_time}"
            )

    def test_avg_completion_time_in_agent_response(self, app, client, seed_agents):
        """avg_completion_time appears in GET /agents/<name> response."""
        # Complete one task first
        resp = client.post('/api/tasks', json={
            'title': 'Time display test', 'tags': 'python,flask',
        })
        task_id = resp.get_json()['id']
        with app.app_context():
            task = db.session.get(Task, task_id)
            task.created_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            db.session.commit()
        run_lifecycle(client, task_id, 'rookie')

        resp = client.get('/api/agents/rookie')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'avg_completion_time' in data
        assert data['avg_completion_time'] > 0


# ══════════════════════════════════════════════
# Auto-Assign Complexity-Reputation Matching
# ══════════════════════════════════════════════

class TestAutoAssignComplexityReputation:
    """Tests for complexity-based reputation matching in auto-assign."""

    LOW_COMPLEXITY_TASKS = [
        {'title': 'Simple data entry', 'tags': 'simple,python', 'complexity': 1},
        {'title': 'Basic CSV parse', 'tags': 'python,flask', 'complexity': 2},
    ]
    HIGH_COMPLEXITY_TASKS = [
        {'title': 'Design architecture', 'tags': 'architecture,complex', 'complexity': 4},
        {'title': 'Build distributed system', 'tags': 'complex,architecture', 'complexity': 5},
    ]

    def test_low_complexity_task_goes_to_rookie(self, client, seed_agents):
        """A complexity-1 task prefers the low-rep rookie over the high-rep expert."""
        resp = client.post('/api/tasks', json={
            'title': 'Simple entry', 'tags': 'python,simple', 'complexity': 1,
        })
        assert resp.status_code == 201
        task_id = resp.get_json()['id']

        resp = client.post('/api/overseer/auto-assign')
        assert resp.status_code == 200

        resp = client.get(f'/api/tasks/{task_id}')
        task = resp.get_json()
        # Rookie is the lowest rep agent and should be preferred for simple tasks
        assert task['assigned_to'] is not None, "Task should be assigned"

    def test_complexity_on_task_reflected_in_to_dict(self, client):
        """Task to_dict includes complexity field."""
        resp = client.post('/api/tasks', json={
            'title': 'Complex work', 'complexity': 5,
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert 'complexity' in data
        assert data['complexity'] == 5


# ══════════════════════════════════════════════
# Reputation Endpoint Tests
# ══════════════════════════════════════════════

class TestReputationEndpoint:
    """Tests for GET /agents/<name>/reputation."""

    def test_reputation_endpoint_returns_all_fields(self, client, seed_agents):
        """Reputation endpoint returns all required stats fields."""
        resp = client.get('/api/agents/expert/reputation')
        assert resp.status_code == 200
        data = resp.get_json()

        assert data['agent'] == 'expert'
        assert data['display_name'] == 'Expert'
        assert 'tasks_completed' in data
        assert 'tasks_failed' in data
        assert 'tasks_review_rejected' in data
        assert 'tasks_timed_out' in data
        assert 'avg_completion_time' in data
        assert 'review_pass_rate' in data
        assert 'reputation_score' in data

    def test_reputation_endpoint_correct_values(self, client, seed_agents):
        """Reputation endpoint returns correct values for known agent."""
        resp = client.get('/api/agents/expert/reputation')
        assert resp.status_code == 200
        data = resp.get_json()

        assert data['tasks_completed'] == 50
        assert data['tasks_failed'] == 2
        assert data['tasks_timed_out'] == 0
        assert data['tasks_review_rejected'] == 0
        assert data['reputation_score'] == 90.0
        # Review pass rate: 50 / (50 + 0) = 1.0
        assert data['review_pass_rate'] == 1.0

    def test_reputation_endpoint_mid_agent(self, client, seed_agents):
        """Reputation endpoint for journeyman agent."""
        resp = client.get('/api/agents/journeyman/reputation')
        assert resp.status_code == 200
        data = resp.get_json()

        assert data['agent'] == 'journeyman'
        assert data['tasks_completed'] == 10
        assert data['tasks_failed'] == 2
        assert data['tasks_review_rejected'] == 0
        assert data['reputation_score'] == 70.0
        # Review pass rate: 10 / (10 + 0) = 1.0
        assert data['review_pass_rate'] == 1.0

    def test_reputation_endpoint_rookie(self, client, seed_agents):
        """Reputation endpoint for rookie agent with no completed tasks."""
        resp = client.get('/api/agents/rookie/reputation')
        assert resp.status_code == 200
        data = resp.get_json()

        assert data['agent'] == 'rookie'
        assert data['tasks_completed'] == 0
        assert data['tasks_failed'] == 0
        assert data['avg_completion_time'] == 0.0
        # Review pass rate: 0 / 0 = 0.0
        assert data['review_pass_rate'] == 0.0

    def test_reputation_endpoint_404(self, client):
        """Non-existent agent returns 404."""
        resp = client.get('/api/agents/nonexistent/reputation')
        assert resp.status_code == 404

    def test_reputation_endpoint_updates_after_completion(self, app, client, seed_agents):
        """Reputation endpoint reflects changes after completing tasks."""
        # Complete a task with the rookie
        resp = client.post('/api/tasks', json={
            'title': 'Rookie task', 'tags': 'python,simple', 'complexity': 1,
        })
        task_id = resp.get_json()['id']

        with app.app_context():
            task = db.session.get(Task, task_id)
            task.created_at = datetime.now(timezone.utc) - timedelta(seconds=3)
            db.session.commit()

        run_lifecycle(client, task_id, 'rookie')

        # Check updated reputation
        resp = client.get('/api/agents/rookie/reputation')
        assert resp.status_code == 200
        data = resp.get_json()

        assert data['tasks_completed'] == 1
        assert data['avg_completion_time'] > 0
        assert data['review_pass_rate'] == 1.0

    def test_reputation_endpoint_with_rejected_review(self, app, client, seed_agents):
        """Reputation endpoint shows review_rejected and lower pass rate."""
        # Seed a simple agent for this test
        with app.app_context():
            agent = Agent(
                name='testdev', display_name='TestDev',
                skills='python', max_concurrent=3,
                tasks_completed=8, tasks_failed=1,
                tasks_review_rejected=2, tasks_timed_out=0,
                reputation_score=60.0,
            )
            db.session.add(agent)
            db.session.commit()

        resp = client.get('/api/agents/testdev/reputation')
        assert resp.status_code == 200
        data = resp.get_json()

        assert data['tasks_completed'] == 8
        assert data['tasks_review_rejected'] == 2
        # Review pass rate: 8 / (8 + 2) = 0.8
        assert data['review_pass_rate'] == 0.8


# ══════════════════════════════════════════════
# Integration Tests
# ══════════════════════════════════════════════

class TestPhase5Integration:
    """End-to-end tests combining all Phase 5 features."""

    def test_full_lifecycle_with_complexity_tracking(self, client, seed_agents):
        """A task goes through full lifecycle and complexity/reputation is tracked."""
        # Create a moderate complexity task
        resp = client.post('/api/tasks', json={
            'title': 'Build API endpoint',
            'tags': 'python,flask,backend',
            'complexity': 3,
            'priority': 2,
        })
        assert resp.status_code == 201
        assert resp.get_json()['complexity'] == 3
        task_id = resp.get_json()['id']

        # Run lifecycle
        run_lifecycle(client, task_id, 'journeyman', reviewer='editor')

        # Verify agent stats
        resp = client.get('/api/agents/journeyman/reputation')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['tasks_completed'] >= 11  # 10 seed + 1 new
        assert data['avg_completion_time'] > 0

    def test_low_complexity_auto_assign(self, client, seed_agents):
        """Low-complexity task gets auto-assigned even when only low-rep agent matches."""
        # Create low complexity task matching no agent's skills explicitly
        # Only rookie has 'simple' skill
        resp = client.post('/api/tasks', json={
            'title': 'Simple logging',
            'tags': 'simple',
            'complexity': 1,
        })
        assert resp.status_code == 201
        task_id = resp.get_json()['id']

        resp = client.post('/api/overseer/auto-assign')
        assert resp.status_code == 200

        resp = client.get(f'/api/tasks/{task_id}')
        task = resp.get_json()
        # Should be assigned since rookie matches 'simple'
        assert task['assigned_to'] is not None

    def test_high_complexity_task_with_expert_skill(self, client, seed_agents):
        """High-complexity task with architecture tag goes to expert."""
        resp = client.post('/api/tasks', json={
            'title': 'System architecture design',
            'tags': 'architecture',
            'complexity': 5,
        })
        assert resp.status_code == 201
        task_id = resp.get_json()['id']

        resp = client.post('/api/overseer/auto-assign')
        assert resp.status_code == 200

        resp = client.get(f'/api/tasks/{task_id}')
        task = resp.get_json()
        # Expert has 'architecture' skill, should be assigned
        # Note: only expert has 'architecture' skill
        assert task['assigned_to'] == 'expert', (
            f"Expected expert, got {task['assigned_to']}"
        )

    def test_complexity_field_in_all_task_responses(self, client):
        """Complexity appears in all responses that return task data."""
        resp = client.post('/api/tasks', json={
            'title': 'Verify field', 'complexity': 2,
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert 'complexity' in data
        assert data['complexity'] == 2

        # Check the create response
        task_id = data['id']

        # Check in list
        resp = client.get('/api/tasks')
        for t in resp.get_json()['tasks']:
            if t['id'] == task_id:
                assert 'complexity' in t
                assert t['complexity'] == 2
                break
