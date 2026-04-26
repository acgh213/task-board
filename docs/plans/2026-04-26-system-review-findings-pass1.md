# Task Board system review — pass 1 findings

## Scope

First sharp pass across live state, creative workflow symptoms, and cron / overseer behavior.

## Live state snapshot

Checked against the live SQLite DB and cron registry on 2026-04-26.

### Tasks
- Overall task statuses:
  - `completed`: 26
  - `failed`: 2
  - `pending`: 2
- Open `vesper-blog` tasks in active-ish states: **none**
- The only pending tasks are test artifacts:
  - task 18 `Triage test`
  - task 19 `Handoff test`

### Creative agents
- `researcher`: status `busy`, active claimed/in-progress tasks `0`, assigned tasks `0`
- `essayist`: status `busy`, active claimed/in-progress tasks `0`, assigned tasks `0`
- `editor-creative`: status `busy`, active claimed/in-progress tasks `0`, assigned tasks `0`
- `reviewer`: status `idle`, active claimed/in-progress tasks `0`, assigned tasks `0`

### Cron / automation
- `task-board-overseer` cron exists
- model/provider: `openai/gpt-5-nano` via `nous`
- current state: **paused**
- paused at: `2026-04-26T12:21:10.640177-04:00`
- cadence if resumed: `every 30m`

## What is actually wrong

### 1) The creative pipeline is not actively processing work right now
This is the first reality check.

There is no live creative queue to progress. The `vesper-blog` project currently has no tasks in `pending`, `assigned`, `claimed`, `in_progress`, `submitted`, `in_review`, `needs_revision`, `triage`, `timed_out`, `released`, `dead`, `needs_human`, `needs_vesper`, or `blocked`.

So part of the feeling that "nothing is proceeding" is simply that nothing is currently queued in that lane.

### 2) The UI is still lying about creative agent availability
This part is real.

`researcher`, `essayist`, and `editor-creative` are all marked `busy` despite having:
- zero claimed/in-progress tasks
- zero assigned tasks

That means the agent layer can visually look jammed even when the task layer is empty.

### 3) Accepted handoffs do not reset the releasing agent to idle
This looks like the main concrete bug behind the stale creative-agent state.

In `api.py`, `accept_handoff()`:
- clears task claim/lease
- logs a `released` event for the handoff source agent
- reassigns the task to the target agent
- **does not update the releasing agent's status to `idle`**

That matches the live evidence exactly:
- task 27 and task 28 moved `researcher -> essayist -> editor-creative -> reviewer`
- each source agent logged a `released` event during handoff
- those source agents remained `busy` afterward

So the system is preserving the theatrical part of the handoff and forgetting the operational part.

### 4) Reviewer state is governed inconsistently
The `review_task()` path updates `rev_agent.last_heartbeat`, but it does not meaningfully set reviewer availability.

So the system currently has two incompatible reviewer models:
- *review as a pure review action* — reviewer heartbeat updates, but no real status lifecycle
- *review as worker-style task ownership* — reviewer can claim/start/submit a task and then get reset indirectly as the "worker"

That is how task 25 ended up as a weird self-owned review artifact. It was not just ugly data; it exposed that the role boundary is mushy.

### 5) Automation is paused, so nothing will move on its own anyway
Even if there were pending creative tasks, the system-wide overseer automation is currently paused.

That matters because `task-board-overseer` is what is supposed to call:
- `/api/overseer/auto-assign`
- `/api/overseer/auto-triage`
- `/api/overseer/check-timeouts`
- `/api/overseer/reclaim-timeouts`

With that cron paused, the board is not self-propelling. It becomes manual unless something else is driving the API.

## Code evidence

### Stale availability bug
- `api.py:2523-2567` — `accept_handoff()` reassigns tasks and logs release, but does not set the previous agent to `idle`
- `api.py:1677-1725` — `discover_agents(min_available=true)` filters strictly on `agent.status == 'idle'`
- `app.py:345-362` — stats/UI surfaces render raw `agent.status`

This combination is the problem:
- handoff leaves source agents marked `busy`
- discovery filters trust that stale flag
- UI displays that stale flag as truth

### Automation authority
- `overseer_cron.py` is explicit: it is the actor responsible for auto-assign, auto-triage, timeout check, and reclaim
- current cron registry confirms the matching scheduled job exists but is paused

## Pass 1 conclusions

1. The creative lane is not currently blocked by an active task deadlock.
2. The creative lane **is** misrepresented by stale agent statuses.
3. Handoff acceptance is the clearest live bug causing that misrepresentation.
4. Reviewer lifecycle semantics are still sloppy.
5. Whole-system automation is currently paused, so "nothing proceeds" is also true at the operational layer.

## Immediate follow-up tasks to create

1. Fix `accept_handoff()` so the releasing agent is reset to `idle` when it no longer owns active work.
2. Add tests covering agent status cleanup after accepted handoffs.
3. Audit reviewer-state semantics so review actions and worker ownership are not mixed into nonsense.
4. Decide whether `task-board-overseer` should be resumed now, resumed later, or replaced with a more controlled slow-roll trigger.
5. Make agent availability in UI derive from live workload/heartbeat reality rather than trusting a stale status string alone.

## Straight answer

The creative editor/reviewer lane is not blocked by a hidden active task right now.

What is broken is subtler and more annoying:
- the board shows creative agents as operationally stuck because handoff cleanup is incomplete
- the overseer automation that would move future queued work is paused
- there is no currently open creative work item for the system to advance anyway
