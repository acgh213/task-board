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
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/complete', json={'result': 'Done'})

        resp = client.get('/api/tasks?status=pending')
        tasks = resp.get_json()['tasks']
        assert all(t['status'] == 'pending' for t in tasks)

    def test_claim_task(self, client):
        resp = client.post('/api/tasks', json={'title': 'Claim me'})
        task_id = resp.get_json()['id']
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'claimed'
        assert resp.get_json()['agent'] == 'coder'

    def test_cannot_claim_already_claimed(self, client):
        resp = client.post('/api/tasks', json={'title': 'Claim me'})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'editor'})
        assert resp.status_code == 409

    def test_complete_task(self, client):
        resp = client.post('/api/tasks', json={'title': 'Do it'})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/complete', json={'result': 'All done!'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'completed'
        assert resp.get_json()['result'] == 'All done!'

    def test_fail_task(self, client):
        resp = client.post('/api/tasks', json={'title': 'Break it'})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/fail', json={'error': 'Oops'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'failed'

    def test_release_task(self, client):
        resp = client.post('/api/tasks', json={'title': 'Release me'})
        task_id = resp.get_json()['id']
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/release')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'pending'
        assert resp.get_json()['agent'] is None

    def test_delete_task(self, client):
        resp = client.post('/api/tasks', json={'title': 'Delete me'})
        task_id = resp.get_json()['id']
        resp = client.delete(f'/api/tasks/{task_id}')
        assert resp.status_code == 200
        resp = client.get(f'/api/tasks/{task_id}')
        assert resp.status_code == 404


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
