"""Tests for Task #10: Achievement badge system."""

import json
import pytest
from datetime import datetime, timezone, timedelta
from models import db, Task, Agent, Review, Achievement, AgentBadge


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
    client.post(f'/api/tasks/{task_id}/assign', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/claim', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/start', json={'agent': agent})
    client.post(f'/api/tasks/{task_id}/submit?skip_wait=true', json={'agent': agent, 'result': result})
    return client.post(f'/api/tasks/{task_id}/review',
                       json={'reviewer': reviewer, 'decision': 'approve'})


class TestBadgeCriteriaEvaluation:
    """Test each badge's criteria evaluation logic."""

    def test_first_mission_badge(self, app):
        """🎯 First Mission: tasks_completed >= 1."""
        from api import _check_badges
        agent = Agent(name='newbie', display_name='Newbie', tasks_completed=1)
        db.session.add(agent)
        db.session.commit()
        new_badges = _check_badges('newbie')
        badge_names = [b['badge_name'] for b in new_badges]
        assert 'First Mission' in badge_names

    def test_first_mission_not_earned(self, app):
        """Not earned when tasks_completed == 0."""
        from api import _check_badges
        agent = Agent(name='noob', display_name='Noob', tasks_completed=0)
        db.session.add(agent)
        db.session.commit()
        new_badges = _check_badges('noob')
        badge_names = [b['badge_name'] for b in new_badges]
        assert 'First Mission' not in badge_names

    def test_on_fire_badge(self, app):
        """🔥 On Fire: tasks_completed >= 5 AND tasks_failed == 0."""
        from api import _check_badges
        agent = Agent(name='hotshot', display_name='Hotshot',
                      tasks_completed=5, tasks_failed=0)
        db.session.add(agent)
        db.session.commit()
        new_badges = _check_badges('hotshot')
        badge_names = [b['badge_name'] for b in new_badges]
        assert 'On Fire' in badge_names

    def test_on_fire_not_earned_with_failures(self, app):
        """Not earned if there are failures."""
        from api import _check_badges
        agent = Agent(name='damp', display_name='Damp',
                      tasks_completed=5, tasks_failed=1)
        db.session.add(agent)
        db.session.commit()
        new_badges = _check_badges('damp')
        badge_names = [b['badge_name'] for b in new_badges]
        assert 'On Fire' not in badge_names

    def test_ironclad_badge(self, app):
        """🛡️ Ironclad: tasks_completed >= 10 AND tasks_failed == 0."""
        from api import _check_badges
        agent = Agent(name='tank', display_name='Tank',
                      tasks_completed=10, tasks_failed=0)
        db.session.add(agent)
        db.session.commit()
        new_badges = _check_badges('tank')
        badge_names = [b['badge_name'] for b in new_badges]
        assert 'Ironclad' in badge_names

    def test_phoenix_badge(self, app):
        """🔄 Phoenix: tasks_completed >= 1 AND tasks_failed >= 1."""
        from api import _check_badges
        agent = Agent(name='riser', display_name='Riser',
                      tasks_completed=5, tasks_failed=2)
        db.session.add(agent)
        db.session.commit()
        new_badges = _check_badges('riser')
        badge_names = [b['badge_name'] for b in new_badges]
        assert 'Phoenix' in badge_names

    def test_phoenix_not_earned_no_failures(self, app):
        """Not earned if no failures."""
        from api import _check_badges
        agent = Agent(name='perfect', display_name='Perfect',
                      tasks_completed=5, tasks_failed=0)
        db.session.add(agent)
        db.session.commit()
        new_badges = _check_badges('perfect')
        badge_names = [b['badge_name'] for b in new_badges]
        assert 'Phoenix' not in badge_names

    def test_architect_badge(self, app):
        """🏗️ Architect: completed 5+ tasks with complexity >= 4."""
        from api import _check_badges
        agent = Agent(name='arch', display_name='Architect')
        db.session.add(agent)
        db.session.flush()
        for i in range(5):
            t = Task(title=f'Complex {i}', status='completed',
                     claimed_by='arch', complexity=4)
            db.session.add(t)
        db.session.commit()
        new_badges = _check_badges('arch')
        badge_names = [b['badge_name'] for b in new_badges]
        assert 'Architect' in badge_names

    def test_eagle_eye_badge(self, app):
        """👀 Eagle Eye: reviewed 10+ tasks."""
        from api import _check_badges
        agent = Agent(name='eagle', display_name='Eagle Eye')
        db.session.add(agent)
        db.session.flush()
        for i in range(10):
            t = Task(title=f'Rev {i}')
            db.session.add(t)
            db.session.flush()
            db.session.add(Review(task_id=t.id, reviewer='eagle', decision='approve'))
        db.session.commit()
        new_badges = _check_badges('eagle')
        badge_names = [b['badge_name'] for b in new_badges]
        assert 'Eagle Eye' in badge_names


class TestDuplicateProtection:
    """Test that badges can't be earned twice."""

    def test_cannot_earn_same_badge_twice(self, app):
        """Duplicate prevention: same badge not added again."""
        from api import _check_badges
        # Seed achievement if not already
        ach = Achievement.query.filter_by(name='First Mission').first()
        if not ach:
            ach = Achievement(name='First Mission', icon='🎯',
                              description='Test', criteria='{"type":"tasks_completed","min":1}')
            db.session.add(ach)
            db.session.flush()

        agent = Agent(name='dup_test', display_name='Dup', tasks_completed=1)
        db.session.add(agent)
        db.session.commit()

        # First call should earn it
        first = _check_badges('dup_test')
        assert len([b for b in first if b['badge_name'] == 'First Mission']) == 1

        # Second call should not re-earn it
        second = _check_badges('dup_test')
        assert len([b for b in second if b['badge_name'] == 'First Mission']) == 0


class TestBadgeEarningEvents:
    """Test that badge earning creates event log entries."""

    def test_badge_earned_logs_event(self, app):
        """Earning a badge creates a 'badge_earned' event."""
        from api import _check_badges
        from models import EventLog
        agent = Agent(name='event_test', display_name='Event Test', tasks_completed=1)
        db.session.add(agent)
        db.session.commit()
        _check_badges('event_test')
        events = EventLog.query.filter_by(
            agent='event_test', event_type='badge_earned'
        ).all()
        assert len(events) >= 1
        details = json.loads(events[0].details) if events[0].details else {}
        assert 'badge_name' in details


class TestAgentBadgeListEndpoint:
    """Test GET /api/agents/<name>/badges endpoint."""

    def test_agent_badge_list(self, client):
        """GET /api/agents/<name>/badges returns earned badges."""
        client.post('/api/agents', json={'name': 'badge_guy', 'display_name': 'Badge Guy'})
        # Manually award a badge
        with client.application.app_context():
            ach = Achievement.query.first()
            if ach:
                badge = AgentBadge(agent_name='badge_guy', badge_id=ach.id)
                db.session.add(badge)
                db.session.commit()
        resp = client.get('/api/agents/badge_guy/badges')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['agent'] == 'badge_guy'
        assert len(data['badges']) >= 1
        badge = data['badges'][0]
        assert 'badge_name' in badge
        assert 'earned_at' in badge

    def test_agent_no_badges(self, client):
        """Agent with no badges returns empty list."""
        client.post('/api/agents', json={'name': 'naked', 'display_name': 'Naked'})
        resp = client.get('/api/agents/naked/badges')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['badges'] == []

    def test_nonexistent_agent_returns_404(self, client):
        """Non-existent agent returns 404."""
        resp = client.get('/api/agents/nobody/badges')
        assert resp.status_code == 404


class TestRetroactiveBadgeAwarding:
    """Test POST /api/agents/<name>/check-badges for retroactive awarding."""

    def test_retroactive_badge_check(self, client):
        """POST /api/agents/<name>/check-badges awards badges retroactively."""
        client.post('/api/agents', json={
            'name': 'retro_agent',
            'display_name': 'Retro Agent',
        })
        # Set agent stats to qualify for First Mission
        with client.application.app_context():
            agent = db.session.get(Agent, 'retro_agent')
            agent.tasks_completed = 5
            db.session.commit()

        resp = client.post('/api/agents/retro_agent/check-badges')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['agent'] == 'retro_agent'
        badge_names = [b['badge_name'] for b in data['new_badges']]
        assert 'First Mission' in badge_names

    def test_retroactive_no_new_badges(self, client):
        """No new badges if agent doesn't qualify."""
        client.post('/api/agents', json={
            'name': 'still_noob',
            'display_name': 'Still Noob',
        })
        resp = client.post('/api/agents/still_noob/check-badges')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['new_badges'] == []


class TestAchievementsEndpoint:
    """Test GET /api/achievements endpoint."""

    def test_list_achievements(self, client):
        """GET /api/achievements returns all badge definitions."""
        resp = client.get('/api/achievements')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'achievements' in data
        assert len(data['achievements']) >= 8  # 8 badges seeded
        names = [a['name'] for a in data['achievements']]
        assert 'First Mission' in names
        assert 'Speed Demon' in names
        assert 'Eagle Eye' in names
