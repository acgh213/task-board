# Agent Auto-Discovery & Task Polling Plan

> **Task:** Design how Hermes agents automatically discover and poll the task board for work.
> **Date:** 2026-04-25
> **Author:** Planner Agent

## 1. Current Architecture (As-Is)

The task board is a Flask + SQLite REST API (`http://localhost:8893`) with these key characteristics:

- **Task Model** — `id`, `title`, `description`, `status` (pending/claimed/completed/failed), `priority` (1–5), `agent`, `tags`, `project`, timestamps
- **Agent Model** — `name` (PK), `display_name`, `model`, `status` (idle/busy/offline), `tasks_completed`, `tasks_failed`
- **Hermes Profiles** — Four agent profiles exist in `~/.hermes/profiles/`: `coder`, `editor`, `researcher`, `planner`. Each has a `config.yaml` and skills manifest.
- **Auth** — `X-ExeDev-Email` header required for all endpoints except `/health`.
- **API** — REST with claim/complete/fail/release lifecycle, filterable by status, agent, project, tags.

**Current workflow (manual):**
1. Agent or human calls `GET /api/tasks?status=pending`
2. Agent picks a task and calls `POST /api/tasks/{id}/claim` with its name
3. Agent works on the task
4. Agent reports via `POST /api/tasks/{id}/complete` or `/fail`

**Missing:** No automated polling mechanism. No way for agents to know which tasks match their skills. No lease/heartbeat to handle crashes. No webhook push.

---

## 2. Design Decisions

### 2.1 Polling vs Webhooks

**Recommendation: Hybrid approach — primarily polling with optional webhook support.**

| Criterion | Polling | Webhooks | Hybrid |
|---|---|---|---|
| Simplicity | ✅ Simplest to implement | ❌ Requires server-side event system | ✅ Poll as default, webhook as optimization |
| Reliability | ✅ Deterministic, no lost events | ❌ Delivery failures need retry logic | ✅ Polling is fallback-safe |
| Latency | ❌ Bounded by poll interval | ✅ Instant notifications | ✅ Webhooks for low-latency, poll for reliability |
| Server Load | ❌ N requests per interval | ✅ Push on change only | ✅ Tune-able via poll interval |
| Hermes Fit | ✅ Cron jobs are easy to configure | ❌ Would need Flask-SSE or similar | ✅ Naturally maps to Hermes cron + optional hooks |

**Implementation plan:**
- **Default:** Each agent polls the task board on a configurable timer via a Hermes cron job or a lightweight polling loop.
- **Optional webhook extension:** The task board exposes a webhook registration endpoint; the agent registers a callback URL. When a new task matching the agent's profile is created, the board POSTs to the callback. The agent still polls as a backup.

### 2.2 Polling Design

Each agent runs a lightweight **polling daemon** (or cron job) that:

1. Calls `GET /api/tasks?status=pending` with optional tag/project filters
2. Ranks returned tasks by priority, then creation time
3. Checks if any task matches the agent's declared skills
4. Claims the best-matching task via `POST /api/tasks/{id}/claim`
5. Executes the task
6. Reports completion via `POST /api/tasks/{id}/complete`

**Polling interval:** Configurable per agent, default 15 seconds. Backoff strategy: if no tasks found for N consecutive polls, increase interval up to a maximum (e.g., 60 seconds).

**Configuration in Hermes config.yaml:**

```yaml
# Section to add to each agent's config.yaml
task_board:
  url: http://localhost:8893
  auth_email: cassie@omg.lol
  poll_interval: 15          # seconds between polls
  max_poll_interval: 60      # max backoff when idle
  backoff_factor: 2          # multiplier on consecutive empty polls
  backoff_threshold: 3       # empty polls before backing off
  tags: []                   # preferred task tags (empty = accept any)
  projects: []               # preferred projects (empty = accept any)
  skills_match: true         # only claim tasks matching agent skills
  heartbeat_interval: 30     # seconds between heartbeats
  lease_duration: 300        # seconds before lease expires (5 min)
```

### 2.3 Skill-Based Task Matching

Each Hermes profile has a skills manifest at `~/.hermes/profiles/<name>/skills/.bundled_manifest` mapping skill names to hashes. Tasks can declare required skills in their `tags` field using a convention like `skill:coding`, `skill:research`, etc.

**Matching algorithm:**

1. Agent polls pending tasks with `tag={skill_prefix}` filter if skills are configured
2. For each returned task, score by:
   - **Skill match:** Task tags contain a skill the agent has (+3)
   - **Priority:** Higher priority = higher score (1=urgent: +5, 2=high: +4, ..., 5=low: +1)
   - **Project match:** Task project matches agent's preferred projects (+2)
   - **Age:** Older tasks get a slight bonus (+0.1 per minute waiting, cap +2)
3. Claim the highest-scoring task (with random tiebreak)

**Tag convention for skills:**

Tasks declare required skills in comma-separated `tags`:
```
tags: "backend,api,skill:coding"
tags: "documentation,skill:editing"
tags: "research,architecture,skill:research"
tags: "planning,management,skill:planning"
```

**Server-side enhancement (future):** Add a `skills` field to the Agent model and auto-filter tasks during polling.

### 2.4 Heartbeat & Lease System

To handle agent crashes, we need a **lease** mechanism that releases orphaned tasks.

**Current states:** `pending` → `claimed` → `completed` / `failed` / `released`

**Proposed extension with leases:**

```
pending → claimed (with heartbeat) → completed
                                  → failed
                                  → auto-released (lease expired)
                                  → escalated (another agent claims)
```

**Heartbeat mechanism:**

1. When agent claims a task, a `lease_expires_at` timestamp is set (current time + `lease_duration`)
2. Agent sends periodic heartbeats: `POST /api/tasks/{id}/heartbeat`
3. Server extends `lease_expires_at` by `lease_duration` on each heartbeat
4. A **lease reaper** background job (in the task board server) runs every 30 seconds:
   - Finds all claimed tasks where `lease_expires_at < now()`
   - Releases them back to `pending` status
   - Increments a `lease_breaker_count` or logs the agent for potential crash

**New API endpoints needed:**

```python
@api_bp.route('/tasks/<int:task_id>/heartbeat', methods=['POST'])
def heartbeat_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json()
    agent_name = data.get('agent') if data else None
    
    if task.status != 'claimed':
        return jsonify({'error': f'Task is {task.status}, cannot heartbeat'}), 409
    if agent_name and task.agent != agent_name:
        return jsonify({'error': 'Wrong agent for this task'}), 403
    
    task.lease_expires_at = datetime.now(timezone.utc) + lease_duration
    db.session.commit()
    return jsonify({'status': 'ok', 'lease_expires_at': task.lease_expires_at.isoformat()})
```

**New Task model field:**

```python
lease_expires_at = db.Column(db.DateTime, nullable=True)
lease_heartbeat_count = db.Column(db.Integer, default=0)
```

**Lease reaper (background thread in app):**

```python
def lease_reaper(app, interval=30, lease_duration=300):
    """Background thread that releases expired leases."""
    while True:
        time.sleep(interval)
        with app.app_context():
            expired = Task.query.filter(
                Task.status == 'claimed',
                Task.lease_expires_at < datetime.now(timezone.utc)
            ).all()
            for task in expired:
                task.release()
                log.warning(f"Lease expired for task {task.id}, agent {task.agent}")
            db.session.commit()
```

### 2.5 Crash Recovery

When an agent crashes, three recovery paths exist:

**Path 1: Automatic (Lease Expiry)**
- The lease reaper detects the expired lease and releases the task
- Another agent polls, finds the released task, and claims it
- No human intervention needed

**Path 2: Agent Restart Recovery**
- On restart, the agent queries `GET /api/tasks?agent={name}&status=claimed`
- If it finds claimed-but-not-completed tasks:
  - Sends a heartbeat to refresh the lease
  - Optionally resumes work (if it has state persistence) or re-starts
  - If it can't resume, calls `POST /api/tasks/{id}/fail` with a "crash recovery" message

**Path 3: Escalation for Stale Tasks**
- If a task has been claimed for an unusually long time (e.g., > 3× lease_duration without completion), it's flagged as `stale`
- Another agent can call `POST /api/tasks/{id}/escalate` to take over
- The original agent's stats are penalized (increment `tasks_failed`)

### 2.6 Agent Registration

When an agent starts up, it should register itself:

1. Call `GET /api/agents` to check if it exists
2. If not, the server auto-registers it (add POST `/api/agents` endpoint)
3. Agent updates its status to `idle`
4. On graceful shutdown, update status to `offline`

**New API endpoint:**

```python
@api_bp.route('/agents', methods=['POST'])
def register_agent():
    data = request.get_json()
    if not data or 'name' not in data:
        return jsonify({'error': 'agent name required'}), 400
    
    existing = db.session.get(Agent, data['name'])
    if existing:
        existing.status = data.get('status', existing.status)
        existing.model = data.get('model', existing.model)
    else:
        agent = Agent(
            name=data['name'],
            display_name=data.get('display_name', data['name']),
            model=data.get('model', ''),
            status=data.get('status', 'idle'),
        )
        db.session.add(agent)
    
    db.session.commit()
    return jsonify(agent.to_dict() if existing else agent.to_dict()), 201 if not existing else 200
```

---

## 3. Polling Daemon Implementation (Reference)

### 3.1 Simplified Polling Loop (Python)

```python
#!/usr/bin/env python3
"""hermes-poll.py — Lightweight polling daemon for a single Hermes agent."""

import time
import requests
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hermes-poll")

class TaskBoardPollingDaemon:
    def __init__(self, config):
        self.base_url = config.get("url", "http://localhost:8893")
        self.agent_name = config["agent_name"]
        self.auth_email = config.get("auth_email", "cassie@omg.lol")
        self.headers = {
            "X-ExeDev-Email": self.auth_email,
            "Content-Type": "application/json",
        }
        self.poll_interval = config.get("poll_interval", 15)
        self.max_poll_interval = config.get("max_poll_interval", 60)
        self.backoff_factor = config.get("backoff_factor", 2)
        self.backoff_threshold = config.get("backoff_threshold", 3)
        self.lease_duration = config.get("lease_duration", 300)
        self.heartbeat_interval = config.get("heartbeat_interval", 30)
        self.skills = config.get("skills", [])
        self.projects = config.get("projects", [])
        self.tags = config.get("tags", [])
        self.empty_polls = 0
        self.current_task_id = None
        self.last_heartbeat = 0

    def run(self):
        log.info(f"Starting polling daemon for agent: {self.agent_name}")
        self.register()
        while True:
            try:
                if self.current_task_id:
                    self._heartbeat_loop()
                else:
                    self._poll_for_work()
            except Exception as e:
                log.error(f"Poll error: {e}")
            time.sleep(self._current_interval())

    def register(self):
        resp = requests.post(
            f"{self.base_url}/api/agents",
            headers=self.headers,
            json={"name": self.agent_name, "status": "idle"},
        )
        if resp.ok:
            log.info(f"Registered as agent: {self.agent_name}")

    def _poll_for_work(self):
        params = {"status": "pending"}
        if self.tags:
            params["tag"] = ",".join(self.tags)
        if self.projects:
            params["project"] = ",".join(self.projects)

        resp = requests.get(
            f"{self.base_url}/api/tasks",
            headers=self.headers,
            params=params,
        )
        if not resp.ok:
            return

        tasks = resp.json().get("tasks", [])
        if not tasks:
            self.empty_polls += 1
            return

        self.empty_polls = 0
        best_task = self._best_match(tasks)
        if best_task:
            self._claim_and_execute(best_task)

    def _best_match(self, tasks):
        """Score and pick the best matching task."""
        scored = []
        for task in tasks:
            score = 0
            # Priority score
            score += 6 - task["priority"]  # 1→5, 2→4, 3→3, 4→2, 5→1
            # Skill match (tag contains matching skill)
            task_tags = task.get("tags", "").lower()
            for skill in self.skills:
                if skill.lower() in task_tags:
                    score += 3
            # Project match
            if self.projects and task.get("project") in self.projects:
                score += 2
            # Age bonus
            scored.append((score, task))
        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        return scored[0][1] if scored else None

    def _claim_and_execute(self, task):
        resp = requests.post(
            f"{self.base_url}/api/tasks/{task['id']}/claim",
            headers=self.headers,
            json={"agent": self.agent_name},
        )
        if resp.status_code == 409:
            return  # Someone else claimed it first
        if not resp.ok:
            log.error(f"Claim failed: {resp.text}")
            return

        self.current_task_id = task["id"]
        log.info(f"Claimed task {task['id']}: {task['title']}")

        # Execute the task (delegate to Hermes or external process)
        result = self._execute_task(task)

        if result["success"]:
            requests.post(
                f"{self.base_url}/api/tasks/{task['id']}/complete",
                headers=self.headers,
                json={"result": result["output"]},
            )
        else:
            requests.post(
                f"{self.base_url}/api/tasks/{task['id']}/fail",
                headers=self.headers,
                json={"error": result["error"]},
            )

        self.current_task_id = None

    def _heartbeat_loop(self):
        now = time.time()
        if now - self.last_heartbeat >= self.heartbeat_interval:
            requests.post(
                f"{self.base_url}/api/tasks/{self.current_task_id}/heartbeat",
                headers=self.headers,
                json={"agent": self.agent_name},
            )
            self.last_heartbeat = now

    def _execute_task(self, task):
        """Placeholder — calls the actual Hermes agent logic."""
        log.info(f"Executing task {task['id']}")
        return {"success": True, "output": f"Task {task['id']} completed."}

    def _current_interval(self):
        if self.empty_polls >= self.backoff_threshold:
            backoff = self.poll_interval * (self.backoff_factor ** (
                self.empty_polls - self.backoff_threshold + 1
            ))
            return min(backoff, self.max_poll_interval)
        return self.poll_interval
```

### 3.2 Hermes Cron Job Variant

For agents that don't need a persistent daemon, use a cron-style approach via Hermes's `cron` config:

```yaml
cron:
  tasks:
    - schedule: "*/15 * * * * *"  # every 15 seconds
      command: "hermes-poll --agent coder --one-shot"
```

The `--one-shot` flag polls once, claims a task if available, executes it, and exits. This is simpler but has overhead on each invocation.

---

## 4. Task Board Server-Side Changes Required

### 4.1 Database Migration

Add to `models.py`:

```python
class Task(db.Model):
    # ... existing fields ...
    lease_expires_at = db.Column(db.DateTime, nullable=True)
    lease_heartbeat_count = db.Column(db.Integer, default=0)
```

### 4.2 New API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/agents` | Register or update agent |
| `POST` | `/api/tasks/{id}/heartbeat` | Send heartbeat to extend lease |

### 4.3 Lease Reaper Thread

A background thread in the Flask app that periodically releases expired leases:

```python
import threading
import time

def start_lease_reaper(app):
    def reaper():
        while True:
            time.sleep(30)
            with app.app_context():
                from models import db, Task
                from datetime import datetime, timezone
                expired = Task.query.filter(
                    Task.status == 'claimed',
                    Task.lease_expires_at.isnot(None),
                    Task.lease_expires_at < datetime.now(timezone.utc)
                ).all()
                for task in expired:
                    agent_name = task.agent
                    task.release()
                    app.logger.warning(
                        f"Lease expired for task {task.id} (agent: {agent_name})"
                    )
                db.session.commit()
    thread = threading.Thread(target=reaper, daemon=True)
    thread.start()
```

### 4.4 Skill Profile Endpoint (Optional Future)

```python
@api_bp.route('/tasks/suggest', methods=['GET'])
def suggest_tasks():
    """Suggest tasks matching an agent's skills."""
    agent_name = request.args.get('agent')
    if not agent_name:
        return jsonify({'error': 'agent name required'}), 400
    
    pending = Task.query.filter_by(status='pending').order_by(
        Task.priority, Task.created_at
    ).all()
    
    # Scoring logic here, return top N suggestions without claiming
    suggestions = []
    for task in pending:
        score = 0
        # ... same scoring as polling daemon
        if score > 0:
            suggestions.append((score, task))
    
    suggestions.sort(key=lambda x: -x[0])
    return jsonify({
        'suggestions': [t.to_dict() for _, t in suggestions[:10]]
    })
```

---

## 5. Configuration per Agent Profile

Each agent's `config.yaml` should get a new `task_board` section:

### Coder
```yaml
task_board:
  url: http://localhost:8893
  auth_email: cassie@omg.lol
  poll_interval: 10
  tags: ["skill:coding", "backend", "api", "database"]
  projects: ["task-board", "hermes"]
  skills: ["coding", "backend", "api"]
```

### Editor
```yaml
task_board:
  url: http://localhost:8893
  auth_email: cassie@omg.lol
  poll_interval: 20
  tags: ["skill:editing", "documentation", "review"]
  projects: ["task-board", "hermes"]
  skills: ["editing", "documentation"]
```

### Researcher
```yaml
task_board:
  url: http://localhost:8893
  auth_email: cassie@omg.lol
  poll_interval: 30
  tags: ["skill:research", "architecture", "design"]
  projects: ["task-board", "hermes"]
  skills: ["research", "architecture"]
```

### Planner
```yaml
task_board:
  url: http://localhost:8893
  auth_email: cassie@omg.lol
  poll_interval: 15
  tags: ["skill:planning", "management", "architecture"]
  projects: ["task-board", "hermes"]
  skills: ["planning", "management"]
```

---

## 6. Implementation Roadmap

| Phase | What | Effort | Dependencies |
|-------|------|--------|-------------|
| **Phase 1** | Add `lease_expires_at` and `lease_heartbeat_count` to Task model + migration | Small | None |
| **Phase 2** | Add heartbeat API endpoint (`POST /api/tasks/{id}/heartbeat`) | Small | Phase 1 |
| **Phase 3** | Add agent registration endpoint (`POST /api/agents`) | Small | None |
| **Phase 4** | Implement lease reaper background thread | Small | Phase 1 |
| **Phase 5** | Build polling daemon script (`hermes-poll.py`) | Medium | Phases 1–3 |
| **Phase 6** | Wire polling daemon into Hermes agent lifecycle (cron or persistent) | Medium | Phase 5 |
| **Phase 7** | Add skill-based scoring to the polling daemon | Small | Phase 5 |
| **Phase 8** | Add crash recovery on agent restart | Small | Phase 1, 2 |
| **Phase 9** | Optional: webhook support (Flask-SSE or callback registration) | Large | Phase 5 |

---

## 7. Failure Modes & Mitigations

| Failure Mode | Detection | Mitigation |
|---|---|---|
| Agent crashes mid-task | Heartbeat stops → lease expires | Lease reaper releases task; other agent picks it up |
| Agent hangs (Zombie) | Heartbeats continue but task not progressing | Maximum task duration check; escalate after N× lease_duration |
| Network partition | Heartbeat fails | Agent retries; lease eventually expires if partition persists |
| Two agents claim same task | 409 Conflict on claim | Loser gets 409 and moves to next task |
| Task board down | Connection refused | Agent backs off polling; retries with exponential backoff |
| Stale agent registration | Agent registered as busy but crashed | Lease reaper detects no active tasks; resets agent status |

---

## 8. Summary

**Polling** (not webhooks) is the recommended primary mechanism because it's simpler, more reliable, and maps naturally to Hermes's existing architecture. Each agent runs a lightweight polling loop that:

1. **Registers** itself on startup
2. **Polls** pending tasks on a configurable interval (with backoff when idle)
3. **Scores** tasks by skill match, priority, project fit, and age
4. **Claims** the best match
5. **Heartbeats** periodically to maintain the lease
6. **Reports** completion or failure
7. **Recovers** from crashes via lease-based automatic task release

The system is designed as a thin layer on top of the existing API — no framework changes needed. The polling daemon can be deployed as a persistent background process or a cron job, configurable per agent profile.
