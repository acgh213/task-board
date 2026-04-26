# api.py
import json
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from models import db, Task, Agent, Review, EventLog, STATE_TRANSITIONS
from models import ESCALATION_TAGS_HUMAN, ESCALATION_TAGS_VESPER
from config import Config

api_bp = Blueprint('api', __name__)


# ── helpers ─────────────────────────────────

def _get_email():
    """Extract the authenticated email from header."""
    return request.headers.get('X-ExeDev-Email', 'anonymous')


def _log_event(task_id, event_type, agent=None, details=None):
    """Create an EventLog entry."""
    log = EventLog(
        task_id=task_id,
        event_type=event_type,
        agent=agent,  # None for human-triggered events
        details=json.dumps(details or {}),
    )
    db.session.add(log)
    return log


def _get_agent_or_create(name, display_name=None, model=None):
    """Get an existing agent or create a minimal one."""
    agent = db.session.get(Agent, name)
    if agent is None:
        agent = Agent(
            name=name,
            display_name=display_name or name,
            model=model or '',
        )
        db.session.add(agent)
    return agent


def _validate_transition(task, new_status):
    """Validate that a state transition is allowed. Returns (ok, error_msg)."""
    if task.status == new_status:
        return False, f'Task is already in status {new_status}'
    if task.is_terminal():
        return False, f'Task is in terminal status {task.status}, no transitions allowed'
    if task.can_transition_to(new_status):
        return True, None
    return False, f'Cannot transition from {task.status} to {new_status}'


def _check_escalation_tags(task):
    """Auto-escalate if task tags match dangerous patterns. Delegates to model method."""
    return task.check_escalation_tags()


# ── Task CRUD ───────────────────────────────

@api_bp.route('/tasks', methods=['GET'])
def list_tasks():
    query = Task.query
    status = request.args.get('status')
    agent = request.args.get('agent')
    project = request.args.get('project')
    tag = request.args.get('tag')
    assigned_to = request.args.get('assigned_to')
    claimed_by = request.args.get('claimed_by')

    if status:
        query = query.filter_by(status=status)
    if agent:
        query = query.filter(
            (Task.claimed_by == agent) | (Task.assigned_to == agent)
        )
    if assigned_to:
        query = query.filter_by(assigned_to=assigned_to)
    if claimed_by:
        query = query.filter_by(claimed_by=claimed_by)
    if project:
        query = query.filter_by(project=project)
    if tag:
        query = query.filter(Task.tags.contains(tag))

    total = query.count()

    try:
        page = int(request.args.get('page', 1))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = int(request.args.get('per_page', 50))
    except (ValueError, TypeError):
        per_page = 50

    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 50

    query = query.order_by(Task.priority, Task.created_at)
    tasks = query.offset((page - 1) * per_page).limit(per_page).all()
    return jsonify({
        'tasks': [t.to_dict() for t in tasks],
        'total': total,
        'page': page,
        'per_page': per_page,
    })


@api_bp.route('/tasks', methods=['POST'])
def create_task():
    data = request.get_json()
    if not data or 'title' not in data:
        return jsonify({'error': 'title is required'}), 400

    title = data['title'].strip()
    if not title:
        return jsonify({'error': 'title cannot be empty'}), 400

    priority = data.get('priority', 3)
    if not isinstance(priority, int) or priority < 1 or priority > 5:
        return jsonify({'error': 'priority must be integer 1-5'}), 400

    task = Task(
        title=title,
        description=data.get('description', ''),
        priority=priority,
        tags=data.get('tags', ''),
        project=data.get('project', 'general'),
        reserved_for=data.get('reserved_for', None),
    )

    # Check escalation tags on creation
    escalate_to = _check_escalation_tags(task)
    if escalate_to:
        task.status = escalate_to

    db.session.add(task)
    db.session.flush()

    _log_event(task.id, 'task_created', details={
        'title': title,
        'tags': task.tags,
        'status': task.status,
    })

    if escalate_to:
        _log_event(task.id, 'escalated', details={
            'from_status': 'pending',
            'to_status': escalate_to,
            'reason': f'Task tags triggered escalation: {task.tags}',
        })

    db.session.commit()
    return jsonify(task.to_dict()), 201


@api_bp.route('/tasks/<int:task_id>', methods=['GET'])
def get_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = task.to_dict()
    data['reviews'] = [r.to_dict() for r in task.reviews]
    data['events'] = [e.to_dict() for e in task.events]
    return jsonify(data)


@api_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    return jsonify({'deleted': task_id})


# ── State Machine Endpoints ────────────────

@api_bp.route('/tasks/<int:task_id>/assign', methods=['POST'])
def assign_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json() or {}
    agent_name = data.get('agent')

    if not agent_name:
        return jsonify({'error': 'agent name required'}), 400

    ok, err = _validate_transition(task, 'assigned')
    if not ok:
        return jsonify({'error': err}), 409

    _get_agent_or_create(agent_name)

    now = datetime.now(timezone.utc)
    task.status = 'assigned'
    task.assigned_to = agent_name
    task.assigned_at = now
    task.updated_at = now

    _log_event(task.id, 'assigned', agent=agent_name, details={
        'assigned_to': agent_name,
    })

    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/claim', methods=['POST'])
def claim_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json() or {}
    agent_name = data.get('agent')

    if not agent_name:
        return jsonify({'error': 'agent name required'}), 400

    now = datetime.now(timezone.utc)

    # Lease expiry check FIRST — if someone else holds it but lease expired, void their claim
    if task.claimed_by and task.claimed_by != agent_name:
        if not task.lease_expired():
            return jsonify({
                'error': f'Task is already claimed by {task.claimed_by} and lease has not expired'
            }), 409
        else:
            # Lease expired — void the old claim, log it
            old_claimant = task.claimed_by
            task.claimed_by = None
            task.assigned_to = None
            _log_event(task.id, 'lease_expired', details={
                'old_claimant': old_claimant,
                'reason': 'lease expired, task released for re-claim'
            })

    # Now validate state transition
    ok, err = _validate_transition(task, 'claimed')
    if not ok:
        return jsonify({'error': err}), 409

    # Locking rule: only assigned agent can claim if assigned, else any agent
    if task.assigned_to and task.assigned_to != agent_name:
        return jsonify({
            'error': f'Task is assigned to {task.assigned_to}, not {agent_name}'
        }), 409

    _get_agent_or_create(agent_name)

    task.status = 'claimed'
    task.claimed_by = agent_name
    task.claimed_at = now
    task.lease_expires_at = now + Config.LEASE_DURATION
    task.heartbeat_at = now
    task.last_seen = now
    task.updated_at = now

    _log_event(task.id, 'claimed', agent=agent_name, details={
        'claimed_by': agent_name,
        'lease_expires_at': task.lease_expires_at.isoformat() if task.lease_expires_at else None,
    })

    # Update agent status
    agent = db.session.get(Agent, agent_name)
    if agent:
        agent.status = 'busy'
        agent.last_heartbeat = now

    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/start', methods=['POST'])
def start_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json() or {}
    agent_name = data.get('agent')

    if not agent_name:
        return jsonify({"error": "agent name required"}), 400
    ok, err = _validate_transition(task, 'in_progress')
    if not ok:
        return jsonify({'error': err}), 409

    if task.claimed_by and task.claimed_by != agent_name:
        return jsonify({'error': f'Task is claimed by {task.claimed_by}, not {agent_name}'}), 409

    now = datetime.now(timezone.utc)
    task.status = 'in_progress'
    task.in_progress_at = now
    task.heartbeat_at = now
    task.last_seen = now
    task.updated_at = now

    _log_event(task.id, 'in_progress', agent=agent_name)

    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/submit', methods=['POST'])
def submit_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json() or {}
    agent_name = data.get('agent')

    if not agent_name:
        return jsonify({"error": "agent name required"}), 400
    ok, err = _validate_transition(task, 'submitted')
    if not ok:
        return jsonify({'error': err}), 409

    # Check agent ownership
    if task.claimed_by and task.claimed_by != agent_name:
        return jsonify({'error': f'Task is claimed by {task.claimed_by}, not {agent_name}'}), 409

    result_text = data.get('result')
    if not result_text:
        return jsonify({'error': 'result is required'}), 400

    now = datetime.now(timezone.utc)
    task.result = result_text
    task.submitted_at = now
    task.heartbeat_at = now
    task.last_seen = now
    task.updated_at = now

    _log_event(task.id, 'submitted', agent=agent_name, details={
        'result_length': len(result_text),
    })

    # Determine next status: auto-transition to in_review or escalate
    escalate_to = _check_escalation_tags(task)
    if escalate_to:
        task.status = escalate_to
        _log_event(task.id, 'escalated', agent=agent_name, details={
            'from_status': 'submitted',
            'to_status': escalate_to,
            'reason': f'Submission triggered escalation: {task.tags}',
        })
    else:
        task.status = 'in_review'
        _log_event(task.id, 'in_review', details={
            'note': 'Auto-transitioned to in_review after submission',
        })

    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/review', methods=['POST'])
def review_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json() or {}
    reviewer = data.get('reviewer', _get_email())
    decision = data.get('decision')
    feedback = data.get('feedback', '')

    if not decision or decision not in ('approve', 'reject', 'request_changes'):
        return jsonify({'error': 'decision must be approve, reject, or request_changes'}), 400

    # Must be in_review
    if task.status != 'in_review':
        return jsonify({'error': f'Task is in status {task.status}, must be in_review to review'}), 409

    # No self-review
    if reviewer == task.claimed_by:
        return jsonify({'error': 'Reviewer cannot be the same agent who worked on the task'}), 409

    # Create review record
    review = Review(
        task_id=task.id,
        reviewer=reviewer,
        decision=decision,
        feedback=feedback,
    )
    db.session.add(review)

    now = datetime.now(timezone.utc)
    rev_agent = db.session.get(Agent, reviewer)
    if rev_agent:
        rev_agent.last_heartbeat = now

    if decision == 'approve':
        ok, err = _validate_transition(task, 'completed')
        if not ok:
            return jsonify({'error': err}), 409
        task.status = 'completed'
        task.completed_at = now

        # Update worker agent reputation
        worker = db.session.get(Agent, task.claimed_by) if task.claimed_by else None
        if worker:
            worker.tasks_completed = (worker.tasks_completed or 0) + 1
            worker.status = 'idle'
            worker.update_reputation()

        _log_event(task.id, 'completed', agent=reviewer, details={
            'decision': 'approve',
            'reviewer': reviewer,
        })

    elif decision == 'reject':
        ok, err = _validate_transition(task, 'failed')
        if not ok:
            return jsonify({'error': err}), 409
        task.status = 'failed'
        task.last_error = feedback or 'Rejected by reviewer'
        task.failure_reason = 'review_rejected'
        task.attempts = (task.attempts or 0) + 1

        # Update worker agent reputation
        worker = db.session.get(Agent, task.claimed_by) if task.claimed_by else None
        if worker:
            worker.tasks_failed = (worker.tasks_failed or 0) + 1
            worker.tasks_review_rejected = (worker.tasks_review_rejected or 0) + 1
            worker.status = 'idle'
            worker.update_reputation()

        _log_event(task.id, 'reviewed', agent=reviewer, details={
            'decision': 'reject',
            'feedback': feedback,
            'reviewer': reviewer,
        })

    else:  # request_changes
        ok, err = _validate_transition(task, 'needs_revision')
        if not ok:
            return jsonify({'error': err}), 409
        task.status = 'needs_revision'
        task.last_error = feedback or 'Changes requested by reviewer'

        _log_event(task.id, 'reviewed', agent=reviewer, details={
            'decision': 'request_changes',
            'feedback': feedback,
            'reviewer': reviewer,
        })

    task.updated_at = now
    db.session.commit()
    return jsonify({
        'task': task.to_dict(),
        'review': review.to_dict(),
    })


@api_bp.route('/tasks/<int:task_id>/heartbeat', methods=['POST'])
def task_heartbeat(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json() or {}
    agent_name = data.get('agent')

    if not agent_name:
        return jsonify({"error": "agent name required"}), 400
    if task.status not in ('claimed', 'in_progress'):
        return jsonify({'error': f'Task is in status {task.status}, heartbeat only valid for claimed/in_progress'}), 409

    if task.claimed_by and task.claimed_by != agent_name:
        return jsonify({'error': f'Task is claimed by {task.claimed_by}, not {agent_name}'}), 409

    now = datetime.now(timezone.utc)
    task.heartbeat_at = now
    task.last_seen = now
    task.lease_expires_at = now + Config.LEASE_DURATION
    task.updated_at = now

    # Update agent heartbeat
    agent = db.session.get(Agent, agent_name)
    if agent:
        agent.last_heartbeat = now
        agent.status = 'busy'

    _log_event(task.id, 'heartbeat', agent=agent_name)
    db.session.commit()

    return jsonify({
        'status': task.status,
        'lease_expires_at': task.lease_expires_at.isoformat() if task.lease_expires_at else None,
        'heartbeat_at': task.heartbeat_at.isoformat() if task.heartbeat_at else None,
    })


@api_bp.route('/tasks/<int:task_id>/escalate', methods=['POST'])
def escalate_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json() or {}
    target = data.get('target', 'needs_human')
    reason = data.get('reason', '')

    if target not in ('needs_human', 'needs_vesper', 'blocked'):
        return jsonify({'error': 'target must be needs_human, needs_vesper, or blocked'}), 400

    ok, err = _validate_transition(task, target)
    if not ok:
        return jsonify({'error': err}), 409

    now = datetime.now(timezone.utc)
    old_status = task.status
    task.status = target
    task.last_error = reason
    task.updated_at = now

    _log_event(task.id, 'escalated', details={
        'from_status': old_status,
        'to_status': target,
        'reason': reason,
        'target': target,
    })

    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/release', methods=['POST'])
def release_task(task_id):
    task = Task.query.get_or_404(task_id)

    if task.status not in frozenset(['claimed', 'in_progress', 'needs_revision', 'assigned']):
        return jsonify({'error': f'Task is {task.status}, cannot release'}), 409

    agent_name = task.claimed_by or task.assigned_to
    now = datetime.now(timezone.utc)

    # Update agent stats if failed/timeout
    if agent_name:
        agent = db.session.get(Agent, agent_name)
        if agent:
            agent.status = 'idle'
            agent.last_heartbeat = now

    # Go to pending (release clears assignment back to the pool)
    old_status = task.status
    task.status = 'pending'
    task.assigned_to = None

    old_claimed_by = task.claimed_by
    task.claimed_by = None
    task.lease_expires_at = None
    task.heartbeat_at = None
    task.updated_at = now

    _log_event(task.id, 'released', agent=agent_name, details={
        'old_status': old_status,
        'previous_agent': old_claimed_by,
    })

    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/requeue', methods=['POST'])
def requeue_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json() or {}
    force_dead = data.get('force_dead', False)

    # Can only requeue from failed or timed_out
    if task.status not in ('failed', 'timed_out'):
        return jsonify({'error': f'Task is {task.status}, can only requeue from failed or timed_out'}), 409

    now = datetime.now(timezone.utc)

    # Check max attempts
    if (task.attempts or 0) >= (task.max_attempts or 3) or force_dead:
        ok, err = _validate_transition(task, 'dead')
        if not ok:
            return jsonify({'error': err}), 409
        task.status = 'dead'
        task.completed_at = now
        task.updated_at = now
        _log_event(task.id, 'dead', details={
            'reason': f'Max attempts ({task.attempts}/{task.max_attempts}) reached',
        })
        db.session.commit()
        return jsonify(task.to_dict())

    # Requeue: go to released, then auto-transition to pending
    task.status = 'released'
    task.claimed_by = None
    task.assigned_to = None
    task.lease_expires_at = None
    task.heartbeat_at = None
    task.updated_at = now

    _log_event(task.id, 'released', details={
        'reason': f'Requeued from {task.status}, attempt {task.attempts}/{task.max_attempts}',
    })

    # Auto-transition released -> pending
    task.status = 'pending'
    task.updated_at = now

    _log_event(task.id, 'requeued', details={
        'attempts': task.attempts,
        'max_attempts': task.max_attempts,
    })

    db.session.commit()
    return jsonify(task.to_dict())


# ── Events ─────────────────────────────────

@api_bp.route('/tasks/<int:task_id>/events', methods=['GET'])
def task_events(task_id):
    Task.query.get_or_404(task_id)
    events = EventLog.query.filter_by(task_id=task_id).order_by(EventLog.created_at).all()
    return jsonify({'events': [e.to_dict() for e in events]})


@api_bp.route('/events', methods=['GET'])
def global_events():
    query = EventLog.query
    task_id = request.args.get('task_id')
    event_type = request.args.get('event_type')
    agent = request.args.get('agent')

    if task_id:
        query = query.filter_by(task_id=task_id)
    if event_type:
        query = query.filter_by(event_type=event_type)
    if agent:
        query = query.filter_by(agent=agent)

    total = query.count()
    try:
        page = int(request.args.get('page', 1))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = int(request.args.get('per_page', 50))
    except (ValueError, TypeError):
        per_page = 50

    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 50

    events = query.order_by(EventLog.created_at.desc()).offset(
        (page - 1) * per_page).limit(per_page).all()
    return jsonify({
        'events': [e.to_dict() for e in events],
        'total': total,
        'page': page,
        'per_page': per_page,
    })


# ── Heartbeat (deprecated, use task-level heartbeat) ──
@api_bp.route('/agents/heartbeat', methods=['POST'])
def agent_heartbeat():
    """Agent-level heartbeat: update agent's last_heartbeat and status."""
    data = request.get_json() or {}
    agent_name = data.get('agent')

    if not agent_name:
        return jsonify({'error': 'agent name required'}), 400

    agent = _get_agent_or_create(agent_name)
    now = datetime.now(timezone.utc)
    agent.last_heartbeat = now
    agent.status = data.get('status', 'busy')

    db.session.commit()
    return jsonify({
        'agent': agent.name,
        'last_heartbeat': agent.last_heartbeat.isoformat() if agent.last_heartbeat else None,
    })


# ── Agents ─────────────────────────────────

@api_bp.route('/agents', methods=['GET'])
def list_agents():
    agents = Agent.query.all()
    return jsonify({'agents': [a.to_dict() for a in agents]})


@api_bp.route('/agents/<name>', methods=['GET'])
def get_agent(name):
    agent = Agent.query.get_or_404(name)
    return jsonify(agent.to_dict())


@api_bp.route('/agents', methods=['POST'])
def register_agent():
    data = request.get_json()
    if not data or 'name' not in data:
        return jsonify({'error': 'name is required'}), 400

    name = data['name'].strip()
    if not name:
        return jsonify({'error': 'name cannot be empty'}), 400

    existing = db.session.get(Agent, name)
    is_new = existing is None
    if existing:
        # Update existing
        existing.display_name = data.get('display_name', existing.display_name)
        existing.model = data.get('model', existing.model)
        existing.role = data.get('role', existing.role)
        existing.skills = data.get('skills', existing.skills)
        existing.preferred_projects = data.get('preferred_projects', existing.preferred_projects)
        existing.max_concurrent = data.get('max_concurrent', existing.max_concurrent)
    else:
        existing = Agent(
            name=name,
            display_name=data.get('display_name', name),
            model=data.get('model', ''),
            role=data.get('role', 'worker'),
            skills=data.get('skills', ''),
            preferred_projects=data.get('preferred_projects', ''),
            max_concurrent=data.get('max_concurrent', 3),
        )
        db.session.add(existing)

    _log_event(None, 'agent_registered', agent=name, details={
        'display_name': existing.display_name,
        'role': existing.role,
        'skills': existing.skills,
    })

    db.session.commit()
    return jsonify(existing.to_dict()), (201 if is_new else 200)


# ── Stats ──────────────────────────────────

@api_bp.route('/stats', methods=['GET'])
def stats():
    total = Task.query.count()
    by_status = {}
    for s in ['pending', 'assigned', 'claimed', 'in_progress', 'submitted',
              'in_review', 'completed', 'failed', 'blocked', 'needs_human',
              'needs_vesper', 'needs_revision', 'timed_out', 'released', 'dead']:
        count = Task.query.filter_by(status=s).count()
        if count > 0:
            by_status[s] = count

    by_agent = {}
    for agent in Agent.query.all():
        active = Task.query.filter(
            (Task.claimed_by == agent.name) | (Task.assigned_to == agent.name)
        ).filter(Task.status.in_(['claimed', 'in_progress', 'assigned'])).count()
        by_agent[agent.name] = {
            'active': active,
            'completed': agent.tasks_completed,
            'failed': agent.tasks_failed,
            'reputation_score': agent.reputation_score,
            'status': agent.status,
        }

    return jsonify({
        'total_tasks': total,
        'by_status': by_status,
        'by_agent': by_agent,
    })


# ── Overseer: timeout check ────────────────────────────────

@api_bp.route('/overseer/check-timeouts', methods=['POST'])
def check_timeouts():
    """Scan for tasks with expired leases and transition them to timed_out.
    This is the Overseer's heartbeat check — call periodically via cron."""
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)

    # Find tasks in claimed/in_progress with expired leases
    stuck = Task.query.filter(
        Task.status.in_(['claimed', 'in_progress']),
        Task.lease_expires_at.isnot(None),
    ).all()

    timed_out_count = 0
    for task in stuck:
        expires = task.lease_expires_at
        if expires and expires.tzinfo is not None:
            expires = expires.replace(tzinfo=None)
        if expires and now_naive > expires:
            old_status = task.status
            ok, err = _validate_transition(task, 'timed_out')
            if ok:
                task.status = 'timed_out'
                task.last_error = f'Lease expired at {expires.isoformat()}'
                task.failure_reason = 'timeout'
                task.timed_out_count = (task.timed_out_count or 0) + 1
                task.updated_at = now
                timed_out_count += 1

                _log_event(task.id, 'timed_out', details={
                    'from_status': old_status,
                    'lease_expires_at': expires.isoformat(),
                    'checked_at': now.isoformat(),
                })

                # Update agent stats
                if task.claimed_by:
                    agent = db.session.get(Agent, task.claimed_by)
                    if agent:
                        agent.tasks_timed_out = (agent.tasks_timed_out or 0) + 1
                        agent.status = 'idle'

    db.session.commit()
    return jsonify({
        'checked': len(stuck),
        'timed_out': timed_out_count,
        'checked_at': now.isoformat(),
    })


# ── Resolve: needs_human / needs_vesper → in_review ────────

@api_bp.route('/tasks/<int:task_id>/resolve', methods=['POST'])
def resolve_task(task_id):
    """Resolve a task from needs_human/needs_vesper status.
    Human/Vesper reviews and routes it: approve → completed,
    reject → failed, or send back through pipeline → assigned/pending."""
    task = Task.query.get_or_404(task_id)
    if task.status not in ('needs_human', 'needs_vesper'):
        return jsonify({'error': f'Task is {task.status}, not in needs_human/needs_vesper'}), 409

    data = request.get_json() or {}
    decision = data.get('decision')  # approve, reject, reassign, release
    reason = data.get('reason', '')

    if decision not in ('approve', 'reject', 'reassign', 'release'):
        return jsonify({'error': 'decision must be approve, reject, reassign, or release'}), 400

    now = datetime.now(timezone.utc)
    old_status = task.status

    if decision == 'approve':
        task.status = 'completed'
        task.completed_at = now
        task.result = reason or task.result
        task.updated_at = now
        _log_event(task.id, 'resolved', details={
            'from_status': old_status, 'decision': 'approve', 'reason': reason,
        })
    elif decision == 'reject':
        task.status = 'failed'
        task.last_error = reason
        task.failure_reason = 'rejected_by_human'
        task.updated_at = now
        _log_event(task.id, 'resolved', details={
            'from_status': old_status, 'decision': 'reject', 'reason': reason,
        })
    elif decision == 'reassign':
        task.status = 'pending'
        task.claimed_by = None
        task.assigned_to = None
        task.lease_expires_at = None
        task.updated_at = now
        _log_event(task.id, 'resolved', details={
            'from_status': old_status, 'decision': 'reassign', 'reason': reason,
        })
    elif decision == 'release':
        task.status = 'pending'
        task.claimed_by = None
        task.assigned_to = None
        task.lease_expires_at = None
        task.updated_at = now
        _log_event(task.id, 'resolved', details={
            'from_status': old_status, 'decision': 'release', 'reason': reason,
        })

    db.session.commit()
    return jsonify(task.to_dict())


# ── Overseer: auto-assign ────────────────────────────────

@api_bp.route('/overseer/auto-assign', methods=['POST'])
def auto_assign():
    """Scan pending tasks, score each available agent, and assign to the best match."""
    now = datetime.now(timezone.utc)
    assigned_count = 0
    skipped_count = 0
    results = []

    # 1. Get all pending tasks
    pending_tasks = Task.query.filter_by(status='pending').order_by(
        Task.priority, Task.created_at
    ).all()

    # 2. Get all non-offline agents
    agents = Agent.query.filter(Agent.status != 'offline').all()

    for task in pending_tasks:
        best_agent = None
        best_score = 0

        task_tags = {t.strip().lower() for t in task.tags.split(',') if t.strip()}
        task_project = task.project or ''

        for agent in agents:
            score = 0

            # Skill match: task tags overlapping agent skills (+3 per match)
            agent_skills = {s.strip().lower() for s in agent.skills.split(',') if s.strip()}
            skill_matches = task_tags & agent_skills
            score += len(skill_matches) * 3

            # Project match (+2)
            agent_projects = {p.strip().lower() for p in agent.preferred_projects.split(',') if p.strip()}
            project_match = task_project.lower() in agent_projects
            if project_match:
                score += 2

            # Agent MUST have at least one skill match or project match to be eligible
            if len(skill_matches) == 0 and not project_match:
                continue

            # Priority bonus: P1=+5, P2=+4, P3=+3, P4=+2, P5=+1
            priority_bonus = max(0, 6 - task.priority)
            score += priority_bonus

            # Availability (+2 if has capacity)
            active_count = Task.query.filter(
                Task.claimed_by == agent.name,
                Task.status.in_(['claimed', 'in_progress'])
            ).count()
            if active_count < agent.max_concurrent:
                score += 2

            # Reputation score / 20
            score += agent.reputation_score / 20

            if score > best_score:
                best_score = score
                best_agent = agent

        # Check reserved_for — only assign if best_agent matches
        if best_agent and task.reserved_for:
            best_agent_skills = {s.strip().lower() for s in best_agent.skills.split(',') if s.strip()}
            agent_role = best_agent.role.strip().lower()
            reserved = task.reserved_for.strip().lower()
            if agent_role != reserved and reserved not in best_agent_skills:
                best_agent = None
                best_score = 0

        if best_agent and best_score > 0:
            # Assign the task
            task.status = 'assigned'
            task.assigned_to = best_agent.name
            task.assigned_at = now
            task.updated_at = now

            _log_event(task.id, 'assigned', agent=best_agent.name, details={
                'assigned_to': best_agent.name,
                'auto_assign': True,
                'score': best_score,
            })
            assigned_count += 1
            results.append({
                'task_id': task.id,
                'assigned_to': best_agent.name,
                'score': best_score,
            })
        else:
            skipped_count += 1
            results.append({
                'task_id': task.id,
                'assigned_to': None,
                'score': 0,
                'reason': 'no matching agent',
            })

    db.session.commit()
    return jsonify({
        'assigned': assigned_count,
        'skipped': skipped_count,
        'total': len(pending_tasks),
        'results': results,
    })


# ── Overseer: pending-for-agent ─────────────────────────

@api_bp.route('/overseer/pending-for-agent/<name>', methods=['GET'])
def pending_for_agent(name):
    """Return pending tasks whose tags overlap with this agent's skills."""
    agent = db.session.get(Agent, name)
    if not agent:
        return jsonify({'error': f'Agent {name} not found'}), 404

    agent_skills = {s.strip().lower() for s in agent.skills.split(',') if s.strip()}
    if not agent_skills:
        return jsonify({'tasks': [], 'total': 0})

    pending_tasks = Task.query.filter_by(status='pending').order_by(
        Task.priority, Task.created_at
    ).all()

    matching = []
    for task in pending_tasks:
        task_tags = {t.strip().lower() for t in task.tags.split(',') if t.strip()}
        if task_tags & agent_skills:
            matching.append(task)

    return jsonify({
        'tasks': [t.to_dict() for t in matching],
        'total': len(matching),
    })


# ── Overseer: reclaim timeouts ──────────────────────────

@api_bp.route('/overseer/reclaim-timeouts', methods=['POST'])
def reclaim_timeouts():
    """Check timed-out tasks and either release them (if under max_attempts) or mark dead."""
    now = datetime.now(timezone.utc)
    released_count = 0
    dead_count = 0
    results = []

    timed_out_tasks = Task.query.filter_by(status='timed_out').all()

    for task in timed_out_tasks:
        task.attempts = (task.attempts or 0) + 1

        if (task.attempts or 0) < (task.max_attempts or 3):
            # Release: go to released -> auto pending
            old_agent = task.claimed_by or task.assigned_to
            task.status = 'released'
            task.claimed_by = None
            task.assigned_to = None
            task.lease_expires_at = None
            task.heartbeat_at = None
            task.updated_at = now

            _log_event(task.id, 'released', agent=old_agent, details={
                'reason': f'Reclaim from timed_out, attempt {task.attempts}/{task.max_attempts}',
            })

            # Auto-transition to pending
            task.status = 'pending'
            task.updated_at = now
            _log_event(task.id, 'requeued', details={
                'attempts': task.attempts,
                'max_attempts': task.max_attempts,
                'reason': 'auto-reclaim after timeout',
            })
            released_count += 1
            results.append({
                'task_id': task.id,
                'action': 'released',
                'attempts': task.attempts,
            })
        else:
            # Max attempts reached — mark dead
            task.status = 'dead'
            task.completed_at = now
            task.updated_at = now
            task.last_error = f'Max attempts ({task.attempts}/{task.max_attempts}) reached after timeouts'

            _log_event(task.id, 'dead', details={
                'reason': f'Max attempts ({task.attempts}/{task.max_attempts}) reached via reclaim-timeouts',
            })
            dead_count += 1
            results.append({
                'task_id': task.id,
                'action': 'dead',
                'attempts': task.attempts,
            })

    db.session.commit()
    return jsonify({
        'checked': len(timed_out_tasks),
        'released': released_count,
        'dead': dead_count,
        'results': results,
    })


# ── Overseer: dashboard ─────────────────────────────────

@api_bp.route('/overseer/dashboard', methods=['GET'])
def overseer_dashboard():
    """Summary stats for the overseer dashboard."""
    now = datetime.now(timezone.utc)

    # Tasks by status
    by_status = {}
    for s in ['pending', 'assigned', 'claimed', 'in_progress', 'submitted',
              'in_review', 'completed', 'failed', 'blocked', 'needs_human',
              'needs_vesper', 'needs_revision', 'timed_out', 'released', 'dead']:
        count = Task.query.filter_by(status=s).count()
        if count > 0:
            by_status[s] = count

    # Agent load info
    agent_load = {}
    for agent in Agent.query.all():
        active = Task.query.filter(
            Task.claimed_by == agent.name,
            Task.status.in_(['claimed', 'in_progress'])
        ).count()
        assigned = Task.query.filter_by(
            assigned_to=agent.name, status='assigned'
        ).count()
        available_slots = agent.max_concurrent - active
        agent_load[agent.name] = {
            'active': active,
            'assigned': assigned,
            'max_concurrent': agent.max_concurrent,
            'available_slots': max(0, available_slots),
            'status': agent.status,
            'skills': agent.skills,
            'reputation_score': agent.reputation_score,
        }

    # Recent events (last 20)
    recent_events = EventLog.query.order_by(
        EventLog.created_at.desc()
    ).limit(20).all()

    # Stats
    total_tasks = Task.query.count()
    locked_count = Task.query.filter(Task.status.in_(['claimed', 'in_progress'])).count()
    timed_out_count = Task.query.filter_by(status='timed_out').count()

    return jsonify({
        'total_tasks': total_tasks,
        'by_status': by_status,
        'agent_load': agent_load,
        'locked_tasks': locked_count,
        'timed_out_tasks': timed_out_count,
        'recent_events': [e.to_dict() for e in recent_events],
        'checked_at': now.isoformat(),
    })
