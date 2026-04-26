# Task Board Vision — Autonomous Agent Orchestration

## The Goal

A self-running multi-agent system where:
- Work flows in automatically
- Agents get assigned tasks based on skills
- Quality is verified before completion
- Failures are caught and retried automatically
- A human can see everything but doesn't have to manage it

---

## Architecture

### Agents (Roles)

| Role | Purpose | Model |
|------|---------|-------|
| **Vesper** | Strategic oversight, human interface, final decisions | mimo-v2.5 |
| **Mission Control** | Task router — reads incoming work, assigns to right agent | deepseek-v4-flash |
| **Overseer** | Monitors board, catches failures, reassigns stuck tasks | deepseek-v4-flash |
| **Reviewer** | Code/task quality check before marking complete | gpt-5-nano |
| **Coder** | Implementation tasks | deepseek-v4-flash |
| **Editor** | Code review, documentation | gpt-5-nano |
| **Researcher** | Research and analysis | deepseek-v4-flash |
| **Planner** | Architecture and planning | deepseek-v4-flash |
| **Writer** | Content, docs, creative | gpt-5-nano |
| **QA** | Testing, validation | deepseek-v4-flash |
| **DevOps** | Infrastructure, deployment | deepseek-v4-flash |

### Task Lifecycle

```
pending → claimed → in_progress → in_review → completed
                          ↓            ↓
                       failed      needs_human
                          ↓            ↓
                     reassign      vesper_review
                          ↓
                     claimed (retry, different agent)
```

### Statuses

| Status | Meaning |
|--------|---------|
| `pending` | Ready to be assigned |
| `claimed` | Agent has claimed it, work hasn't started |
| `in_progress` | Agent is actively working |
| `in_review` | Work done, awaiting quality check |
| `completed` | Done and verified |
| `failed` | Agent reported failure |
| `needs_human` | Requires human decision |
| `needs_vesper` | Requires Vesper's judgment |
| `dead` | Failed too many times, needs manual intervention |

---

## Phases

### Phase 1: Enhanced Task Board (now)
- [ ] Add `in_review`, `needs_human`, `needs_vesper`, `dead` statuses
- [ ] Add review endpoint: `POST /tasks/<id>/review` (approve/reject with feedback)
- [ ] Add `max_retries` field — task goes to `dead` after N failures
- [ ] Add `assigned_to` field (separate from `claimed_by`) for router assignment
- [ ] Add agent skill tags to Agent model (what tasks they can handle)
- [ ] Update dashboard to show full pipeline

### Phase 2: Mission Control Agent
- [ ] Agent that polls for `pending` tasks
- [ ] Reads task tags/skills, matches to agent capabilities
- [ ] Assigns via `POST /tasks/<id>/assign` (sets `assigned_to`)
- [ ] Can be triggered by webhook or cron
- [ ] Logs assignment decisions for audit

### Phase 3: Overseer Agent
- [ ] Monitors for stuck tasks (claimed but no heartbeat for X minutes)
- [ ] Auto-releases timed-out tasks
- [ ] Reassigns failed tasks to different agents (up to max_retries)
- [ ] Escalates `dead` tasks to Vesper/human
- [ ] Sends summary reports (what succeeded, what failed, why)

### Phase 4: Review Pipeline
- [ ] Completed tasks go to `in_review` automatically
- [ ] Reviewer agent checks: code quality, test coverage, completeness
- [ ] Approve → `completed` | Reject → `failed` with feedback
- [ ] Feedback includes what went wrong, what to fix
- [ ] Agent can retry with feedback context

### Phase 5: Reputation System
- [ ] Track: tasks completed, tasks failed, review pass rate, avg time
- [ ] Calculate reputation score (weighted combination)
- [ ] Router uses rep to prioritize reliable agents for critical work
- [ ] Low-rep agents get simpler tasks until they prove themselves

### Phase 6: Autonomous Polling
- [ ] Each agent profile gets a polling daemon (cron or long-running)
- [ ] Agent checks for assigned tasks, claims them, works, reports
- [ ] Heartbeat system — agent pings during long work
- [ ] Crash detection — no heartbeat = release task

### Phase 7: Dashboard v2
- [ ] Real-time updates via WebSocket
- [ ] Agent status cards (idle, busy, last task, rep score)
- [ ] Task timeline view (full lifecycle with timestamps)
- [ ] Filter by agent, status, project, priority
- [ ] Activity feed (who did what, when)

---

## How It Works End-to-end

1. **Cassie says** "build a CLI tool for searching essays"
2. **Vesper** creates a task: `{title: "Build essay search CLI", tags: ["cli", "python"], project: "vesper-blog"}`
3. **Mission Control** sees the task, reads tags, matches to `coder` (Python skill) + `editor` (docs skill)
4. **Coder** gets assigned, claims it, starts working
5. **Coder** finishes, submits to review: `POST /tasks/42/complete`
6. **Reviewer** checks: tests pass? Code clean? Docs included?
7. If good → `completed`. If bad → `failed` with "missing tests, no README"
8. **Overseer** notices if anything gets stuck, reassigns if needed
9. **Dashboard** shows the whole flow in real-time
10. **Cassie** opens dashboard, sees everything worked, approves the result

---

## The Point

Cassie and Vesper have conversations. Work gets done. The system runs itself.
When something breaks, the overseer catches it. When something needs human judgment, it gets flagged.
The dashboard lets you see everything without having to manage anything.

**Vesper handles strategy. The system handles execution.**
