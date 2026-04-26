# SQLite Queue Patterns for Agent Task Boards

## Research Context

This document evaluates which SQLite-backed queue patterns should be added to the
task board to make it production-ready for multi-agent orchestration. The current
implementation (models.py + api.py) uses a simple status-based approach with
`pending → claimed → completed/failed` state transitions.

## 1. Current Architecture

### What We Have

| Feature | Status |
|---|---|
| Status-based lifecycle (`pending`/`claimed`/`completed`/`failed`) | ✅ |
| Priority ordering | ✅ (integer 1-5) |
| Agent assignment / release | ✅ |
| Basic retry via `release` endpoint | ✅ (manual only) |
| Authentication via `X-ExeDev-Email` header | ✅ |

### Key Limitation

The current `claim` action is a **simple status check**:

```python
if task.status != 'pending':
    return jsonify({'error': f'Task is {task.status}, cannot claim'}), 409

task.claim(data['agent'])
db.session.commit()
```

If the claiming agent crashes or gets stuck, the task is **permanently stuck** in
`claimed` status. There is no automatic redelivery, no timeout, and no way to
detect that an agent has silently died. This is the fundamental gap.

---

## 2. Pattern: Visibility Timeouts

Also known as **"lease-based claiming"** or **"heartbeat timeout"**. This is the
Amazon SQS model.

### How It Works

Instead of a binary `status = 'claimed'`, each claimed task gets a **timeout
timestamp** in the future. A task is available for claiming only if:

1. Its status is `pending`, OR
2. Its status is `claimed` but its timeout has **expired** (the original claimer
   didn't complete or extend in time)

### SQL Pattern (from goqite / sqliteq)

The canonical implementation uses a single atomic `UPDATE ... WHERE` to claim:

```sql
-- Schema uses 'timeout' field instead of 'status' column
CREATE TABLE tasks (
  id       TEXT PRIMARY KEY,
  created  TEXT NOT NULL,
  queue    TEXT NOT NULL,
  body     TEXT NOT NULL,
  timeout  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ')),
  received INTEGER NOT NULL DEFAULT 0,
  priority INTEGER NOT NULL DEFAULT 0
);

-- Atomic claim: no advisory locks needed
UPDATE tasks
SET timeout = ?, received = received + 1
WHERE id = (
  SELECT id FROM tasks
  WHERE queue = ? AND ? >= timeout AND received < ?
  ORDER BY priority DESC, created
  LIMIT 1
)
RETURNING id, body, received;
```

Key points:
- No separate `status` column — the `timeout` field encodes availability.
- `WHERE ? >= timeout` means "visible if current time is past the timeout".
- `received < ?` enforces a max-receive count for dead-lettering.
- `RETURNING` gives the consumer the message with its current `received` count.
- SQLite's single-writer guarantee makes this race-condition-free.

### Adaptation for Our Schema

We can keep our existing `status` column for readability and add a `timeout`
column. The visibility timeout check becomes:

```python
# When listing available tasks for claiming:
available = Task.query.filter(
    (Task.status == 'pending') |
    ((Task.status == 'claimed') & (Task.timeout < datetime.now(timezone.utc)))
).order_by(Task.priority, Task.created_at).first()
```

### Why We Need This

Without a visibility timeout, a single agent crash permanently blocks a task.
With auto-redelivery after timeout, the system is **self-healing**.

---

## 3. Pattern: Fencing Tokens

A **fencing token** is a monotonically increasing number (or token) that the
storage layer uses to reject stale operations from a consumer that was paused
or disconnected.

### How It Works (from Martin Kleppmann)

In distributed locking, a fencing token prevents the following scenario:
1. Agent A claims task (gets token = 5).
2. Agent A starts processing but gets GC-paused for 30 seconds.
3. Visibility timeout expires. Agent B claims the same task (gets token = 6).
4. Agent A resumes and tries to complete the task.
5. The storage layer checks: `token 5 < last_seen_token 6` — **rejected**.

### Implementation in goqite/sqliteq

Both libraries use a `received` counter (number of times delivered) as a simple
fencing token. When you `delete(id, received)`, the SQL is:

```sql
DELETE FROM tasks WHERE id = ? AND received = ?
```

If the message was redelivered (received incremented), the `DELETE` affects 0
rows and the stale consumer's operation is safely ignored.

### Adaptation for Our Schema

Add a `received_count` column to the Task model. The `complete`, `fail`, and
`release` operations check that the count hasn't changed since the task was
claimed:

```python
def complete(self, result_text, expected_received):
    if self.received_count != expected_received:
        return False  # Stale handle — task was redelivered to another agent
    self.status = 'completed'
    self.result = result_text
    self.completed_at = datetime.now(timezone.utc)
    return True
```

### Why We Need This

Fencing tokens prevent the **double-complete** or **stale-complete** problem.
Without them, an agent that was paused after its timeout expired could
accidentally overwrite another agent's work. This is critical for correctness.

---

## 4. Pattern: Heartbeat Leases

A **heartbeat lease** (or **keepalive**) allows a long-running agent to
periodically extend its visibility timeout, signaling "I'm still alive, don't
redeliver this task."

### How It Works

1. Agent claims task with an initial timeout (e.g., 60 seconds).
2. Agent processes work and periodically calls `extend(id, received, duration)`.
3. If the agent crashes, heartbeats stop, the timeout expires, and the task is
   **automatically** redelivered to another agent.
4. If a stale agent tries to extend, the fencing token check fails.

### SQL Pattern

```sql
-- Extend only if this consumer still holds the lease
UPDATE tasks
SET timeout = ?
WHERE id = ? AND received = ? AND timeout > ?
-- Returns affected rows: 1 = success, 0 = stale handle
```

### API Design

```python
@api_bp.route('/tasks/<int:task_id>/extend', methods=['POST'])
def extend_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json()
    duration = data.get('duration', 60)  # seconds

    if task.received_count != data.get('received', 0):
        return jsonify({'error': 'Stale handle'}), 409

    task.timeout = datetime.now(timezone.utc) + timedelta(seconds=duration)
    db.session.commit()
    return jsonify({'extended_until': task.timeout.isoformat()})
```

### Why We Need This

Agent tasks like code generation, research, or file processing may take minutes,
not seconds. Without heartbeat leases, you must set a very long visibility
timeout (defeating crash detection) or risk interrupting legitimate work.

---

## 5. Pattern: Dead-Letter Queues

When a task has been retried too many times (exhausted `max_receive_count`), it
is moved to a **dead-letter queue** (DLQ) for manual inspection.

### How It Works

- Each claim increments `received_count`.
- When `received_count >= max_receive_count` (default: 3), the task is no longer
  returned by `receive()` queries.
- A separate endpoint/UI shows dead letters for admin review.
- Dead letters can be **requeued** (reset as fresh) or **purged**.

### SQL Pattern

```sql
-- Dead-letter query (tasks that can never be claimed again)
SELECT * FROM tasks WHERE received >= 3;

-- Requeue: reset received count and timeout
UPDATE tasks SET received = 0, timeout = now() WHERE received >= 3;
```

### Why We Need This

Without DLQ, a permanently failing task is retried forever (wasting resources)
or gets stuck in a failed state with no visibility. DLQ provides a **safety net**
that breaks the retry loop and requires human intervention.

---

## 6. Comparison: goqite vs sqliteq vs Our Current Code

| Feature | goqite (Go) | sqliteq (TS) | Our code |
|---|---|---|---|
| **Visibility timeout** | `timeout` column, atomic claim | `timeout` column, atomic claim | ❌ None — stuck forever on crash |
| **Fencing tokens** | `received` count on delete | `received` count on delete/extension | ❌ No stale-handle detection |
| **Heartbeat / extend** | `Extend(id, duration)` API | `extend(id, received, delay)` | ❌ No extend endpoint |
| **Dead-letter queue** | Implicit via `maxReceive` filter | Explicit `deadLetters()` API | ❌ Tasks stay in `failed` permanently |
| **Priority** | Via `priority` column | Via `priority` column DESC | ✅ Via `priority` integer |
| **Delayed messages** | Via `not_after` column | Via `delay` option on send | ❌ No delayed scheduling |
| **Batch operations** | ❌ Not supported | `sendBatch` / `receiveBatch` | ❌ Single-task only |
| **Single-table multi-queue** | ✅ `queue` column | ✅ `queue` column | ✅ Via `project` filter |
| **Stats** | ❌ Not built-in | `stats()` method | ✅ Basic stats endpoint |
| **Auth** | ❌ Bring your own | ❌ Bring your own | ✅ X-ExeDev-Email header |

---

## 7. Performance Considerations

SQLite is more than adequate for an agent task board:

- **goqite benchmark:** ~18,500 operations/sec (send+receive+delete) on M3 Mac.
- **sqliteq benchmark:** ~20,000 ops/sec on M-series Mac.
- **Our bottleneck:** LLM API calls take seconds — queue operations take
  microseconds. Queue performance is irrelevant compared to agent work.

**Key deployment notes:**
- Enable **WAL mode** (`PRAGMA journal_mode=WAL`) for concurrent reads.
- Set **`PRAGMA busy_timeout=5000`** so writers wait instead of failing.
- SQLite is single-writer — this is fine for a task board since contention is
  low and agents work in parallel on different tasks.

---

## 8. Recommendations

### Must-Have (Phase 1)

These three patterns form the **minimum viable production queue**:

1.  **Visibility Timeouts**
    - Add `timeout` column (nullable DateTime) to the Task model.
    - Modify claim to set `timeout = now + default_timeout`.
    - Modify the work-claiming query to include tasks where
      `timeout < now()` alongside `status = 'pending'`.
    - Default timeout: **300 seconds** (5 min), configurable per queue/project.

2.  **Fencing Tokens**
    - Add `received_count` column (Integer, default=0) to the Task model.
    - Increment on each claim.
    - Pass `received_count` in claim response. Require it in
      `complete`, `fail`, `release`, and `extend` calls.
    - Reject operations where the count doesn't match (409 Conflict).

3.  **Dead-Letter Queue**
    - Add `max_receive_count` constant (default: 3 claims per task).
    - When `received_count >= max_receive_count`, exclude from claim queries.
    - Add a `GET /api/tasks?status=dead` endpoint for admin review.
    - Add `POST /api/tasks/<id>/requeue` to reset a dead letter.

### Nice-to-Have (Phase 2)

4.  **Heartbeat / Extend API**
    - Add `POST /api/tasks/<id>/extend` endpoint.
    - Accept `{ "received": N, "duration": 120 }`.
    - Validate fencing token before extending.

5.  **Delayed Tasks**
    - Allow a `send_after` / `delay` option on task creation.
    - Exclude from claim queries until `send_after` is past.

### Not Recommended Now

6.  **Multi-queue table** (shared table with `queue` column per goqite/sqliteq)
    — not needed since our `project` field already partitions tasks and
    SQLAlchemy doesn't benefit from a shared-table design the way raw SQL does.

7.  **Batch operations** — not needed for agent task boards where each task
    is independently significant and LLM-bound.

---

## 9. Implementation Sketch (models.py changes)

```python
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

    # NEW COLUMNS for production queue:
    timeout = db.Column(db.DateTime, nullable=True)
    received_count = db.Column(db.Integer, default=0)

    MAX_RECEIVE_COUNT = 3
    DEFAULT_TIMEOUT_SECONDS = 300

    def claim(self, agent_name):
        self.status = 'claimed'
        self.agent = agent_name
        self.claimed_at = datetime.now(timezone.utc)
        self.timeout = datetime.now(timezone.utc) + \
            timedelta(seconds=self.DEFAULT_TIMEOUT_SECONDS)
        self.received_count = (self.received_count or 0) + 1

    def complete(self, result_text, expected_received):
        if self.received_count != expected_received:
            return False  # Stale handle
        self.status = 'completed'
        self.result = result_text
        self.completed_at = datetime.now(timezone.utc)
        return True

    def extend_timeout(self, expected_received, duration_seconds):
        if self.received_count != expected_received:
            return False
        self.timeout = datetime.now(timezone.utc) + \
            timedelta(seconds=duration_seconds)
        return True

    @property
    def is_dead(self):
        return (self.received_count or 0) >= self.MAX_RECEIVE_COUNT

    @classmethod
    def next_available(cls):
        """Find next claimable task (pending or timed-out claimed)."""
        now = datetime.now(timezone.utc)
        return cls.query.filter(
            cls.is_dead == False,
            (cls.status == 'pending') |
            ((cls.status == 'claimed') & (cls.timeout < now))
        ).order_by(cls.priority, cls.created_at).first()
```

---

## 10. Summary of Recommendations

| Pattern | Priority | Why |
|---|---|---|
| Visibility Timeouts | **P0** | Prevents stuck tasks on agent crash |
| Fencing Tokens | **P0** | Prevents stale/duplicate completion |
| Dead-Letter Queue | **P0** | Prevents infinite retry loops |
| Heartbeat / Extend | **P1** | Supports long-running agent tasks |
| Delayed Messages | **P2** | Useful but not critical for MVP |

The combined effect: a **self-healing, crash-safe** task board where agents can
claim, process, heartbeat, and complete work without coordination, and failures
are automatically detected and recovered.

---

## Sources

- [goqite](https://github.com/maragudk/goqite) — Go queue library on SQLite
  inspired by AWS SQS (MIT, 516★)
- [sqliteq](https://github.com/minnzen/sqliteq) — TypeScript queue on SQLite
  with atomic claim, visibility timeout, fencing (MIT)
- [Building a Durable Message Queue on SQLite for AI Agent Orchestration](https://dev.to/minnzen/building-a-durable-message-queue-on-sqlite-for-ai-agent-orchestration-335m)
- [How to Do Distributed Locking](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html)
  — Martin Kleppmann on fencing tokens
- [Amazon SQS Visibility Timeout](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html)
- [You Really Don't Need Anything Fancy for SQL Queues](https://news.ycombinator.com/item?id=27482402)
  — Hacker News discussion on SQL as a queue
