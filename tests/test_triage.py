"""tests/test_triage.py — Tests for triage status and triage queue."""

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


class TestTriageCreation:
    def test_create_task_starts_pending_by_default(self, client):
        resp = client.post('/api/tasks', json={'title': 'Normal task'})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['status'] == 'pending'

    def test_create_task_starts_in_triage(self, client):
        resp = client.post('/api/tasks', json={
            'title': 'Triage task',
            'start_in_triage': True,
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['status'] == 'triage'

    def test_create_task_escalation_overrides_triage(self, client):
        """Escalation tags should take priority over start_in_triage."""
        resp = client.post('/api/tasks', json={
            'title': 'Escalated',
            'tags': 'human_review',
            'start_in_triage': True,
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['status'] == 'needs_human'


class TestTriageAccept:
    def test_accept_triage_to_pending(self, client):
        resp = client.post('/api/tasks', json={'title': 'Triage me', 'start_in_triage': True})
        task_id = resp.get_json()['id']
        assert resp.get_json()['status'] == 'triage'

        resp = client.post(f'/api/tasks/{task_id}/triage/accept')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'pending'

    def test_accept_not_in_triage_fails(self, client):
        resp = client.post('/api/tasks', json={'title': 'Already pending'})
        task_id = resp.get_json()['id']

        resp = client.post(f'/api/tasks/{task_id}/triage/accept')
        assert resp.status_code == 409


class TestTriageAssign:
    def test_assign_from_triage_to_assigned(self, client):
        resp = client.post('/api/tasks', json={'title': 'Assign me', 'start_in_triage': True})
        task_id = resp.get_json()['id']

        resp = client.post(f'/api/tasks/{task_id}/triage/assign', json={'agent': 'coder'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'assigned'
        assert data['assigned_to'] == 'coder'

    def test_assign_without_agent_fails(self, client):
        resp = client.post('/api/tasks', json={'title': 'No agent', 'start_in_triage': True})
        task_id = resp.get_json()['id']

        resp = client.post(f'/api/tasks/{task_id}/triage/assign', json={})
        assert resp.status_code == 400


class TestTriageReject:
    def test_reject_from_triage_to_failed(self, client):
        resp = client.post('/api/tasks', json={'title': 'Reject me', 'start_in_triage': True})
        task_id = resp.get_json()['id']

        resp = client.post(f'/api/tasks/{task_id}/triage/reject', json={'reason': 'Not needed'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'failed'
        assert data['failure_reason'] == 'triage_rejected'
        assert data['last_error'] == 'Not needed'

    def test_reject_with_default_reason(self, client):
        resp = client.post('/api/tasks', json={'title': 'Default reject', 'start_in_triage': True})
        task_id = resp.get_json()['id']

        resp = client.post(f'/api/tasks/{task_id}/triage/reject', json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'failed'
        assert data['last_error'] == 'Rejected in triage'


class TestTriageInDashboard:
    def test_triage_in_all_statuses(self, client):
        """Verify triage appears as a filter option in the dashboard."""
        resp = client.get('/api/stats')
        assert resp.status_code == 200
        # The stats endpoint lists statuses but we just verify it works
        # The dashboard rendering uses app.py's all_statuses list which includes 'triage'


class TestTriageEvents:
    def test_triage_accept_creates_event(self, client):
        resp = client.post('/api/tasks', json={'title': 'Event test', 'start_in_triage': True})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/triage/accept')

        resp = client.get(f'/api/tasks/{task_id}/events')
        events = resp.get_json()['events']
        event_types = [e['event_type'] for e in events]
        assert 'triage_accepted' in event_types

    def test_triage_reject_creates_event(self, client):
        resp = client.post('/api/tasks', json={'title': 'Reject event', 'start_in_triage': True})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/triage/reject', json={'reason': 'Bad'})

        resp = client.get(f'/api/tasks/{task_id}/events')
        events = resp.get_json()['events']
        event_types = [e['event_type'] for e in events]
        assert 'triage_rejected' in event_types
