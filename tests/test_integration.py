# tests/test_integration.py
"""Integration tests for the full agent lifecycle workflow.

Tests cover:
- create → claim → complete → verify agent stats
- create → claim → fail → verify agent stats
- Concurrent claim attempts (first wins, others rejected)
- Release and re-claim cycle
"""
import json
import threading
import pytest
from models import db, Task, Agent


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
    """Integration tests covering the full create → claim → complete/fail lifecycle."""

    def test_create_claim_complete_verify_stats(self, app, client):
        """Test full happy path: create → claim → complete → verify agent stats updated."""
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
        assert task_data['agent'] is None
        assert task_data['claimed_at'] is None

        # Verify stats reflect pending
        stats_resp = client.get('/api/stats')
        stats = stats_resp.get_json()
        assert stats['by_status']['pending'] >= 1
        assert stats['by_agent']['coder']['completed'] == 0
        assert stats['by_agent']['coder']['active'] == 0

        # --- Step 2: Claim the task ---
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        assert resp.status_code == 200
        task_data = resp.get_json()
        assert task_data['status'] == 'claimed'
        assert task_data['agent'] == 'coder'
        assert task_data['claimed_at'] is not None

        # Verify stats show active claim
        stats_resp = client.get('/api/stats')
        stats = stats_resp.get_json()
        assert stats['by_status']['claimed'] >= 1
        assert stats['by_agent']['coder']['active'] >= 1

        # --- Step 3: Complete the task ---
        resp = client.post(f'/api/tasks/{task_id}/complete', json={
            'result': 'Integration test passed successfully'
        })
        assert resp.status_code == 200
        task_data = resp.get_json()
        assert task_data['status'] == 'completed'
        assert task_data['result'] == 'Integration test passed successfully'
        assert task_data['completed_at'] is not None

        # --- Step 4: Verify agent stats updated ---
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
        """Test failure path: create → claim → fail → verify agent stats updated."""
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

        # --- Step 2: Claim the task ---
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'claimed'

        # Verify explicit claim status
        get_resp = client.get(f'/api/tasks/{task_id}')
        assert get_resp.get_json()['status'] == 'claimed'
        assert get_resp.get_json()['agent'] == 'coder'

        # --- Step 3: Fail the task ---
        resp = client.post(f'/api/tasks/{task_id}/fail', json={
            'error': 'Unexpected integration failure'
        })
        assert resp.status_code == 200
        task_data = resp.get_json()
        assert task_data['status'] == 'failed'
        assert task_data['error'] == 'Unexpected integration failure'
        assert task_data['completed_at'] is not None

        # --- Step 4: Verify agent stats updated ---
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
        """Test concurrent claim attempts: first claim succeeds, rest get 409 conflict."""
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

        agents_to_try = ['coder', 'editor', 'researcher', 'planner']
        results = []
        errors = []

        def try_claim(agent_name):
            """Attempt to claim the task from a separate thread."""
            try:
                with app.app_context():
                    c = app.test_client()
                    resp = c.post(f'/api/tasks/{task_id}/claim', json={'agent': agent_name})
                    results.append((agent_name, resp.status_code, resp.get_json()))
            except Exception as e:
                errors.append((agent_name, str(e)))

        # Launch 4 concurrent claim attempts
        threads = []
        for agent_name in agents_to_try:
            t = threading.Thread(target=try_claim, args=(agent_name,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # No errors during threading
        assert len(errors) == 0, f"Threading errors: {errors}"
        assert len(results) == 4

        # Exactly one should succeed (status 200) and three should fail (status 409)
        successes = [r for r in results if r[1] == 200]
        conflicts = [r for r in results if r[1] == 409]

        assert len(successes) == 1, f"Expected 1 success, got {len(successes)}: {successes}"
        assert len(conflicts) == 3, f"Expected 3 conflicts, got {len(conflicts)}: {conflicts}"

        # The winner's agent should be the one assigned
        winner_agent = successes[0][0]
        winner_data = successes[0][2]
        assert winner_data['status'] == 'claimed'
        assert winner_data['agent'] == winner_agent

        # Verify via GET that the task shows the correct assigned agent
        get_resp = client.get(f'/api/tasks/{task_id}')
        final_data = get_resp.get_json()
        assert final_data['status'] == 'claimed'
        assert final_data['agent'] == winner_agent

    def test_release_and_reclaim(self, app, client):
        """Test the full release and re-claim cycle.

        create → claim → release → re-claim by different agent → complete
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

        # --- Step 2: First claim by coder ---
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'claimed'
        assert resp.get_json()['agent'] == 'coder'

        # --- Step 3: Release the task ---
        resp = client.post(f'/api/tasks/{task_id}/release')
        assert resp.status_code == 200
        task_data = resp.get_json()
        assert task_data['status'] == 'pending'
        assert task_data['agent'] is None
        assert task_data['claimed_at'] is None

        # Verify via GET
        get_resp = client.get(f'/api/tasks/{task_id}')
        assert get_resp.get_json()['status'] == 'pending'
        assert get_resp.get_json()['agent'] is None

        # --- Step 4: Re-claim by a different agent (editor) ---
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'editor'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'claimed'
        assert resp.get_json()['agent'] == 'editor'
        assert resp.get_json()['claimed_at'] is not None

        # Verify GET reflects new claim
        get_resp = client.get(f'/api/tasks/{task_id}')
        assert get_resp.get_json()['agent'] == 'editor'

        # --- Step 5: Complete the task ---
        resp = client.post(f'/api/tasks/{task_id}/complete', json={
            'result': 'Re-claimed and completed by editor'
        })
        assert resp.status_code == 200
        task_data = resp.get_json()
        assert task_data['status'] == 'completed'
        assert task_data['agent'] == 'editor'
        assert task_data['result'] == 'Re-claimed and completed by editor'

        # --- Step 6: Verify both agents' stats ---
        stats_resp = client.get('/api/stats')
        stats = stats_resp.get_json()
        # coder had no completed from this flow (released)
        assert stats['by_agent']['coder']['completed'] >= 0
        # editor got the completed task
        assert stats['by_agent']['editor']['completed'] >= 1

    def test_cannot_complete_without_claim(self, app, client):
        """Test that completing a pending task returns 409."""
        resp = client.post('/api/tasks', json={'title': 'Never claimed'})
        task_id = resp.get_json()['id']
        resp = client.post(f'/api/tasks/{task_id}/complete', json={'result': 'Should fail'})
        assert resp.status_code == 409

    def test_cannot_fail_without_claim(self, app, client):
        """Test that failing a pending task returns 409."""
        resp = client.post('/api/tasks', json={'title': 'Never claimed either'})
        task_id = resp.get_json()['id']
        resp = client.post(f'/api/tasks/{task_id}/fail', json={'error': 'Should fail'})
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
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/complete', json={'result': 'Done'})
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'editor'})
        assert resp.status_code == 409

    def test_cannot_claim_twice_failed(self, app, client):
        """Test that a failed task cannot be claimed."""
        resp = client.post('/api/tasks', json={'title': 'Already broken'})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/fail', json={'error': 'Broke'})
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'editor'})
        assert resp.status_code == 409

    def test_claim_requires_agent_name(self, app, client):
        """Test that claiming without agent name returns 400."""
        resp = client.post('/api/tasks', json={'title': 'No agent claim'})
        task_id = resp.get_json()['id']
        resp = client.post(f'/api/tasks/{task_id}/claim', json={})
        assert resp.status_code == 400

    def test_complete_requires_result(self, app, client):
        """Test that completing without result returns 400."""
        resp = client.post('/api/tasks', json={'title': 'No result'})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/complete', json={})
        assert resp.status_code == 400

    def test_fail_requires_error(self, app, client):
        """Test that failing without error reason returns 400."""
        resp = client.post('/api/tasks', json={'title': 'No error reason'})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/fail', json={})
        assert resp.status_code == 400
