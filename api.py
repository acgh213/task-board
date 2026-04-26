# api.py
from flask import Blueprint, request, jsonify
from models import db, Task, Agent

api_bp = Blueprint('api', __name__)


@api_bp.route('/tasks', methods=['GET'])
def list_tasks():
    query = Task.query
    status = request.args.get('status')
    agent = request.args.get('agent')
    project = request.args.get('project')
    tag = request.args.get('tag')

    if status:
        query = query.filter_by(status=status)
    if agent:
        query = query.filter_by(agent=agent)
    if project:
        query = query.filter_by(project=project)
    if tag:
        query = query.filter(Task.tags.contains(tag))

    total = query.count()

    # Parse pagination params with sensible defaults
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
    )
    db.session.add(task)
    db.session.commit()
    return jsonify(task.to_dict()), 201


@api_bp.route('/tasks/<int:task_id>', methods=['GET'])
def get_task(task_id):
    task = Task.query.get_or_404(task_id)
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/claim', methods=['POST'])
def claim_task(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json()
    if not data or 'agent' not in data:
        return jsonify({'error': 'agent name required'}), 400

    if task.status != 'pending':
        return jsonify({'error': f'Task is {task.status}, cannot claim'}), 409

    task.claim(data['agent'])
    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/complete', methods=['POST'])
def complete_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.status != 'claimed':
        return jsonify({'error': f'Task is {task.status}, cannot complete'}), 409

    data = request.get_json()
    if not data or 'result' not in data:
        return jsonify({'error': 'result required'}), 400

    task.complete(data['result'])

    # Update agent stats
    if task.agent:
        agent = db.session.get(Agent, task.agent)
        if agent:
            agent.tasks_completed += 1
            agent.status = 'idle'

    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/fail', methods=['POST'])
def fail_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.status != 'claimed':
        return jsonify({'error': f'Task is {task.status}, cannot fail'}), 409

    data = request.get_json()
    if not data or 'error' not in data:
        return jsonify({'error': 'error reason required'}), 400

    task.fail(data['error'])

    if task.agent:
        agent = db.session.get(Agent, task.agent)
        if agent:
            agent.tasks_failed += 1
            agent.status = 'idle'

    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>/release', methods=['POST'])
def release_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.status != 'claimed':
        return jsonify({'error': f'Task is {task.status}, cannot release'}), 409
    task.release()
    db.session.commit()
    return jsonify(task.to_dict())


@api_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    return jsonify({'deleted': task_id})


@api_bp.route('/agents', methods=['GET'])
def list_agents():
    agents = Agent.query.all()
    return jsonify({'agents': [a.to_dict() for a in agents]})


@api_bp.route('/stats', methods=['GET'])
def stats():
    total = Task.query.count()
    by_status = {}
    for status in ['pending', 'claimed', 'completed', 'failed']:
        by_status[status] = Task.query.filter_by(status=status).count()

    by_agent = {}
    for agent in Agent.query.all():
        active = Task.query.filter_by(agent=agent.name, status='claimed').count()
        by_agent[agent.name] = {
            'active': active,
            'completed': agent.tasks_completed,
            'failed': agent.tasks_failed,
        }

    return jsonify({
        'total_tasks': total,
        'by_status': by_status,
        'by_agent': by_agent,
    })
