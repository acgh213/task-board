"""Tests for Task #9: Agent XP and leveling system."""

import json
import pytest
from datetime import datetime, timezone, timedelta, date
from models import db, Task, Agent, EventLog


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


def _create_task(client, title, tags='', priority=3, project='general', complexity=3):
    resp = client.post('/api/tasks', json={
        'title': title, 'tags': tags, 'priority': priority,
        'project': project, 'complexity': complexity,
    })
    assert resp.status_code == 201
    return resp.get_json()


def _complete_task(client, task_id, agent='coder', reviewer='editor', result='Done'):
    """Run full lifecycle with speed control for testing XP bonuses."""
    client.post(f'/api/tasks/{task_id}/assign', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/claim', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/start', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/submit?skip_wait=true', json={'agent': agent, 'result': result})
    return client.post(f'/api/tasks/{task_id}/review',
                       json={'reviewer': reviewer, 'decision': 'approve'})


class TestXPCalculation:
    """Test the XP calculation logic."""

    def test_basic_xp(self, app):
        """Base XP = 50 * complexity (3 default = 150)."""
        from api import _calculate_xp
        task = Task(title='Test', complexity=3)
        xp = _calculate_xp(task)
        assert xp == 150, f"Expected 150, got {xp}"

    def test_xp_complexity_1(self, app):
        """Complexity 1 → base XP = 50."""
        from api import _calculate_xp
        task = Task(title='Simple', complexity=1)
        xp = _calculate_xp(task)
        assert xp == 50

    def test_xp_complexity_5(self, app):
        """Complexity 5 → base XP = 250."""
        from api import _calculate_xp
        task = Task(title='Complex', complexity=5)
        xp = _calculate_xp(task)
        assert xp == 250

    def test_speed_bonus_under_60s(self, app):
        """Speed bonus +25 if completed in under 60 seconds."""
        from api import _calculate_xp
        now = datetime.now(timezone.utc)
        task = Task(title='Fast', complexity=3,
                     created_at=now - timedelta(seconds=30),
                     completed_at=now)
        xp = _calculate_xp(task)
        assert xp == 175, f"Expected 175 (150 base + 25 speed), got {xp}"

    def test_speed_bonus_under_300s(self, app):
        """Speed bonus +10 if completed in under 300 seconds (but >= 60)."""
        from api import _calculate_xp
        now = datetime.now(timezone.utc)
        task = Task(title='Medium', complexity=3,
                     created_at=now - timedelta(seconds=120),
                     completed_at=now)
        xp = _calculate_xp(task)
        assert xp == 160, f"Expected 160 (150 base + 10 speed), got {xp}"

    def test_no_speed_bonus_over_300s(self, app):
        """No speed bonus if completed in >= 300 seconds."""
        from api import _calculate_xp
        now = datetime.now(timezone.utc)
        task = Task(title='Slow', complexity=3,
                     created_at=now - timedelta(seconds=350),
                     completed_at=now)
        xp = _calculate_xp(task)
        assert xp == 150, f"Expected 150 (no speed bonus), got {xp}"

    def test_review_bonus_approve(self, app):
        """Review bonus +15 for approved review."""
        from api import _calculate_xp
        task = Task(title='Reviewed', complexity=3)
        xp = _calculate_xp(task, review_decision='approve')
        assert xp == 165, f"Expected 165 (150 base + 15 review), got {xp}"

    def test_no_review_bonus_for_direct(self, app):
        """No review bonus for direct completion (no review_decision)."""
        from api import _calculate_xp
        task = Task(title='Direct', complexity=3)
        xp = _calculate_xp(task)
        assert xp == 150, f"Expected 150 (no review bonus), got {xp}"

    def test_combined_bonuses(self, app):
        """Fast + approved = 150 + 25 + 15 = 190."""
        from api import _calculate_xp
        now = datetime.now(timezone.utc)
        task = Task(title='FastReviewed', complexity=3,
                     created_at=now - timedelta(seconds=30),
                     completed_at=now)
        xp = _calculate_xp(task, review_decision='approve')
        assert xp == 190, f"Expected 190, got {xp}"


class TestLevelTiers:
    """Test level tier boundaries."""

    def test_level_1_rookie(self, app):
        """0 XP → level 1, Rookie."""
        agent = Agent(name='rookie', display_name='Rookie')
        agent.xp = 0
        agent.compute_level()
        assert agent.level == 1
        assert agent.level_name == 'Rookie'

    def test_level_2_at_boundary(self, app):
        """100 XP → level 2, Operative."""
        agent = Agent(name='op', display_name='Op')
        agent.xp = 100
        agent.compute_level()
        assert agent.level == 2
        assert agent.level_name == 'Operative'

    def test_level_2_upper_boundary(self, app):
        """499 XP → level 2."""
        agent = Agent(name='op_upper', display_name='Op Upper')
        agent.xp = 499
        agent.compute_level()
        assert agent.level == 2

    def test_level_3_at_boundary(self, app):
        """500 XP → level 3, Specialist."""
        agent = Agent(name='spec', display_name='Spec')
        agent.xp = 500
        agent.compute_level()
        assert agent.level == 3
        assert agent.level_name == 'Specialist'

    def test_level_4_at_boundary(self, app):
        """1500 XP → level 4, Expert."""
        agent = Agent(name='expert', display_name='Expert')
        agent.xp = 1500
        agent.compute_level()
        assert agent.level == 4
        assert agent.level_name == 'Expert'

    def test_level_5_commander(self, app):
        """5000+ XP → level 5, Commander."""
        agent = Agent(name='commander', display_name='Commander')
        agent.xp = 5000
        agent.compute_level()
        assert agent.level == 5
        assert agent.level_name == 'Commander'

    def test_level_5_high_xp(self, app):
        """10000 XP → still level 5."""
        agent = Agent(name='high', display_name='High')
        agent.xp = 10000
        agent.compute_level()
        assert agent.level == 5


class TestStreakTracking:
    """Test streak tracking logic."""

    def test_first_completion_sets_streak_1(self, app):
        """First completion → streak = 1."""
        from api import _update_streak
        agent = Agent(name='streaker', display_name='Streaker')
        db.session.add(agent)
        db.session.commit()
        streak = _update_streak('streaker')
        assert streak == 1
        assert agent.last_active_date == date.today()

    def test_consecutive_day_increments(self, app):
        """Second consecutive day → streak = 2."""
        from api import _update_streak
        agent = Agent(name='streaker2', display_name='Streaker2')
        db.session.add(agent)
        yesterday = date.today() - timedelta(days=1)
        agent.last_active_date = yesterday
        agent.streak = 1
        db.session.commit()
        streak = _update_streak('streaker2')
        assert streak == 2

    def test_same_day_no_change(self, app):
        """Same day → streak unchanged."""
        from api import _update_streak
        agent = Agent(name='streaker3', display_name='Streaker3')
        db.session.add(agent)
        today = date.today()
        agent.last_active_date = today
        agent.streak = 5
        db.session.commit()
        streak = _update_streak('streaker3')
        assert streak == 5  # unchanged
        assert agent.last_active_date == today

    def test_missed_day_resets(self, app):
        """Missed > 1 day → streak = 1."""
        from api import _update_streak
        agent = Agent(name='streaker4', display_name='Streaker4')
        db.session.add(agent)
        two_days_ago = date.today() - timedelta(days=2)
        agent.last_active_date = two_days_ago
        agent.streak = 3
        db.session.commit()
        streak = _update_streak('streaker4')
        assert streak == 1  # reset
        assert agent.last_active_date == date.today()


class TestXPEndpoints:
    """Test the XP API endpoints."""

    def test_get_xp_endpoint(self, client):
        """GET /api/agents/<name>/xp returns correct data."""
        # Register an agent
        client.post('/api/agents', json={
            'name': 'xptest',
            'display_name': 'XP Test',
            'skills': 'python',
        })
        resp = client.get('/api/agents/xptest/xp')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['agent'] == 'xptest'
        assert data['xp'] == 0
        assert data['level'] == 1
        assert data['level_name'] == 'Rookie'
        assert data['streak'] == 0
        assert 'recent_xp_gains' in data

    def test_xp_increases_on_task_complete(self, client):
        """XP increases after completing a task."""
        # Register agent and reviewer
        client.post('/api/agents', json={
            'name': 'worker_xp',
            'display_name': 'Worker XP',
        })
        client.post('/api/agents', json={
            'name': 'reviewer_xp',
            'display_name': 'Reviewer XP',
        })

        t = _create_task(client, 'XP Task', complexity=3)
        _complete_task(client, t['id'], agent='worker_xp', reviewer='reviewer_xp')

        resp = client.get('/api/agents/worker_xp/xp')
        data = resp.get_json()
        # Base: 50*3=150 + speed bonus (likely <60s since fast) + 15 review bonus
        assert data['xp'] >= 165, f"Expected >= 165 XP, got {data['xp']}"
        assert data['level'] >= 1
        assert data['streak'] >= 1

    def test_recent_xp_gains(self, client):
        """Recent XP gains list shows earned XP entries."""
        client.post('/api/agents', json={
            'name': 'gain_agent',
            'display_name': 'Gain Agent',
        })
        client.post('/api/agents', json={
            'name': 'gain_reviewer',
            'display_name': 'Gain Reviewer',
        })

        t = _create_task(client, 'Gain Task', complexity=2)
        _complete_task(client, t['id'], agent='gain_agent', reviewer='gain_reviewer')

        resp = client.get('/api/agents/gain_agent/xp')
        data = resp.get_json()
        assert len(data['recent_xp_gains']) >= 1
        gain = data['recent_xp_gains'][0]
        assert gain['xp_gained'] >= 100  # 50*2=100 base
        assert gain['task_id'] == t['id']
        assert 'earned_at' in gain

    def test_leaderboard_endpoint(self, client):
        """POST /api/agents/xp/leaderboard returns sorted agents."""
        client.post('/api/agents', json={'name': 'low_xp', 'display_name': 'Low'})
        client.post('/api/agents', json={'name': 'high_xp', 'display_name': 'High'})

        # Manually set XP
        with client.application.app_context():
            low = db.session.get(Agent, 'low_xp')
            low.xp = 50
            low.compute_level()
            high = db.session.get(Agent, 'high_xp')
            high.xp = 500
            high.compute_level()
            db.session.commit()

        resp = client.post('/api/agents/xp/leaderboard')
        assert resp.status_code == 200
        data = resp.get_json()
        leaderboard = data['leaderboard']
        assert len(leaderboard) >= 2
        # high_xp should be before low_xp
        high_idx = next(i for i, a in enumerate(leaderboard) if a['name'] == 'high_xp')
        low_idx = next(i for i, a in enumerate(leaderboard) if a['name'] == 'low_xp')
        assert high_idx < low_idx, "high_xp should rank above low_xp"
        # Check high_xp has level 3 (Specialist)
        high_entry = leaderboard[high_idx]
        assert high_entry['level'] >= 3
        assert high_entry['level_name'] == 'Specialist'


class TestXPInAgentDict:
    """Test that XP fields appear in agent.to_dict()."""

    def test_to_dict_includes_xp_fields(self, app):
        """agent.to_dict() includes xp, level, level_name, streak."""
        agent = Agent(name='dict_test', display_name='Dict Test')
        agent.xp = 100
        agent.level = 2
        agent.streak = 3
        db.session.add(agent)
        db.session.commit()
        d = agent.to_dict()
        assert d['xp'] == 100
        assert d['level'] == 2
        assert d['level_name'] == 'Operative'
        assert d['streak'] == 3
