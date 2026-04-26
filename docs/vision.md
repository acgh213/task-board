# Task Board Vision

This file is the *vision / roadmap* document, not the source of truth for live operational state.

If `README.md` says what the system is today, this file says where it is trying to go, what already landed, and which parts are still more ambition than fact.

---

## The point

Build a small-agent coordination system that is:

- observable instead of mystical
- review-gated instead of trust-me-bro autonomous
- stylish enough to feel alive
- structured enough to survive real work
- honest about where automation stops and human judgment begins

The goal is not “remove the human.” The goal is “stop making the human do tedious orchestration by hand.”

---

## Current baseline

The current codebase already has:

- role-based agents with editable metadata
- task assignment, claiming, start, submit, review, release, escalation, and timeout flows
- agent-to-agent handoffs with audit history
- XP, levels, badges, streaks, and reputation tracking
- dashboard telemetry, leaderboard, and activity feed
- task audit/timeline surfaces
- triage endpoints and auto-triage behavior
- template/workflow support
- poll daemon + overseer cron path for automation

It is not starting from zero. A lot of the foundational plumbing exists.

---

## Operational caveat

Vision docs are where systems go to become delusional if nobody slaps them.

So, plainly:

- “Supported by the codebase” is **not** the same as “currently enabled in production.”
- Overseer behavior exists, but whether the cron job is running is deployment-specific.
- Agent state can drift if lifecycle cleanup is wrong; this is exactly why auditability matters.
- The live system should be judged by code *and* runtime behavior, not only by what the plan once promised.

---

## Agent roles

These are functional roles, not theology and not substrate branding.

| Role | Purpose |
|------|---------|
| **Vesper** | Strategic oversight, escalation point, human interface, taste, final judgment |
| **Mission Control** | Assignment / routing behavior |
| **Overseer** | Timeout cleanup, reclaim/reassign, safety-net monitoring |
| **Reviewer** | Quality gate before completion |
| **Systems / Coder roles** | Implementation and technical execution |
| **Researcher** | Research and synthesis |
| **Essayist / Writer** | Drafting and content generation |
| **Editor** | Revision, polish, documentation cleanup |
| **Planner** | Architecture and decomposition |
| **QA** | Validation and test-focused work |
| **DevOps** | Infra / deploy / operational work |

Model choices may change. Role semantics matter more than whichever model slug happened to be fashionable for ten minutes.

---

## Lifecycle model

```text
pending → assigned → claimed → in_progress → submitted → in_review → completed
            │            │            │             ├── request_changes → needs_revision
            │            │            │             └── reject → failed
            │            │            └── timeout / release paths
            │            └── release
            └── triage / reassignment / escalation side paths
```

Additional states in play:

- `triage`
- `blocked`
- `needs_human`
- `needs_vesper`
- `timed_out`
- `released`
- `dead`

---

## What is already done

### Phase 1 — Core task board ✅
- Expanded lifecycle states
- Review endpoint and review records
- `assigned_to` separated from `claimed_by`
- agent skill metadata
- dashboard support for broader workflow states

### Phase 2 — Assignment layer ✅
- skill/tag-based assignment behavior
- pending-task discovery by agent
- audit logging for assignment decisions

### Phase 3 — Overseer foundation ✅
- timeout detection
- reclaim/reassign behavior
- escalation path for exhausted tasks
- cron-runner path via `overseer_cron.py`

### Phase 4 — Review pipeline ✅
- submit → review flow
- approve / reject / request-changes outcomes
- no-self-review rule
- retry path with feedback context

### Phase 5 — Agent progression ✅
- XP and level calculation
- badges
- streaks
- reputation-aware selection logic

### Phase 6 — Polling / autonomy scaffolding ✅
- `poll_daemon.py`
- heartbeat support
- lease expiry handling
- idle backoff

### Phase 7 — Dashboard v2 ✅
- live updates via Socket.IO
- richer agent cards
- timeline / audit visibility
- activity feed
- telemetry panels
- leaderboard
- command palette

---

## What is partly true, but should be treated carefully

These are implemented enough to mention, but not polished enough to brag without qualifiers:

### Triage
The system supports triage and auto-triage, but tasks do not automatically start there unless explicitly requested or escalated.

### Full autonomy
The repo supports autonomous behavior *if* the pollers and overseer are actually running. Left idle, it is a capable board, not a self-animating organism.

### Agent status reliability
Agent status is useful, but only if lifecycle transitions keep it honest. Stale “busy” states are exactly the kind of drift this system has to guard against.

---

## Next meaningful work

### 1) Runtime truthfulness
Keep tightening the gap between UI story and live reality.

That means:
- status cleanup stays correct after handoffs, releases, and reviews
- dashboard language reflects actual automation state
- docs stop speaking in permanent-present fantasy tense

### 2) Workflow ergonomics
- better dependency visualization
- clearer multi-step template spawning UX
- cleaner separation between assignment, ownership, and reviewer roles
- more legible completed-work presentation without losing history

### 3) Governance and recovery
- stronger stale-state repair tools
- deliberate cron/automation control surfaces
- clearer “what is paused / what is live” indicators
- better review around escalation and dead-task handling

### 4) Documentation honesty
- separate live truth from roadmap
- keep model/provider names out of places where they will immediately rot
- stop hardcoding counts unless they are intentionally maintained

---

## Longer-range ideas

These are still vision, not promises.

### Dependencies and workflow graphs
- richer dependency graph UI
- better unblock propagation
- more explicit gate / fan-out / fan-in patterns

### Agent communication maturity
- stronger A2A-style card semantics
- structured handoff payloads and schemas everywhere
- better discovery and compatibility matching

### Review sophistication
- specialized reviewer roles
- less muddled reviewer ownership semantics
- clearer post-review routing

### Dashboard refinement
- stronger time-window controls
- better historical telemetry views
- improved mobile ergonomics
- more explicit “automation live vs paused” status

---

## End-to-end ideal

In the best version of this system:

1. Cassie or Vesper creates work.
2. The board decides whether it should go to triage, assignment, or escalation.
3. The right agent picks it up.
4. Work can hand off cleanly when another role should take over.
5. Review is real, not ceremonial.
6. Failures are logged and recoverable.
7. The dashboard tells the truth about what is happening.
8. Humans step in for judgment, not for janitorial clicking.

That is the whole game.

---

## Final stance

A good agent system is not one that *sounds* autonomous.

It is one where:
- the workflow is inspectable,
- the failure modes are survivable,
- the review path has teeth,
- and the docs do not flirt with bullshit.

That is the vision worth keeping.
