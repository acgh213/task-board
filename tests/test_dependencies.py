# tests/test_dependencies.py
import json
import pytest
from datetime import datetime, timezone
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
    """Helper: run full happy-path lifecycle."""
    client.post(f'/api/tasks/{task_id}/assign', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/claim', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/start', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/submit?skip_wait=true', json={'agent': agent, 'result': result})
    return client.post(f'/api/tasks/{task_id}/review', json={'reviewer': reviewer, 'decision': 'approve'})


class TestBlockedByField:
    """Test the blocked_by field on the Task model."""

    def test_blocked_by_column_exists(self, app):
        """Verify blocked_by column is present on new tasks."""
        task = Task(title="Dependent task")
        db.session.add(task)
        db.session.commit()
        assert hasattr(task, 'blocked_by')
        assert task.blocked_by == '' or task.blocked_by is None

    def test_blocked_by_in_to_dict(self, app):
        """Verify blocked_by appears in to_dict()."""
        task = Task(title="Dict test")
        db.session.add(task)
        db.session.commit()
        d = task.to_dict()
        assert 'blocked_by' in d

    def test_blocked_by_can_be_set(self, app):
        """Verify blocked_by can store comma-separated IDs."""
        t1 = Task(title="Blocker")
        t2 = Task(title="Blocked")
        db.session.add(t1)
        db.session.add(t2)
        db.session.flush()
        t2.blocked_by = str(t1.id)
        db.session.commit()
        assert t2.blocked_by == str(t1.id)

    def test_get_blocking_task_ids_empty(self, app):
        """Empty blocked_by returns empty list."""
        task = Task(title="No deps")
        db.session.add(task)
        db.session.commit()
        assert task.get_blocking_task_ids() == []

    def test_get_blocking_task_ids(self, app):
        """Returns list of ints from blocked_by string."""
        task = Task(title="Has deps")
        db.session.add(task)
        db.session.commit()
        task.blocked_by = '1,2,3'
        assert task.get_blocking_task_ids() == [1, 2, 3]

    def test_get_blocking_tasks(self, app):
        """Returns actual Task objects blocking this one."""
        t1 = Task(title="Blocker 1")
        t2 = Task(title="Blocker 2")
        t3 = Task(title="Blocked")
        db.session.add_all([t1, t2, t3])
        db.session.flush()
        t3.blocked_by = f'{t1.id},{t2.id}'
        db.session.commit()
        blocking = t3.get_blocking_tasks()
        assert len(blocking) == 2
        assert t1 in blocking
        assert t2 in blocking


class TestDependencyResolution:
    """Test the dependency resolution engine."""

    def test_are_dependencies_met_no_deps(self, app):
        """No dependencies means always met."""
        task = Task(title="Independent")
        db.session.add(task)
        db.session.commit()
        assert task.are_dependencies_met() is True

    def test_are_dependencies_met_all_completed(self, app):
        """All blocking tasks completed = met."""
        t1 = Task(title="Blocker", status='completed')
        t2 = Task(title="Blocked")
        db.session.add_all([t1, t2])
        db.session.flush()
        t2.blocked_by = str(t1.id)
        db.session.commit()
        assert t2.are_dependencies_met() is True

    def test_are_dependencies_met_not_completed(self, app):
        """Any blocking task not completed = not met."""
        t1 = Task(title="Blocker", status='pending')
        t2 = Task(title="Blocked")
        db.session.add_all([t1, t2])
        db.session.flush()
        t2.blocked_by = str(t1.id)
        db.session.commit()
        assert t2.are_dependencies_met() is False

    def test_dependency_resolution_on_complete(self, app, client):
        """When blocker completes, blocked task auto-transitions to pending."""
        # Create blocker
        resp = client.post('/api/tasks', json={'title': 'Blocker'})
        blocker_id = resp.get_json()['id']
        # Create blocked task
        resp = client.post('/api/tasks', json={'title': 'Blocked'})
        blocked_id = resp.get_json()['id']
        # Block it
        resp = client.post(f'/api/tasks/{blocked_id}/block', json={'blocked_by': str(blocker_id)})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'blocked'
        # Complete the blocker
        resp = _full_complete(client, blocker_id)
        assert resp.status_code == 200
        assert resp.get_json()['task']['status'] == 'completed'
        # Check the blocked task is now pending
        resp = client.get(f'/api/tasks/{blocked_id}')
        assert resp.get_json()['status'] == 'pending'

    def test_multiple_dependencies_all_met(self, app, client):
        """Multiple deps: all must be complete to unblock."""
        # Create two blockers
        resp = client.post('/api/tasks', json={'title': 'Blocker A'})
        id_a = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'Blocker B'})
        id_b = resp.get_json()['id']
        # Create blocked task
        resp = client.post('/api/tasks', json={'title': 'Blocked'})
        blocked_id = resp.get_json()['id']
        # Block by both
        resp = client.post(f'/api/tasks/{blocked_id}/block', json={'blocked_by': f'{id_a},{id_b}'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'blocked'
        # Complete only A
        _full_complete(client, id_a)
        resp = client.get(f'/api/tasks/{blocked_id}')
        assert resp.get_json()['status'] == 'blocked'  # still blocked
        # Complete B
        _full_complete(client, id_b)
        resp = client.get(f'/api/tasks/{blocked_id}')
        assert resp.get_json()['status'] == 'pending'  # now unblocked

    def test_no_auto_unblock_when_one_dep_remains(self, app, client):
        """If only one of multiple deps completes, task stays blocked."""
        resp = client.post('/api/tasks', json={'title': 'Blocker 1'})
        id1 = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'Blocker 2'})
        id2 = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'Blocked'})
        blocked_id = resp.get_json()['id']
        client.post(f'/api/tasks/{blocked_id}/block', json={'blocked_by': f'{id1},{id2}'})
        _full_complete(client, id1)
        resp = client.get(f'/api/tasks/{blocked_id}')
        assert resp.get_json()['status'] == 'blocked'

    def test_unblock_all_endpoint(self, app, client):
        """POST /tasks/<id>/unblock-all clears deps when all met."""
        resp = client.post('/api/tasks', json={'title': 'Blocker'})
        bid = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'Blocked'})
        blocked_id = resp.get_json()['id']
        client.post(f'/api/tasks/{blocked_id}/block', json={'blocked_by': str(bid)})
        # Complete the blocker
        _full_complete(client, bid)
        # Now unblock-all
        resp = client.post(f'/api/tasks/{blocked_id}/unblock-all')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['blocked_by'] == ''
        assert data['status'] == 'pending'

    def test_unblock_all_fails_if_deps_not_met(self, app, client):
        """unblock-all returns 409 if dependencies not met."""
        resp = client.post('/api/tasks', json={'title': 'Blocker'})
        bid = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'Blocked'})
        blocked_id = resp.get_json()['id']
        client.post(f'/api/tasks/{blocked_id}/block', json={'blocked_by': str(bid)})
        resp = client.post(f'/api/tasks/{blocked_id}/unblock-all')
        assert resp.status_code == 409

    def test_unblock_all_no_deps(self, app, client):
        """unblock-all returns 400 if no blocked_by set."""
        resp = client.post('/api/tasks', json={'title': 'No deps'})
        tid = resp.get_json()['id']
        resp = client.post(f'/api/tasks/{tid}/unblock-all')
        assert resp.status_code == 400

    def test_remove_specific_block(self, app, client):
        """DELETE /tasks/<id>/block/<blocking_id> removes one dep."""
        resp = client.post('/api/tasks', json={'title': 'Blocker'})
        bid = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'Blocked'})
        blocked_id = resp.get_json()['id']
        client.post(f'/api/tasks/{blocked_id}/block', json={'blocked_by': str(bid)})
        # Remove the block
        resp = client.delete(f'/api/tasks/{blocked_id}/block/{bid}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['blocked_by'] == ''
        assert data['status'] == 'pending'

    def test_remove_nonexistent_block(self, app, client):
        """Removing a non-existent block returns 404."""
        resp = client.post('/api/tasks', json={'title': 'Task'})
        tid = resp.get_json()['id']
        resp = client.delete(f'/api/tasks/{tid}/block/9999')
        assert resp.status_code == 404


class TestCycleDetection:
    """Test circular dependency detection."""

    def test_detect_direct_cycle(self, app):
        """A blocks A is not allowed."""
        task = Task(title="Self-blocker")
        db.session.add(task)
        db.session.flush()
        cleaned, error = Task.validate_blocked_by(str(task.id), task_id=task.id)
        assert error is not None
        assert 'cannot block itself' in error

    def test_detect_indirect_cycle(self, app):
        """A blocks B blocks A is not allowed."""
        t1 = Task(title="Task A")
        t2 = Task(title="Task B")
        db.session.add_all([t1, t2])
        db.session.flush()
        # Set B blocked_by A first
        t2.blocked_by = str(t1.id)
        db.session.commit()
        # Now try to set A blocked_by B
        cleaned, error = Task.validate_blocked_by(str(t2.id), task_id=t1.id)
        assert error is not None
        assert 'Circular dependency' in error

    def test_no_cycle_with_unrelated_tasks(self, app):
        """A blocks B, C blocks D — no cycle."""
        t1 = Task(title="A")
        t2 = Task(title="B")
        t3 = Task(title="C")
        t4 = Task(title="D")
        db.session.add_all([t1, t2, t3, t4])
        db.session.flush()
        t2.blocked_by = str(t1.id)
        db.session.commit()
        cleaned, error = Task.validate_blocked_by(str(t3.id), task_id=t4.id)
        assert error is None
        assert cleaned == str(t3.id)

    def test_cycle_rejected_via_api(self, app, client):
        """API returns 400 for circular dependency."""
        resp = client.post('/api/tasks', json={'title': 'A'})
        id_a = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'B'})
        id_b = resp.get_json()['id']
        # Make B blocked by A
        client.post(f'/api/tasks/{id_b}/block', json={'blocked_by': str(id_a)})
        # Now try to make A blocked by B
        resp = client.post(f'/api/tasks/{id_a}/block', json={'blocked_by': str(id_b)})
        assert resp.status_code == 400
        assert 'Circular dependency' in resp.get_json()['error']

    def test_valid_chain_allowed(self, app, client):
        """A blocks B blocks C is fine (no cycle)."""
        resp = client.post('/api/tasks', json={'title': 'A'})
        id_a = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'B'})
        id_b = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'C'})
        id_c = resp.get_json()['id']
        # B blocked by A
        resp = client.post(f'/api/tasks/{id_b}/block', json={'blocked_by': str(id_a)})
        assert resp.status_code == 200
        # C blocked by B
        resp = client.post(f'/api/tasks/{id_c}/block', json={'blocked_by': str(id_b)})
        assert resp.status_code == 200


class TestDependenciesAPI:
    """Test the dependency API endpoints."""

    def test_get_dependencies_endpoint(self, app, client):
        """GET /tasks/<id>/dependencies returns blocking tasks."""
        resp = client.post('/api/tasks', json={'title': 'Blocker'})
        bid = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'Blocked'})
        blocked_id = resp.get_json()['id']
        client.post(f'/api/tasks/{blocked_id}/block', json={'blocked_by': str(bid)})
        resp = client.get(f'/api/tasks/{blocked_id}/dependencies')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['task_id'] == blocked_id
        assert len(data['dependencies']) == 1
        assert data['dependencies'][0]['id'] == bid
        assert data['all_met'] is False

    def test_get_dependencies_all_met(self, app, client):
        """Dependencies endpoint shows all_met=True when blockers completed."""
        resp = client.post('/api/tasks', json={'title': 'Blocker'})
        bid = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'Blocked'})
        blocked_id = resp.get_json()['id']
        client.post(f'/api/tasks/{blocked_id}/block', json={'blocked_by': str(bid)})
        _full_complete(client, bid)
        resp = client.get(f'/api/tasks/{blocked_id}/dependencies')
        data = resp.get_json()
        assert data['all_met'] is True
        assert data['dependencies'][0]['status'] == 'completed'

    def test_empty_dependencies(self, app, client):
        """Task with no blocked_by returns empty dependencies."""
        resp = client.post('/api/tasks', json={'title': 'Solo'})
        tid = resp.get_json()['id']
        resp = client.get(f'/api/tasks/{tid}/dependencies')
        data = resp.get_json()
        assert len(data['dependencies']) == 0
        assert data['all_met'] is True

    def test_block_endpoint_validates_existence(self, app, client):
        """Setting blocked_by to non-existent task returns 400."""
        resp = client.post('/api/tasks', json={'title': 'Task'})
        tid = resp.get_json()['id']
        resp = client.post(f'/api/tasks/{tid}/block', json={'blocked_by': '99999'})
        assert resp.status_code == 400
        assert 'not found' in resp.get_json()['error']

    def test_block_endpoint_auto_blocks(self, app, client):
        """Setting blocked_by on a pending task auto-transitions to blocked if deps not met."""
        resp = client.post('/api/tasks', json={'title': 'Blocker'})
        bid = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'Blocked'})
        blocked_id = resp.get_json()['id']
        # Blocker is still pending, so blocked should become 'blocked'
        resp = client.post(f'/api/tasks/{blocked_id}/block', json={'blocked_by': str(bid)})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['blocked_by'] == str(bid)
        assert data['status'] == 'blocked'

    def test_block_endpoint_auto_unblocks_if_already_met(self, app, client):
        """If blocker is already completed when setting blocked_by, task stays or becomes pending."""
        resp = client.post('/api/tasks', json={'title': 'Blocker'})
        bid = resp.get_json()['id']
        _full_complete(client, bid)
        resp = client.post('/api/tasks', json={'title': 'Blocked'})
        blocked_id = resp.get_json()['id']
        resp = client.post(f'/api/tasks/{blocked_id}/block', json={'blocked_by': str(bid)})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'pending'

    def test_block_endpoint_on_already_blocked_task(self, app, client):
        """Setting blocked_by again overwrites."""
        resp = client.post('/api/tasks', json={'title': 'A'})
        id_a = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'B'})
        id_b = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'C'})
        id_c = resp.get_json()['id']
        # Block C by A
        client.post(f'/api/tasks/{id_c}/block', json={'blocked_by': str(id_a)})
        # Now overwrite with B
        resp = client.post(f'/api/tasks/{id_c}/block', json={'blocked_by': str(id_b)})
        assert resp.status_code == 200
        assert resp.get_json()['blocked_by'] == str(id_b)

    def test_chain_resolution(self, app, client):
        """A blocks B blocks C: completing A does NOT unblock C, completing B does."""
        resp = client.post('/api/tasks', json={'title': 'A'})
        id_a = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'B'})
        id_b = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'C'})
        id_c = resp.get_json()['id']
        # B blocked by A
        client.post(f'/api/tasks/{id_b}/block', json={'blocked_by': str(id_a)})
        # C blocked by B
        client.post(f'/api/tasks/{id_c}/block', json={'blocked_by': str(id_b)})
        # Complete A -> B should unblock
        _full_complete(client, id_a)
        resp = client.get(f'/api/tasks/{id_b}')
        assert resp.get_json()['status'] == 'pending'
        # C should still be blocked (B not yet completed)
        resp = client.get(f'/api/tasks/{id_c}')
        assert resp.get_json()['status'] == 'blocked'
        # Complete B -> C should unblock
        _full_complete(client, id_b)
        resp = client.get(f'/api/tasks/{id_c}')
        assert resp.get_json()['status'] == 'pending'

    def test_delete_task_with_dependents(self, app, client):
        """Deleting a task that is a dependency should not crash."""
        resp = client.post('/api/tasks', json={'title': 'Blocker'})
        bid = resp.get_json()['id']
        resp = client.post('/api/tasks', json={'title': 'Blocked'})
        blocked_id = resp.get_json()['id']
        client.post(f'/api/tasks/{blocked_id}/block', json={'blocked_by': str(bid)})
        # Delete the blocker
        resp = client.delete(f'/api/tasks/{bid}')
        assert resp.status_code == 200
        # Blocked task should still exist with stale reference
        resp = client.get(f'/api/tasks/{blocked_id}')
        assert resp.status_code == 200
        # are_dependencies_met should handle missing tasks gracefully
        data = resp.get_json()
        # get_blocking_tasks will return empty since the task was deleted
        resp = client.get(f'/api/tasks/{blocked_id}/dependencies')
        deps = resp.get_json()
        assert len(deps['dependencies']) == 0
