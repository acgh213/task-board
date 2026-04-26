# Phase 2 Specification — Mission Control Auto-Assignment

## Overview

Add automatic task routing based on agent skills, a polling daemon for agents, and a cron-triggered overseer that keeps the pipeline moving.

---

## New API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/overseer/auto-assign` | Scan pending tasks, match to agents, assign |
| `GET` | `/overseer/pending-for-agent/<name>` | Get tasks matching this agent's skills |
| `POST` | `/overseer/reclaim-timeouts` | Check timeouts + auto-requeue if under max_attempts |
| `GET` | `/overseer/dashboard` | Summary: tasks by status, agent load, recent events |

## Skill Matching Algorithm

When auto-assigning:

1. Get all `pending` tasks
2. Get all agents with `status != 'offline'`
3. For each task, score each agent:
   - **Skill match:** Task tags overlap with agent skills (+3 per match)
   - **Priority:** P1=+5, P2=+4, P3=+3, P4=+2, P5=+1
   - **Project match:** Task project in agent's preferred projects (+2)
   - **Availability:** Agent has capacity (active tasks < max_concurrent) (+2)
   - **Reputation:** Agent reputation_score / 20 (+0-5)
4. Assign task to highest-scoring agent
5. If no agent matches (score 0), leave pending

## Agent Poll Script

A standalone Python script (`poll_daemon.py`) that agents run:

```bash
# Usage
python poll_daemon.py --agent coder --interval 15
```

Behavior:
1. Fetch pending tasks matching agent's skills via `/overseer/pending-for-agent/<name>`
2. If tasks available, claim the best one
3. While working, send heartbeat every 30s
4. On completion, POST result
5. On failure, POST error
6. If idle for 3+ polls, back off to 60s interval

## Cron Jobs

| Job | Interval | What it does |
|-----|----------|--------------|
| `auto-assign` | Every 2 min | POST /overseer/auto-assign |
| `check-timeouts` | Every 1 min | POST /overseer/check-timeouts |
| `reclaim-timeouts` | Every 5 min | POST /overseer/reclaim-timeouts |

## Tests

### Auto-assign tests
- Pending task gets assigned to matching agent
- Task with no matching agent stays pending
- Busy agent (at max_concurrent) is skipped
- Priority affects assignment order
- reserved_for tasks only go to matching agent type
- Escalation tags still trigger on auto-assigned tasks

### Poll daemon tests (integration)
- Daemon claims a pending task
- Daemon sends heartbeats
- Daemon handles failure gracefully

### Reclaim tests
- Timed-out task under max_attempts → released
- Timed-out task at max_attempts → dead
- Reclaimed task can be claimed by different agent

### Dashboard tests
- Returns correct counts
- Returns agent load info
