# config.py
import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')

# Escalation tags — tasks with these auto-escalate to needs_human
ESCALATION_TAGS = frozenset([
    'publish', 'deploy', 'push_to_production',
    'destructive', 'delete', 'drop',
    'credentials', 'api_key', 'token',
    'payment', 'money', 'billing',
    'external_service', 'webhook', 'public_api',
    'public_message', 'announce', 'social',
    'human_review', 'human_approval', 'human_needed',
])

# All valid status values
VALID_STATUSES = frozenset([
    'pending', 'assigned', 'claimed', 'in_progress', 'submitted',
    'in_review', 'completed', 'failed', 'blocked', 'needs_human',
    'needs_vesper', 'needs_revision', 'timed_out', 'released', 'dead',
])

# Terminal statuses — no further transitions expected
TERMINAL_STATUSES = frozenset(['completed', 'dead'])

# Statuses where an agent "owns" the task (for heartbeat/lease tracking)
CLAIMED_STATUSES = frozenset(['claimed', 'in_progress'])

# Statuses from which an agent can voluntarily release
RELEASABLE_STATUSES = frozenset(['claimed', 'in_progress', 'needs_revision'])


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-change-in-prod')
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'sqlite:////home/exedev/task-board/taskboard.db'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    LEASE_DURATION = timedelta(minutes=5)
    HEARTBEAT_TIMEOUT = timedelta(minutes=2)


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    LEASE_DURATION = timedelta(minutes=5)
    HEARTBEAT_TIMEOUT = timedelta(minutes=2)
