# Phase 1 Specification — Enhanced Task Board

## Overview

Transform the task board from a simple queue into a governed multi-agent orchestration system with explicit lifecycle, locking, review, reputation, and audit logging.

---

## Database Schema Changes

### Task Model (updated)

```python
class Task(db.Model):
    # Identity
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    project = db.Column(db.String(100), default='general')
    tags = db.Column(db.String(500), default='')  # required skills/tags
    reserved_for = db.Column(db.String(50), nullable=True)  # specific agent type
    
    # Lifecycle
    status = db.Column(db.String(30), default='pending', index=True)
    # pending → assigned → claimed → in_progress → submitted → in_review → completed
    # Side paths: blocked, failed, needs_human, needs_vesper, needs_revision, timed_out, released
    priority = db.Column(db.Integer, default=3)
    
    # Assignment & Locking
    assigned_to = db.Column(db.String(50), nullable=True, index=True)  # Mission Control assigns
    claimed_by = db.Column(db.String(50), nullable=True, index=True)  # Agent claims
    lease_expires_at = db.Column(db.DateTime, nullable=True)
    heartbeat_at = db.Column(db.DateTime, nullable=True)
    last_seen = db.Column(db.DateTime, nullable=True)
    
    # Failure Tracking
    attempts = db.Column(db.Integer, default=0)
    max_attempts = db.Column(db.Integer, default=3)
    last_error = db.Column(db.Text, nullable=True)
    failure_reason = db.Column(db.String(100), nullable=True)  # categorized: timeout, code_error, missing_deps, etc.
    
    # Escalation
    escalation_rules = db.Column(db.Text, default='{}')  # JSON: auto-escalate conditions
    
    # Result
    result = db.Column(db.Text, nullable=True)  # agent's output/deliverable
    submitted_at = db.Column(db.DateTime, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=utcnow)
    assigned_at = db.Column(db.DateTime, nullable=True)
    claimed_at = db.Column(db.DateTime, nullable=True)
    in_progress_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
```

### Review Model (new)

```python
class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    reviewer = db.Column(db.String(50), nullable=False)  # agent name or "human" or "vesper"
    decision = db.Column(db.String(20), nullable=False)  # approve | reject | request_changes
    feedback = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=utcnow)
    
    task = db.relationship('Task', backref=db.backref('reviews', lazy=True))
```

### EventLog Model (new)

```python
class EventLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=True)
    event_type = db.Column(db.String(50), nullable=False, index=True)
    # task_created, assigned, claimed, released, in_progress, submitted, 
    # reviewed, completed, failed, timed_out, escalated, reassigned
    agent = db.Column(db.String(50), nullable=True)
    details = db.Column(db.Text, default='{}')  # JSON
    created_at = db.Column(db.DateTime, default=utcnow)
    
    task = db.relationship('Task', backref=db.backref('events', lazy=True))
```

### Agent Model (updated)

```python
class Agent(db.Model):
    name = db.Column(db.String(50), primary_key=True)
    display_name = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(100), default='')
    role = db.Column(db.String(30), default='worker')  # worker | mission_control | overseer | reviewer
    
    # Capabilities
    skills = db.Column(db.String(500), default='')  # comma-separated: python,flask,research,docs
    max_concurrent = db.Column(db.Integer, default=3)
    
    # Reputation (lightweight)
    tasks_completed = db.Column(db.Integer, default=0)
    tasks_failed = db.Column(db.Integer, default=0)
    tasks_review_rejected = db.Column(db.Integer, default=0)
    tasks_timed_out = db.Column(db.Integer, default=0)
    reputation_score = db.Column(db.Float, default=50.0)  # 0-100, starts at 50
    
    # Status
    status = db.Column(db.String(20), default='idle')  # idle | busy | offline
    last_heartbeat = db.Column(db.DateTime, nullable=True)
```

---

## API Changes

### New Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/tasks/<id>/assign` | Mission Control assigns agent |
| `POST` | `/api/tasks/<id>/claim` | Agent claims (only if assigned to them or unassigned) |
| `POST` | `/api/tasks/<id>/start` | Agent starts work (claimed → in_progress) |
| `POST` | `/api/tasks/<id>/submit` | Agent submits work (in_progress → submitted) |
| `POST` | `/api/tasks/<id>/heartbeat` | Agent sends heartbeat |
| `POST` | `/api/tasks/<id>/review` | Reviewer approves/rejects/request_changes |
| `POST` | `/api/tasks/<id>/escalate` | Agent requests human/vesper review |
| `POST` | `/api/tasks/<id>/release` | Agent releases claim |
| `GET`  | `/api/tasks/<id>/events` | Get event history for task |
| `GET`  | `/api/events` | Global event log with filters |
| `GET`  | `/api/agents/<name>` | Agent detail with reputation |
| `POST` | `/api/agents` | Register/update agent |
| `POST` | `/api/tasks/<id>/requeue` | Overseer requeues a failed/stuck task |

### State Machine Rules

```
pending → assigned (Mission Control)
assigned → claimed (assigned agent only)
claimed → in_progress (claimed agent)
in_progress → submitted (claimed agent, with result)
submitted → in_review (auto)
in_review → completed (reviewer approve)
in_review → needs_revision (reviewer request_changes)
in_review → failed (reviewer reject)
needs_revision → claimed (same agent, with feedback)
needs_revision → assigned (reassigned if agent failed)

Any claimed state → timed_out (Overseer, if heartbeat expired)
Any claimed state → released (agent voluntarily)
Any state → needs_human (agent or Overseer escalation)
Any state → needs_vesper (agent or Overseer escalation)
Any state → blocked (dependency or external blocker)

failed → released (Overseer requeues if attempts < max_attempts)
failed → dead (Overseer escalates if attempts >= max_attempts)
timed_out → released (Overseer requeues if attempts < max_attempts)
```

### Claim Locking Rules

1. Agent can only claim if: `status == 'assigned' AND (assigned_to == agent_name OR assigned_to IS NULL)`
2. Claim sets: `claimed_by = agent_name`, `claimed_at = now`, `lease_expires_at = now + LEASE_DURATION`
3. While claimed, no other agent can claim (409 Conflict)
4. Lease expires → task becomes claimable again (Overseer or auto-check)
5. Agent can release voluntarily → task goes back to pending/assigned
6. Heartbeat extends lease: `lease_expires_at = now + LEASE_DURATION`

### Review Rules

1. Task must be in `submitted` status to review
2. Reviewer must not be the same agent who worked on the task (no self-review)
3. Approve → `completed`, update agent reputation (+)
4. Request changes → `needs_revision`, includes feedback, agent can retry
5. Reject → `failed`, includes reason, agent reputation (-)
6. Review is logged as event

### Escalation Rules (auto-escalate to needs_human/needs_vesper)

Tasks with these tags automatically escalate:
- `publish`, `deploy`, `push_to_production`
- `destructive`, `delete`, `drop`
- `credentials`, `api_key`, `token`
- `payment`, `money`, `billing`
- `external_service`, `webhook`, `public_api`
- `public_message`, `announce`, `social`

---

## Test Plan

### Model Tests
- Task state transitions (all valid paths)
- Task state rejection (invalid transitions)
- Lease expiry detection
- Heartbeat extends lease
- Attempt counting
- Review creation and relationship
- EventLog creation and querying
- Agent reputation calculation

### API Tests
- Full lifecycle: create → assign → claim → start → submit → review → complete
- Assignment locking (can't claim if not assigned to you)
- Claim locking (can't double-claim)
- Lease expiry and re-claim
- Review: approve, reject, request_changes
- Escalation: auto-escalate on dangerous tags
- Event logging: every action creates event
- Failure flow: fail → requeue → retry
- Max attempts → dead
- Heartbeat extends lease
- Agent registration with skills

### Integration Tests
- Complete workflow with review
- Failure and retry with different agent
- Overseer detects timeout and reassigns
- Escalation rules trigger correctly

---

## Implementation Order

1. Update models (Task, Agent, new Review, new EventLog)
2. Update config (LEASE_DURATION, etc.)
3. Rewrite API with state machine
4. Add event logging to every endpoint
5. Add review endpoints
6. Add heartbeat endpoint
7. Add escalation rules
8. Write all tests
9. Run tests, fix issues
10. Update dashboard templates
11. Commit and push
12. Have reviewer agent check quality
