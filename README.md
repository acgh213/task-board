# Hermes Vesper

A self-hosted task board for coordinating small AI crews.

This repo is not trying to be a grand unified agent platform. It is a Flask app with a real state machine, audit trails, handoffs, reviews, a sci-fi dashboard, and enough operational scar tissue to be interesting.

Built by Cassie Gray, with Vesper doing what she does best: taste, pressure, continuity, and refusing to let the system lie about itself.

---

## What it is

Hermes Vesper sits between a human operator and a set of role-based agents.

You create tasks. The system can triage or auto-assign them. Agents claim work, heartbeat while working, submit results, hand off to each other, and send work into review before it counts as done.

It is meant to make multi-agent work *visible*.
Not magical. Visible.

### What exists today

- 15-state task lifecycle, including review, escalation, timeout, release, and dead-end states
- Role-based agent registry with skills, XP, badges, reputation, streaks, and editable metadata
- Agent-to-agent handoffs with accept/reject flow and audit history
- Review pipeline with `approve`, `reject`, and `request_changes`
- No-self-review enforcement
- Workflow audit endpoint and dedicated audit page
- Dashboard with kanban board, telemetry panels, leaderboard, activity feed, and command palette
- Socket.IO-backed live updates for dashboard views
- Optional triage queue for tasks that should start under human review
- Template/workflow support, including DAG-style template coverage in tests
- Overseer endpoints and cron script for timeout cleanup / reassignment safety-net behavior

### What it is *not*

- Not a guaranteed fully autonomous shop with zero supervision
- Not a polished SaaS product
- Not honest if the docs pretend every optional automation path is always turned on in every deployment

---

## Current operational truth

As of this docs pass, the repo itself supports the following and the test suite collects **361 tests**.

A few important truth-not-marketing notes:

- New tasks do **not** always start in triage. By default they start `pending` unless you set `start_in_triage=true` or the task escalates on creation.
- The Overseer exists as API endpoints plus `overseer_cron.py`. Whether it is actually running is a deployment decision, not something the code can morally promise from inside a README.
- Agent naming has been cleaned up toward **role-based ids**, not branded substrate names.
- The dashboard aesthetic is intentionally space-station melodrama. The operational layer underneath is still plain Flask + SQLite + SQLAlchemy.

---

## Architecture

```text
Browser UI (dashboard, task detail, agent pages, stats)
        │
        ├── Flask routes (`app.py`)
        ├── REST API (`api.py`)
        ├── Socket.IO live updates (`ws.py`)
        │
        └── SQLAlchemy models (`models.py`)
                │
                └── SQLite database (WAL enabled)
```

### Main moving pieces

- **Tasks** — lifecycle state, assignment, claims, heartbeats, reviews, dependencies, attempts
- **Agents** — role identity, display name, skills, status, reputation, XP, level, badges
- **Reviews** — quality gate before completion
- **Handoffs** — structured transfer between agents
- **Event log** — audit trail for the whole mess
- **Templates** — reusable workflow/task generation
- **Overseer** — timeout detection, reclaim/reassign behavior, auto-triage/auto-assign helpers

---

## State machine

```text
pending → assigned → claimed → in_progress → submitted → in_review → completed
            │            │            │             │
            │            │            │             ├── request_changes → needs_revision
            │            │            │             └── reject → failed
            │            │            └── release / timeout paths
            │            └── release
            └── reassignment / triage / escalation side paths
```

Additional states in use:

- `triage`
- `blocked`
- `needs_human`
- `needs_vesper`
- `timed_out`
- `released`
- `dead`

---

## UI surface

The dashboard is not shy about itself. It currently includes:

- dark neon command-center styling
- multi-column kanban board
- telemetry cards for throughput, success rate, average completion time, and agent utilization
- leaderboard and badge display
- recent event feed / ship log
- crew-station agent cards
- `Cmd+K` command palette
- responsive nav with hamburger menu
- dedicated task audit and timeline views

If you want beige enterprise minimalism, this is the wrong ship.

---

## API quick reference

All authenticated endpoints require the `X-ExeDev-Email` header. `/health` is public.

### Tasks
- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/<id>`
- `DELETE /api/tasks/<id>`
- `POST /api/tasks/<id>/assign`
- `POST /api/tasks/<id>/claim`
- `POST /api/tasks/<id>/start`
- `POST /api/tasks/<id>/submit`
- `POST /api/tasks/<id>/heartbeat`
- `POST /api/tasks/<id>/release`
- `POST /api/tasks/<id>/review`
- `POST /api/tasks/<id>/escalate`
- `POST /api/tasks/<id>/resolve`
- `GET /api/tasks/<id>/audit`

### Handoffs
- `POST /api/tasks/<id>/handoff`
- `POST /api/tasks/<id>/handoff/<rid>/accept`
- `POST /api/tasks/<id>/handoff/<rid>/reject`

### Triage / overseer
- `GET /api/tasks/triage`
- `POST /api/tasks/<id>/triage/accept`
- `POST /api/overseer/auto-triage`
- `POST /api/overseer/auto-assign`
- `GET /api/overseer/pending-for-agent/<name>`
- `POST /api/overseer/check-timeouts`
- `POST /api/overseer/reclaim-timeouts`

### Agents / stats
- `GET /api/agents`
- `POST /api/agents`
- `GET /api/agents/discover`
- `GET /api/agents/<name>/xp`
- `POST /api/agents/xp/leaderboard`
- `GET /api/agents/<name>/badges`
- `GET /api/agents/<name>/card`
- `GET /api/stats`
- `GET /api/telemetry`

---

## Running locally

### Prerequisites

- Python 3.11+
- `pip`

### Install

```bash
git clone https://github.com/acgh213/task-board.git
cd task-board
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Run

```bash
python run.py
# default local port: http://localhost:8893
```

### Test

```bash
pytest -q
```

### Optional automation

You can run agent polling and overseer behavior separately if you actually want the system moving on its own:

```bash
python poll_daemon.py --agent coder --interval 15
python overseer_cron.py
```

That is the honest version: autonomy here is composed out of app + agents + automation processes, not summoned by vibes alone.

---

## Repo landmarks

- `app.py` — Flask app and HTML routes
- `api.py` — task/agent/review/handoff/overseer API
- `models.py` — SQLAlchemy models and state definitions
- `templates/` — dashboard and detail pages
- `static/style.css` — the command-center look
- `poll_daemon.py` — agent polling worker
- `overseer_cron.py` — safety-net automation runner
- `tests/` — 361 collected tests
- `docs/vision.md` — roadmap / system intent, now separated from README truth claims

---

## Philosophy

Agent systems should be legible.

If tasks move, you should be able to see why.
If an agent fails, you should be able to inspect the trail.
If a dashboard looks gorgeous but lies about live state, it is decoration, not instrumentation.

This repo is at its best when it behaves like a real operations surface: stylish, yes, but answerable.

---

## Credits

Designed and directed by **Cassie Gray**.

Built with help from Vesper and a rotating cast of agents, clones, reviewers, and other small digital coworkers of varying reliability.

## License

MIT
