# Task Board — Agent Task Queue

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** A lightweight task queue that agents can check, claim, and report back on — enabling autonomous multi-agent work distribution.

**Architecture:** Flask + SQLite REST API with a web dashboard. Agents interact via HTTP (check tasks, claim, complete). Human-facing dashboard shows real-time task status.

**Tech Stack:** Flask, SQLAlchemy, SQLite, Jinja2, pytest

---

## API Design

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tasks` | List tasks (filterable by status, agent, priority) |
| `POST` | `/api/tasks` | Create a new task |
| `GET` | `/api/tasks/<id>` | Get task details |
| `POST` | `/api/tasks/<id>/claim` | Claim a task (agent locks it) |
| `POST` | `/api/tasks/<id>/complete` | Mark task done with result |
| `POST` | `/api/tasks/<id>/fail` | Mark task failed with reason |
| `POST` | `/api/tasks/<id>/release` | Release a claimed task back to queue |
| `GET` | `/api/agents` | List agents and their active tasks |
| `GET` | `/api/stats` | Queue statistics |

### Task Model

```python
class Task:
    id: int              # Auto-increment
    title: str           # Short description
    description: str     # Full details (markdown OK)
    status: str          # pending | claimed | completed | failed
    priority: int        # 1 (urgent) to 5 (low), default 3
    agent: str           # Which agent claimed it (nullable)
    result: str          # Agent's report on completion (nullable)
    error: str           # Failure reason (nullable)
    created_at: datetime
    claimed_at: datetime  # When agent claimed it
    completed_at: datetime
    tags: str            # Comma-separated tags for filtering
    project: str         # Which project this belongs to
```

### Agent Model

```python
class Agent:
    name: str            # Primary key (matches Hermes profile name)
    display_name: str    # Human-friendly name
    model: str           # Which model they run
    status: str          # idle | busy | offline
    tasks_completed: int
    tasks_failed: int
```

---

## Tasks

### Task 1: Project Setup

**Objective:** Create project structure, dependencies, and test config.

**Files:**
- Create: `/home/exedev/task-board/requirements.txt`
- Create: `/home/exedev/task-board/pytest.ini`
- Create: `/home/exedev/task-board/run.py`

**Step 1: Create requirements.txt**

```
flask>=3.0
flask-sqlalchemy>=3.1
pytest>=8.0
pytest-cov>=5.0
```

**Step 2: Create pytest.ini**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short
```

**Step 3: Create run.py**

```python
#!/usr/bin/env python3
"""Entry point for Task Board."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8893))
    app.run(host='0.0.0.0', port=port, debug=True)
```

**Step 4: Verify**

Run: `cd /home/exedev/task-board && pip install -r requirements.txt`
Expected: Install succeeds

---

### Task 2: Database Models (TDD)

**Objective:** Define Task and Agent models with full test coverage.

**Files:**
- Create: `/home/exedev/task-board/models.py`
- Create: `/home/exedev/task-board/tests/__init__.py`
- Create: `/home/exedev/task-board/tests/test_models.py`

**Step 1: Write failing tests**

```python
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
```

**Step 2: Run tests to verify failure**

Run: `cd /home/exedev/task-board && python -m pytest tests/test_models.py -v`
Expected: FAIL — ImportError (no models module)

**Step 3: Implement models**

```python
# models.py
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Task(db.Model):
    __tablename__ = 'tasks'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    status = db.Column(db.String(20), default='pending', index=True)
    priority = db.Column(db.Integer, default=3)
    agent = db.Column(db.String(50), nullable=True, index=True)
    result = db.Column(db.Text, nullable=True)
    error = db.Column(db.Text, nullable=True)
    tags = db.Column(db.String(500), default='')
    project = db.Column(db.String(100), default='general')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    claimed_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    def claim(self, agent_name):
        self.status = 'claimed'
        self.agent = agent_name
        self.claimed_at = datetime.now(timezone.utc)

    def complete(self, result_text):
        self.status = 'completed'
        self.result = result_text
        self.completed_at = datetime.now(timezone.utc)

    def fail(self, error_text):
        self.status = 'failed'
        self.error = error_text
        self.completed_at = datetime.now(timezone.utc)

    def release(self):
        self.status = 'pending'
        self.agent = None
        self.claimed_at = None

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'status': self.status,
            'priority': self.priority,
            'agent': self.agent,
            'result': self.result,
            'error': self.error,
            'tags': self.tags,
            'project': self.project,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'claimed_at': self.claimed_at.isoformat() if self.claimed_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }


class Agent(db.Model):
    __tablename__ = 'agents'

    name = db.Column(db.String(50), primary_key=True)
    display_name = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(100), default='')
    status = db.Column(db.String(20), default='idle')
    tasks_completed = db.Column(db.Integer, default=0)
    tasks_failed = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            'name': self.name,
            'display_name': self.display_name,
            'model': self.model,
            'status': self.status,
            'tasks_completed': self.tasks_completed,
            'tasks_failed': self.tasks_failed,
        }
```

**Step 4: Run tests to verify pass**

Run: `cd /home/exedev/task-board && python -m pytest tests/test_models.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add models.py tests/
git commit -m "feat: add Task and Agent models with TDD"
```

---

### Task 3: App Factory + Config

**Objective:** Create Flask app with SQLite config.

**Files:**
- Create: `/home/exedev/task-board/app.py`
- Create: `/home/exedev/task-board/config.py`

**Step 1: Create config.py**

```python
# config.py
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-change-in-prod')
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        f'sqlite:///{os.path.join(BASE_DIR, "instance", "task_board.db")}'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
```

**Step 2: Create app.py**

```python
# app.py
from flask import Flask
from models import db
from config import Config, TestingConfig


def create_app(testing=False):
    app = Flask(__name__)
    app.config.from_object(TestingConfig if testing else Config)

    db.init_app(app)

    with app.app_context():
        db.create_all()

    # Register blueprints (added in later tasks)
    # from api import api_bp
    # app.register_blueprint(api_bp, url_prefix='/api')

    # Health endpoint
    @app.route('/health')
    def health():
        return {'status': 'ok'}

    return app
```

**Step 3: Verify**

Run: `cd /home/exedev/task-board && python -c "from app import create_app; app = create_app(testing=True); print('OK')"`
Expected: OK

---

### Task 4: REST API (TDD)

**Objective:** Full CRUD API for tasks and agents.

**Files:**
- Create: `/home/exedev/task-board/api.py`
- Create: `/home/exedev/task-board/tests/test_api.py`

**Step 1: Write failing tests**

```python
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
```

**Step 2: Run tests to verify failure**

Run: `cd /home/exedev/task-board && python -m pytest tests/test_api.py -v`
Expected: FAIL — ImportError (no api module)

**Step 3: Implement API**

```python
# api.py
from flask import Blueprint, request, jsonify
from models import db, Task, Agent

api_bp = Blueprint('api', __name__)


@api_bp.route('/tasks', methods=['GET'])
def list_tasks():
    query = Task.query
    status = request.args.get('status')
    agent = request.args.get('agent')
    project = request.args.get('project')
    tag = request.args.get('tag')

    if status:
        query = query.filter_by(status=status)
    if agent:
        query = query.filter_by(agent=agent)
    if project:
        query = query.filter_by(project=project)
    if tag:
        query = query.filter(Task.tags.contains(tag))

    query = query.order_by(Task.priority, Task.created_at)
    tasks = query.all()
    return jsonify({'tasks': [t.to_dict() for t in tasks]})


@api_bp.route('/tasks', methods=['POST'])
def create_task():
    data = request.get_json()
    if not data or 'title' not in data:
        return jsonify({'error': 'title is required'}), 400

    task = Task(
        title=data['title'],
        description=data.get('description', ''),
        priority=data.get('priority', 3),
        tags=data.get('tags', ''),
        project=data.get('project', 'general'),
    )
    db.session.add(task)
    db.session.commit()
    return jsonify(task.to_dict()), 201


@api_bp.route('/tasks/<int:task_id>', methods=['GET'])
def get_task(task_id):
    task = Task.query.get_or_404(task_id)
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/claim', methods=['POST'])
def claim_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json()
    if not data or 'agent' not in data:
        return jsonify({'error': 'agent name required'}), 400

    if task.status != 'pending':
        return jsonify({'error': f'Task is {task.status}, cannot claim'}), 409

    task.claim(data['agent'])
    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/complete', methods=['POST'])
def complete_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json()
    if not data or 'result' not in data:
        return jsonify({'error': 'result required'}), 400

    task.complete(data['result'])

    # Update agent stats
    if task.agent:
        agent = Agent.query.get(task.agent)
        if agent:
            agent.tasks_completed += 1
            agent.status = 'idle'

    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/fail', methods=['POST'])
def fail_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json()
    if not data or 'error' not in data:
        return jsonify({'error': 'error reason required'}), 400

    task.fail(data['error'])

    if task.agent:
        agent = Agent.query.get(task.agent)
        if agent:
            agent.tasks_failed += 1
            agent.status = 'idle'

    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/release', methods=['POST'])
def release_task(task_id):
    task = Task.query.get_or_404(task_id)
    task.release()
    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    return jsonify({'deleted': task_id})


@api_bp.route('/agents', methods=['GET'])
def list_agents():
    agents = Agent.query.all()
    return jsonify({'agents': [a.to_dict() for a in agents]})


@api_bp.route('/stats', methods=['GET'])
def stats():
    total = Task.query.count()
    by_status = {}
    for status in ['pending', 'claimed', 'completed', 'failed']:
        by_status[status] = Task.query.filter_by(status=status).count()

    by_agent = {}
    for agent in Agent.query.all():
        active = Task.query.filter_by(agent=agent.name, status='claimed').count()
        by_agent[agent.name] = {
            'active': active,
            'completed': agent.tasks_completed,
            'failed': agent.tasks_failed,
        }

    return jsonify({
        'total_tasks': total,
        'by_status': by_status,
        'by_agent': by_agent,
    })
```

**Step 4: Update app.py to register blueprint**

Add to `create_app()`:
```python
from api import api_bp
app.register_blueprint(api_bp, url_prefix='/api')
```

**Step 5: Run tests**

Run: `cd /home/exedev/task-board && python -m pytest tests/test_api.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add api.py app.py tests/test_api.py
git commit -m "feat: REST API for task CRUD, claim/complete/fail/release"
```

---

### Task 5: Web Dashboard

**Objective:** Human-facing web UI showing task board.

**Files:**
- Create: `/home/exedev/task-board/templates/base.html`
- Create: `/home/exedev/task-board/templates/dashboard.html`
- Create: `/home/exedev/task-board/templates/task.html`
- Create: `/home/exedev/task-board/static/style.css`

**Step 1: Create base.html**

Dark theme, clean layout. Navigation: Dashboard | Tasks | Agents | Stats.

**Step 2: Create dashboard.html**

Four columns: Pending | Claimed | Completed | Failed. Cards for each task showing title, agent, priority, time. Click to view details.

**Step 3: Create task.html**

Full task detail view with description, status, agent, result/error, timeline.

**Step 4: Create style.css**

Dark theme matching other dashboards. Card-based layout. Priority colors (1=red, 2=orange, 3=blue, 4=gray, 5=dim).

**Step 5: Add dashboard routes to app.py**

```python
@app.route('/')
def dashboard():
    tasks = Task.query.order_by(Task.priority, Task.created_at.desc()).all()
    agents = Agent.query.all()
    stats = {
        'pending': Task.query.filter_by(status='pending').count(),
        'claimed': Task.query.filter_by(status='claimed').count(),
        'completed': Task.query.filter_by(status='completed').count(),
        'failed': Task.query.filter_by(status='failed').count(),
    }
    return render_template('dashboard.html', tasks=tasks, agents=agents, stats=stats)
```

---

### Task 6: Auth + Health + Systemd

**Objective:** exe.dev auth, health endpoint, systemd service.

**Files:**
- Modify: `/home/exedev/task-board/app.py` (add auth middleware)
- Create: `/home/exedev/task-board/task-board.service`

**Step 1: Add auth middleware**

Same pattern as other apps — check `X-ExeDev-Email` header, bypass for `/health` and tests.

**Step 2: Create systemd service**

```ini
[Unit]
Description=Hermes Task Board
After=network.target

[Service]
Type=simple
User=exedev
WorkingDirectory=/home/exedev/task-board
ExecStart=/home/exedev/venv/bin/python run.py
Restart=always
RestartSec=5
Environment=PORT=8893

[Install]
WantedBy=multi-user.target
```

**Step 3: Enable and start**

```bash
sudo cp task-board.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now task-board
```

---

### Task 7: GitHub Push

**Objective:** Push to GitHub with README.

```bash
cd /home/exedev/task-board
git init
git add -A
git commit -m "feat: Hermes Task Board — agent task queue"
gh repo create acgh213/task-board --public --source=. --push
```

README should document: what it is, API endpoints, how agents interact, how to run.

---

## Agent Workflow (How Agents Use This)

### Checking for tasks
```bash
curl http://localhost:8893/api/tasks?status=pending | jq
```

### Claiming a task
```bash
curl -X POST http://localhost:8893/api/tasks/1/claim \
  -H "Content-Type: application/json" \
  -d '{"agent": "coder"}'
```

### Reporting completion
```bash
curl -X POST http://localhost:8893/api/tasks/1/complete \
  -H "Content-Type: application/json" \
  -d '{"result": "Built the feature. Tests pass. PR ready."}'
```

### Reporting failure
```bash
curl -X POST http://localhost:8893/api/tasks/1/fail \
  -H "Content-Type: application/json" \
  -d '{"error": "Missing dependency, need XYZ installed first"}'
```
