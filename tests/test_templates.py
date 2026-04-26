# tests/test_templates.py
import json
import pytest
from models import db, Task, TaskTemplate


@pytest.fixture
def app():
    from app import create_app
    app = create_app(testing=True)
    with app.app_context():
        db.create_all()
        # Seed agents
        from models import Agent
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


@pytest.fixture
def seed_templates(app):
    """Seed starter templates into the database."""
    templates = [
        TaskTemplate(
            name='feature-build',
            description='Build a new feature from research to documentation',
            steps=json.dumps([
                {
                    'title': 'Research {topic}',
                    'description': 'Research requirements for {topic}',
                    'tags': 'research',
                    'agent': 'researcher',
                    'priority': 2,
                },
                {
                    'title': 'Plan {topic} implementation',
                    'description': 'Plan the implementation of {topic}',
                    'tags': 'planning',
                    'agent': 'planner',
                    'priority': 2,
                    'depends_on': 0,
                },
                {
                    'title': 'Implement {topic}',
                    'description': 'Write code for {topic}',
                    'tags': 'python,flask',
                    'agent': 'coder',
                    'priority': 1,
                    'depends_on': 1,
                },
                {
                    'title': 'Test {topic}',
                    'description': 'Write tests for {topic}',
                    'tags': 'python,testing',
                    'agent': 'coder',
                    'priority': 2,
                    'depends_on': 2,
                },
                {
                    'title': 'Review {topic}',
                    'description': 'Code review for {topic}',
                    'tags': 'code_review',
                    'agent': 'editor',
                    'priority': 2,
                    'depends_on': 3,
                },
                {
                    'title': 'Document {topic}',
                    'description': 'Write documentation for {topic}',
                    'tags': 'documentation',
                    'agent': 'editor',
                    'priority': 3,
                    'depends_on': 4,
                },
            ]),
        ),
        TaskTemplate(
            name='bug-fix',
            description='Fix a bug with investigation and verification',
            steps=json.dumps([
                {
                    'title': 'Investigate {issue}',
                    'description': 'Investigate the {issue} bug',
                    'tags': 'research,debugging',
                    'agent': 'researcher',
                    'priority': 1,
                },
                {
                    'title': 'Write failing test for {issue}',
                    'description': 'Write a test that reproduces {issue}',
                    'tags': 'python,testing',
                    'agent': 'coder',
                    'priority': 2,
                    'depends_on': 0,
                },
                {
                    'title': 'Fix {issue}',
                    'description': 'Fix the {issue} bug',
                    'tags': 'python,fix',
                    'agent': 'coder',
                    'priority': 1,
                    'depends_on': 1,
                },
                {
                    'title': 'Verify {issue} fix',
                    'description': 'Verify the fix for {issue}',
                    'tags': 'testing,verification',
                    'agent': 'coder',
                    'priority': 2,
                    'depends_on': 2,
                },
            ]),
        ),
        TaskTemplate(
            name='documentation',
            description='Create documentation for a topic',
            steps=json.dumps([
                {
                    'title': 'Research {topic}',
                    'description': 'Research {topic} for documentation',
                    'tags': 'research,documentation',
                    'agent': 'researcher',
                    'priority': 2,
                },
                {
                    'title': 'Write {topic} draft',
                    'description': 'Write documentation draft for {topic}',
                    'tags': 'writing,documentation',
                    'agent': 'editor',
                    'priority': 2,
                    'depends_on': 0,
                },
                {
                    'title': 'Review {topic} documentation',
                    'description': 'Review documentation for {topic}',
                    'tags': 'review,documentation',
                    'agent': 'editor',
                    'priority': 2,
                    'depends_on': 1,
                },
            ]),
        ),
    ]
    with app.app_context():
        for t in templates:
            db.session.add(t)
        db.session.commit()
    return templates


class TestTemplateListing:
    def test_list_templates_empty(self, client):
        """GET /api/templates returns empty list when no templates exist."""
        resp = client.get('/api/templates')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 0
        assert data['templates'] == []

    def test_list_templates_with_data(self, client, seed_templates):
        """GET /api/templates returns all seeded templates."""
        resp = client.get('/api/templates')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] == 3
        names = {t['name'] for t in data['templates']}
        assert 'feature-build' in names
        assert 'bug-fix' in names
        assert 'documentation' in names

    def test_template_steps_are_parsed(self, client, seed_templates):
        """Template steps should be returned as a parsed JSON list."""
        resp = client.get('/api/templates')
        data = resp.get_json()
        fb = [t for t in data['templates'] if t['name'] == 'feature-build'][0]
        assert isinstance(fb['steps'], list)
        assert len(fb['steps']) == 6

    def test_template_has_description(self, client, seed_templates):
        """Template should include description."""
        resp = client.get('/api/templates')
        data = resp.get_json()
        fb = [t for t in data['templates'] if t['name'] == 'feature-build'][0]
        assert 'description' in fb
        assert 'feature' in fb['description'].lower()


class TestTemplateCreate:
    def test_create_from_template_not_found(self, client):
        """POST /api/templates/nonexistent/create returns 404."""
        resp = client.post('/api/templates/nonexistent/create', json={
            'variables': {'topic': 'user auth'},
        })
        assert resp.status_code == 404

    def test_create_feature_build_with_variables(self, client, seed_templates):
        """Create tasks from feature-build template with variable substitution."""
        resp = client.post('/api/templates/feature-build/create', json={
            'variables': {'topic': 'user auth'},
            'project': 'hermes',
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['template'] == 'feature-build'
        assert data['created'] == 6
        assert len(data['tasks']) == 6

        # Verify variable substitution
        titles = [t['title'] for t in data['tasks']]
        assert 'Research user auth' in titles
        assert 'Implement user auth' in titles
        assert 'Document user auth' in titles

        # Verify descriptions
        tasks_by_title = {t['title']: t for t in data['tasks']}
        research_task = tasks_by_title['Research user auth']
        assert research_task['description'] == 'Research requirements for user auth'
        assert research_task['tags'] == 'research'
        assert research_task['status'] == 'pending'

        # Verify agent reservations
        imple_task = tasks_by_title['Implement user auth']
        assert imple_task['reserved_for'] == 'coder'
        assert imple_task['project'] == 'hermes'

    def test_create_bug_fix_with_variables(self, client, seed_templates):
        """Create tasks from bug-fix template."""
        resp = client.post('/api/templates/bug-fix/create', json={
            'variables': {'issue': 'login crash'},
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['created'] == 4
        titles = [t['title'] for t in data['tasks']]
        assert 'Investigate login crash' in titles
        assert 'Fix login crash' in titles

    def test_create_documentation_with_variables(self, client, seed_templates):
        """Create tasks from documentation template."""
        resp = client.post('/api/templates/documentation/create', json={
            'variables': {'topic': 'API v2'},
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['created'] == 3
        titles = [t['title'] for t in data['tasks']]
        assert 'Research API v2' in titles
        assert 'Write API v2 draft' in titles

    def test_create_uses_correct_steps_and_tags(self, client, seed_templates):
        """Each step's tags and agent assignments should be correct."""
        resp = client.post('/api/templates/feature-build/create', json={
            'variables': {'topic': 'auth'},
        })
        assert resp.status_code == 201
        tasks = resp.get_json()['tasks']

        # Find the review step
        review = [t for t in tasks if 'Review' in t['title']][0]
        assert review['tags'] == 'code_review'
        assert review['reserved_for'] == 'editor'

    def test_create_without_variables_keeps_placeholders(self, client, seed_templates):
        """If no variables are provided, {placeholders} remain in text."""
        resp = client.post('/api/templates/feature-build/create', json={})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['created'] == 6
        titles = [t['title'] for t in data['tasks']]
        # Placeholders should remain since no variables provided
        assert any('{topic}' in t for t in titles)

    def test_create_no_steps(self, client, seed_templates):
        """Create with no steps should return an error."""
        # Add a template with empty steps
        from models import db, TaskTemplate
        empty_template = TaskTemplate(
            name='empty',
            description='Empty template',
            steps='[]',
        )
        with client.application.app_context():
            db.session.add(empty_template)
            db.session.commit()

        resp = client.post('/api/templates/empty/create', json={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'error' in data

    def test_dependency_events_created(self, client, seed_templates):
        """Dependency events should be logged for steps with depends_on."""
        resp = client.post('/api/templates/feature-build/create', json={
            'variables': {'topic': 'auth'},
        })
        assert resp.status_code == 201
        tasks = resp.get_json()['tasks']

        # Get the plan task (depends_on: 0 = research task)
        tasks_sorted = sorted(tasks, key=lambda t: t['id'])
        # Check events on the plan task (should have dependency_set event)
        plan_task = [t for t in tasks if 'Plan' in t['title']][0]
        resp2 = client.get(f"/api/tasks/{plan_task['id']}")
        assert resp2.status_code == 200
        task_detail = resp2.get_json()
        events = task_detail.get('events', [])
        event_types = [e['event_type'] for e in events]
        assert 'dependency_set' in event_types

    def test_priority_preserved(self, client, seed_templates):
        """Step priority should be preserved when creating tasks."""
        resp = client.post('/api/templates/feature-build/create', json={
            'variables': {'topic': 'auth'},
        })
        assert resp.status_code == 201
        tasks = resp.get_json()['tasks']
        # The implement step has priority 1
        impl = [t for t in tasks if t['title'] == 'Implement auth'][0]
        assert impl['priority'] == 1
        # The document step has priority 3
        doc = [t for t in tasks if t['title'] == 'Document auth'][0]
        assert doc['priority'] == 3


class TestVariableSubstitution:
    def test_multiple_variables(self, client, seed_templates):
        """Multiple variables should all be substituted."""
        # We need to add a template with multiple variable placeholders
        with client.application.app_context():
            from models import db, TaskTemplate
            multi = TaskTemplate(
                name='multi-var',
                description='Template with multiple variables',
                steps=json.dumps([
                    {
                        'title': 'Build {feature} for {project}',
                        'description': 'Build {feature} for {project} in {language}',
                        'tags': '{language}',
                        'agent': 'coder',
                        'priority': 2,
                    },
                ]),
            )
            db.session.add(multi)
            db.session.commit()

        resp = client.post('/api/templates/multi-var/create', json={
            'variables': {
                'feature': 'login',
                'project': 'hermes',
                'language': 'python',
            },
        })
        assert resp.status_code == 201
        task = resp.get_json()['tasks'][0]
        assert task['title'] == 'Build login for hermes'
        assert task['description'] == 'Build login for hermes in python'
        assert task['tags'] == 'python'

    def test_partial_variable_substitution(self, client, seed_templates):
        """Unsupplied variables should leave {placeholders} intact."""
        with client.application.app_context():
            from models import db, TaskTemplate
            partial = TaskTemplate(
                name='partial-var',
                description='Partial variables',
                steps=json.dumps([
                    {
                        'title': 'Do {action} on {target}',
                        'description': 'Run {action}',
                        'tags': 'test',
                        'agent': 'coder',
                        'priority': 2,
                    },
                ]),
            )
            db.session.add(partial)
            db.session.commit()

        resp = client.post('/api/templates/partial-var/create', json={
            'variables': {'action': 'deploy'},
        })
        assert resp.status_code == 201
        task = resp.get_json()['tasks'][0]
        assert task['title'] == 'Do deploy on {target}'
        assert '{target}' in task['title']
