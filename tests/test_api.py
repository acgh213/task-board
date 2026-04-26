# tests/test_api.py
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


def _full_complete(client, task_id, agent='coder', reviewer='editor', result='Done'):
    """Helper: run full happy-path lifecycle and return the review response."""
    client.post(f'/api/tasks/{task_id}/assign', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/claim', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/start', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/submit', json={'agent': agent, 'result': result})
    return client.post(f'/api/tasks/{task_id}/review', json={'reviewer': reviewer, 'decision': 'approve'})


def _full_fail(client, task_id, agent='coder', reviewer='editor', result='Result', feedback='Oops'):
    """Helper: run lifecycle ending in rejection."""
    client.post(f'/api/tasks/{task_id}/assign', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/claim', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/start', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/submit', json={'agent': agent, 'result': result})
    return client.post(f'/api/tasks/{task_id}/review', json={'reviewer': reviewer, 'decision': 'reject', 'feedback': feedback})


class TestTaskAPI:
    def test_create_task(self, client):
        resp = client.post('/api/tasks', json={
            'title': 'Build feature X',
            'description': 'Implement the thing',
            'priority': 2,
            'project': 'hermes',
            'tags': 'backend,api'
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['title'] == 'Build feature X'
        assert data['status'] == 'pending'
        assert data['priority'] == 2

    def test_list_tasks(self, client):
        client.post('/api/tasks', json={'title': 'Task 1'})
        client.post('/api/tasks', json={'title': 'Task 2'})
        resp = client.get('/api/tasks')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['tasks']) == 2

    def test_filter_by_status(self, client):
        client.post('/api/tasks', json={'title': 'Pending'})
        resp = client.post('/api/tasks', json={'title': 'Done'})
        task_id = resp.get_json()['id']
        _full_complete(client, task_id)

        resp = client.get('/api/tasks?status=pending')
        tasks = resp.get_json()['tasks']
        assert all(t['status'] == 'pending' for t in tasks)

    def test_claim_task(self, client):
        resp = client.post('/api/tasks', json={'title': 'Claim me'})
        task_id = resp.get_json()['id']
        # Must assign before claiming
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'claimed'
        assert resp.get_json()['claimed_by'] == 'coder'

    def test_cannot_claim_already_claimed(self, client):
        resp = client.post('/api/tasks', json={'title': 'Claim me'})
        task_id = resp.get_json()['id']
        # Must assign before claiming
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'editor'})
        assert resp.status_code == 409

    def test_complete_task(self, client):
        resp = client.post('/api/tasks', json={'title': 'Do it'})
        task_id = resp.get_json()['id']
        resp = _full_complete(client, task_id, result='All done!')
        assert resp.status_code == 200
        assert resp.get_json()['task']['status'] == 'completed'
        assert resp.get_json()['task']['result'] == 'All done!'

    def test_fail_task(self, client):
        resp = client.post('/api/tasks', json={'title': 'Break it'})
        task_id = resp.get_json()['id']
        resp = _full_fail(client, task_id, feedback='Oops')
        assert resp.status_code == 200
        assert resp.get_json()['task']['status'] == 'failed'

    def test_release_task(self, client):
        resp = client.post('/api/tasks', json={'title': 'Release me'})
        task_id = resp.get_json()['id']
        # Must assign and claim before release
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/release')
        assert resp.status_code == 200
        # Release clears assignment back to pending
        assert resp.get_json()['status'] == 'pending'
        assert resp.get_json()['claimed_by'] is None
        assert resp.get_json()['assigned_to'] is None

    def test_delete_task(self, client):
        resp = client.post('/api/tasks', json={'title': 'Delete me'})
        task_id = resp.get_json()['id']
        resp = client.delete(f'/api/tasks/{task_id}')
        assert resp.status_code == 200
        resp = client.get(f'/api/tasks/{task_id}')
        assert resp.status_code == 404


class TestPagination:
    def test_pagination_defaults(self, client):
        """Default per_page=50, returns total count."""
        for i in range(60):
            client.post('/api/tasks', json={'title': f'Task {i}'})
        resp = client.get('/api/tasks')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['tasks']) == 50
        assert data['total'] == 60
        assert data['page'] == 1
        assert data['per_page'] == 50

    def test_pagination_custom_page_and_per_page(self, client):
        for i in range(30):
            client.post('/api/tasks', json={'title': f'Task {i}'})
        # Page 2 with 10 per page
        resp = client.get('/api/tasks?page=2&per_page=10')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['tasks']) == 10
        assert data['total'] == 30
        assert data['page'] == 2
        assert data['per_page'] == 10
        # Verify it's the second batch: task titles start at index 10
        titles = [t['title'] for t in data['tasks']]
        assert titles == [f'Task {i}' for i in range(10, 20)]

    def test_pagination_with_filters(self, client):
        client.post('/api/tasks', json={'title': 'Alpha', 'status': 'pending'})
        for i in range(5):
            resp = client.post('/api/tasks', json={'title': f'Beta {i}'})
            tid = resp.get_json()['id']
            _full_complete(client, tid, result='done')
        client.post('/api/tasks', json={'title': 'Gamma', 'status': 'pending'})

        # Filter by status=completed, paginated
        resp = client.get('/api/tasks?status=completed&per_page=2')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['tasks']) == 2
        assert data['total'] == 5
        assert all(t['status'] == 'completed' for t in data['tasks'])

    def test_pagination_out_of_bounds(self, client):
        client.post('/api/tasks', json={'title': 'Only one'})
        resp = client.get('/api/tasks?page=100&per_page=10')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['tasks']) == 0
        assert data['total'] == 1
        assert data['page'] == 100

    def test_pagination_negative_values_use_defaults(self, client):
        client.post('/api/tasks', json={'title': 'Task'})
        resp = client.get('/api/tasks?page=-1&per_page=-5')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['page'] == 1
        assert data['per_page'] == 50
        assert len(data['tasks']) == 1

    def test_pagination_non_integer_params_use_defaults(self, client):
        client.post('/api/tasks', json={'title': 'Task'})
        resp = client.get('/api/tasks?page=abc&per_page=xyz')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['page'] == 1
        assert data['per_page'] == 50

    def test_pagination_with_zero_per_page_uses_default(self, client):
        client.post('/api/tasks', json={'title': 'Task'})
        resp = client.get('/api/tasks?page=1&per_page=0')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['per_page'] == 50

    def test_pagination_combines_with_other_filters(self, client):
        client.post('/api/tasks', json={'title': 'Alpha', 'project': 'proj1'})
        client.post('/api/tasks', json={'title': 'Beta', 'project': 'proj2'})
        client.post('/api/tasks', json={'title': 'Gamma', 'project': 'proj1'})
        resp = client.get('/api/tasks?project=proj1&per_page=1&page=1')
        data = resp.get_json()
        assert len(data['tasks']) == 1
        assert data['total'] == 2


class TestAgentAPI:
    def test_list_agents(self, client):
        resp = client.get('/api/agents')
        assert resp.status_code == 200
        agents = resp.get_json()['agents']
        assert len(agents) == 4

    def test_agent_stats(self, client):
        resp = client.get('/api/stats')
        assert resp.status_code == 200
        stats = resp.get_json()
        assert 'total_tasks' in stats
        assert 'by_status' in stats
