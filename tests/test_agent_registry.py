"""Tests for Task #17: Agent registry with skill-based discovery."""

import json
import pytest
from models import db, Agent


@pytest.fixture
def app():
    from app import create_app
    app = create_app(testing=True)
    with app.app_context():
        db.create_all()
        # Seed agents with diverse skills, roles, reputations
        agents_data = [
            ('coder', 'Coder', 'worker', 'python,flask,backend,api', 90, 'idle'),
            ('editor', 'Editor', 'worker', 'text,docs,frontend,ui', 75, 'idle'),
            ('researcher', 'Researcher', 'worker', 'research,data,analysis', 60, 'idle'),
            ('planner', 'Planner', 'mission_control', 'planning,strategy', 85, 'busy'),
            ('python_dev', 'Python Dev', 'worker', 'python,django,flask,api', 80, 'idle'),
            ('frontend_dev', 'Frontend Dev', 'worker', 'react,typescript,css,ui', 55, 'idle'),
            ('low_rep', 'Low Rep', 'worker', 'python', 30, 'idle'),
            ('busy_worker', 'Busy Worker', 'worker', 'python,flask', 70, 'busy'),
        ]
        for name, display, role, skills, rep, status in agents_data:
            db.session.add(Agent(
                name=name, display_name=display, role=role,
                skills=skills, reputation_score=rep, status=status,
            ))
        db.session.commit()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


class TestSkillMatching:
    """Test skill-based filtering."""

    def test_exact_skill_match(self, client):
        """Agents with ALL listed skills are returned."""
        resp = client.get('/api/agents/discover?skills=python,flask')
        assert resp.status_code == 200
        data = resp.get_json()
        names = [a['name'] for a in data['agents']]
        assert 'coder' in names  # has python,flask
        assert 'python_dev' in names  # has python (and django,flask)
        assert 'busy_worker' in names  # has python,flask
        # low_rep has python but NOT flask, so should NOT be in results
        assert 'low_rep' not in names, "low_rep lacks 'flask'"
        # frontend_dev has no python/flask overlap
        assert 'frontend_dev' not in names
        # editor has no python/flask overlap
        assert 'editor' not in names
        # researcher has no python/flask overlap
        assert 'researcher' not in names

    def test_subset_skill_match(self, client):
        """Agent with a subset of required skills is not returned (must have ALL)."""
        resp = client.get('/api/agents/discover?skills=python,react')
        data = resp.get_json()
        names = [a['name'] for a in data['agents']]
        # No agent has BOTH python and react
        assert 'coder' not in names  # has python, not react
        assert 'frontend_dev' not in names  # has react, not python
        assert 'editor' not in names  # has neither

    def test_no_skill_match(self, client):
        """No agents match when skills don't exist."""
        resp = client.get('/api/agents/discover?skills=quantum,physics')
        data = resp.get_json()
        assert len(data['agents']) == 0
        assert data['total'] == 0

    def test_all_agents_without_skills_param(self, client):
        """Without skills parameter, all agents are returned."""
        resp = client.get('/api/agents/discover')
        data = resp.get_json()
        # All 8 seeded agents
        assert data['total'] == 8


class TestRoleFiltering:
    """Test role-based filtering."""

    def test_filter_by_role_worker(self, client):
        """Only workers are returned when role=worker."""
        resp = client.get('/api/agents/discover?role=worker')
        data = resp.get_json()
        names = [a['name'] for a in data['agents']]
        assert 'coder' in names
        assert 'editor' in names
        assert 'planner' not in names  # mission_control
        for a in data['agents']:
            assert a['role'] == 'worker'

    def test_filter_by_role_mission_control(self, client):
        """Only mission_control agents."""
        resp = client.get('/api/agents/discover?role=mission_control')
        data = resp.get_json()
        names = [a['name'] for a in data['agents']]
        assert 'planner' in names
        assert 'coder' not in names

    def test_no_match_role(self, client):
        """No agents match non-existent role."""
        resp = client.get('/api/agents/discover?role=overseer')
        data = resp.get_json()
        assert data['total'] == 0


class TestReputationFiltering:
    """Test reputation-based filtering."""

    def test_min_reputation_filter(self, client):
        """Only agents with reputation >= min_reputation."""
        resp = client.get('/api/agents/discover?min_reputation=80')
        data = resp.get_json()
        names = [a['name'] for a in data['agents']]
        assert 'coder' in names  # 90
        assert 'planner' in names  # 85
        assert 'python_dev' in names  # 80
        assert 'editor' not in names  # 75
        assert 'low_rep' not in names  # 30

    def test_min_reputation_no_match(self, client):
        """No agents match high threshold."""
        resp = client.get('/api/agents/discover?min_reputation=95')
        data = resp.get_json()
        assert data['total'] == 0

    def test_min_reputation_all_pass(self, client):
        """All agents pass zero threshold."""
        resp = client.get('/api/agents/discover?min_reputation=0')
        data = resp.get_json()
        assert data['total'] == 8


class TestAvailabilityFiltering:
    """Test availability filtering."""

    def test_min_available_returns_idle_only(self, client):
        """Only idle agents when min_available=true."""
        resp = client.get('/api/agents/discover?min_available=true')
        data = resp.get_json()
        names = [a['name'] for a in data['agents']]
        assert 'coder' in names  # idle
        assert 'editor' in names  # idle
        assert 'planner' not in names  # busy
        assert 'busy_worker' not in names  # busy
        for a in data['agents']:
            assert a['status'] == 'idle'

    def test_min_available_false_returns_all(self, client):
        """min_available=false returns all agents."""
        resp = client.get('/api/agents/discover?min_available=false')
        data = resp.get_json()
        assert data['total'] == 8


class TestCombinedFilters:
    """Test combining multiple filters."""

    def test_skills_and_role(self, client):
        """Combined skills + role filter."""
        resp = client.get('/api/agents/discover?skills=python,flask&role=worker')
        data = resp.get_json()
        names = [a['name'] for a in data['agents']]
        assert 'coder' in names
        assert 'python_dev' in names
        assert 'planner' not in names  # wrong role
        for a in data['agents']:
            assert a['role'] == 'worker'

    def test_skills_and_reputation(self, client):
        """Combined skills + reputation filter."""
        resp = client.get('/api/agents/discover?skills=python&min_reputation=80')
        data = resp.get_json()
        names = [a['name'] for a in data['agents']]
        assert 'coder' in names  # has python, rep 90
        assert 'python_dev' in names  # has python, rep 80
        assert 'low_rep' not in names  # has python, but rep 30 < 80

    def test_skills_availability_and_reputation(self, client):
        """Three filters combined."""
        resp = client.get('/api/agents/discover?skills=python,flask&min_reputation=80&min_available=true')
        data = resp.get_json()
        names = [a['name'] for a in data['agents']]
        assert 'coder' in names  # matches all
        # python_dev has skills (python,django,flask,api), rep 80, idle -> match
        assert 'python_dev' in names
        # busy_worker has skills, rep 70 < 80, also busy -> no match
        assert 'busy_worker' not in names


class TestSortedOutput:
    """Test the sorting of discovery results."""

    def test_sorted_by_skill_match_then_reputation(self, client):
        """Results sorted by skill_match_count DESC, then reputation DESC."""
        resp = client.get('/api/agents/discover?skills=python,flask,api')
        data = resp.get_json()
        agents = data['agents']
        # Check sorting: descending by match count, then reputation
        for i in range(len(agents) - 1):
            a, b = agents[i], agents[i + 1]
            if a['skill_match_count'] == b['skill_match_count']:
                assert a['reputation_score'] >= b['reputation_score'], \
                    f"{a['name']} (rep {a['reputation_score']}) should be before {b['name']} (rep {b['reputation_score']})"
            else:
                assert a['skill_match_count'] >= b['skill_match_count'], \
                    f"{a['name']} ({a['skill_match_count']} matches) should be before {b['name']} ({b['skill_match_count']} matches)"

    def test_skill_match_counts(self, client):
        """Verify skill_match_count values are correct."""
        resp = client.get('/api/agents/discover?skills=python,flask')
        data = resp.get_json()
        for a in data['agents']:
            agent_skills = set(s.strip().lower() for s in a['skills'])
            expected_count = len([s for s in ['python', 'flask'] if s in agent_skills])
            assert a['skill_match_count'] == expected_count, \
                f"{a['name']}: expected {expected_count}, got {a['skill_match_count']}"


class TestEmptyResults:
    """Test edge cases returning empty results."""

    def test_no_agents_match_any_filter(self, client):
        """No agents match extreme filter values."""
        resp = client.get('/api/agents/discover?skills=quantum,physics&min_reputation=100')
        data = resp.get_json()
        assert data['total'] == 0
        assert data['agents'] == []

    def test_unknown_role(self, client):
        """Non-existent role returns empty."""
        resp = client.get('/api/agents/discover?role=king')
        data = resp.get_json()
        assert data['total'] == 0
