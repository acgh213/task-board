# models.py
import json
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc)


# ──────────────────────────────────────────────
# Escalation tag sets
# ──────────────────────────────────────────────
ESCALATION_TAGS_HUMAN = frozenset({
    'human_review', 'human_approval', 'human_needed',
    'publish', 'deploy', 'push_to_production',
    'payment', 'money', 'billing',
    'public_message', 'announce', 'social',
})

ESCALATION_TAGS_VESPER = frozenset({
    'credentials', 'api_key', 'token',
    'destructive', 'delete', 'drop',
    'external_service', 'webhook', 'public_api',
})


# ──────────────────────────────────────────────
# State machine: valid transitions
# ──────────────────────────────────────────────
STATE_TRANSITIONS = {
    'pending':       frozenset(['assigned', 'blocked', 'needs_human', 'needs_vesper']),
    'assigned':      frozenset(['claimed', 'blocked', 'needs_human', 'needs_vesper', 'pending']),
    'claimed':       frozenset(['in_progress', 'released', 'timed_out', 'blocked',
                                'needs_human', 'needs_vesper']),
    'in_progress':   frozenset(['submitted', 'released', 'timed_out', 'blocked',
                                'needs_human', 'needs_vesper', 'failed']),
    'submitted':     frozenset(['in_review', 'needs_human', 'needs_vesper', 'blocked']),
    'in_review':     frozenset(['completed', 'failed', 'needs_revision',
                                'needs_human', 'needs_vesper', 'blocked']),
    'needs_revision': frozenset(['claimed', 'assigned', 'released', 'timed_out',
                                 'needs_human', 'needs_vesper', 'blocked', 'failed']),
    'completed':     frozenset(),          # terminal
    'failed':        frozenset(['released', 'dead', 'needs_human', 'needs_vesper', 'blocked']),
    'timed_out':     frozenset(['released', 'dead', 'needs_human', 'needs_vesper', 'blocked']),
    'blocked':       frozenset(['pending', 'assigned', 'needs_human', 'needs_vesper']),
    'needs_human':   frozenset(['pending', 'assigned', 'in_review', 'completed', 'blocked']),
    'needs_vesper':  frozenset(['pending', 'assigned', 'in_review', 'completed', 'blocked']),
    'released':      frozenset(['pending', 'assigned', 'dead']),
    'dead':          frozenset(),          # terminal
}

# Statuses that are "locked" — owned by an agent
LOCKED_STATUSES = frozenset(['claimed', 'in_progress', 'submitted', 'in_review', 'needs_revision'])


# ──────────────────────────────────────────────
# Task Model
# ──────────────────────────────────────────────
class Task(db.Model):
    __tablename__ = 'tasks'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    project = db.Column(db.String(100), default='general')
    tags = db.Column(db.String(500), default='')
    reserved_for = db.Column(db.String(50), nullable=True)

    # Lifecycle
    status = db.Column(db.String(30), default='pending', index=True)
    priority = db.Column(db.Integer, default=3)

    # Assignment & Locking
    assigned_to = db.Column(db.String(50), nullable=True, index=True)
    claimed_by = db.Column(db.String(50), nullable=True, index=True)
    lease_expires_at = db.Column(db.DateTime, nullable=True)
    heartbeat_at = db.Column(db.DateTime, nullable=True)
    last_seen = db.Column(db.DateTime, nullable=True)

    # Failure Tracking
    attempts = db.Column(db.Integer, default=0)
    max_attempts = db.Column(db.Integer, default=3)
    timed_out_count = db.Column(db.Integer, default=0)
    last_error = db.Column(db.Text, nullable=True)
    failure_reason = db.Column(db.String(100), nullable=True)

    # Escalation
    escalation_rules = db.Column(db.Text, default='{}')

    # Result
    result = db.Column(db.Text, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=utcnow)
    assigned_at = db.Column(db.DateTime, nullable=True)
    claimed_at = db.Column(db.DateTime, nullable=True)
    in_progress_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    # ── helpers ──────────────────────────────

    def can_transition_to(self, new_status):
        allowed = STATE_TRANSITIONS.get(self.status, frozenset())
        return new_status in allowed

    def is_locked(self):
        return self.status in LOCKED_STATUSES

    def is_terminal(self):
        return self.status in frozenset(['completed', 'dead'])

    def is_claimed_status(self):
        return self.status in frozenset(['claimed', 'in_progress'])

    def lease_expired(self):
        if self.lease_expires_at is None:
            return True
        # SQLite stores datetimes as naive; compare as naive UTC
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expires = self.lease_expires_at
        if expires.tzinfo is not None:
            expires = expires.replace(tzinfo=None)
        return now > expires

    def check_escalation_tags(self):
        """If any of the task's tags match escalation patterns, return target status."""
        if not self.tags:
            return None
        task_tags = {t.strip().lower() for t in self.tags.split(',') if t.strip()}
        for tag in task_tags:
            if tag in ESCALATION_TAGS_HUMAN:
                return 'needs_human'
            if tag in ESCALATION_TAGS_VESPER:
                return 'needs_vesper'
        return None

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'status': self.status,
            'priority': self.priority,
            'project': self.project,
            'tags': self.tags,
            'reserved_for': self.reserved_for,
            'assigned_to': self.assigned_to,
            'claimed_by': self.claimed_by,
            'lease_expires_at': self.lease_expires_at.isoformat() if self.lease_expires_at else None,
            'heartbeat_at': self.heartbeat_at.isoformat() if self.heartbeat_at else None,
            'last_seen': self.last_seen.isoformat() if self.last_seen else None,
            'attempts': self.attempts,
            'max_attempts': self.max_attempts,
            'timed_out_count': self.timed_out_count,
            'last_error': self.last_error,
            'failure_reason': self.failure_reason,
            'escalation_rules': self.escalation_rules,
            'result': self.result,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None,
            'claimed_at': self.claimed_at.isoformat() if self.claimed_at else None,
            'in_progress_at': self.in_progress_at.isoformat() if self.in_progress_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


# ──────────────────────────────────────────────
# Review Model
# ──────────────────────────────────────────────
class Review(db.Model):
    __tablename__ = 'reviews'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    reviewer = db.Column(db.String(50), nullable=False)
    decision = db.Column(db.String(20), nullable=False)  # approve | reject | request_changes
    feedback = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=utcnow)

    task = db.relationship('Task', backref=db.backref('reviews', lazy=True))

    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'reviewer': self.reviewer,
            'decision': self.decision,
            'feedback': self.feedback,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ──────────────────────────────────────────────
# EventLog Model
# ──────────────────────────────────────────────
class EventLog(db.Model):
    __tablename__ = 'event_logs'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=True)
    event_type = db.Column(db.String(50), nullable=False, index=True)
    agent = db.Column(db.String(50), nullable=True)
    details = db.Column(db.Text, default='{}')  # JSON
    created_at = db.Column(db.DateTime, default=utcnow)

    task = db.relationship('Task', backref=db.backref('events', lazy=True))

    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'event_type': self.event_type,
            'agent': self.agent,
            'details': json.loads(self.details) if self.details else {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ──────────────────────────────────────────────
# Agent Model
# ──────────────────────────────────────────────
class Agent(db.Model):
    __tablename__ = 'agents'

    name = db.Column(db.String(50), primary_key=True)
    display_name = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(100), default='')
    role = db.Column(db.String(30), default='worker')  # worker | mission_control | overseer | reviewer

    # Capabilities
    skills = db.Column(db.String(500), default='')
    preferred_projects = db.Column(db.String(500), default='')
    max_concurrent = db.Column(db.Integer, default=3)

    # Reputation (lightweight)
    tasks_completed = db.Column(db.Integer, default=0)
    tasks_failed = db.Column(db.Integer, default=0)
    tasks_review_rejected = db.Column(db.Integer, default=0)
    tasks_timed_out = db.Column(db.Integer, default=0)
    reputation_score = db.Column(db.Float, default=50.0)

    # Status
    status = db.Column(db.String(20), default='idle')  # idle | busy | offline
    last_heartbeat = db.Column(db.DateTime, nullable=True)

    def update_reputation(self):
        """Recalculate reputation score."""
        total = (self.tasks_completed or 0) + (self.tasks_failed or 0) + (self.tasks_timed_out or 0) + (self.tasks_review_rejected or 0)
        if total == 0:
            self.reputation_score = 50.0
        else:
            # Base: completed as ratio, penalized by failures and timeouts
            score = ((self.tasks_completed or 0) / total) * 100
            score -= ((self.tasks_failed or 0) / total) * 20
            score -= ((self.tasks_timed_out or 0) / total) * 15
            score -= ((self.tasks_review_rejected or 0) / total) * 10
            self.reputation_score = max(0.0, min(100.0, score))

    def to_dict(self):
        return {
            'name': self.name,
            'display_name': self.display_name,
            'model': self.model,
            'role': self.role,
            'skills': self.skills,
            'preferred_projects': self.preferred_projects,
            'max_concurrent': self.max_concurrent,
            'tasks_completed': self.tasks_completed,
            'tasks_failed': self.tasks_failed,
            'tasks_review_rejected': self.tasks_review_rejected,
            'tasks_timed_out': self.tasks_timed_out,
            'reputation_score': self.reputation_score,
            'status': self.status,
            'last_heartbeat': self.last_heartbeat.isoformat() if self.last_heartbeat else None,
        }
