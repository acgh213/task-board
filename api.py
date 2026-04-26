# api.py
import json
from datetime import datetime, timezone, date, timedelta
from flask import Blueprint, request, jsonify
from models import db, Task, TaskTemplate, Agent, Review, EventLog, HandoffRequest, STATE_TRANSITIONS
from models import Achievement, AgentBadge
from models import ESCALATION_TAGS_HUMAN, ESCALATION_TAGS_VESPER
from config import Config
from ws import socketio
from schemas import AgentMessage, TextPart, DataPart, FilePart, HandoffRequest as HandoffRequestSchema, HandoffResponse

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


# ── SocketIO event emission ────────────────

def _emit_task_update(task):
    """Emit a SocketIO event with the full task data after a state change."""
    try:
        socketio.emit('task_update', task.to_dict())
    except Exception:
        pass

def _emit_agent_update(agent):
    """Emit a SocketIO event with the full agent data after a state change."""
    try:
        socketio.emit('agent_update', agent.to_dict())
    except Exception:
        pass

def _emit_new_event(event):
    """Emit a SocketIO event when a new event log entry is created."""
    try:
        socketio.emit('new_event', event.to_dict())
    except Exception:
        pass


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


def _sync_agent_status(agent_name, now=None):
    """Refresh an agent's idle/busy status from live active task ownership."""
    if not agent_name:
        return None

    agent = db.session.get(Agent, agent_name)
    if agent is None or agent.status == 'offline':
        return agent

    active_states = ('assigned', 'claimed', 'in_progress', 'submitted', 'in_review', 'needs_revision')
    active_count = Task.query.filter(
        ((Task.claimed_by == agent_name) | (Task.assigned_to == agent_name)),
        Task.status.in_(active_states),
    ).count()

    agent.status = 'busy' if active_count > 0 else 'idle'
    if now is not None:
        agent.last_heartbeat = now
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


# ── Dependency resolution ────────────────────

def _resolve_dependencies(task, target_status='pending'):
    """Check if any tasks blocked by this completed task can now be unblocked.
    Returns list of (task_id, old_status, new_status) for auto-transitioned tasks."""
    if task.status != 'completed' or not task.id:
        return []
    transitions = []
    for dep_task in task.get_dependent_tasks():
        if dep_task.status == 'blocked' and dep_task.are_dependencies_met():
            old_status = dep_task.status
            dep_task.status = target_status
            dep_task.updated_at = datetime.now(timezone.utc)
            _log_event(dep_task.id, 'dependency_resolved', details={
                'blocking_task_id': task.id,
                'from_status': old_status,
                'to_status': target_status,
                'reason': f'Blocking task {task.id} completed, all dependencies met',
            })
            transitions.append((dep_task.id, old_status, target_status))
            _emit_task_update(dep_task)
    return transitions


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

    complexity = data.get('complexity', 3)
    if not isinstance(complexity, int) or complexity < 1 or complexity > 5:
        return jsonify({'error': 'complexity must be integer 1-5'}), 400

    task = Task(
        title=title,
        description=data.get('description', ''),
        priority=priority,
        complexity=complexity,
        tags=data.get('tags', ''),
        project=data.get('project', 'general'),
        reserved_for=data.get('reserved_for', None),
    )

    # Check escalation tags on creation
    escalate_to = _check_escalation_tags(task)
    if escalate_to:
        task.status = escalate_to
    elif data.get('start_in_triage', False):
        task.status = 'triage'

    db.session.add(task)
    db.session.flush()

    _log_event(task.id, 'task_created', details={
        'title': title,
        'tags': task.tags,
        'status': task.status,
    })

    # Vesper XP: if task created by authenticated user 'vesper', grant +10 XP
    email = _get_email()
    if email == 'vesper':
        _get_agent_or_create('vesper', display_name='Vesper', model='deepseek-v4-flash')
        agent = db.session.get(Agent, 'vesper')
        if agent:
            agent.xp = (agent.xp or 0) + 10
            agent.compute_level()
            _log_event(task.id, 'xp_gained', agent='vesper', details={
                'xp_gained': 10,
                'total_xp': agent.xp,
                'level': agent.level,
                'reason': 'Task creation by Vesper',
            })

    if escalate_to:
        _log_event(task.id, 'escalated', details={
            'from_status': 'pending',
            'to_status': escalate_to,
            'reason': f'Task tags triggered escalation: {task.tags}',
        })

    db.session.commit()
    _emit_task_update(task)
    return jsonify(task.to_dict()), 201


# ── Triage Endpoints ────────────────────────

@api_bp.route('/tasks/<int:task_id>/triage/accept', methods=['POST'])
def triage_accept(task_id):
    """Accept a task from triage → pending."""
    task = Task.query.get_or_404(task_id)
    ok, err = _validate_transition(task, 'pending')
    if not ok:
        return jsonify({'error': err}), 409

    now = datetime.now(timezone.utc)
    task.status = 'pending'
    task.updated_at = now

    _log_event(task.id, 'triage_accepted', details={
        'from_status': 'triage',
        'to_status': 'pending',
    })

    db.session.commit()
    _emit_task_update(task)
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/triage/assign', methods=['POST'])
def triage_assign(task_id):
    """Assign a task from triage → assigned (with agent)."""
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

    _log_event(task.id, 'triage_assigned', agent=agent_name, details={
        'from_status': 'triage',
        'to_status': 'assigned',
        'assigned_to': agent_name,
    })

    db.session.commit()
    _emit_task_update(task)
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/triage/reject', methods=['POST'])
def triage_reject(task_id):
    """Reject a task from triage → failed with reason."""
    task = Task.query.get_or_404(task_id)
    data = request.get_json() or {}
    reason = data.get('reason', 'Rejected in triage')

    ok, err = _validate_transition(task, 'failed')
    if not ok:
        return jsonify({'error': err}), 409

    now = datetime.now(timezone.utc)
    task.status = 'failed'
    task.last_error = reason
    task.failure_reason = 'triage_rejected'
    task.updated_at = now

    _log_event(task.id, 'triage_rejected', details={
        'from_status': 'triage',
        'to_status': 'failed',
        'reason': reason,
    })

    db.session.commit()
    _emit_task_update(task)
    return jsonify(task.to_dict())


# ── Triage Queue Enhancements (Task #5) ─────

@api_bp.route('/tasks/triage', methods=['GET'])
def list_triage_tasks():
    """List all tasks in triage status."""
    query = Task.query.filter_by(status='triage')
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
    total = query.count()
    tasks = query.order_by(Task.priority, Task.created_at).offset(
        (page - 1) * per_page).limit(per_page).all()
    return jsonify({
        'tasks': [t.to_dict() for t in tasks],
        'total': total,
        'page': page,
        'per_page': per_page,
    })


@api_bp.route('/triage/stats', methods=['GET'])
def triage_stats():
    """Return statistics about the triage queue."""
    triage_tasks = Task.query.filter_by(status='triage').all()
    total = len(triage_tasks)
    by_priority = {}
    by_complexity = {}
    for t in triage_tasks:
        p = t.priority or 3
        by_priority[p] = by_priority.get(p, 0) + 1
        c = t.complexity or 3
        by_complexity[c] = by_complexity.get(c, 0) + 1
    escalation_count = 0
    for t in triage_tasks:
        if t.check_escalation_tags():
            escalation_count += 1
    return jsonify({
        'total_in_triage': total,
        'by_priority': by_priority,
        'by_complexity': by_complexity,
        'escalation_prone': escalation_count,
    })


@api_bp.route('/triage/bulk-accept', methods=['POST'])
def triage_bulk_accept():
    """Accept all tasks currently in triage into pending status."""
    data = request.get_json() or {}
    task_ids = data.get('task_ids')
    now = datetime.now(timezone.utc)
    accepted = []
    skipped = []
    if task_ids is not None:
        for tid in task_ids:
            task = db.session.get(Task, tid)
            if not task:
                skipped.append({'task_id': tid, 'reason': 'not_found'})
                continue
            if task.status != 'triage':
                skipped.append({'task_id': tid, 'reason': f'status_is_{task.status}'})
                continue
            ok, err = _validate_transition(task, 'pending')
            if not ok:
                skipped.append({'task_id': tid, 'reason': err})
                continue
            task.status = 'pending'
            task.updated_at = now
            _log_event(task.id, 'triage_accepted', details={
                'from_status': 'triage', 'to_status': 'pending',
                'bulk': True,
            })
            accepted.append(task.id)
    else:
        for task in Task.query.filter_by(status='triage').all():
            ok, err = _validate_transition(task, 'pending')
            if not ok:
                skipped.append({'task_id': task.id, 'reason': err})
                continue
            task.status = 'pending'
            task.updated_at = now
            _log_event(task.id, 'triage_accepted', details={
                'from_status': 'triage', 'to_status': 'pending',
                'bulk': True,
            })
            accepted.append(task.id)
    db.session.commit()
    for tid in accepted:
        t = db.session.get(Task, tid)
        if t:
            _emit_task_update(t)
    return jsonify({
        'accepted': len(accepted),
        'skipped': len(skipped),
        'accepted_ids': accepted,
        'skipped': skipped,
    })


@api_bp.route('/triage/bulk-reject', methods=['POST'])
def triage_bulk_reject():
    """Reject all tasks currently in triage, moving them to failed."""
    data = request.get_json() or {}
    task_ids = data.get('task_ids')
    reason = data.get('reason', 'Bulk rejected in triage')
    now = datetime.now(timezone.utc)
    rejected = []
    skipped = []
    if task_ids is not None:
        for tid in task_ids:
            task = db.session.get(Task, tid)
            if not task:
                skipped.append({'task_id': tid, 'reason': 'not_found'})
                continue
            if task.status != 'triage':
                skipped.append({'task_id': tid, 'reason': f'status_is_{task.status}'})
                continue
            ok, err = _validate_transition(task, 'failed')
            if not ok:
                skipped.append({'task_id': tid, 'reason': err})
                continue
            task.status = 'failed'
            task.last_error = reason
            task.failure_reason = 'triage_rejected'
            task.updated_at = now
            _log_event(task.id, 'triage_rejected', details={
                'from_status': 'triage', 'to_status': 'failed',
                'reason': reason, 'bulk': True,
            })
            rejected.append(task.id)
    else:
        for task in Task.query.filter_by(status='triage').all():
            ok, err = _validate_transition(task, 'failed')
            if not ok:
                skipped.append({'task_id': task.id, 'reason': err})
                continue
            task.status = 'failed'
            task.last_error = reason
            task.failure_reason = 'triage_rejected'
            task.updated_at = now
            _log_event(task.id, 'triage_rejected', details={
                'from_status': 'triage', 'to_status': 'failed',
                'reason': reason, 'bulk': True,
            })
            rejected.append(task.id)
    db.session.commit()
    for tid in rejected:
        t = db.session.get(Task, tid)
        if t:
            _emit_task_update(t)
    return jsonify({
        'rejected': len(rejected),
        'skipped': len(skipped),
        'rejected_ids': rejected,
        'skipped': skipped,
    })


@api_bp.route('/tasks/<int:task_id>', methods=['GET'])
def get_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = task.to_dict()
    data['reviews'] = [r.to_dict() for r in task.reviews]
    data['events'] = [e.to_dict() for e in task.events]
    return jsonify(data)


@api_bp.route('/tasks/<int:task_id>/audit', methods=['GET'])
def task_audit_api(task_id):
    """Return the full lifecycle audit trail for a task.

    Reconstructs claimed_by state across events, extracts XP and handoff data,
    and provides a summary with total events, XP, agents involved, and handoffs.
    """
    task = Task.query.get_or_404(task_id)
    events = EventLog.query.filter_by(task_id=task_id).order_by(EventLog.created_at).all()

    lifecycle = []
    # Track state for claimed_by reconstruction
    current_claimed_by = None
    total_xp_awarded = 0
    agents_involved = set()
    handoffs_count = 0

    for evt in events:
        details = json.loads(evt.details) if evt.details else {}

        # Extract from_status / to_status from details
        from_status = details.get('from_status')
        to_status = details.get('to_status')

        # If no explicit from_status/to_status, try to derive from event type
        if not from_status and not to_status:
            if evt.event_type == 'task_created':
                to_status = details.get('status', 'pending')

        # Reconstruct claimed_by state
        claimed_by_before = current_claimed_by

        # Update claimed_by tracking based on event type and details
        if evt.event_type == 'claimed':
            current_claimed_by = details.get('claimed_by', evt.agent)
        elif evt.event_type in ('released', 'lease_expired'):
            current_claimed_by = None
        elif evt.event_type == 'handoff_accepted':
            # Handoff accepted: release old claimant
            current_claimed_by = None
        elif details.get('claimed_by') is None and 'claimed_by' in details:
            current_claimed_by = None
        elif details.get('claimed_by'):
            current_claimed_by = details['claimed_by']
        elif evt.event_type == 'assigned':
            # Assigned does not change claimed_by
            pass
        elif evt.event_type in ('completed', 'failed', 'timed_out', 'dead'):
            # Terminal events: clear claimed_by
            current_claimed_by = None

        claimed_by_after = current_claimed_by

        # XP extraction: look for xp_gained in details
        xp_awarded = None
        if 'xp_gained' in details:
            xp_awarded = details['xp_gained']
            total_xp_awarded += int(xp_awarded)
        elif evt.event_type in ('xp_gained',) and 'xp_gained' in details:
            xp_awarded = details['xp_gained']
            total_xp_awarded += int(xp_awarded)

        # Handoff ID extraction
        handoff_id = details.get('handoff_request_id')

        # Count handoffs (handoff_requested events)
        if evt.event_type == 'handoff_requested' and handoff_id is not None:
            handoffs_count += 1

        # Track agents involved
        if evt.agent:
            agents_involved.add(evt.agent)

        entry = {
            'timestamp': evt.created_at.isoformat() if evt.created_at else None,
            'event_type': evt.event_type,
            'actor': evt.agent,
            'status_before': from_status,
            'status_after': to_status,
            'claimed_by_before': claimed_by_before,
            'claimed_by_after': claimed_by_after,
            'xp_awarded': xp_awarded,
            'handoff_id': handoff_id,
            'details': details,
        }
        lifecycle.append(entry)

    summary = {
        'total_events': len(events),
        'total_xp_awarded': total_xp_awarded,
        'agents_involved': sorted(list(agents_involved)) if agents_involved else [],
        'handoffs': handoffs_count,
        'final_status': task.status,
    }

    return jsonify({
        'task_id': task.id,
        'task_title': task.title,
        'lifecycle': lifecycle,
        'summary': summary,
    })


@api_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    return jsonify({'deleted': task_id})


# ── Dependency Endpoints ────────────────────

@api_bp.route('/tasks/<int:task_id>/dependencies', methods=['GET'])
def get_task_dependencies(task_id):
    """Return blocking tasks (dependencies) for a task with their status."""
    task = Task.query.get_or_404(task_id)
    blocking = task.get_blocking_tasks()
    return jsonify({
        'task_id': task.id,
        'blocked_by': task.blocked_by or '',
        'dependencies': [{
            'id': t.id,
            'title': t.title,
            'status': t.status,
        } for t in blocking],
        'all_met': task.are_dependencies_met(),
    })


@api_bp.route('/tasks/<int:task_id>/block', methods=['POST'])
def set_task_block(task_id):
    """Set blocked_by on a task."""
    task = Task.query.get_or_404(task_id)
    data = request.get_json() or {}
    blocked_by_value = data.get('blocked_by', '')

    # Validate
    cleaned, error = Task.validate_blocked_by(blocked_by_value, task_id=task.id)
    if error:
        return jsonify({'error': error}), 400

    now = datetime.now(timezone.utc)
    task.blocked_by = cleaned
    task.updated_at = now

    # Auto-transition to 'blocked' if dependencies aren't met
    if cleaned and not task.are_dependencies_met():
        if task.can_transition_to('blocked'):
            task.status = 'blocked'
            _log_event(task.id, 'blocked', details={
                'blocked_by': cleaned,
                'reason': 'Dependencies not met, auto-blocked',
            })
        else:
            # If can't go to blocked from current status, still set blocked_by but log it
            _log_event(task.id, 'dependency_set', details={
                'blocked_by': cleaned,
                'note': 'Task could not auto-transition to blocked from current status',
            })
    elif cleaned and task.are_dependencies_met():
        # All deps already met, can stay or move to pending
        if task.status == 'blocked':
            task.status = 'pending'
            _log_event(task.id, 'dependency_set', details={
                'blocked_by': cleaned,
                'reason': 'All dependencies already met, unblocked',
            })
        else:
            _log_event(task.id, 'dependency_set', details={
                'blocked_by': cleaned,
            })
    else:
        _log_event(task.id, 'dependency_cleared', details={
            'reason': 'blocked_by was cleared',
        })

    db.session.commit()
    _emit_task_update(task)
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/block/<int:blocking_task_id>', methods=['DELETE'])
def remove_task_block(task_id, blocking_task_id):
    """Remove a specific blocking dependency."""
    task = Task.query.get_or_404(task_id)
    ids = task.get_blocking_task_ids()

    if blocking_task_id not in ids:
        return jsonify({'error': f'Task {task_id} is not blocked by task {blocking_task_id}'}), 404

    ids.remove(blocking_task_id)
    now = datetime.now(timezone.utc)
    task.blocked_by = ','.join(str(i) for i in ids) if ids else ''
    task.updated_at = now

    _log_event(task.id, 'dependency_removed', details={
        'removed_blocking_task_id': blocking_task_id,
        'remaining_blocked_by': task.blocked_by,
    })

    # If no more dependencies or all met, unblock
    if task.are_dependencies_met() and task.status == 'blocked':
        task.status = 'pending'
        _log_event(task.id, 'dependency_resolved', details={
            'reason': f'Removed blocking task {blocking_task_id}, all dependencies met',
            'from_status': 'blocked',
            'to_status': 'pending',
        })

    db.session.commit()
    _emit_task_update(task)
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/unblock-all', methods=['POST'])
def unblock_task(task_id):
    """Clear all dependencies if all are met."""
    task = Task.query.get_or_404(task_id)

    if not task.blocked_by:
        return jsonify({'error': 'Task has no blocked_by dependencies'}), 400

    if not task.are_dependencies_met():
        return jsonify({'error': 'Not all dependencies are completed'}), 409

    now = datetime.now(timezone.utc)
    old_blocked_by = task.blocked_by
    task.blocked_by = ''
    task.updated_at = now

    if task.status == 'blocked':
        task.status = 'pending'
        _log_event(task.id, 'dependency_resolved', details={
            'reason': 'All dependencies met, unblocked via unblock-all',
            'from_status': 'blocked',
            'to_status': 'pending',
            'cleared_deps': old_blocked_by,
        })
    else:
        _log_event(task.id, 'dependency_cleared', details={
            'cleared_deps': old_blocked_by,
        })

    db.session.commit()
    _emit_task_update(task)
    return jsonify(task.to_dict())


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
    _emit_task_update(task)
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
    _emit_task_update(task)
    agent = db.session.get(Agent, agent_name)
    if agent:
        _emit_agent_update(agent)
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
    _emit_task_update(task)
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/submit', methods=['POST'])
def submit_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json() or {}
    agent_name = data.get('agent')

    if not agent_name:
        return jsonify({"error": "agent name required"}), 400

    # Minimum wait time enforcement: at least 5 seconds must pass after claim
    skip_wait = request.args.get('skip_wait', '').lower() == 'true'
    if not skip_wait and task.claimed_at:
        now = datetime.now(timezone.utc)
        claimed = task.claimed_at
        if claimed.tzinfo:
            claimed = claimed.replace(tzinfo=None)
        now_naive = now.replace(tzinfo=None)
        elapsed = (now_naive - claimed).total_seconds()
        if elapsed < 5:
            return jsonify({'error': 'Agent must work on task for at least 5 seconds before submitting'}), 409

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
    _emit_task_update(task)
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

    # Must be in_review or assigned (for reviewer handoffs)
    if task.status not in ('in_review', 'assigned'):
        return jsonify({'error': f'Task is in status {task.status}, must be in_review or assigned to review'}), 409

    # No self-review: determine who did the work
    worker = task.claimed_by
    if not worker and task.status == 'assigned':
        # Reviewer handoff path: claimed_by was cleared, find previous worker from events
        last_worker_event = EventLog.query.filter_by(task_id=task.id).filter(
            EventLog.event_type.in_(['claimed', 'submitted']),
            EventLog.agent.isnot(None),
        ).order_by(EventLog.id.desc()).first()
        worker = last_worker_event.agent if last_worker_event else None
    if worker and reviewer == worker:
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
            # Update avg_completion_time
            if task.created_at and task.completed_at:
                # Both naive UTC (SQLite strips tzinfo on read); ensure consistency
                created = task.created_at.replace(tzinfo=None) if task.created_at.tzinfo else task.created_at
                completed = task.completed_at.replace(tzinfo=None) if task.completed_at.tzinfo else task.completed_at
                delta = (completed - created).total_seconds()
                if delta >= 0:
                    prev_avg = worker.avg_completion_time or 0.0
                    prev_count = (worker.tasks_completed or 0) - 1
                    if prev_count > 0:
                        worker.avg_completion_time = (prev_avg * prev_count + delta) / (prev_count + 1)
                    else:
                        worker.avg_completion_time = delta
            worker.update_reputation()
            # Grant XP for approved task completion (Task #9)
            _grant_xp(worker.name, task, review_decision='approve')

        _log_event(task.id, 'completed', agent=reviewer, details={
            'decision': 'approve',
            'reviewer': reviewer,
        })

        # Resolve any tasks blocked by this completed task
        _resolve_dependencies(task)

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

        # Update worker agent status — they need to pick up the revision
        worker = db.session.get(Agent, task.claimed_by) if task.claimed_by else None
        if worker:
            worker.status = 'idle'

        _log_event(task.id, 'reviewed', agent=reviewer, details={
            'decision': 'request_changes',
            'feedback': feedback,
            'reviewer': reviewer,
        })

    task.updated_at = now
    # Sync reviewer agent status (busy if still has active tasks, idle otherwise)
    _sync_agent_status(reviewer, now)
    db.session.commit()
    _emit_task_update(task)
    # Emit agent updates if workers were affected
    if task.claimed_by:
        worker = db.session.get(Agent, task.claimed_by)
        if worker:
            _emit_agent_update(worker)
    rev_agent_obj = db.session.get(Agent, reviewer) if reviewer else None
    if rev_agent_obj:
        _emit_agent_update(rev_agent_obj)

    # Check for newly earned badges (Task #10)
    new_badges = []
    if decision == 'approve' and task.claimed_by:
        new_badges = _check_badges(task.claimed_by)

    # Vesper XP: grant +5 XP for reviews done by Vesper
    if reviewer == 'vesper':
        _get_agent_or_create('vesper', display_name='Vesper', model='deepseek-v4-flash')
        vesper_agent = db.session.get(Agent, 'vesper')
        if vesper_agent:
            vesper_agent.xp = (vesper_agent.xp or 0) + 5
            vesper_agent.compute_level()
            _log_event(task.id, 'xp_gained', agent='vesper', details={
                'xp_gained': 5,
                'total_xp': vesper_agent.xp,
                'level': vesper_agent.level,
                'reason': 'Review by Vesper',
            })

    return jsonify({
        'task': task.to_dict(),
        'review': review.to_dict(),
        'new_badges': new_badges,
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

    _emit_task_update(task)
    if agent:
        _emit_agent_update(agent)

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
    _emit_task_update(task)
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
    _emit_task_update(task)
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
        _emit_task_update(task)
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
    _emit_task_update(task)
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
    _emit_agent_update(agent)
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


@api_bp.route('/agents/<name>/reputation', methods=['GET'])
def agent_reputation(name):
    """Return detailed reputation stats for an agent."""
    agent = Agent.query.get_or_404(name)

    # Compute review pass rate
    total_reviews = (agent.tasks_completed or 0) + (agent.tasks_review_rejected or 0)
    review_pass_rate = 0.0
    if total_reviews > 0:
        review_pass_rate = (agent.tasks_completed or 0) / total_reviews

    return jsonify({
        'agent': agent.name,
        'display_name': agent.display_name,
        'tasks_completed': agent.tasks_completed or 0,
        'tasks_failed': agent.tasks_failed or 0,
        'tasks_review_rejected': agent.tasks_review_rejected or 0,
        'tasks_timed_out': agent.tasks_timed_out or 0,
        'avg_completion_time': agent.avg_completion_time or 0.0,
        'review_pass_rate': round(review_pass_rate, 4),
        'reputation_score': agent.reputation_score,
    })


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
    _emit_agent_update(existing)
    return jsonify(existing.to_dict()), (201 if is_new else 200)


# ── Agent Card Endpoints ────────────────────

@api_bp.route('/agents/cards', methods=['GET'])
def list_agent_cards():
    """Return A2A-compatible agent cards for all agents."""
    agents = Agent.query.all()
    cards = [_build_agent_card(a) for a in agents]
    return jsonify({'cards': cards})


@api_bp.route('/agents/<name>/card', methods=['GET'])
def get_agent_card(name):
    """Return A2A-compatible agent card for a single agent."""
    agent = Agent.query.get_or_404(name)
    return jsonify(_build_agent_card(agent))


def _build_agent_card(agent):
    """Build an A2A-compatible Agent Card from an Agent model."""
    return {
        'name': agent.name,
        'display_name': agent.display_name,
        'role': agent.role,
        'skills': agent.skills_list,
        'input_modes': ['text', 'data'],
        'output_modes': ['text', 'data'],
        'preferred_projects': agent.preferred_projects_list,
        'max_concurrent': agent.max_concurrent,
        'model': agent.model,
        'status': agent.status,
        'reputation_score': agent.reputation_score,
    }

# ── XP & Leveling Helpers (Task #9) ────────

def _calculate_xp(task, review_decision=None):
    """Calculate XP for a completed task. Returns xp_gained."""
    complexity = task.complexity or 3
    base_xp = 50 * complexity
    bonus = 0

    # Speed bonus
    if task.created_at and task.completed_at:
        created = task.created_at
        completed = task.completed_at
        if created.tzinfo:
            created = created.replace(tzinfo=None)
        if completed.tzinfo:
            completed = completed.replace(tzinfo=None)
        delta_sec = (completed - created).total_seconds()
        if delta_sec >= 0:
            if delta_sec < 60:
                bonus += 25
            elif delta_sec < 300:
                bonus += 10

    # Review bonus
    if review_decision == 'approve':
        bonus += 15

    return base_xp + bonus


def _update_streak(agent_name):
    """Update streak for an agent after task completion. Returns new streak."""
    from datetime import date
    agent = db.session.get(Agent, agent_name)
    if not agent:
        return 0
    today = date.today()
    if agent.last_active_date:
        yesterday = today - timedelta(days=1)
        if agent.last_active_date == yesterday:
            agent.streak = (agent.streak or 0) + 1
        elif agent.last_active_date == today:
            pass  # no change
        else:
            agent.streak = 1
    else:
        agent.streak = 1
    agent.last_active_date = today
    return agent.streak


def _grant_xp(agent_name, task, review_decision=None):
    """Calculate and grant XP to an agent. Returns (xp_gained, new_total, new_level)."""
    agent = db.session.get(Agent, agent_name)
    if not agent:
        return 0, 0, 1
    xp_gained = _calculate_xp(task, review_decision)
    agent.xp = (agent.xp or 0) + xp_gained
    agent.compute_level()
    _update_streak(agent_name)
    # Log XP gain event
    _log_event(task.id, 'xp_gained', agent=agent_name, details={
        'xp_gained': xp_gained,
        'total_xp': agent.xp,
        'level': agent.level,
    })
    return xp_gained, agent.xp, agent.level


def _get_recent_xp_gains(agent_name, limit=10):
    """Get the last N xp_gained event log entries for an agent."""
    events = EventLog.query.filter_by(
        agent=agent_name, event_type='xp_gained'
    ).order_by(EventLog.created_at.desc()).limit(limit).all()
    return [{
        'xp_gained': json.loads(e.details).get('xp_gained', 0) if e.details else 0,
        'task_id': e.task_id,
        'earned_at': e.created_at.isoformat() if e.created_at else None,
    } for e in events]


# ── Badge Checking (Task #10) ──────────────

def _check_badges(agent_name):
    """Evaluate all badge criteria for an agent. Returns list of newly earned badges."""
    from models import Agent, Achievement, AgentBadge
    agent = db.session.get(Agent, agent_name)
    if not agent:
        return []

    achievements = Achievement.query.all()
    newly_earned = []

    for ach in achievements:
        # Skip if already earned
        already = AgentBadge.query.filter_by(
            agent_name=agent_name, badge_id=ach.id
        ).first()
        if already:
            continue

        criteria = json.loads(ach.criteria) if isinstance(ach.criteria, str) else ach.criteria
        ctype = criteria.get('type')

        earned = False
        if ctype == 'tasks_completed':
            earned = (agent.tasks_completed or 0) >= criteria.get('min', 1)
        elif ctype == 'tasks_completed_min_failures':
            earned = (agent.tasks_completed or 0) >= criteria.get('min', 5)
            if earned and criteria.get('failures_max', 0) == 0:
                earned = (agent.tasks_failed or 0) == 0
        elif ctype == 'speed_demon':
            # Check if any task was completed in under max_seconds
            max_sec = criteria.get('max_seconds', 60)
            fast_tasks = Task.query.filter(
                Task.status == 'completed',
                Task.claimed_by == agent_name,
                Task.created_at.isnot(None),
                Task.completed_at.isnot(None),
            ).all()
            for t in fast_tasks:
                created = t.created_at
                completed = t.completed_at
                if created.tzinfo:
                    created = created.replace(tzinfo=None)
                if completed.tzinfo:
                    completed = completed.replace(tzinfo=None)
                delta = (completed - created).total_seconds()
                if delta >= 0 and delta < max_sec:
                    earned = True
                    break
        elif ctype == 'gold_standard':
            min_reviews = criteria.get('min_reviews', 10)
            pass_rate = criteria.get('pass_rate', 1.0)
            total_reviews = (agent.tasks_completed or 0) + (agent.tasks_review_rejected or 0)
            if total_reviews >= min_reviews:
                rate = (agent.tasks_completed or 0) / total_reviews
                earned = rate >= pass_rate
        elif ctype == 'phoenix':
            earned = (agent.tasks_completed or 0) >= criteria.get('min_completed', 1) and \
                     (agent.tasks_failed or 0) >= criteria.get('min_failed', 1)
        elif ctype == 'high_complexity':
            min_count = criteria.get('min_count', 5)
            min_complexity = criteria.get('min_complexity', 4)
            count = Task.query.filter(
                Task.status == 'completed',
                Task.claimed_by == agent_name,
                Task.complexity >= min_complexity,
            ).count()
            earned = count >= min_count
        elif ctype == 'reviews_count':
            min_count = criteria.get('min_count', 10)
            count = Review.query.filter_by(reviewer=agent_name).count()
            earned = count >= min_count

        if earned:
            badge = AgentBadge(agent_name=agent_name, badge_id=ach.id)
            db.session.add(badge)
            db.session.flush()
            _log_event(None, 'badge_earned', agent=agent_name, details={
                'badge_id': ach.id,
                'badge_name': ach.name,
                'badge_icon': ach.icon,
            })
            newly_earned.append({
                'badge_id': ach.id,
                'badge_name': ach.name,
                'badge_icon': ach.icon,
                'description': ach.description,
                'earned_at': badge.earned_at.isoformat() if badge.earned_at else None,
            })

    if newly_earned:
        db.session.commit()
    return newly_earned


# ── XP Endpoints (Task #9) ──────────────────

@api_bp.route('/agents/<name>/xp', methods=['GET'])
def agent_xp(name):
    """Return XP, level, streak, and recent XP gains for an agent."""
    agent = Agent.query.get_or_404(name)
    recent = _get_recent_xp_gains(name)
    return jsonify({
        'agent': agent.name,
        'display_name': agent.display_name,
        'xp': agent.xp or 0,
        'level': agent.level or 1,
        'level_name': agent.level_name,
        'streak': agent.streak or 0,
        'recent_xp_gains': recent,
    })


@api_bp.route('/agents/xp/leaderboard', methods=['POST'])
def xp_leaderboard():
    """Return all agents sorted by XP descending."""
    agents = Agent.query.order_by(Agent.xp.desc()).all()
    return jsonify({
        'leaderboard': [{
            'name': a.name,
            'display_name': a.display_name,
            'xp': a.xp or 0,
            'level': a.level or 1,
            'level_name': a.level_name,
            'streak': a.streak or 0,
            'tasks_completed': a.tasks_completed or 0,
        } for a in agents],
    })


# ── Achievement Badge Endpoints (Task #10) ──

@api_bp.route('/achievements', methods=['GET'])
def list_achievements():
    """List all badge definitions."""
    achievements = Achievement.query.all()
    return jsonify({
        'achievements': [a.to_dict() for a in achievements],
    })


@api_bp.route('/agents/<name>/badges', methods=['GET'])
def agent_badges(name):
    """List agent's earned badges with timestamps."""
    agent = Agent.query.get_or_404(name)
    badges = AgentBadge.query.filter_by(agent_name=name).order_by(
        AgentBadge.earned_at.desc()
    ).all()
    return jsonify({
        'agent': name,
        'badges': [b.to_dict() for b in badges],
    })


@api_bp.route('/agents/<name>/check-badges', methods=['POST'])
def retro_check_badges(name):
    """Manually trigger badge check for retroactive awarding."""
    agent = Agent.query.get_or_404(name)
    newly_earned = _check_badges(name)
    return jsonify({
        'agent': name,
        'new_badges': newly_earned,
    })


# ── Agent Discovery Endpoint (Task #17) ─────

@api_bp.route('/agents/discover', methods=['GET'])
def discover_agents():
    """Discover agents by skills, role, reputation, and availability."""
    skills_param = request.args.get('skills', '')
    role = request.args.get('role')
    min_reputation = request.args.get('min_reputation', type=int)
    min_available = request.args.get('min_available', '').lower() == 'true'

    required_skills = [s.strip().lower() for s in skills_param.split(',') if s.strip()]

    agents = Agent.query.all()
    results = []

    for agent in agents:
        # Role filter
        if role and agent.role != role:
            continue

        # Reputation filter
        if min_reputation is not None and (agent.reputation_score or 0) < min_reputation:
            continue

        # Availability filter
        if min_available and agent.status != 'idle':
            continue

        # Skill matching: agent must have ALL listed skills
        agent_skills = [s.strip().lower() for s in (agent.skills or '').split(',') if s.strip()]
        if required_skills:
            if not all(skill in agent_skills for skill in required_skills):
                continue

        skill_match_count = len([s for s in required_skills if s in agent_skills])
        results.append({
            'name': agent.name,
            'display_name': agent.display_name,
            'role': agent.role,
            'skills': agent.skills_list,
            'reputation_score': agent.reputation_score or 0,
            'status': agent.status,
            'skill_match_count': skill_match_count,
        })

    # Sort by skill match count DESC, then reputation DESC
    results.sort(key=lambda r: (-r['skill_match_count'], -r['reputation_score']))
    return jsonify({
        'agents': results,
        'total': len(results),
    })


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
    # Emit updates for all timed-out tasks
    for task in stuck:
        if task.status == 'timed_out':
            _emit_task_update(task)
            if task.claimed_by:
                agent = db.session.get(Agent, task.claimed_by)
                if agent:
                    _emit_agent_update(agent)
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
        # Resolve any tasks blocked by this completed task
        _resolve_dependencies(task)
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
    _emit_task_update(task)
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

            # Complexity-reputation matching:
            # Low-complexity tasks (1-2): prefer lower-rep agents
            # High-complexity tasks (4-5): prefer higher-rep agents
            # Mid-complexity (3): neutral
            task_complexity = task.complexity or 3
            normalized_rep = (agent.reputation_score - 50.0) / 50.0  # -1.0 to 1.0
            if task_complexity <= 2:
                # Prefer low-rep agents: bonus for negative normalized_rep
                score += (1.0 - normalized_rep) * 1.5
            elif task_complexity >= 4:
                # Prefer high-rep agents: bonus for positive normalized_rep
                score += (1.0 + normalized_rep) * 1.5
            # For complexity 3, no adjustment

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
    # Emit updates for all assigned tasks
    for task in pending_tasks:
        if task.status == 'assigned':
            _emit_task_update(task)
    return jsonify({
        'assigned': assigned_count,
        'skipped': skipped_count,
        'total': len(pending_tasks),
        'results': results,
    })


# ── Overseer: auto-triage ──────────────────────────────

@api_bp.route('/overseer/auto-triage', methods=['POST'])
def auto_triage():
    """Auto-triage tasks in triage status using rules:
    - complexity <= 2 AND matching agent skills → auto-accept to pending
    - complexity >= 4 → auto-escalate to needs_human
    Log all triage decisions.
    """
    now = datetime.now(timezone.utc)
    accepted_count = 0
    escalated_count = 0
    skipped_count = 0
    results = []

    triage_tasks = Task.query.filter_by(status='triage').order_by(
        Task.priority, Task.created_at
    ).all()

    agents = Agent.query.filter(Agent.status != 'offline').all()

    for task in triage_tasks:
        task_tags = {t.strip().lower() for t in task.tags.split(',') if t.strip()}
        complexity = task.complexity or 3

        # Rule: complexity >= 4 → auto-escalate to needs_human
        if complexity >= 4:
            ok, err = _validate_transition(task, 'needs_human')
            if ok:
                task.status = 'needs_human'
                task.updated_at = now
                _log_event(task.id, 'auto_triage', details={
                    'from_status': 'triage',
                    'to_status': 'needs_human',
                    'reason': f'Complexity {complexity} >= 4, auto-escalated',
                })
                escalated_count += 1
                results.append({
                    'task_id': task.id,
                    'action': 'escalated',
                    'to_status': 'needs_human',
                    'reason': 'high_complexity',
                })
            else:
                skipped_count += 1
                results.append({
                    'task_id': task.id,
                    'action': 'skipped',
                    'reason': f'Cannot transition to needs_human: {err}',
                })
            continue

        # Rule: complexity <= 2 AND matching agent skills → auto-accept to pending
        if complexity <= 2:
            matching_skills = False
            for agent in agents:
                agent_skills = {s.strip().lower() for s in agent.skills.split(',') if s.strip()}
                if task_tags & agent_skills:
                    matching_skills = True
                    break

            if matching_skills:
                ok, err = _validate_transition(task, 'pending')
                if ok:
                    task.status = 'pending'
                    task.updated_at = now
                    _log_event(task.id, 'auto_triage', details={
                        'from_status': 'triage',
                        'to_status': 'pending',
                        'reason': f'Complexity {complexity} <= 2, matching agent skills found',
                    })
                    accepted_count += 1
                    results.append({
                        'task_id': task.id,
                        'action': 'accepted',
                        'to_status': 'pending',
                        'reason': 'low_complexity_with_skills',
                    })
                else:
                    skipped_count += 1
                    results.append({
                        'task_id': task.id,
                        'action': 'skipped',
                        'reason': f'Cannot transition to pending: {err}',
                    })
            else:
                skipped_count += 1
                results.append({
                    'task_id': task.id,
                    'action': 'skipped',
                    'reason': 'low_complexity_no_matching_agent_skills',
                })
        else:
            skipped_count += 1
            results.append({
                'task_id': task.id,
                'action': 'skipped',
                'reason': f'Complexity {complexity} not in auto-triage rules',
            })

    db.session.commit()
    for task in triage_tasks:
        if task.status != 'triage':
            _emit_task_update(task)
    return jsonify({
        'accepted': accepted_count,
        'escalated': escalated_count,
        'skipped': skipped_count,
        'total': len(triage_tasks),
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
    # Emit updates for all reclaimed tasks
    for task in timed_out_tasks:
        _emit_task_update(task)
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


# ── Telemetry ───────────────────────────────

@api_bp.route('/telemetry', methods=['GET'])
def telemetry():
    """Return real-time telemetry data for the dashboard panels."""
    from datetime import timedelta
    from sqlalchemy import func, text as sa_text

    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)

    # 1. Throughput: task_created events in last hour, grouped by 5-min buckets
    # SQLite: floor epoch seconds / 300 to create 5-min buckets
    bucket_expr = func.floor(
        func.strftime('%s', EventLog.created_at) / 300
    ) * 300

    throughput_rows = db.session.query(
        bucket_expr.label('bucket'),
        func.count(EventLog.id).label('count')
    ).filter(
        EventLog.event_type == 'task_created',
        EventLog.created_at >= one_hour_ago,
    ).group_by(
        bucket_expr
    ).order_by(
        bucket_expr
    ).all()

    throughput = []
    for row in throughput_rows:
        bucket_ts = datetime.fromtimestamp(row.bucket, tz=timezone.utc)
        throughput.append({
            'bucket': bucket_ts.isoformat(),
            'count': row.count,
        })

    # If no throughput data, return empty array
    if not throughput:
        throughput = []

    # 2. Success rate: completed / (completed + failed + dead)
    completed_count = Task.query.filter_by(status='completed').count()
    failed_count = Task.query.filter_by(status='failed').count()
    dead_count = Task.query.filter_by(status='dead').count()
    total_ended = completed_count + failed_count + dead_count
    success_rate = round((completed_count / total_ended * 100) if total_ended > 0 else 0.0, 1)

    # 3. Avg completion time: from completed tasks' created_at -> completed_at delta
    completed_tasks = Task.query.filter(
        Task.status == 'completed',
        Task.created_at.isnot(None),
        Task.completed_at.isnot(None),
    ).all()

    avg_seconds = 0.0
    if completed_tasks:
        total_seconds = 0.0
        count = 0
        for t in completed_tasks:
            created = t.created_at
            completed = t.completed_at
            if created and completed:
                if created.tzinfo:
                    created = created.replace(tzinfo=None)
                if completed.tzinfo:
                    completed = completed.replace(tzinfo=None)
                delta = (completed - created).total_seconds()
                if delta >= 0:
                    total_seconds += delta
                    count += 1
        if count > 0:
            avg_seconds = round(total_seconds / count, 1)

    # 4. Agent utilization: active tasks / max_concurrent for each agent
    agents = Agent.query.all()
    agent_utilization = []
    for agent in agents:
        active_tasks = Task.query.filter(
            Task.claimed_by == agent.name,
            Task.status.in_(['claimed', 'in_progress'])
        ).count()
        max_conc = agent.max_concurrent or 1
        ratio = round(active_tasks / max_conc, 2)
        color = 'green' if ratio < 0.5 else ('yellow' if ratio <= 0.8 else 'red')
        agent_utilization.append({
            'name': agent.name,
            'display_name': agent.display_name,
            'active': active_tasks,
            'max_concurrent': max_conc,
            'ratio': ratio,
            'color': color,
        })

    return jsonify({
        'throughput': throughput,
        'success_rate': success_rate,
        'completed': completed_count,
        'failed': failed_count,
        'dead': dead_count,
        'avg_completion_time_seconds': avg_seconds,
        'agent_utilization': agent_utilization,
    })


# ── Templates ───────────────────────────────

@api_bp.route('/templates', methods=['GET'])
def list_templates():
    """List all task templates."""
    templates = TaskTemplate.query.all()
    return jsonify({
        'templates': [t.to_dict() for t in templates],
        'total': len(templates),
    })


@api_bp.route('/templates/<name>/create', methods=['POST'])
def create_from_template(name):
    """Create tasks from a template, substituting variables in step titles/descriptions/tags."""
    template = TaskTemplate.query.filter_by(name=name).first()
    if not template:
        return jsonify({'error': f'Template "{name}" not found'}), 404

    data = request.get_json() or {}
    variables = data.get('variables', {})
    project = data.get('project', 'general')

    steps = template.get_steps()
    if not steps:
        return jsonify({'error': 'Template has no steps'}), 400

    created_tasks = []
    step_id_map = {}  # step index -> task id for dependency tracking

    for i, step in enumerate(steps):
        title_template = step.get('title', '')
        desc_template = step.get('description', '')
        tags_template = step.get('tags', '')

        # Variable substitution
        title = _substitute_vars(title_template, variables)
        description = _substitute_vars(desc_template, variables)
        tags = _substitute_vars(tags_template, variables)

        priority = step.get('priority', 3)
        reserved_for = step.get('reserved_for', None) or step.get('agent', None)

        task = Task(
            title=title,
            description=description,
            priority=priority,
            tags=tags,
            project=project,
            reserved_for=reserved_for,
        )

        # Check escalation tags
        escalate_to = _check_escalation_tags(task)
        if escalate_to:
            task.status = escalate_to

        db.session.add(task)
        db.session.flush()

        _log_event(task.id, 'task_created', details={
            'title': title,
            'tags': tags,
            'status': task.status,
            'template': name,
            'step_index': i,
        })

        step_id_map[i] = task.id
        created_tasks.append(task)

    # Set up dependencies if any step has depends_on — wire blocked_by fields
    for i, step in enumerate(steps):
        depends_on = step.get('depends_on')
        if depends_on is not None and depends_on in step_id_map:
            parent_id = step_id_map[depends_on]
            child_id = step_id_map[i]
            child_task = db.session.get(Task, child_id)
            if child_task:
                existing = child_task.get_blocking_task_ids()
                if parent_id not in existing:
                    existing.append(parent_id)
                child_task.blocked_by = ','.join(str(x) for x in existing)
                # Auto-transition to blocked if dependencies aren't met
                if not child_task.are_dependencies_met() and child_task.can_transition_to('blocked'):
                    child_task.status = 'blocked'
                child_task.updated_at = datetime.now(timezone.utc)
                _log_event(child_id, 'dependency_set', details={
                    'depends_on_task_id': parent_id,
                    'blocked_by': child_task.blocked_by,
                    'from_template': name,
                })

    db.session.commit()
    return jsonify({
        'template': name,
        'created': len(created_tasks),
        'tasks': [t.to_dict() for t in created_tasks],
    }), 201


# ── Handoff Endpoints ───────────────────────

@api_bp.route('/tasks/<int:task_id>/handoff', methods=['POST'])
def create_handoff(task_id):
    """Create a handoff request for a task."""
    task = Task.query.get_or_404(task_id)
    data = request.get_json() or {}

    try:
        schema = HandoffRequestSchema(**data)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    # Auto-infer from_agent: schema > task.claimed_by > auth header
    from_agent = schema.from_agent or task.claimed_by or task.assigned_to or _get_email()

    handoff = HandoffRequest(
        task_id=task.id,
        from_agent=from_agent,
        to_agent=schema.to_agent,
        message=schema.message,
        status='pending',
    )
    db.session.add(handoff)
    db.session.flush()  # Get the handoff.id before logging

    _log_event(task.id, 'handoff_requested', agent=from_agent, details={
        'to_agent': schema.to_agent,
        'message': schema.message,
        'handoff_request_id': handoff.id,
    })

    db.session.commit()
    return jsonify(handoff.to_dict()), 201


@api_bp.route('/tasks/<int:task_id>/handoff/<int:request_id>/accept', methods=['POST'])
def accept_handoff(task_id, request_id):
    """Accept a handoff request — reassign the task to the requesting agent."""
    task = Task.query.get_or_404(task_id)
    handoff = HandoffRequest.query.get_or_404(request_id)

    if handoff.task_id != task.id:
        return jsonify({'error': 'Handoff request does not belong to this task'}), 400

    if handoff.status != 'pending':
        return jsonify({'error': f'Handoff request is already {handoff.status}'}), 409

    handoff.status = 'accepted'

    # Reassign the task to the new agent
    now = datetime.now(timezone.utc)
    task.assigned_to = handoff.to_agent
    task.claimed_by = None
    task.lease_expires_at = None
    task.heartbeat_at = None
    task.updated_at = now

    # Release from current locked state so new agent can claim
    release_states = frozenset(['claimed', 'in_progress', 'submitted', 'in_review', 'needs_revision'])
    if task.status in release_states:
        task.status = 'pending'
        _log_event(task.id, 'released', agent=handoff.from_agent, details={
            'reason': f'Handoff to {handoff.to_agent}',
        })

    # Now assign to new agent
    if task.can_transition_to('assigned'):
        task.status = 'assigned'

    # Create agent for to_agent if needed
    _get_agent_or_create(handoff.to_agent)
    db.session.flush()
    _sync_agent_status(handoff.from_agent, now)
    _sync_agent_status(handoff.to_agent, now)

    _log_event(task.id, 'handoff_accepted', agent=handoff.to_agent, details={
        'from_agent': handoff.from_agent,
        'to_agent': handoff.to_agent,
        'handoff_request_id': handoff.id,
    })

    db.session.commit()
    _emit_task_update(task)
    from_agent = db.session.get(Agent, handoff.from_agent)
    if from_agent:
        _emit_agent_update(from_agent)
    to_agent = db.session.get(Agent, handoff.to_agent)
    if to_agent:
        _emit_agent_update(to_agent)
    return jsonify({
        'handoff': handoff.to_dict(),
        'task': task.to_dict(),
    })


@api_bp.route('/tasks/<int:task_id>/handoff/<int:request_id>/reject', methods=['POST'])
def reject_handoff(task_id, request_id):
    """Reject a handoff request."""
    task = Task.query.get_or_404(task_id)
    handoff = HandoffRequest.query.get_or_404(request_id)

    if handoff.task_id != task.id:
        return jsonify({'error': 'Handoff request does not belong to this task'}), 400

    if handoff.status != 'pending':
        return jsonify({'error': f'Handoff request is already {handoff.status}'}), 409

    handoff.status = 'rejected'

    data = request.get_json() or {}
    reason = data.get('reason', '')

    _log_event(task.id, 'handoff_rejected', agent=handoff.from_agent, details={
        'from_agent': handoff.from_agent,
        'to_agent': handoff.to_agent,
        'reason': reason,
        'handoff_request_id': handoff.id,
    })

    db.session.commit()
    return jsonify(handoff.to_dict())


# ── Handoff History Endpoints (Task #8) ─────

@api_bp.route('/tasks/<int:task_id>/handoffs', methods=['GET'])
def task_handoffs(task_id):
    """Return handoff history for a specific task."""
    task = Task.query.get_or_404(task_id)
    handoffs = HandoffRequest.query.filter_by(task_id=task.id).order_by(
        HandoffRequest.created_at.desc()
    ).all()
    return jsonify({
        'task_id': task.id,
        'handoffs': [h.to_dict() for h in handoffs],
        'total': len(handoffs),
    })


@api_bp.route('/agents/<name>/handoffs', methods=['GET'])
def agent_handoffs(name):
    """Return handoff history for a specific agent, as either sender or receiver."""
    agent = Agent.query.get_or_404(name)
    handoffs = HandoffRequest.query.filter(
        (HandoffRequest.from_agent == name) | (HandoffRequest.to_agent == name)
    ).order_by(HandoffRequest.created_at.desc()).all()
    return jsonify({
        'agent': name,
        'handoffs': [h.to_dict() for h in handoffs],
        'total': len(handoffs),
    })


def _substitute_vars(text, variables):
    """Replace {var_name} placeholders with values from the variables dict."""
    if not text:
        return text
    for key, value in variables.items():
        text = text.replace(f'{{{key}}}', str(value))
    return text
