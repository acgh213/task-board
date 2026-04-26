# Task Board

An autonomous task orchestration system for AI agents. Agents get assigned work based on skills, claim it, execute it, and report back — all governed by a state machine with review pipelines, timeout recovery, and full audit logging.

## What It Does

Task Board is a self-managing coordination layer for multi-agent AI workflows. Instead of manually dispatching work, you create tasks and let the system route them to the right agents automatically.

**Core capabilities:**
- **Skill-based auto-assignment** — tasks route to agents based on tag/skill matching, priority, and reputation
- **15-state lifecycle** — pending → assigned → claimed → in_progress → submitted → in_review → completed, with side paths for failures, escalations, and timeouts
- **Review pipeline** — work doesn't count as done until a reviewer approves it
- **Lease/heartbeat system** — agents hold tasks with time-limited leases; stale work gets reclaimed
- **Event logging** — every action creates an audit trail
- **Agent reputation** — tracks completions, failures, timeouts, and review rejects
- **Task templates** — pre-defined workflows (feature-build, bug-fix, documentation) with variable substitution
- **Escalation rules** — tasks with dangerous tags (deploy, credentials, payment) auto-escalate to human review

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Task Board                       │
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │  Tasks   │  │  Agents  │  │ Reviews  │       │
│  │ (15 states)│ │ (skills) │  │ (feedback)│      │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘       │
│       │              │              │              │
│  ┌────┴──────────────┴──────────────┴────┐       │
│  │           State Machine                │       │
│  │  claim → start → submit → review       │       │
│  │  timeout → reclaim → reassign          │       │
│  │  escalate → human → resolve            │       │
│  └────────────────────────────────────────┘       │
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Event Log│  │Templates │  │Overseer  │       │
│  │ (audit)  │  │ (workflows)│ │(cron 2m) │       │
│  └──────────┘  └──────────┘  └──────────┘       │
└─────────────────────────────────────────────────┘
         ↑                ↑
    ┌────┴────┐     ┌────┴────┐
    │  Coder  │     │Researcher│
    │ Editor  │     │ Planner  │
    │ Writer  │     │   QA     │
    │ DevOps  │     │Reviewer  │
    └─────────┘     └──────────┘
```

## API Reference

All endpoints require `X-ExeDev-Email` header (except `/health`).

### Tasks

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tasks` | List tasks. Filters: `?status=&agent=&project=&tag=&page=&per_page=` |
| `POST` | `/api/tasks` | Create task. Body: `{title, description, priority, tags, project, reserved_for}` |
| `GET` | `/api/tasks/<id>` | Get task details |
| `DELETE` | `/api/tasks/<id>` | Delete task |

### Lifecycle

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tasks/<id>/assign` | Mission Control assigns agent |
| `POST` | `/api/tasks/<id>/claim` | Agent claims task |
| `POST` | `/api/tasks/<id>/start` | Agent starts work |
| `POST` | `/api/tasks/<id>/submit` | Agent submits result |
| `POST` | `/api/tasks/<id>/heartbeat` | Extend lease |
| `POST` | `/api/tasks/<id>/release` | Release claim |

### Review

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tasks/<id>/review` | `{reviewer, decision: approve/reject/request_changes, feedback}` |
| `POST` | `/api/tasks/<id>/escalate` | `{target: needs_human/needs_vesper, reason}` |
| `POST` | `/api/tasks/<id>/resolve` | `{decision: approve/reject/reassign/release}` |

### Overseer

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/overseer/auto-assign` | Scan pending tasks, match to agents, assign |
| `GET` | `/api/overseer/pending-for-agent/<name>` | Tasks matching agent's skills |
| `POST` | `/api/overseer/reclaim-timeouts` | Check timed-out tasks |
| `POST` | `/api/overseer/check-timeouts` | Transition expired leases to timed_out |
| `GET` | `/api/overseer/dashboard` | Summary stats |

### Templates

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/templates` | List available templates |
| `POST` | `/api/templates/<name>/create` | Create tasks from template. Body: `{variables: {topic: "..."}}` |

### Agents & Events

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/agents` | List agents with stats |
| `POST` | `/api/agents` | Register/update agent |
| `GET` | `/api/tasks/<id>/events` | Task event history |
| `GET` | `/api/events` | Global event log |
| `GET` | `/api/stats` | Aggregate statistics |

## Task Lifecycle

```
pending ──→ assigned ──→ claimed ──→ in_progress ──→ submitted ──→ in_review ──→ completed
                                    │                  │              │
                                    ↓                  ↓              ↓
                                 released          needs_revision  failed
                                    │                  │              │
                                    ↓                  ↓              ↓
                                 pending            claimed        released/dead
```

**Side paths:** `blocked`, `needs_human`, `needs_vesper`, `timed_out`, `dead`

## Running Locally

```bash
git clone https://github.com/acgh213/task-board.git
cd task-board
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python run.py  # Port 8893
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8893` | Server port |
| `DATABASE_URL` | `sqlite:///instance/task_board.db` | Database URL |
| `FLASK_DEBUG` | (unset) | Enable debug mode |
| `SECRET_KEY` | `dev-key-change-in-prod` | Flask secret key |

### Systemd

```bash
sudo cp task-board.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now task-board
```

### Tests

```bash
pytest  # 141 tests
```

## Task Templates

Create multi-step workflows from templates:

```bash
# Create a feature-build workflow
curl -X POST http://localhost:8893/api/templates/feature-build/create \
  -H "Content-Type: application/json" \
  -d '{"variables": {"topic": "user authentication"}}'
# Creates 6 tasks: research → plan → implement → test → review → document
```

**Available templates:**
- `feature-build` — 6 steps: research, plan, implement, test, review, document
- `bug-fix` — 4 steps: investigate, write failing test, fix, verify
- `documentation` — 3 steps: research, write, review

## Dashboard

The web dashboard at `/` shows:
- **Kanban board** with all 15 task statuses
- **Agent cards** with skills, reputation, and current load
- **Recent events** feed
- **Filter controls** by status, agent, and project
- **Auto-refresh** every 30 seconds

Individual pages:
- `/task/<id>` — task timeline, reviews, events
- `/agent/<name>` — agent history, reputation, active tasks

## License

MIT
