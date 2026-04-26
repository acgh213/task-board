# Slow-roll observable rollout for Task Board

> **For Vesper:** Keep rollout intentionally narrow at first. The point is not throughput. The point is legibility.

## Goal
Introduce new Task Board behavior in a way Cassie can actually watch: assignment, claim, start, submit, review, escalation, and cleanup should all be visible as discrete events instead of a blur of auto-completed noise.

## Operating principles
- **One visible experiment at a time** for new workflow surfaces.
- **No fake busyness**: do not flood the board with placeholder tasks just to make it look active.
- **Use real lifecycle transitions**: assigned → claimed → in_progress → submitted → in_review → completed/failed.
- **Cassie is final approver / escalation point**, not a routine middle reviewer.
- **Audit over speed**: every rollout step should produce a file, event trail, or test result worth inspecting.

## Phase 0 — board hygiene before rollout
This phase is already substantially complete and should remain the baseline.

### Required conditions
1. Misleading branded agent identities removed from active runtime surfaces.
2. Stale active tasks cleaned into valid terminal states.
3. Completed tasks rendered in a collapsible archive rather than left to bloat the live board.
4. Friendly display names shown in dashboard and task detail views.

### Verification
- `pytest tests/test_phase7.py tests/test_models.py -q`
- query the live DB for active tasks and confirm only genuinely live work remains

## Phase 1 — single-task observation loop
Use exactly **one** task to observe the full lifecycle.

### Task shape
- project: `task-board`
- tags: `planning,rollout,observability`
- priority: `2`
- assigned to a single specialist, preferably `planner`

### Expected observable sequence
1. Task is **assigned** by mission control.
2. Specialist **claims** it.
3. Specialist moves it to **in_progress**.
4. Specialist **submits** a real artifact.
5. Reviewer moves it through **in_review**.
6. Reviewer either:
   - approves → `completed`
   - requests changes → `needs_revision`
   - rejects → `failed`

### What to inspect
- event log continuity
- agent labeling in task cards
- whether stale empty states reappear correctly
- whether the dashboard still distinguishes active vs done cleanly

## Phase 2 — paired handoff trial
Only after Phase 1 looks clean.

### Add one downstream task
Create a second task that explicitly depends on the first task’s artifact.

### Conditions
- use `blocked_by` or an equivalent dependency mechanism if/when present
- the upstream task must contain a real result payload
- the downstream task should not become active until the upstream artifact is usable

### Goal
Watch one actual handoff without producing the old mess of duplicate active states and vague placeholder reviews.

## Phase 3 — controlled parallelism
Only after the paired handoff behaves.

### Limits
- maximum 2–3 simultaneously active rollout tasks
- no self-review
- no automatic fan-out to every available agent

### Purpose
Verify that the board feels stable under light concurrency rather than only when nearly idle.

## Rollout checklist

### Before creating a new rollout task
- [ ] Is this task real, not decorative?
- [ ] Does it produce an inspectable artifact?
- [ ] Does it need a specialist assignment, or can it stay pending?
- [ ] Will Cassie learn something by watching this one?

### Before completing a rollout task
- [ ] Result field contains real content or a file path
- [ ] Review payload is specific
- [ ] No orphaned downstream task was created accidentally
- [ ] Event log tells a coherent story

## Suggested next rollout tasks
1. **Artifact-backed handoff test**
   - create one planning task and one implementation-followup task
   - verify the followup references a real file, not just summary text
2. **Escalation semantics test**
   - create a task that should route to `needs_human` / final approval
   - confirm Cassie only appears at the end, not as routine glue
3. **Review quality test**
   - confirm reviewer payloads are substantive and not empty bureaucratic fluff

## Git discipline for rollout work
The repo currently has a lot of unrelated dirt. Treat rollout work as a surgical subset.

### Safe pattern
1. Inspect `git status --short`.
2. Diff only the files touched by the rollout cleanup.
3. Stage explicit files; do **not** use `git add .` here.
4. Commit rollout work separately from broader repo cleanup.
5. Only then decide whether to normalize the rest of the repo.

### Files likely belonging to this rollout slice
- `app.py`
- `models.py`
- `templates/dashboard.html`
- `templates/tasks.html`
- `templates/task.html`
- `static/style.css`
- `tests/test_phase7.py`
- `docs/plans/2026-04-26-creative-review-loop-spec.md`
- `docs/plans/2026-04-26-slow-roll-observable-rollout.md`

## Definition of success
The board should show a small number of real tasks moving clearly through the workflow, with clean labels, a compact done archive, and no zombie active state hanging around pretending to be work.

If it starts looking busy but unreadable again, the rollout failed, even if technically more tasks got done.