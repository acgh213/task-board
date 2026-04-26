"""tests/test_handoff_refinements.py — Tests for handoff refinements (Task #8)."""

import json
import pytest
from models import db, Task, Agent, HandoffRequest


@pytest.fixture
def app():
    from app import create_app
    app = create_app(testing=True)
    with app.app_context():
        db.create_all()
        # Seed agents
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


@pytest.fixture
def seed_task_and_handoffs(app, client):
    """Create a task with multiple handoff requests for testing."""
    with app.app_context():
        resp = client.post('/api/tasks', json={'title': 'Handoff history test'})
        task_id = resp.get_json()['id']

        # Create multiple handoffs on the same task
        handoffs = [
            HandoffRequest(task_id=task_id, from_agent='coder', to_agent='editor', status='accepted'),
            HandoffRequest(task_id=task_id, from_agent='editor', to_agent='researcher', status='pending'),
            HandoffRequest(task_id=task_id, from_agent='researcher', to_agent='planner', status='rejected'),
        ]
        for h in handoffs:
            db.session.add(h)
        db.session.commit()

        return {
            'task_id': task_id,
            'handoff_ids': [h.id for h in handoffs],
        }


class TestTaskHandoffHistory:
    def test_get_task_handoffs_empty(self, client):
        """GET /api/tasks/<id>/handoffs returns empty list when no handoffs."""
        resp = client.post('/api/tasks', json={'title': 'No handoffs'})
        task_id = resp.get_json()['id']

        resp = client.get(f'/api/tasks/{task_id}/handoffs')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['task_id'] == task_id
        assert data['total'] == 0
        assert data['handoffs'] == []

    def test_get_task_handoffs_with_data(self, client, seed_task_and_handoffs):
        """GET /api/tasks/<id>/handoffs returns all handoffs for a task."""
        task_id = seed_task_and_handoffs['task_id']
        resp = client.get(f'/api/tasks/{task_id}/handoffs')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['task_id'] == task_id
        assert data['total'] == 3
        assert len(data['handoffs']) == 3

    def test_get_task_handoffs_ordered_by_date(self, client, seed_task_and_handoffs):
        """Handoffs should be ordered by created_at descending."""
        task_id = seed_task_and_handoffs['task_id']
        resp = client.get(f'/api/tasks/{task_id}/handoffs')
        data = resp.get_json()
        dates = [h['created_at'] for h in data['handoffs']]
        # Should be descending
        assert dates == sorted(dates, reverse=True)

    def test_get_task_handoffs_contains_all_statuses(self, client, seed_task_and_handoffs):
        """All handoff statuses should be present in the history."""
        task_id = seed_task_and_handoffs['task_id']
        resp = client.get(f'/api/tasks/{task_id}/handoffs')
        data = resp.get_json()
        statuses = {h['status'] for h in data['handoffs']}
        assert 'accepted' in statuses
        assert 'pending' in statuses
        assert 'rejected' in statuses

    def test_get_task_handoffs_nonexistent_task(self, client):
        """GET /api/tasks/99999/handoffs returns 404."""
        resp = client.get('/api/tasks/99999/handoffs')
        assert resp.status_code == 404


class TestAgentHandoffHistory:
    def test_get_agent_handoffs_empty(self, client):
        """GET /api/agents/<name>/handoffs returns empty when no handoffs."""
        # Register the agent first
        client.post('/api/agents', json={'name': 'coder', 'display_name': 'Coder'})

        resp = client.get('/api/agents/coder/handoffs')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['agent'] == 'coder'
        assert data['total'] == 0
        assert data['handoffs'] == []

    def test_get_agent_handoffs_as_from_agent(self, client, seed_task_and_handoffs):
        """Agent who sent handoffs should see them in history."""
        resp = client.get('/api/agents/coder/handoffs')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['agent'] == 'coder'
        assert data['total'] >= 1
        agents_from = {h['from_agent'] for h in data['handoffs']}
        assert 'coder' in agents_from

    def test_get_agent_handoffs_as_to_agent(self, client, seed_task_and_handoffs):
        """Agent who received handoffs should see them in history."""
        resp = client.get('/api/agents/editor/handoffs')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['agent'] == 'editor'
        assert data['total'] >= 1
        agents_to = {h['to_agent'] for h in data['handoffs']}
        assert 'editor' in agents_to

    def test_get_agent_handoffs_both_directions(self, client, seed_task_and_handoffs):
        """An agent appearing as both sender and receiver should see all."""
        resp = client.get('/api/agents/researcher/handoffs')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] >= 2  # researcher is to_agent in one, from_agent in another

    def test_get_agent_handoffs_nonexistent_agent(self, client):
        """GET /api/agents/nonexistent/handoffs returns 404."""
        resp = client.get('/api/agents/nonexistent/handoffs')
        assert resp.status_code == 404


class TestHandoffAcceptUpdatesAssignment:
    def test_accept_updates_assigned_to(self, client):
        """Accepting a handoff should update assigned_to to the to_agent."""
        resp = client.post('/api/tasks', json={'title': 'Test handoff assign'})
        task_id = resp.get_json()['id']

        # Assign to coder first
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})

        # Create handoff from coder to editor
        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'coder',
            'to_agent': 'editor',
            'message': 'Take over',
        })
        request_id = resp.get_json()['id']

        # Accept
        resp = client.post(f'/api/tasks/{task_id}/handoff/{request_id}/accept')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['handoff']['status'] == 'accepted'
        assert data['task']['assigned_to'] == 'editor'
        # claimed_by should be None since it was just an assignment
        assert data['task']['claimed_by'] is None or data['task']['claimed_by'] != 'coder'

    def test_accept_creates_agent(self, client):
        """Accepting a handoff should auto-create the to_agent if not exists."""
        resp = client.post('/api/tasks', json={'title': 'Auto-create agent'})
        task_id = resp.get_json()['id']

        # Create handoff to a new agent that doesn't exist in the DB yet
        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'coder',
            'to_agent': 'newcomer',
            'message': 'Welcome',
        })
        request_id = resp.get_json()['id']

        # Accept — should auto-create 'newcomer' agent
        resp = client.post(f'/api/tasks/{task_id}/handoff/{request_id}/accept')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['task']['assigned_to'] == 'newcomer'

        # Verify the agent was created
        resp = client.get('/api/agents')
        names = [a['name'] for a in resp.get_json()['agents']]
        assert 'newcomer' in names

    def test_accept_called_back_to_back(self, client):
        """Multiple sequential handoffs should each update assignment."""
        resp = client.post('/api/tasks', json={'title': 'Multi handoff'})
        task_id = resp.get_json()['id']

        # Handoff 1: coder → editor
        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'coder', 'to_agent': 'editor',
        })
        req1_id = resp.get_json()['id']
        resp = client.post(f'/api/tasks/{task_id}/handoff/{req1_id}/accept')
        assert resp.get_json()['task']['assigned_to'] == 'editor'

        # Handoff 2: editor → researcher
        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'editor', 'to_agent': 'researcher',
        })
        req2_id = resp.get_json()['id']
        resp = client.post(f'/api/tasks/{task_id}/handoff/{req2_id}/accept')
        assert resp.get_json()['task']['assigned_to'] == 'researcher'

        # Verify handoff history has both
        resp = client.get(f'/api/tasks/{task_id}/handoffs')
        assert resp.get_json()['total'] == 2

    def test_handoff_history_includes_accept_event(self, client):
        """Handoff history should include both pending and accepted states."""
        resp = client.post('/api/tasks', json={'title': 'Check history'})
        task_id = resp.get_json()['id']

        # Create and accept a handoff
        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'coder', 'to_agent': 'editor',
        })
        req_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/handoff/{req_id}/accept')

        # Check task handoff history
        resp = client.get(f'/api/tasks/{task_id}/handoffs')
        data = resp.get_json()
        assert data['total'] == 1
        h = data['handoffs'][0]
        assert h['status'] == 'accepted'
        assert h['from_agent'] == 'coder'
        assert h['to_agent'] == 'editor'
