"""tests/test_triage_enhancements.py — Tests for triage queue enhancements (Task #5)."""

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
        ]:
            db.session.add(Agent(name=name, display_name=display, model=model))
        db.session.commit()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seed_triage_tasks(app):
    """Seed several tasks in triage status."""
    with app.app_context():
        tasks = [
            Task(title='Triage P1', status='triage', priority=1, tags='research'),
            Task(title='Triage P2', status='triage', priority=2, tags='code'),
            Task(title='Triage P3', status='triage', priority=3, tags='docs'),
            Task(title='Triage Escalate', status='triage', priority=2, tags='human_review'),
            Task(title='Normal pending', status='pending', priority=3),
        ]
        for t in tasks:
            db.session.add(t)
        db.session.commit()
    return tasks


class TestListTriageTasks:
    def test_list_triage_empty(self, client):
        """GET /api/tasks/triage returns empty list when no triage tasks exist."""
        resp = client.get('/api/tasks/triage')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 0
        assert data['tasks'] == []

    def test_list_triage_with_tasks(self, client, seed_triage_tasks):
        """GET /api/tasks/triage returns only triage-status tasks."""
        resp = client.get('/api/tasks/triage')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 4
        for t in data['tasks']:
            assert t['status'] == 'triage'

    def test_list_triage_excludes_non_triage(self, client, seed_triage_tasks):
        """Non-triage tasks should not appear in the triage list."""
        resp = client.get('/api/tasks/triage')
        data = resp.get_json()
        titles = [t['title'] for t in data['tasks']]
        assert 'Normal pending' not in titles


class TestTriageStats:
    def test_triage_stats_empty(self, client):
        """GET /api/triage/stats returns zeros when no triage tasks."""
        resp = client.get('/api/triage/stats')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total_in_triage'] == 0
        assert data['by_priority'] == {}
        assert data['by_complexity'] == {}
        assert data['escalation_prone'] == 0

    def test_triage_stats_with_tasks(self, client, seed_triage_tasks):
        """Stats should reflect the seeded triage tasks."""
        resp = client.get('/api/triage/stats')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total_in_triage'] == 4
        # Check priority breakdown (JSON keys are strings)
        assert data['by_priority'].get('1') == 1
        assert data['by_priority'].get('2') == 2  # P2 + escalate
        assert data['by_priority'].get('3') == 1
        # Check escalation_prone (tasks with escalation tags)
        assert data['escalation_prone'] == 1  # 'Triage Escalate' has human_review tag


class TestTriageBulkAccept:
    def test_bulk_accept_all(self, client, seed_triage_tasks):
        """POST /api/triage/bulk-accept accepts all triage tasks."""
        resp = client.post('/api/triage/bulk-accept', json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['accepted'] == 4
        assert len(data['accepted_ids']) == 4

        # Verify all triage tasks are now pending
        resp = client.get('/api/tasks/triage')
        assert resp.get_json()['total'] == 0

    def test_bulk_accept_specific_ids(self, client, seed_triage_tasks):
        """Bulk accept with specific task_ids only accepts those."""
        triage_tasks = Task.query.filter_by(status='triage').all()
        target_ids = [t.id for t in triage_tasks[:2]]

        resp = client.post('/api/triage/bulk-accept', json={
            'task_ids': target_ids,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['accepted'] == 2

        # Check remaining triage tasks
        remaining = Task.query.filter_by(status='triage').count()
        assert remaining == 2

    def test_bulk_accept_skips_non_triage(self, client, seed_triage_tasks):
        """Bulk accept with specific IDs skips tasks not in triage."""
        pending = Task.query.filter_by(status='pending').first()
        resp = client.post('/api/triage/bulk-accept', json={
            'task_ids': [pending.id],
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['accepted'] == 0
        assert len(data['skipped']) == 1
        assert 'not in triage' in data['skipped'][0]['reason'].lower() or \
               data['skipped'][0]['reason'] != 'not_found'

    def test_bulk_accept_creates_events(self, client, seed_triage_tasks):
        """Bulk accept should log triage_accepted events."""
        resp = client.post('/api/triage/bulk-accept', json={})
        assert resp.status_code == 200
        data = resp.get_json()
        for tid in data['accepted_ids']:
            resp2 = client.get(f'/api/tasks/{tid}/events')
            events = resp2.get_json()['events']
            event_types = [e['event_type'] for e in events]
            assert 'triage_accepted' in event_types


class TestTriageBulkReject:
    def test_bulk_reject_all(self, client, seed_triage_tasks):
        """POST /api/triage/bulk-reject rejects all triage tasks."""
        resp = client.post('/api/triage/bulk-reject', json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['rejected'] == 4
        assert len(data['rejected_ids']) == 4

        # Verify all triage tasks are now failed
        resp = client.get('/api/tasks/triage')
        assert resp.get_json()['total'] == 0

        # Check they are failed
        for tid in data['rejected_ids']:
            resp2 = client.get(f'/api/tasks/{tid}')
            assert resp2.get_json()['status'] == 'failed'

    def test_bulk_reject_with_reason(self, client, seed_triage_tasks):
        """Bulk reject with custom reason should set last_error."""
        resp = client.post('/api/triage/bulk-reject', json={
            'reason': 'Not needed',
        })
        assert resp.status_code == 200
        data = resp.get_json()
        for tid in data['rejected_ids']:
            resp2 = client.get(f'/api/tasks/{tid}')
            task = resp2.get_json()
            assert task['last_error'] == 'Not needed'
            assert task['failure_reason'] == 'triage_rejected'

    def test_bulk_reject_specific_ids(self, client, seed_triage_tasks):
        """Bulk reject with specific task_ids only rejects those."""
        triage_tasks = Task.query.filter_by(status='triage').all()
        target_ids = [t.id for t in triage_tasks[:2]]

        resp = client.post('/api/triage/bulk-reject', json={
            'task_ids': target_ids,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['rejected'] == 2

        # Remaining triage should be 2
        remaining = Task.query.filter_by(status='triage').count()
        assert remaining == 2

    def test_bulk_reject_skips_non_triage(self, client, seed_triage_tasks):
        """Bulk reject with specific IDs skips non-triage tasks."""
        pending = Task.query.filter_by(status='pending').first()
        resp = client.post('/api/triage/bulk-reject', json={
            'task_ids': [pending.id],
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['rejected'] == 0
        assert len(data['skipped']) == 1

    def test_bulk_reject_creates_events(self, client, seed_triage_tasks):
        """Bulk reject should log triage_rejected events."""
        resp = client.post('/api/triage/bulk-reject', json={})
        assert resp.status_code == 200
        data = resp.get_json()
        for tid in data['rejected_ids']:
            resp2 = client.get(f'/api/tasks/{tid}/events')
            events = resp2.get_json()['events']
            event_types = [e['event_type'] for e in events]
            assert 'triage_rejected' in event_types

    def test_bulk_reject_default_reason(self, client, seed_triage_tasks):
        """Bulk reject without reason should use default."""
        resp = client.post('/api/triage/bulk-reject', json={})
        assert resp.status_code == 200
        data = resp.get_json()
        for tid in data['rejected_ids']:
            resp2 = client.get(f'/api/tasks/{tid}')
            task = resp2.get_json()
            assert task['last_error'] == 'Bulk rejected in triage'
