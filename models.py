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
    'triage':        frozenset(['pending', 'assigned', 'needs_human', 'needs_vesper', 'blocked', 'failed']),
    'pending':       frozenset(['assigned', 'blocked', 'needs_human', 'needs_vesper', 'triage']),
    'assigned':      frozenset(['claimed', 'blocked', 'needs_human', 'needs_vesper', 'pending', 'completed']),
    'claimed':       frozenset(['in_progress', 'released', 'timed_out', 'blocked',
                                'needs_human', 'needs_vesper']),
    'in_progress':   frozenset(['submitted', 'released', 'timed_out', 'blocked',
                                'needs_human', 'needs_vesper', 'failed']),
    'submitted':     frozenset(['in_review', 'needs_human', 'needs_vesper', 'blocked']),
    'in_review':     frozenset(['completed', 'failed', 'needs_revision',
                                'needs_human', 'needs_vesper', 'blocked']),
    # assigned → completed is valid for reviewer handoffs (reviewer reviews directly)
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

    # Complexity (1-5 scale, 1=simple, 5=complex)
    complexity = db.Column(db.Integer, default=3)

    # Dependencies
    blocked_by = db.Column(db.String(500), default='', nullable=True)

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

    def get_blocking_task_ids(self):
        """Return list of task IDs that block this task."""
        if not self.blocked_by:
            return []
        return [int(x.strip()) for x in self.blocked_by.split(',') if x.strip()]

    def get_blocking_tasks(self):
        """Return the list of Task objects that are blocking this one."""
        ids = self.get_blocking_task_ids()
        if not ids:
            return []
        return Task.query.filter(Task.id.in_(ids)).all()

    def are_dependencies_met(self):
        """Return True if all blocking tasks are completed."""
        blocking = self.get_blocking_tasks()
        if not blocking:
            return True
        return all(t.status == 'completed' for t in blocking)

    def get_dependent_task_ids(self):
        """Return list of task IDs that depend on (are blocked by) this task."""
        from sqlalchemy import text
        if self.id is None:
            return []
        pattern = f'%{self.id}%'
        rows = db.session.execute(
            text('SELECT id FROM tasks WHERE blocked_by LIKE :pat'),
            {'pat': pattern}
        ).fetchall()
        result = []
        for (tid,) in rows:
            if tid == self.id:
                continue
            task = db.session.get(Task, tid)
            if task and self.id in task.get_blocking_task_ids():
                result.append(tid)
        return result

    def get_dependent_tasks(self):
        """Return list of Task objects that depend on this one."""
        ids = self.get_dependent_task_ids()
        if not ids:
            return []
        return Task.query.filter(Task.id.in_(ids)).all()

    @staticmethod
    def validate_blocked_by(value, task_id=None):
        """Validate a blocked_by string and check for circular dependencies.
        Returns (cleaned_string, error_message)."""
        if not value:
            return '', None
        ids = [int(x.strip()) for x in value.split(',') if x.strip()]
        if not ids:
            return '', None

        # Check that all referenced task IDs exist
        existing = {t.id for t in Task.query.filter(Task.id.in_(ids)).all()}
        missing = [str(i) for i in ids if i not in existing]
        if missing:
            return None, f'Referenced tasks not found: {", ".join(missing)}'

        # Check for circular dependencies
        if task_id is not None:
            for bid in ids:
                if bid == task_id:
                    return None, f'Task cannot block itself'
                blocker = db.session.get(Task, bid)
                if blocker:
                    blocker_deps = blocker.get_blocking_task_ids()
                    if task_id in blocker_deps:
                        return None, f'Circular dependency detected: task {bid} is blocked by task {task_id}'

        return ','.join(str(i) for i in ids), None

    def to_dict(self):
        def _agent_display(name):
            if not name:
                return None
            agent = db.session.get(Agent, name)
            return agent.display_name if agent else name

        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'status': self.status,
            'priority': self.priority,
            'complexity': self.complexity,
            'project': self.project,
            'tags': self.tags,
            'reserved_for': self.reserved_for,
            'reserved_for_display': _agent_display(self.reserved_for),
            'assigned_to': self.assigned_to,
            'assigned_to_display': _agent_display(self.assigned_to),
            'claimed_by': self.claimed_by,
            'claimed_by_display': _agent_display(self.claimed_by),
            'lease_expires_at': self.lease_expires_at.isoformat() if self.lease_expires_at else None,
            'heartbeat_at': self.heartbeat_at.isoformat() if self.heartbeat_at else None,
            'last_seen': self.last_seen.isoformat() if self.last_seen else None,
            'attempts': self.attempts,
            'max_attempts': self.max_attempts,
            'timed_out_count': self.timed_out_count,
            'last_error': self.last_error,
            'failure_reason': self.failure_reason,
            'escalation_rules': self.escalation_rules,
            'blocked_by': self.blocked_by or '',
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
# HandoffRequest Model
# ──────────────────────────────────────────────
class HandoffRequest(db.Model):
    __tablename__ = 'handoff_requests'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    from_agent = db.Column(db.String(50), nullable=False)
    to_agent = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, default='')
    status = db.Column(db.String(20), default='pending')  # pending | accepted | rejected
    created_at = db.Column(db.DateTime, default=utcnow)

    task = db.relationship('Task', backref=db.backref('handoff_requests', lazy=True))

    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'from_agent': self.from_agent,
            'to_agent': self.to_agent,
            'message': self.message,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ──────────────────────────────────────────────
# TaskTemplate Model
# ──────────────────────────────────────────────
class TaskTemplate(db.Model):
    __tablename__ = 'task_templates'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text, default='')
    steps = db.Column(db.Text, default='[]')  # JSON array of step definitions
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'steps': json.loads(self.steps) if self.steps else [],
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def get_steps(self):
        """Parse and return the steps JSON."""
        return json.loads(self.steps) if self.steps else []


# ──────────────────────────────────────────────
# Achievement / Badge Models (Task #10)
# ──────────────────────────────────────────────
class Achievement(db.Model):
    __tablename__ = 'achievements'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(500), default='')
    icon = db.Column(db.String(10), default='🎖️')
    criteria = db.Column(db.Text, default='{}')  # JSON
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'icon': self.icon,
            'criteria': json.loads(self.criteria) if self.criteria else {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class AgentBadge(db.Model):
    __tablename__ = 'agent_badges'

    id = db.Column(db.Integer, primary_key=True)
    agent_name = db.Column(db.String(50), db.ForeignKey('agents.name'), nullable=False)
    badge_id = db.Column(db.Integer, db.ForeignKey('achievements.id'), nullable=False)
    earned_at = db.Column(db.DateTime, default=utcnow)

    agent = db.relationship('Agent', backref=db.backref('badges', lazy='dynamic'))
    achievement = db.relationship('Achievement', backref=db.backref('awarded_badges', lazy='dynamic'))

    __table_args__ = (
        db.UniqueConstraint('agent_name', 'badge_id', name='uq_agent_badge'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'agent_name': self.agent_name,
            'badge_id': self.badge_id,
            'badge_name': self.achievement.name if self.achievement else None,
            'badge_icon': self.achievement.icon if self.achievement else None,
            'badge_description': self.achievement.description if self.achievement else None,
            'earned_at': self.earned_at.isoformat() if self.earned_at else None,
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
    avg_completion_time = db.Column(db.Float, default=0.0)  # seconds

    # XP & Leveling (Task #9)
    xp = db.Column(db.Integer, default=0)
    level = db.Column(db.Integer, default=1)
    streak = db.Column(db.Integer, default=0)
    last_active_date = db.Column(db.Date, nullable=True)

    # Status
    status = db.Column(db.String(20), default='idle')  # idle | busy | offline
    last_heartbeat = db.Column(db.DateTime, nullable=True)

    XP_LEVEL_TIERS = [
        (0, 1, 'Rookie'),
        (100, 2, 'Operative'),
        (500, 3, 'Specialist'),
        (1500, 4, 'Expert'),
        (5000, 5, 'Commander'),
    ]

    @property
    def level_name(self):
        _, _, name = self._level_info()
        return name

    def _level_info(self):
        """Return (threshold_xp, level_num, level_name) for current XP."""
        result = self.XP_LEVEL_TIERS[0]
        for threshold, lvl, name in self.XP_LEVEL_TIERS:
            if self.xp >= threshold:
                result = (threshold, lvl, name)
        return result

    def compute_level(self):
        """Recalculate level from XP and return it."""
        _, lvl, _ = self._level_info()
        self.level = lvl
        return lvl

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

    @property
    def skills_list(self):
        """Parse comma-separated skills into a list."""
        return [s.strip() for s in self.skills.split(',') if s.strip()]

    @property
    def preferred_projects_list(self):
        """Parse comma-separated preferred_projects into a list."""
        return [p.strip() for p in self.preferred_projects.split(',') if p.strip()]

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
            'avg_completion_time': self.avg_completion_time,
            'xp': self.xp,
            'level': self.level,
            'level_name': self.level_name,
            'streak': self.streak,
            'status': self.status,
            'last_heartbeat': self.last_heartbeat.isoformat() if self.last_heartbeat else None,
        }
