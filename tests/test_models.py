# tests/test_models.py
import json
import pytest
from datetime import datetime, timezone, timedelta
from models import db, Task, Agent, Review, EventLog, STATE_TRANSITIONS


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
        assert task.attempts == 0
        assert task.max_attempts == 3
        assert task.created_at is not None

    def test_task_with_all_fields(self, app):
        task = Task(
            title="Full task",
            description="All fields",
            project="test",
            tags="python,api",
            reserved_for="worker",
            priority=1,
            max_attempts=5,
            escalation_rules='{"auto_escalate": true}',
        )
        db.session.add(task)
        db.session.commit()
        assert task.reserved_for == "worker"
        assert task.max_attempts == 5
        assert task.escalation_rules == '{"auto_escalate": true}'

    def test_to_dict_includes_new_fields(self, app):
        task = Task(title="Dict test")
        db.session.add(task)
        db.session.commit()
        d = task.to_dict()
        assert 'assigned_to' in d
        assert 'claimed_by' in d
        assert 'lease_expires_at' in d
        assert 'heartbeat_at' in d
        assert 'attempts' in d
        assert 'max_attempts' in d
        assert 'failure_reason' in d
        assert 'escalation_rules' in d
        assert 'submitted_at' in d
        assert 'in_progress_at' in d
        assert 'updated_at' in d

    def test_state_transitions_valid(self, app):
        task = Task(title="Transition test")
        db.session.add(task)
        db.session.commit()

        # pending -> assigned
        assert task.can_transition_to('assigned')
        task.status = 'assigned'

        # assigned -> claimed
        assert task.can_transition_to('claimed')
        task.status = 'claimed'

        # claimed -> in_progress
        assert task.can_transition_to('in_progress')
        task.status = 'in_progress'

        # in_progress -> submitted
        assert task.can_transition_to('submitted')
        task.status = 'submitted'

        # submitted -> in_review
        assert task.can_transition_to('in_review')
        task.status = 'in_review'

        # in_review -> completed
        assert task.can_transition_to('completed')
        task.status = 'completed'

        # completed is terminal
        assert not task.can_transition_to('pending')
        assert task.is_terminal()

    def test_state_transitions_review_paths(self, app):
        task = Task(title="Review paths")
        db.session.add(task)
        db.session.commit()

        # Go to in_review via full chain
        states = ['assigned', 'claimed', 'in_progress', 'submitted', 'in_review']
        for s in states:
            task.status = s
        assert task.can_transition_to('needs_revision')
        assert task.can_transition_to('failed')
        assert task.can_transition_to('completed')

    def test_state_transitions_failed_path(self, app):
        task = Task(title="Failed path")
        task.status = 'failed'
        assert task.can_transition_to('released')
        assert task.can_transition_to('dead')
        assert not task.can_transition_to('pending')  # must go via released

    def test_state_transitions_needs_revision(self, app):
        task = Task(title="Revision path")
        task.status = 'needs_revision'
        assert task.can_transition_to('claimed')
        assert task.can_transition_to('assigned')
        assert task.can_transition_to('failed')

    def test_lease_expiry_detection(self, app):
        task = Task(title="Lease test")
        now = datetime.now(timezone.utc)

        # No lease = expired
        assert task.lease_expired()

        # Future lease = not expired
        task.lease_expires_at = now + timedelta(minutes=10)
        assert not task.lease_expired()

        # Past lease = expired
        task.lease_expires_at = now - timedelta(minutes=1)
        assert task.lease_expired()

    def test_is_locked(self, app):
        task = Task(title="Lock test")
        assert not task.is_locked()
        for s in ['claimed', 'in_progress', 'submitted', 'in_review', 'needs_revision']:
            task.status = s
            assert task.is_locked(), f"{s} should be locked"

    def test_escalation_tags_human(self, app):
        task = Task(title="Escalate", tags="deploy,publish")
        assert task.check_escalation_tags() == 'needs_human'

    def test_escalation_tags_vesper(self, app):
        task = Task(title="Escalate", tags="credentials,api_key")
        assert task.check_escalation_tags() == 'needs_vesper'

    def test_escalation_tags_no_match(self, app):
        task = Task(title="Safe", tags="python,backend")
        assert task.check_escalation_tags() is None

    def test_escalation_tags_empty(self, app):
        task = Task(title="No tags", tags="")
        assert task.check_escalation_tags() is None

    def test_task_invalid_transition(self, app):
        task = Task(title="Invalid")
        db.session.add(task)
        db.session.commit()
        # pending -> completed directly is invalid
        assert not task.can_transition_to('completed')
        # pending -> claimed directly is invalid
        assert not task.can_transition_to('claimed')

    def test_terminal_status(self, app):
        task = Task(title="Terminal test")
        task.status = 'dead'
        assert task.is_terminal()
        task.status = 'completed'
        assert task.is_terminal()
        task.status = 'pending'
        assert not task.is_terminal()


class TestReviewModel:
    def test_create_review(self, app):
        task = Task(title="Reviewable task")
        db.session.add(task)
        db.session.flush()

        review = Review(
            task_id=task.id,
            reviewer="code_reviewer",
            decision="approve",
            feedback="Looks good!",
        )
        db.session.add(review)
        db.session.commit()

        assert review.id is not None
        assert review.task_id == task.id
        assert review.reviewer == "code_reviewer"
        assert review.decision == "approve"
        assert review.feedback == "Looks good!"
        assert review.created_at is not None

    def test_review_relationship(self, app):
        task = Task(title="Task with reviews")
        db.session.add(task)
        db.session.flush()

        r1 = Review(task_id=task.id, reviewer="r1", decision="approve")
        r2 = Review(task_id=task.id, reviewer="r2", decision="reject", feedback="No good")
        db.session.add(r1)
        db.session.add(r2)
        db.session.commit()

        assert len(task.reviews) == 2
        assert task.reviews[0].decision == "approve"

    def test_review_to_dict(self, app):
        task = Task(title="Dict review")
        db.session.add(task)
        db.session.flush()

        review = Review(task_id=task.id, reviewer="test", decision="approve")
        db.session.add(review)
        db.session.commit()

        d = review.to_dict()
        assert d['task_id'] == task.id
        assert d['reviewer'] == 'test'
        assert d['decision'] == 'approve'
        assert 'created_at' in d


class TestEventLogModel:
    def test_create_event(self, app):
        task = Task(title="Eventful task")
        db.session.add(task)
        db.session.flush()

        event = EventLog(
            task_id=task.id,
            event_type="task_created",
            agent="system",
            details='{"key": "value"}',
        )
        db.session.add(event)
        db.session.commit()

        assert event.id is not None
        assert event.task_id == task.id
        assert event.event_type == "task_created"
        assert event.agent == "system"

    def test_event_relationship(self, app):
        task = Task(title="Task with events")
        db.session.add(task)
        db.session.flush()

        for etype in ['assigned', 'claimed', 'completed']:
            db.session.add(EventLog(task_id=task.id, event_type=etype, agent="test"))
        db.session.commit()

        assert len(task.events) == 3

    def test_event_to_dict(self, app):
        task = Task(title="Event dict")
        db.session.add(task)
        db.session.flush()

        event = EventLog(
            task_id=task.id,
            event_type="test_event",
            agent="tester",
            details='{"foo": "bar"}',
        )
        db.session.add(event)
        db.session.commit()

        d = event.to_dict()
        assert d['event_type'] == 'test_event'
        assert d['agent'] == 'tester'
        assert d['details'] == {'foo': 'bar'}

    def test_event_without_task(self, app):
        event = EventLog(
            task_id=None,
            event_type="system_event",
            agent="system",
            details='{"global": true}',
        )
        db.session.add(event)
        db.session.commit()
        assert event.id is not None
        assert event.task_id is None

    def test_event_ordering(self, app):
        task = Task(title="Ordered events")
        db.session.add(task)
        db.session.flush()

        e1 = EventLog(task_id=task.id, event_type="first", agent="a")
        e2 = EventLog(task_id=task.id, event_type="second", agent="b")
        e3 = EventLog(task_id=task.id, event_type="third", agent="c")
        db.session.add_all([e1, e2, e3])
        db.session.commit()

        events = EventLog.query.filter_by(task_id=task.id).order_by(EventLog.created_at).all()
        assert [e.event_type for e in events] == ['first', 'second', 'third']


class TestAgentModel:
    def test_create_agent(self, app):
        agent = Agent(name="coder", display_name="Coder", model="deepseek-v4-flash")
        db.session.add(agent)
        db.session.commit()
        assert agent.name == "coder"
        assert agent.role == "worker"
        assert agent.skills == ""
        assert agent.max_concurrent == 3
        assert agent.tasks_completed == 0
        assert agent.tasks_failed == 0
        assert agent.reputation_score == 50.0
        assert agent.status == "idle"

    def test_agent_with_skills_and_role(self, app):
        agent = Agent(
            name="senior_dev",
            display_name="Senior Dev",
            role="mission_control",
            skills="python,flask,devops",
            max_concurrent=5,
        )
        db.session.add(agent)
        db.session.commit()
        assert agent.role == "mission_control"
        assert agent.skills == "python,flask,devops"
        assert agent.max_concurrent == 5

    def test_agent_reputation_calculation(self, app):
        agent = Agent(name="reputable", display_name="Reputable")
        db.session.add(agent)
        db.session.commit()

        # Default: 50
        assert agent.reputation_score == 50.0

        # All completed -> 100
        agent.tasks_completed = 10
        agent.update_reputation()
        assert agent.reputation_score == 100.0

        # Mixed
        agent.tasks_completed = 8
        agent.tasks_failed = 2
        agent.update_reputation()
        # score = (8/10)*100 - (2/10)*20 = 80 - 4 = 76
        assert agent.reputation_score == 76.0

    def test_agent_reputation_with_timeouts(self, app):
        agent = Agent(name="timeouter", display_name="Timeouter")
        db.session.add(agent)
        agent.tasks_completed = 5
        agent.tasks_failed = 2
        agent.tasks_timed_out = 3
        agent.update_reputation()
        # score = (5/10)*100 - (2/10)*20 - (3/10)*15 = 50 - 4 - 4.5 = 41.5
        assert agent.reputation_score == 41.5

    def test_agent_reputation_floor(self, app):
        agent = Agent(name="bad", display_name="Bad")
        db.session.add(agent)
        agent.tasks_failed = 10
        agent.update_reputation()
        # score = 0 - 20 = -20 -> clamped to 0
        assert agent.reputation_score == 0.0

    def test_agent_to_dict(self, app):
        agent = Agent(
            name="test_agent",
            display_name="Test Agent",
            model="gpt-5",
            role="reviewer",
            skills="code_review",
        )
        db.session.add(agent)
        db.session.commit()

        d = agent.to_dict()
        assert d['name'] == 'test_agent'
        assert d['role'] == 'reviewer'
        assert d['skills'] == 'code_review'
        assert d['reputation_score'] == 50.0
        assert 'last_heartbeat' in d


class TestStateMachineTransitions:
    """Verify all valid transitions from the STATE_TRANSITIONS map."""

    def test_all_transition_paths(self):
        # Verify all valid transitions described in the spec
        expected = {
            'pending':      frozenset(['assigned', 'blocked', 'needs_human', 'needs_vesper']),
            'assigned':     frozenset(['claimed', 'blocked', 'needs_human', 'needs_vesper', 'pending']),
            'claimed':      frozenset(['in_progress', 'released', 'timed_out', 'blocked',
                                       'needs_human', 'needs_vesper']),
            'in_progress':  frozenset(['submitted', 'released', 'timed_out', 'blocked',
                                       'needs_human', 'needs_vesper', 'failed']),
            'submitted':    frozenset(['in_review', 'needs_human', 'needs_vesper', 'blocked']),
            'in_review':    frozenset(['completed', 'failed', 'needs_revision',
                                       'needs_human', 'needs_vesper', 'blocked']),
            'needs_revision': frozenset(['claimed', 'assigned', 'released', 'timed_out',
                                         'needs_human', 'needs_vesper', 'blocked', 'failed']),
            'completed':    frozenset(),
            'failed':       frozenset(['released', 'dead', 'needs_human', 'needs_vesper', 'blocked']),
            'timed_out':    frozenset(['released', 'dead', 'needs_human', 'needs_vesper', 'blocked']),
            'blocked':      frozenset(['pending', 'assigned', 'needs_human', 'needs_vesper']),
            'needs_human':  frozenset(['pending', 'assigned', 'in_review', 'completed', 'blocked']),
            'needs_vesper': frozenset(['pending', 'assigned', 'in_review', 'completed', 'blocked']),
            'released':     frozenset(['pending', 'assigned', 'dead']),
            'dead':         frozenset(),
        }

        for status, expected_transitions in expected.items():
            actual = STATE_TRANSITIONS.get(status, frozenset())
            assert actual == expected_transitions, (
                f"Mismatch for status '{status}': expected {expected_transitions}, got {actual}"
            )
