"""tests/test_template_dag.py — Tests for template DAG dependency wiring (Task #4)."""

import json
import pytest
from models import db, Task, TaskTemplate, Agent


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
def seed_dag_template(app):
    """Seed a simple 3-step linear DAG template."""
    with app.app_context():
        t = TaskTemplate(
            name='dag-linear',
            description='Linear DAG template',
            steps=json.dumps([
                {
                    'title': 'Step 0: First',
                    'description': 'First step, no deps',
                    'tags': 'research',
                    'agent': 'researcher',
                    'priority': 2,
                },
                {
                    'title': 'Step 1: Second',
                    'description': 'Depends on step 0',
                    'tags': 'planning',
                    'agent': 'planner',
                    'priority': 2,
                    'depends_on': 0,
                },
                {
                    'title': 'Step 2: Third',
                    'description': 'Depends on step 1',
                    'tags': 'code',
                    'agent': 'coder',
                    'priority': 1,
                    'depends_on': 1,
                },
            ]),
        )
        db.session.add(t)
        db.session.commit()
    return t


@pytest.fixture
def seed_fan_out_template(app):
    """Seed a fan-out template: step0 → step1, step2 (both depend on step0)."""
    with app.app_context():
        t = TaskTemplate(
            name='dag-fanout',
            description='Fan-out DAG template',
            steps=json.dumps([
                {
                    'title': 'Root',
                    'description': 'Root step',
                    'tags': 'research',
                    'agent': 'researcher',
                    'priority': 2,
                },
                {
                    'title': 'Child A',
                    'description': 'Depends on root',
                    'tags': 'code',
                    'agent': 'coder',
                    'priority': 2,
                    'depends_on': 0,
                },
                {
                    'title': 'Child B',
                    'description': 'Depends on root',
                    'tags': 'code',
                    'agent': 'coder',
                    'priority': 2,
                    'depends_on': 0,
                },
            ]),
        )
        db.session.add(t)
        db.session.commit()
    return t


class TestTemplateDAGBlockedBy:
    def test_dag_sets_blocked_by(self, client, seed_dag_template):
        """Steps with depends_on should have blocked_by set."""
        resp = client.post('/api/templates/dag-linear/create', json={
            'variables': {},
        })
        assert resp.status_code == 201
        tasks = resp.get_json()['tasks']
        tasks_by_title = {t['title']: t for t in tasks}

        # Step 0 should have no blocked_by
        first = tasks_by_title['Step 0: First']
        assert first['blocked_by'] == '' or first['blocked_by'] is None

        # Step 1 should be blocked by step 0
        second = tasks_by_title['Step 1: Second']
        first_id = str(first['id'])
        assert first_id in (second['blocked_by'] or '')

        # Step 2 should be blocked by step 1
        third = tasks_by_title['Step 2: Third']
        second_id = str(second['id'])
        assert second_id in (third['blocked_by'] or '')

    def test_dag_auto_transitions_blocked(self, client, seed_dag_template):
        """Tasks whose dependencies are not met should auto-transition to blocked."""
        resp = client.post('/api/templates/dag-linear/create', json={
            'variables': {},
        })
        assert resp.status_code == 201
        tasks = resp.get_json()['tasks']
        tasks_by_title = {t['title']: t for t in tasks}

        # Step 0 has no deps — should be pending
        first = tasks_by_title['Step 0: First']
        assert first['status'] == 'pending'

        # Step 1 depends on step 0 which is not completed — should be blocked
        second = tasks_by_title['Step 1: Second']
        assert second['status'] == 'blocked'

        # Step 2 depends on step 1 — should be blocked (transitively)
        third = tasks_by_title['Step 2: Third']
        assert third['status'] == 'blocked'

    def test_dag_fan_out_blocked_by(self, client, seed_fan_out_template):
        """Fan-out: both children should be blocked by the root."""
        resp = client.post('/api/templates/dag-fanout/create', json={
            'variables': {},
        })
        assert resp.status_code == 201
        tasks = resp.get_json()['tasks']
        tasks_by_title = {t['title']: t for t in tasks}

        root = tasks_by_title['Root']
        root_id = str(root['id'])

        child_a = tasks_by_title['Child A']
        child_b = tasks_by_title['Child B']

        assert root_id in (child_a['blocked_by'] or '')
        assert root_id in (child_b['blocked_by'] or '')

    def test_dag_fan_out_statuses(self, client, seed_fan_out_template):
        """Fan-out: root is pending, children are blocked."""
        resp = client.post('/api/templates/dag-fanout/create', json={
            'variables': {},
        })
        assert resp.status_code == 201
        tasks = resp.get_json()['tasks']
        tasks_by_title = {t['title']: t for t in tasks}

        assert tasks_by_title['Root']['status'] == 'pending'
        assert tasks_by_title['Child A']['status'] == 'blocked'
        assert tasks_by_title['Child B']['status'] == 'blocked'

    def test_dag_events_logged(self, client, seed_dag_template):
        """Dependency_set events should be logged for dependent tasks."""
        resp = client.post('/api/templates/dag-linear/create', json={
            'variables': {},
        })
        assert resp.status_code == 201
        tasks = resp.get_json()['tasks']
        tasks_by_title = {t['title']: t for t in tasks}

        second = tasks_by_title['Step 1: Second']
        resp2 = client.get(f'/api/tasks/{second["id"]}')
        assert resp2.status_code == 200
        events = resp2.get_json()['events']
        event_types = [e['event_type'] for e in events]
        assert 'dependency_set' in event_types

    def test_dag_no_dep_remains_pending(self, client, seed_dag_template):
        """A template with no depends_on should create pending tasks with no blocked_by."""
        # Create a simple template with no dependencies
        with client.application.app_context():
            t = TaskTemplate(
                name='no-dep',
                description='No dependencies',
                steps=json.dumps([
                    {'title': 'Alone', 'description': 'Just me', 'tags': '', 'agent': 'coder', 'priority': 2},
                ]),
            )
            db.session.add(t)
            db.session.commit()

        resp = client.post('/api/templates/no-dep/create', json={})
        assert resp.status_code == 201
        task = resp.get_json()['tasks'][0]
        assert task['status'] == 'pending'
        assert task['blocked_by'] == '' or task['blocked_by'] is None

    def test_dag_dependency_resolved_after_completion(self, client, seed_dag_template):
        """After completing a blocking task, the blocked task should unblock when checked."""
        resp = client.post('/api/templates/dag-linear/create', json={
            'variables': {},
        })
        assert resp.status_code == 201
        tasks = resp.get_json()['tasks']
        tasks_by_title = {t['title']: t for t in tasks}

        first = tasks_by_title['Step 0: First']
        second = tasks_by_title['Step 1: Second']

        # Should be blocked initially
        assert second['status'] == 'blocked'

        # Complete the blocking task (step 0) — assign, claim, start, submit, review
        agent = 'researcher'
        client.post(f'/api/tasks/{first["id"]}/assign', json={'agent': agent})
        client.post(f'/api/tasks/{first["id"]}/claim', json={'agent': agent})
        client.post(f'/api/tasks/{first["id"]}/start', json={'agent': agent})
        client.post(f'/api/tasks/{first["id"]}/submit?skip_wait=true', json={'agent': agent, 'result': 'done'})
        resp = client.post(f'/api/tasks/{first["id"]}/review', json={
            'reviewer': 'editor',
            'decision': 'approve',
        })
        assert resp.status_code == 200

        # Now check the dependency task — should have been auto-resolved
        resp = client.get(f'/api/tasks/{second["id"]}')
        assert resp.status_code == 200
        data = resp.get_json()
        # The task should no longer be blocked
        assert data['status'] != 'blocked', (
            f'Expected step 1 to be unblocked after step 0 completed, '
            f'but got status={data["status"]}'
        )
