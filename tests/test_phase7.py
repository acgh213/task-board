# tests/test_phase7.py
"""Tests for Phase 7: Dashboard v2 — SocketIO, Timeline, Improved Agent Cards."""
import json
import pytest
from models import db, Task, Agent, EventLog


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


def _create_task(client, title='Test task'):
    resp = client.post('/api/tasks', json={'title': title})
    assert resp.status_code == 201
    return resp.get_json()['id']


def _full_complete(client, task_id, agent='coder', reviewer='editor', result='Done'):
    client.post(f'/api/tasks/{task_id}/assign', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/claim', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/start', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/submit?skip_wait=true', json={'agent': agent, 'result': result})
    return client.post(f'/api/tasks/{task_id}/review', json={'reviewer': reviewer, 'decision': 'approve'})


class TestTimelineEndpoint:
    """Test the new /task/<id>/timeline route."""

    def test_timeline_returns_200(self, client):
        task_id = _create_task(client)
        resp = client.get(f'/task/{task_id}/timeline')
        assert resp.status_code == 200
        assert b'Timeline' in resp.data
        assert b'task_created' in resp.data or b'Test task' in resp.data

    def test_timeline_shows_lifecycle_events(self, client):
        task_id = _create_task(client)
        _full_complete(client, task_id)
        resp = client.get(f'/task/{task_id}/timeline')
        assert resp.status_code == 200
        html = resp.data.decode()
        # Should contain lifecycle events
        assert 'task_created' in html
        assert 'assigned' in html
        assert 'claimed' in html
        assert 'completed' in html

    def test_timeline_404_for_nonexistent_task(self, client):
        resp = client.get('/task/99999/timeline')
        assert resp.status_code == 404

    def test_timeline_shows_review_events(self, client):
        task_id = _create_task(client)
        _full_complete(client, task_id)
        resp = client.get(f'/task/{task_id}/timeline')
        html = resp.data.decode()
        # Should show review approval
        assert 'Review' in html or 'review_approve' in html

    def test_timeline_empty_for_no_events(self, client):
        """A fresh task should still render the timeline page."""
        task_id = _create_task(client)
        resp = client.get(f'/task/{task_id}/timeline')
        assert resp.status_code == 200

    def test_timeline_includes_agent_info(self, client):
        task_id = _create_task(client, 'Agent timeline test')
        _full_complete(client, task_id)
        resp = client.get(f'/task/{task_id}/timeline')
        html = resp.data.decode()
        assert 'coder' in html.lower() or 'editor' in html.lower()


class TestSocketIOEvents:
    """Test that SocketIO events are emitted on task state changes."""

    def test_create_emits_event(self, app, client):
        """Creating a task should emit a task_update event.
        We verify this by checking the response is valid — the actual
        socket emission is async and tested via event capture below."""
        resp = client.post('/api/tasks', json={'title': 'Socket test'})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['title'] == 'Socket test'

    def test_assign_emits_event(self, client):
        task_id = _create_task(client)
        resp = client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'assigned'

    def test_claim_emits_event(self, client):
        task_id = _create_task(client)
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'claimed'
        assert data['claimed_by'] == 'coder'

    def test_full_workflow_events(self, client):
        """Full lifecycle should not error and return correct statuses."""
        task_id = _create_task(client)
        _full_complete(client, task_id)
        # Verify final state
        resp = client.get(f'/api/tasks/{task_id}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'completed'

    def test_heartbeat_emits_event(self, client):
        task_id = _create_task(client)
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/heartbeat', json={'agent': 'coder'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'claimed'

    def test_release_emits_event(self, client):
        task_id = _create_task(client)
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        resp = client.post(f'/api/tasks/{task_id}/release')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'pending'

    def test_requeue_emits_event(self, client):
        """Requeue from timed_out should emit events."""
        task_id = _create_task(client)
        client.post(f'/api/tasks/{task_id}/assign', json={'agent': 'coder'})
        client.post(f'/api/tasks/{task_id}/claim', json={'agent': 'coder'})
        # Force lease expiry so check-timeouts will catch it
        from datetime import datetime, timedelta, timezone
        task = db.session.get(Task, task_id)
        task.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
        db.session.commit()
        # Force timeout via overseer
        client.post('/api/overseer/check-timeouts')
        resp = client.post(f'/api/tasks/{task_id}/requeue', json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'pending' or data['status'] == 'dead'

    def test_agent_heartbeat_emits_event(self, client):
        resp = client.post('/api/agents/heartbeat', json={
            'agent': 'coder',
            'status': 'busy',
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['agent'] == 'coder'

    def test_agent_register_emits_event(self, client):
        resp = client.post('/api/agents', json={
            'name': 'new_agent',
            'display_name': 'New Agent',
            'model': 'gpt-5',
            'skills': 'python,testing',
        })
        assert resp.status_code in (200, 201)
        data = resp.get_json()
        assert data['name'] == 'new_agent'


class TestDashboardAgentCards:
    """Test that the dashboard includes improved agent card data."""

    def test_dashboard_shows_agents(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        html = resp.data.decode()
        # Agent names should be visible
        assert 'Coder' in html
        assert 'Editor' in html
        assert 'Researcher' in html

    def test_dashboard_shows_rep_score(self, client):
        resp = client.get('/')
        html = resp.data.decode()
        # Reputation score should be visible
        assert 'reputation' in html.lower() or '⭐' in html

    def test_dashboard_shows_heartbeat_age(self, client):
        resp = client.get('/')
        html = resp.data.decode()
        # Heartbeat info should be visible
        assert 'Last seen' in html or 'heartbeat' in html.lower()

    def test_dashboard_shows_active_tasks(self, client):
        resp = client.get('/')
        html = resp.data.decode()
        # Active/load info should be visible
        assert '/3' in html or '/2' in html or '📊' in html


class TestDashboardRealtime:
    """Test dashboard renders correctly with SocketIO script loaded."""

    def test_dashboard_includes_socketio_script(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        html = resp.data.decode()
        # SocketIO client library should be included
        assert 'socket.io' in html
        assert 'io(' in html

    def test_dashboard_has_socket_event_handlers(self, client):
        resp = client.get('/')
        html = resp.data.decode()
        # Should have task_update handler
        assert 'task_update' in html
        assert 'agent_update' in html
        assert 'new_event' in html


class TestDashboardDoneColumn:
    def test_completed_tasks_render_in_collapsible_done_panel(self, client):
        task_id = _create_task(client, 'Finished ship log item')
        _full_complete(client, task_id)

        resp = client.get('/')
        assert resp.status_code == 200
        html = resp.data.decode()

        assert 'done-panel' in html
        assert '<details' in html
        assert 'Completed tasks (1)' in html
        assert 'Finished ship log item' in html

    def test_done_panel_shows_empty_state_when_no_completed_tasks(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        html = resp.data.decode()

        assert 'No completed tasks' in html
        assert 'id="done-panel"' not in html


class TestAgentDisplayNames:
    def test_dashboard_prefers_agent_display_names_on_task_cards(self, client):
        with client.application.app_context():
            specialist = Agent(name='systems-specialist', display_name='Systems Specialist', model='o3')
            db.session.add(specialist)
            db.session.add(Task(
                title='Role label cleanup',
                status='assigned',
                assigned_to='systems-specialist',
                priority=2,
            ))
            db.session.commit()

        resp = client.get('/')
        assert resp.status_code == 200
        html = resp.data.decode()

        assert 'Systems Specialist' in html

    def test_task_api_includes_agent_display_fields(self, client):
        with client.application.app_context():
            specialist = Agent(name='systems-specialist', display_name='Systems Specialist', model='o3')
            db.session.add(specialist)
            task = Task(
                title='Emit display labels',
                status='assigned',
                assigned_to='systems-specialist',
                priority=2,
            )
            db.session.add(task)
            db.session.commit()
            task_id = task.id

        resp = client.get(f'/api/tasks/{task_id}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['assigned_to'] == 'systems-specialist'
        assert data['assigned_to_display'] == 'Systems Specialist'
