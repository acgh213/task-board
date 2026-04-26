"""Tests for the workflow audit endpoint GET /api/tasks/<id>/audit."""
import json
import pytest
from models import db, Task, Agent, EventLog, HandoffRequest


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
        ]:
            db.session.add(Agent(name=name, display_name=display, model=model))
        db.session.commit()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _full_complete(client, task_id, agent='coder', reviewer='editor', result='Done'):
    """Helper: run full happy-path lifecycle."""
    client.post(f'/api/tasks/{task_id}/assign', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/claim', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/start', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/submit?skip_wait=true', json={'agent': agent, 'result': result})
    return client.post(f'/api/tasks/{task_id}/review', json={'reviewer': reviewer, 'decision': 'approve'})


class TestWorkflowAudit:

    def test_audit_returns_full_lifecycle(self, client):
        """Test audit endpoint returns full lifecycle for a completed task."""
        # Create a task
        resp = client.post('/api/tasks', json={'title': 'Audit test task', 'tags': 'test'})
        assert resp.status_code == 201
        task_id = resp.get_json()['id']

        # Run full lifecycle
        _full_complete(client, task_id)

        # Get audit
        resp = client.get(f'/api/tasks/{task_id}/audit')
        assert resp.status_code == 200
        data = resp.get_json()

        # Check top-level fields
        assert data['task_id'] == task_id
        assert data['task_title'] == 'Audit test task'

        # Check lifecycle has events
        lifecycle = data['lifecycle']
        assert len(lifecycle) >= 6  # created, assigned, claimed, in_progress, submitted, completed

        # Check task_created event
        created_event = [e for e in lifecycle if e['event_type'] == 'task_created']
        assert len(created_event) == 1
        assert created_event[0]['status_before'] is None
        assert created_event[0]['status_after'] == 'pending'
        assert created_event[0]['actor'] is None

        # Check assigned event
        assigned_event = [e for e in lifecycle if e['event_type'] == 'assigned']
        assert len(assigned_event) == 1
        assert assigned_event[0]['actor'] == 'coder'

        # Check claimed event
        claimed_event = [e for e in lifecycle if e['event_type'] == 'claimed']
        assert len(claimed_event) == 1
        assert claimed_event[0]['actor'] == 'coder'

        # Check completed event
        completed_event = [e for e in lifecycle if e['event_type'] == 'completed']
        assert len(completed_event) == 1

    def test_audit_summary_counts(self, client):
        """Test audit summary has correct counts."""
        # Create a task
        resp = client.post('/api/tasks', json={'title': 'Summary test', 'tags': 'test'})
        task_id = resp.get_json()['id']

        # Run full lifecycle
        _full_complete(client, task_id)

        # Get audit
        resp = client.get(f'/api/tasks/{task_id}/audit')
        data = resp.get_json()

        summary = data['summary']
        assert summary['total_events'] >= 6
        assert summary['final_status'] == 'completed'
        assert 'coder' in summary['agents_involved']
        assert 'editor' in summary['agents_involved']

    def test_audit_with_handoffs(self, client):
        """Test audit with handoffs shows handoff events."""
        # Create a task
        resp = client.post('/api/tasks', json={'title': 'Handoff audit test', 'tags': 'test'})
        assert resp.status_code == 201
        task_id = resp.get_json()['id']

        # Start lifecycle
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})

        # Create a handoff request
        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'to_agent': 'researcher',
            'message': 'Handoff for research',
        })
        assert resp.status_code == 201
        handoff_id = resp.get_json()['id']

        # Accept the handoff
        client.post(f'/api/tasks/{task_id}/handoff/{handoff_id}/accept')

        # Continue with new agent
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'researcher'})
        client.post(f'/api/tasks/{task_id}/start', json={'agent': 'researcher'})
        client.post(f'/api/tasks/{task_id}/submit?skip_wait=true', json={
            'agent': 'researcher', 'result': 'Research complete'
        })
        client.post(f'/api/tasks/{task_id}/review', json={
            'reviewer': 'editor', 'decision': 'approve'
        })

        # Get audit
        resp = client.get(f'/api/tasks/{task_id}/audit')
        assert resp.status_code == 200
        data = resp.get_json()

        # Check handoff event
        handoff_events = [e for e in data['lifecycle'] if 'handoff' in e['event_type']]
        assert len(handoff_events) >= 1

        handoff_req = [e for e in handoff_events if e['event_type'] == 'handoff_requested']
        assert len(handoff_req) >= 1
        # handoff_id in the response should match (or contain) the handoff request id
        assert handoff_req[0]['handoff_id'] is not None

        # Check summary includes handoff
        assert data['summary']['handoffs'] >= 1
        assert 'researcher' in data['summary']['agents_involved']
        assert data['summary']['final_status'] == 'completed'

    def test_audit_non_existent_task(self, client):
        """Test audit for non-existent task returns 404."""
        resp = client.get('/api/tasks/99999/audit')
        assert resp.status_code == 404

    def test_audit_shows_xp_events(self, client):
        """Test that XP events are shown in audit."""
        # XP events are generated during task completion for the worker
        resp = client.post('/api/tasks', json={
            'title': 'XP audit test',
            'complexity': 1,
            'tags': 'test',
        })
        task_id = resp.get_json()['id']

        _full_complete(client, task_id)

        resp = client.get(f'/api/tasks/{task_id}/audit')
        data = resp.get_json()

        # Check for XP events
        xp_events = [e for e in data['lifecycle'] if e['xp_awarded'] is not None]
        assert len(xp_events) >= 1

        # Summary should have total_xp_awarded > 0
        assert data['summary']['total_xp_awarded'] > 0

    def test_audit_claim_reconstruction(self, client):
        """Test that claimed_by_before/after reconstruction is correct."""
        resp = client.post('/api/tasks', json={'title': 'Claim track', 'tags': 'test'})
        task_id = resp.get_json()['id']

        # Assign and claim
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        resp_claim = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        assert resp_claim.status_code == 200

        resp = client.get(f'/api/tasks/{task_id}/audit')
        data = resp.get_json()

        claimed_events = [e for e in data['lifecycle'] if e['event_type'] == 'claimed']
        assert len(claimed_events) >= 1

        # Before claim, claimed_by should be None
        assert claimed_events[0]['claimed_by_before'] is None
        # After claim, claimed_by should be 'coder'
        assert claimed_events[0]['claimed_by_after'] == 'coder'

    def test_audit_never_submitted_task(self, client):
        """Test audit for a task that's still in progress."""
        resp = client.post('/api/tasks', json={'title': 'In progress audit', 'tags': 'test'})
        task_id = resp.get_json()['id']

        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/start', json={'agent': 'coder'})

        resp = client.get(f'/api/tasks/{task_id}/audit')
        assert resp.status_code == 200
        data = resp.get_json()

        assert data['summary']['final_status'] == 'in_progress'
        assert data['summary']['total_events'] >= 4  # created, assigned, claimed, in_progress
