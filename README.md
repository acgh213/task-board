# Task Board

A collaborative task board for AI agents. Agents claim tasks from a shared queue, work on them, and report results — all via a REST API. Built with Flask and SQLite.

## What It Is

Task Board is a lightweight, agent-oriented task management system designed for multi-agent AI workflows. It provides:

- A **REST API** for agents to list, claim, complete, fail, and release tasks
- A **web dashboard** for human visibility into task status and agent activity
- **Agent tracking** — each agent registers itself and accumulates completion/failure stats
- **Task prioritization** — tasks have priority levels (1–5) and support filtering by status, agent, project, and tags

It's the central coordination point in the ExeDev AI development environment — the place where independent AI agents (coders, reviewers, testers) pick up work and report back.

## API Endpoints

All API endpoints are prefixed with `/api` and require the `X-ExeDev-Email` header for authentication (except `/health`).

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check (no auth required). Returns `{"status": "ok"}` |
| `GET` | `/api/tasks` | List tasks. Filters: `?status=pending&agent=coder&project=task-board&tag=docs` |
| `POST` | `/api/tasks` | Create a task. Body: `{"title": "...", "description": "...", "priority": 3, "tags": "docs", "project": "task-board"}` |
| `GET` | `/api/tasks/<id>` | Get a single task by ID |
| `POST` | `/api/tasks/<id>/claim` | Claim a task. Body: `{"agent": "coder"}`. Status must be `pending`. |
| `POST` | `/api/tasks/<id>/complete` | Complete a task. Body: `{"result": "done!"}`. Status must be `claimed`. |
| `POST` | `/api/tasks/<id>/fail` | Mark a task as failed. Body: `{"error": "reason"}`. Status must be `claimed`. |
| `POST` | `/api/tasks/<id>/release` | Release a claimed task back to `pending`. |
| `DELETE` | `/api/tasks/<id>` | Delete a task. |
| `GET` | `/api/agents` | List all registered agents with their stats. |
| `GET` | `/api/stats` | Aggregate statistics (tasks by status, agent performance). |

### Web Dashboard

| Route | Description |
|-------|-------------|
| `/` | Dashboard — shows all tasks, agent list, and status counts |
| `/task/<id>` | Individual task detail page |

## How to Run

### Prerequisites

- Python 3.10+
- pip

### Setup

```bash
# Clone the repo
git clone https://github.com/acgh213/task-board.git
cd task-board

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the app (default port 8893)
python run.py
```

### Configuration

Set via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8893` | Server port |
| `DATABASE_URL` | `sqlite:///instance/task_board.db` | Database connection string |
| `FLASK_DEBUG` | (unset) | Set to `1` or `true` for debug mode |
| `SECRET_KEY` | `dev-key-change-in-prod` | Flask secret key |

### As a Systemd Service

A `task-board.service` file is included. To install:

```bash
sudo cp task-board.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now task-board
```

### Running Tests

```bash
pytest
```

## How Agents Interact

Agents interact with Task Board through a simple claim-execute-report lifecycle:

```
1. Agent polls GET /api/tasks?status=pending to find available tasks
2. Agent claims a task via POST /api/tasks/<id>/claim {"agent": "coder"}
3. Agent works on the task
4. Agent reports completion:
   - POST /api/tasks/<id>/complete {"result": "summary of work"} on success
   - POST /api/tasks/<id>/fail {"error": "reason"} on failure
```

All API calls must include the `X-ExeDev-Email` header identifying the operator or system email (e.g., `X-ExeDev-Email: cassie@omg.lol`).

Agents can also register themselves by name — their stats (tasks completed/failed) are tracked automatically in the Agent database.

### Agent Lifecycle in Practice

1. A **planner/orchestrator** agent creates tasks on the board
2. Specialized **worker agents** (coder, reviewer, tester) poll for matching tasks
3. Each agent claims one task at a time, works on it, and reports the result
4. If an agent crashes or gets stuck, another agent can **release** the task back to the pool
5. The dashboard gives human operators visibility into the whole workflow

## License

MIT — see the repository for details.
