"""tests/test_auto_triage.py — Tests for auto-triage rules."""

import json
import pytest
from models import db, Task, Agent


@pytest.fixture
def app():
    from app import create_app
    app = create_app(testing=True)
    with app.app_context():
        db.create_all()
        # Seed agents with skills
        for name, display, model, skills in [
            ('coder', 'Coder', 'deepseek-v4-flash', 'python,flask,backend'),
            ('editor', 'Editor', 'gpt-5-nano', 'writing,editing'),
            ('researcher', 'Researcher', 'deepseek-v4-flash', 'research,analysis'),
        ]:
            db.session.add(Agent(
                name=name, display_name=display, model=model,
                skills=skills, role='worker',
            ))
        db.session.commit()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _create_triage_task(client, title, complexity=3, tags=''):
    """Helper to create a task in triage status."""
    resp = client.post('/api/tasks', json={
        'title': title,
        'complexity': complexity,
        'tags': tags,
        'start_in_triage': True,
    })
    assert resp.status_code == 201
    return resp.get_json()['id']


class TestAutoTriage:
    def test_low_complexity_with_skills_accepted(self, client):
        """Complexity <= 2 with matching skills → auto-accept to pending."""
        task_id = _create_triage_task(client, 'Simple task', complexity=1, tags='python')
        resp = client.post('/api/overseer/auto-triage')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['accepted'] >= 1

        # Verify task is now pending
        resp = client.get(f'/api/tasks/{task_id}')
        assert resp.get_json()['status'] == 'pending'

    def test_low_complexity_no_matching_skills_skipped(self, client):
        """Complexity <= 2 but no matching skills → skipped."""
        task_id = _create_triage_task(client, 'No match', complexity=2, tags='basket_weaving')
        resp = client.post('/api/overseer/auto-triage')
        assert resp.status_code == 200
        data = resp.get_json()
        # Should be skipped since no agent has basket_weaving skill
        skipped = any(r['task_id'] == task_id for r in data['results'] if r['action'] == 'skipped')
        assert skipped or data['skipped'] >= 1

    def test_high_complexity_escalated(self, client):
        """Complexity >= 4 → auto-escalate to needs_human."""
        task_id = _create_triage_task(client, 'Complex task', complexity=5)
        resp = client.post('/api/overseer/auto-triage')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['escalated'] >= 1

        # Verify task is now needs_human
        resp = client.get(f'/api/tasks/{task_id}')
        assert resp.get_json()['status'] == 'needs_human'

    def test_complexity_3_skipped(self, client):
        """Complexity 3 should not match any auto-triage rule."""
        task_id = _create_triage_task(client, 'Mid complexity', complexity=3)
        resp = client.post('/api/overseer/auto-triage')
        assert resp.status_code == 200
        data = resp.get_json()
        # Mid-complexity should be skipped
        skipped = any(r['task_id'] == task_id for r in data['results'] if r['action'] == 'skipped')
        assert skipped or data['skipped'] >= 1

        # Task should still be in triage
        resp = client.get(f'/api/tasks/{task_id}')
        assert resp.get_json()['status'] == 'triage'

    def test_mixed_triage_tasks(self, client):
        """Multiple tasks with different complexities should be handled correctly."""
        low_id = _create_triage_task(client, 'Low', complexity=1, tags='python')
        high_id = _create_triage_task(client, 'High', complexity=5)
        mid_id = _create_triage_task(client, 'Mid', complexity=3)

        resp = client.post('/api/overseer/auto-triage')
        assert resp.status_code == 200
        data = resp.get_json()

        # Low should be accepted (pending)
        resp = client.get(f'/api/tasks/{low_id}')
        assert resp.get_json()['status'] == 'pending'

        # High should be escalated (needs_human)
        resp = client.get(f'/api/tasks/{high_id}')
        assert resp.get_json()['status'] == 'needs_human'

        # Mid should remain in triage
        resp = client.get(f'/api/tasks/{mid_id}')
        assert resp.get_json()['status'] == 'triage'

        assert data['accepted'] >= 1
        assert data['escalated'] >= 1

    def test_auto_triage_no_triage_tasks(self, client):
        """No tasks in triage → all counts zero."""
        resp = client.post('/api/tasks', json={'title': 'Normal task'})
        resp = client.post('/api/overseer/auto-triage')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 0
        assert data['accepted'] == 0
        assert data['escalated'] == 0
        assert data['skipped'] == 0

    def test_auto_triage_logs_events(self, client):
        """Auto-triage decisions should be logged as events."""
        task_id = _create_triage_task(client, 'Log test', complexity=5)
        client.post('/api/overseer/auto-triage')

        resp = client.get(f'/api/tasks/{task_id}/events')
        events = resp.get_json()['events']
        event_types = [e['event_type'] for e in events]
        assert 'auto_triage' in event_types
