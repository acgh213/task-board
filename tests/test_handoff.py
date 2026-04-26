"""tests/test_handoff.py — Tests for handoff request endpoints."""

import json
import pytest
from models import db, Task, Agent


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


class TestHandoffCreation:
    def test_create_handoff_request(self, client):
        """Create a handoff request and verify it's stored."""
        # Create a task first
        resp = client.post('/api/tasks', json={'title': 'Handoff test'})
        assert resp.status_code == 201
        task_id = resp.get_json()['id']

        # Create handoff request
        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'coder',
            'to_agent': 'editor',
            'message': 'Please take over this task',
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['task_id'] == task_id
        assert data['from_agent'] == 'coder'
        assert data['to_agent'] == 'editor'
        assert data['status'] == 'pending'

    def test_create_handoff_without_from_agent_infers(self, client):
        """Handoff request without from_agent auto-infers from auth header."""
        resp = client.post('/api/tasks', json={'title': 'Handoff infer'})
        task_id = resp.get_json()['id']

        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'to_agent': 'editor',
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['from_agent'] == 'anonymous'  # from auth header

    def test_create_handoff_nonexistent_task(self, client):
        """Handoff for non-existent task returns 404."""
        resp = client.post('/api/tasks/99999/handoff', json={
            'from_agent': 'coder',
            'to_agent': 'editor',
        })
        assert resp.status_code == 404


class TestHandoffAccept:
    def test_accept_handoff(self, client):
        """Accepting a handoff reassigns the task."""
        resp = client.post('/api/tasks', json={'title': 'Handoff accept test'})
        task_id = resp.get_json()['id']
        # First assign the task to coder
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})

        # Create handoff request
        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'coder',
            'to_agent': 'editor',
            'message': 'Take over',
        })
        request_id = resp.get_json()['id']

        # Accept the handoff
        resp = client.post(f'/api/tasks/{task_id}/handoff/{request_id}/accept')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['handoff']['status'] == 'accepted'
        assert data['task']['assigned_to'] == 'editor'

    def test_accept_handoff_resets_releasing_agent_to_idle_when_no_active_work(self, client, app):
        """Accepting a handoff should clear the source agent's stale busy status."""
        resp = client.post('/api/tasks', json={'title': 'Handoff status cleanup'})
        task_id = resp.get_json()['id']

        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})

        with app.app_context():
            coder = db.session.get(Agent, 'coder')
            assert coder.status == 'busy'

        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'coder',
            'to_agent': 'editor',
            'message': 'Take over',
        })
        request_id = resp.get_json()['id']

        resp = client.post(f'/api/tasks/{task_id}/handoff/{request_id}/accept')
        assert resp.status_code == 200

        with app.app_context():
            coder = db.session.get(Agent, 'coder')
            editor = db.session.get(Agent, 'editor')
            task = db.session.get(Task, task_id)
            assert coder.status == 'idle'
            assert editor is not None
            assert task.assigned_to == 'editor'
            assert task.claimed_by is None

    def test_accept_wrong_task_handoff_fails(self, client):
        """Accepting a handoff for a different task should fail."""
        resp1 = client.post('/api/tasks', json={'title': 'Task 1'})
        resp2 = client.post('/api/tasks', json={'title': 'Task 2'})
        task1_id = resp1.get_json()['id']
        task2_id = resp2.get_json()['id']

        # Create handoff on task 1
        resp = client.post(f'/api/tasks/{task1_id}/handoff', json={
            'from_agent': 'coder',
            'to_agent': 'editor',
        })
        request_id = resp.get_json()['id']

        # Try to accept on task 2
        resp = client.post(f'/api/tasks/{task2_id}/handoff/{request_id}/accept')
        assert resp.status_code == 400

    def test_accept_already_resolved_handoff_fails(self, client):
        """Accepting an already accepted/rejected handoff should fail."""
        resp = client.post('/api/tasks', json={'title': 'Double accept'})
        task_id = resp.get_json()['id']

        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'coder',
            'to_agent': 'editor',
        })
        request_id = resp.get_json()['id']

        # Accept twice
        client.post(f'/api/tasks/{task_id}/handoff/{request_id}/accept')
        resp = client.post(f'/api/tasks/{task_id}/handoff/{request_id}/accept')
        assert resp.status_code == 409


class TestHandoffReject:
    def test_reject_handoff(self, client):
        """Rejecting a handoff sets status to rejected."""
        resp = client.post('/api/tasks', json={'title': 'Handoff reject test'})
        task_id = resp.get_json()['id']

        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'coder',
            'to_agent': 'editor',
            'message': 'Handoff request',
        })
        request_id = resp.get_json()['id']

        # Reject the handoff
        resp = client.post(f'/api/tasks/{task_id}/handoff/{request_id}/reject', json={
            'reason': 'Busy with other work',
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'rejected'

    def test_reject_preserves_task_assignment(self, client):
        """Rejecting a handoff should not change task assignment."""
        resp = client.post('/api/tasks', json={'title': 'Keep assignment'})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})

        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'coder',
            'to_agent': 'editor',
        })
        request_id = resp.get_json()['id']

        client.post(f'/api/tasks/{task_id}/handoff/{request_id}/reject')

        # Task should still be assigned to coder
        resp = client.get(f'/api/tasks/{task_id}')
        assert resp.get_json()['assigned_to'] == 'coder'


class TestHandoffEvents:
    def test_handoff_creates_event(self, client):
        """Creating a handoff should log an event."""
        resp = client.post('/api/tasks', json={'title': 'Handoff events'})
        task_id = resp.get_json()['id']

        client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'coder',
            'to_agent': 'editor',
        })

        resp = client.get(f'/api/tasks/{task_id}/events')
        events = resp.get_json()['events']
        event_types = [e['event_type'] for e in events]
        assert 'handoff_requested' in event_types
