"""tests/test_reviewer_semantics.py — Tests for reviewer lifecycle semantics.

Covers:
- Self-review prevention on in_review tasks (claimed_by path)
- Self-review prevention on assigned tasks (handoff/EventLog path)
- Worker status reset to idle on request_changes
- Reviewer agent status sync via _sync_agent_status
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


def _create_and_submit(client, agent='coder'):
    """Helper: create task → assign → claim → start → submit."""
    resp = client.post('/api/tasks', json={'title': 'Review test'})
    task_id = resp.get_json()['id']
    client.post(f'/api/tasks/{task_id}/assign', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/claim', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/start', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/submit?skip_wait=true', json={
        'agent': agent, 'result': 'Done',
    })
    return task_id


class TestSelfReviewPrevention:
    """Reviewer cannot review their own work."""

    def test_cannot_self_review_in_review(self, client):
        """Worker cannot review their own submitted task (in_review path)."""
        task_id = _create_and_submit(client, agent='coder')
        resp = client.post(f'/api/tasks/{task_id}/review', json={
            'reviewer': 'coder', 'decision': 'approve',
        })
        assert resp.status_code == 409
        assert 'same agent' in resp.get_json()['error']

    def test_different_reviewer_can_approve(self, client):
        """A different reviewer can approve the task."""
        task_id = _create_and_submit(client, agent='coder')
        resp = client.post(f'/api/tasks/{task_id}/review', json={
            'reviewer': 'editor', 'decision': 'approve',
        })
        assert resp.status_code == 200
        assert resp.get_json()['task']['status'] == 'completed'

    def test_self_review_on_assigned_handoff(self, client):
        """After a handoff, the original worker cannot review via the assigned path.

        Flow: coder claims and submits → handoff accepted (assigned_to=editor, claimed_by=None)
        → coder tries to review → should be blocked.
        """
        task_id = _create_and_submit(client, agent='coder')

        # Move to in_review, then escalate back so we can do a handoff
        # Actually, let's create a simpler path: assign to coder, handoff to editor,
        # then try to have coder review.

        # Create a fresh task for handoff path
        resp = client.post('/api/tasks', json={'title': 'Handoff review test'})
        task_id2 = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id2}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id2}/claim', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id2}/start', json={'agent': 'coder'})

        # Create handoff from coder to editor
        resp = client.post(f'/api/tasks/{task_id2}/handoff', json={
            'from_agent': 'coder', 'to_agent': 'editor',
            'message': 'Please review this',
        })
        handoff_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id2}/handoff/{handoff_id}/accept')

        # Now task is assigned to editor. Check: coder previously worked on it.
        # The EventLog should have 'claimed' and 'in_progress' events from coder.
        # But coder can't review because they did the work.
        # Actually — in this flow the task is assigned to editor as reviewer,
        # and coder was the worker. Let's verify coder can't review it.
        task = db.session.get(Task, task_id2)
        assert task.status == 'assigned'
        assert task.assigned_to == 'editor'
        assert task.claimed_by is None

        # Coder tries to review — should be blocked via EventLog lookup
        resp = client.post(f'/api/tasks/{task_id2}/review', json={
            'reviewer': 'coder', 'decision': 'approve',
        })
        assert resp.status_code == 409
        assert 'same agent' in resp.get_json()['error']

    def test_assigned_reviewer_can_approve(self, client):
        """The assigned reviewer (editor) can approve a handoff task."""
        resp = client.post('/api/tasks', json={'title': 'Handoff approve'})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/start', json={'agent': 'coder'})

        resp = client.post(f'/api/tasks/{task_id}/handoff', json={
            'from_agent': 'coder', 'to_agent': 'editor',
            'message': 'Review please',
        })
        handoff_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/handoff/{handoff_id}/accept')

        # Editor (the assigned reviewer) reviews
        resp = client.post(f'/api/tasks/{task_id}/review', json={
            'reviewer': 'editor', 'decision': 'approve',
        })
        assert resp.status_code == 200
        assert resp.get_json()['task']['status'] == 'completed'


class TestRequestChangesWorkerStatus:
    """When a reviewer requests changes, the worker goes back to idle."""

    def test_request_changes_sets_worker_idle(self, client):
        task_id = _create_and_submit(client, agent='coder')

        # Verify coder is busy before review
        agent_resp = client.get('/api/agents')
        agents = agent_resp.get_json()['agents']
        coder = next(a for a in agents if a['name'] == 'coder')
        assert coder['status'] == 'busy'

        # Reviewer requests changes
        resp = client.post(f'/api/tasks/{task_id}/review', json={
            'reviewer': 'editor', 'decision': 'request_changes',
            'feedback': 'Needs more tests',
        })
        assert resp.status_code == 200
        assert resp.get_json()['task']['status'] == 'needs_revision'

        # Verify coder is now idle
        agent_resp = client.get('/api/agents')
        agents = agent_resp.get_json()['agents']
        coder = next(a for a in agents if a['name'] == 'coder')
        assert coder['status'] == 'idle'

    def test_approve_sets_worker_idle(self, client):
        """On approve, worker should also be idle (existing behavior, verify it)."""
        task_id = _create_and_submit(client, agent='coder')

        resp = client.post(f'/api/tasks/{task_id}/review', json={
            'reviewer': 'editor', 'decision': 'approve',
        })
        assert resp.status_code == 200

        agent_resp = client.get('/api/agents')
        agents = agent_resp.get_json()['agents']
        coder = next(a for a in agents if a['name'] == 'coder')
        assert coder['status'] == 'idle'


class TestReviewerStatusSync:
    """Reviewer agent status is synced properly after review."""

    def test_reviewer_stays_idle_when_no_other_tasks(self, client):
        """Reviewer with no other active tasks should be idle after reviewing."""
        task_id = _create_and_submit(client, agent='coder')

        resp = client.post(f'/api/tasks/{task_id}/review', json={
            'reviewer': 'editor', 'decision': 'approve',
        })
        assert resp.status_code == 200

        agent_resp = client.get('/api/agents')
        agents = agent_resp.get_json()['agents']
        editor = next(a for a in agents if a['name'] == 'editor')
        assert editor['status'] == 'idle'

    def test_reviewer_heartbeat_updated(self, client):
        """Reviewer's last_heartbeat should be updated after review."""
        task_id = _create_and_submit(client, agent='coder')

        resp = client.post(f'/api/tasks/{task_id}/review', json={
            'reviewer': 'editor', 'decision': 'approve',
        })
        assert resp.status_code == 200

        agent_resp = client.get('/api/agents')
        agents = agent_resp.get_json()['agents']
        editor = next(a for a in agents if a['name'] == 'editor')
        assert editor['last_heartbeat'] is not None
