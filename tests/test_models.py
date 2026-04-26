# tests/test_models.py
import pytest
from datetime import datetime
from models import db, Task, Agent


@pytest.fixture
def app():
    from app import create_app
    app = create_app(testing=True)
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


class TestTaskModel:
    def test_create_task(self, app):
        task = Task(title="Test task", description="A test task")
        db.session.add(task)
        db.session.commit()
        assert task.id is not None
        assert task.status == 'pending'
        assert task.priority == 3

    def test_task_defaults(self, app):
        task = Task(title="Default task")
        db.session.add(task)
        db.session.commit()
        assert task.agent is None
        assert task.result is None
        assert task.error is None
        assert task.created_at is not None

    def test_claim_task(self, app):
        task = Task(title="Claim me")
        db.session.add(task)
        db.session.commit()
        task.claim("coder")
        db.session.commit()
        assert task.status == 'claimed'
        assert task.agent == 'coder'
        assert task.claimed_at is not None

    def test_complete_task(self, app):
        task = Task(title="Complete me")
        db.session.add(task)
        db.session.commit()
        task.claim("coder")
        task.complete("Done!")
        db.session.commit()
        assert task.status == 'completed'
        assert task.result == "Done!"
        assert task.completed_at is not None

    def test_fail_task(self, app):
        task = Task(title="Fail me")
        db.session.add(task)
        db.session.commit()
        task.claim("coder")
        task.fail("Broke it")
        db.session.commit()
        assert task.status == 'failed'
        assert task.error == "Broke it"

    def test_release_task(self, app):
        task = Task(title="Release me")
        db.session.add(task)
        db.session.commit()
        task.claim("coder")
        task.release()
        db.session.commit()
        assert task.status == 'pending'
        assert task.agent is None


class TestAgentModel:
    def test_create_agent(self, app):
        agent = Agent(name="coder", display_name="Coder", model="deepseek-v4-flash")
        db.session.add(agent)
        db.session.commit()
        assert agent.name == "coder"
        assert agent.tasks_completed == 0
        assert agent.tasks_failed == 0
