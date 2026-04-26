"""tests/test_agent_cards.py — Tests for A2A-compatible Agent Card system."""

import json
import pytest
from models import db, Task, Agent


@pytest.fixture
def app():
    from app import create_app
    app = create_app(testing=True)
    with app.app_context():
        db.create_all()
        # Seed agents with skills and projects
        for name, display, model, role, skills, projects in [
            ('coder', 'Coder', 'deepseek-v4-flash', 'worker', 'python,flask,backend', 'task-board,hermes'),
            ('editor', 'Editor', 'gpt-5-nano', 'worker', 'writing,editing,markdown', 'docs,blog'),
            ('researcher', 'Researcher', 'deepseek-v4-flash', 'worker', 'research,analysis,data', 'task-board'),
        ]:
            db.session.add(Agent(
                name=name, display_name=display, model=model,
                role=role, skills=skills,
                preferred_projects=projects,
                max_concurrent=3,
            ))
        db.session.commit()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


class TestAgentCardEndpoint:
    def test_get_agent_card(self, client):
        """GET /api/agents/<name>/card returns A2A-compatible Agent Card."""
        resp = client.get('/api/agents/coder/card')
        assert resp.status_code == 200
        card = resp.get_json()

        assert card['name'] == 'coder'
        assert card['display_name'] == 'Coder'
        assert card['role'] == 'worker'
        assert card['skills'] == ['python', 'flask', 'backend']
        assert card['input_modes'] == ['text', 'data']
        assert card['output_modes'] == ['text', 'data']
        assert card['preferred_projects'] == ['task-board', 'hermes']
        assert card['max_concurrent'] == 3
        assert card['model'] == 'deepseek-v4-flash'
        assert card['status'] == 'idle'
        assert card['reputation_score'] == 50.0

    def test_get_agent_card_not_found(self, client):
        """Non-existent agent returns 404."""
        resp = client.get('/api/agents/nonexistent/card')
        assert resp.status_code == 404

    def test_list_agent_cards(self, client):
        """GET /api/agents/cards returns all agent cards."""
        resp = client.get('/api/agents/cards')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'cards' in data
        assert len(data['cards']) == 3

        # Verify card structure
        for card in data['cards']:
            assert 'name' in card
            assert 'display_name' in card
            assert 'role' in card
            assert 'skills' in card
            assert isinstance(card['skills'], list)
            assert 'input_modes' in card
            assert 'output_modes' in card
            assert 'preferred_projects' in card
            assert isinstance(card['preferred_projects'], list)
            assert 'max_concurrent' in card
            assert 'model' in card
            assert 'status' in card
            assert 'reputation_score' in card

    def test_agent_card_fields_are_correct_types(self, client):
        """Verify all fields in Agent Card have correct types."""
        resp = client.get('/api/agents/coder/card')
        card = resp.get_json()

        assert isinstance(card['name'], str)
        assert isinstance(card['display_name'], str)
        assert isinstance(card['role'], str)
        assert isinstance(card['skills'], list)
        assert all(isinstance(s, str) for s in card['skills'])
        assert isinstance(card['input_modes'], list)
        assert isinstance(card['output_modes'], list)
        assert isinstance(card['preferred_projects'], list)
        assert isinstance(card['max_concurrent'], int)
        assert isinstance(card['model'], str)
        assert isinstance(card['status'], str)
        assert isinstance(card['reputation_score'], (int, float))

    def test_agent_card_empty_skills(self, client):
        """Agent with no skills returns empty list, not empty string."""
        from app import create_app as _create_app
        app = _create_app(testing=True)
        with app.app_context():
            db.create_all()
            agent = Agent(name='minimal', display_name='Minimal', skills='')
            db.session.add(agent)
            db.session.commit()
            from api import _build_agent_card
            card = _build_agent_card(agent)
            assert card['skills'] == []
            assert card['preferred_projects'] == []
            db.drop_all()

    def test_agent_card_includes_reputation(self, client):
        """Agent Card should include reputation_score."""
        resp = client.get('/api/agents/coder/card')
        card = resp.get_json()
        assert 'reputation_score' in card
        assert card['reputation_score'] == 50.0
