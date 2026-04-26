# models.py
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


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

    def claim(self, agent_name):
        self.status = 'claimed'
        self.agent = agent_name
        self.claimed_at = datetime.now(timezone.utc)

    def complete(self, result_text):
        self.status = 'completed'
        self.result = result_text
        self.completed_at = datetime.now(timezone.utc)

    def fail(self, error_text):
        self.status = 'failed'
        self.error = error_text
        self.completed_at = datetime.now(timezone.utc)

    def release(self):
        self.status = 'pending'
        self.agent = None
        self.claimed_at = None

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'status': self.status,
            'priority': self.priority,
            'agent': self.agent,
            'result': self.result,
            'error': self.error,
            'tags': self.tags,
            'project': self.project,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'claimed_at': self.claimed_at.isoformat() if self.claimed_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }


class Agent(db.Model):
    __tablename__ = 'agents'

    name = db.Column(db.String(50), primary_key=True)
    display_name = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(100), default='')
    status = db.Column(db.String(20), default='idle')
    tasks_completed = db.Column(db.Integer, default=0)
    tasks_failed = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            'name': self.name,
            'display_name': self.display_name,
            'model': self.model,
            'status': self.status,
            'tasks_completed': self.tasks_completed,
            'tasks_failed': self.tasks_failed,
        }
