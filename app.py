# app.py
import json
from flask import Flask, request, jsonify, render_template
from models import db, Task, Agent, Review, EventLog, Achievement
from config import Config, TestingConfig
from ws import socketio
from datetime import datetime, timezone


def create_app(testing=False):
    app = Flask(__name__)
    app.config.from_object(TestingConfig if testing else Config)

    db.init_app(app)
    socketio.init_app(app)

    with app.app_context():
        db.create_all()
        # Enable WAL mode for concurrent reads + busy timeout for write contention
        db.session.execute(db.text('PRAGMA journal_mode=WAL'))
        db.session.execute(db.text('PRAGMA busy_timeout=5000'))
        db.session.commit()

        # Seed achievement badges (Task #10)
        _seed_achievements()

    # Auth middleware — check X-ExeDev-Email header
    @app.before_request
    def check_auth():
        # Bypass auth for health endpoint and when testing
        if request.path == '/health':
            return None
        if app.config.get('TESTING', False):
            return None

        email = request.headers.get('X-ExeDev-Email')
        if not email:
            return jsonify({'error': 'Unauthorized: X-ExeDev-Email header required'}), 401

    # Register blueprints
    from api import api_bp
    app.register_blueprint(api_bp, url_prefix='/api')

    # Health endpoint
    @app.route('/health')
    def health():
        return {'status': 'ok'}

    # Dashboard routes
    def _agent_label_map(agents=None):
        if agents is None:
            agents = Agent.query.all()
        return {agent.name: agent.display_name for agent in agents}

    @app.route('/')
    def dashboard():
        tasks_query = Task.query.order_by(Task.priority, Task.created_at.desc())
        
        # Apply filters from query params
        status_filter = request.args.get('status')
        agent_filter = request.args.get('agent')
        project_filter = request.args.get('project')
        priority_filter = request.args.get('priority')
        
        if status_filter:
            tasks_query = tasks_query.filter(Task.status == status_filter)
        if agent_filter:
            tasks_query = tasks_query.filter(
                (Task.assigned_to == agent_filter) | (Task.claimed_by == agent_filter)
            )
        if project_filter:
            tasks_query = tasks_query.filter(Task.project == project_filter)
        if priority_filter:
            try:
                tasks_query = tasks_query.filter(Task.priority == int(priority_filter))
            except (ValueError, TypeError):
                pass
        
        tasks = tasks_query.all()
        agents = Agent.query.all()
        agent_labels = _agent_label_map(agents)
        
        # Collect unique statuses and projects for filter dropdowns
        all_statuses = ['pending', 'assigned', 'claimed', 'in_progress', 'submitted',
                        'in_review', 'completed', 'failed', 'blocked', 'needs_human',
                        'needs_vesper', 'needs_revision', 'timed_out', 'released', 'dead',
                        'triage']
        all_projects = db.session.query(Task.project).distinct().filter(Task.project.isnot(None), Task.project != '').order_by(Task.project).all()
        all_projects = [p[0] for p in all_projects]
        
        stats = {}
        for s in all_statuses:
            count = Task.query.filter_by(status=s).count()
            if count > 0:
                stats[s] = count
        # Compute agent load and heartbeat ages
        active_statuses = ['assigned', 'claimed', 'in_progress', 'submitted', 'in_review', 'needs_revision']
        agent_loads = {}
        agent_heartbeat_ages = {}
        for agent in agents:
            load = Task.query.filter(
                Task.status.in_(active_statuses),
                (Task.assigned_to == agent.name) | (Task.claimed_by == agent.name)
            ).count()
            agent_loads[agent.name] = load
            # Compute heartbeat age string
            if agent.last_heartbeat:
                hb = agent.last_heartbeat
                if hb.tzinfo is not None:
                    hb = hb.replace(tzinfo=None)
                now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
                delta = now_naive - hb
                seconds = int(delta.total_seconds())
                if seconds < 60:
                    agent_heartbeat_ages[agent.name] = f'{seconds}s ago'
                elif seconds < 3600:
                    agent_heartbeat_ages[agent.name] = f'{seconds // 60}m ago'
                elif seconds < 86400:
                    agent_heartbeat_ages[agent.name] = f'{seconds // 3600}h ago'
                else:
                    agent_heartbeat_ages[agent.name] = f'{seconds // 86400}d ago'
            else:
                agent_heartbeat_ages[agent.name] = 'Never'
        # Recent events (last 20)
        events = EventLog.query.order_by(EventLog.created_at.desc()).limit(20).all()
        return render_template('dashboard.html', tasks=tasks, agents=agents, stats=stats,
                               agent_loads=agent_loads, agent_heartbeat_ages=agent_heartbeat_ages,
                               events=events, all_statuses=all_statuses,
                               all_projects=all_projects, agent_labels=agent_labels)

    @app.route('/task/<int:task_id>')
    def task_detail(task_id):
        task = Task.query.get_or_404(task_id)
        reviews = Review.query.filter_by(task_id=task_id).all()
        events = EventLog.query.filter_by(task_id=task_id).order_by(EventLog.created_at).all()
        return render_template('task.html', task=task, reviews=reviews, events=events,
                               agent_labels=_agent_label_map())

    @app.route('/agent/<name>')
    def agent_detail(name):
        agent = Agent.query.get_or_404(name)
        # Tasks assigned or claimed by this agent
        tasks = Task.query.filter(
            (Task.assigned_to == name) | (Task.claimed_by == name)
        ).order_by(Task.created_at.desc()).all()
        # Current active task
        active_statuses = ['assigned', 'claimed', 'in_progress', 'submitted', 'in_review', 'needs_revision']
        current_task = Task.query.filter(
            Task.status.in_(active_statuses),
            (Task.assigned_to == name) | (Task.claimed_by == name)
        ).first()
        # Agent load
        agent_load = Task.query.filter(
            Task.status.in_(active_statuses),
            (Task.assigned_to == name) | (Task.claimed_by == name)
        ).count()
        # Events for this agent
        events = EventLog.query.filter_by(agent=name).order_by(EventLog.created_at.desc()).limit(50).all()
        return render_template('agent.html', agent=agent, tasks=tasks, current_task=current_task,
                               agent_load=agent_load, events=events)

    # ── Task Timeline ──────────────────────────────────────────
    @app.route('/task/<int:task_id>/timeline')
    def task_timeline(task_id):
        task = Task.query.get_or_404(task_id)
        events = EventLog.query.filter_by(task_id=task_id).order_by(EventLog.created_at).all()
        reviews = Review.query.filter_by(task_id=task_id).all()
        return render_template('timeline.html', task=task, events=events, reviews=reviews)

    # ── Agent Edit Page ───────────────────────────────────────
    @app.route('/agent/<name>/edit')
    def agent_edit(name):
        agent = Agent.query.get_or_404(name)
        return render_template('agent_edit.html', agent=agent)

    # ── Task Audit Page ───────────────────────────────────────
    @app.route('/task/<int:task_id>/audit')
    def task_audit(task_id):
        task = Task.query.get_or_404(task_id)
        # Build audit data from EventLog entries (matching API format)
        events = EventLog.query.filter_by(task_id=task_id).order_by(EventLog.created_at).all()
        lifecycle = []
        current_claimed_by = None
        total_xp_awarded = 0
        agents_involved = set()
        handoffs_count = 0

        for evt in events:
            import json as _json
            details = _json.loads(evt.details) if evt.details else {}

            # Status reconstruction
            from_status = details.get('from_status')
            to_status = details.get('to_status')
            if not from_status and not to_status:
                if evt.event_type == 'task_created':
                    to_status = details.get('status', 'pending')

            # Claimed_by reconstruction
            claimed_by_before = current_claimed_by
            if evt.event_type == 'claimed':
                current_claimed_by = details.get('claimed_by', evt.agent)
            elif evt.event_type in ('released', 'lease_expired'):
                current_claimed_by = None
            elif evt.event_type == 'handoff_accepted':
                current_claimed_by = None
            elif details.get('claimed_by') is None and 'claimed_by' in details:
                current_claimed_by = None
            elif details.get('claimed_by'):
                current_claimed_by = details['claimed_by']
            elif evt.event_type in ('completed', 'failed', 'timed_out', 'dead'):
                current_claimed_by = None
            claimed_by_after = current_claimed_by

            # XP extraction
            xp_awarded = None
            if 'xp_gained' in details:
                xp_awarded = details['xp_gained']
                total_xp_awarded += int(xp_awarded)
            elif evt.event_type in ('xp_gained',) and 'xp_gained' in details:
                xp_awarded = details['xp_gained']
                total_xp_awarded += int(xp_awarded)
            elif 'xp_awarded' in details:
                xp_awarded = details['xp_awarded']
                total_xp_awarded += int(xp_awarded)

            handoff_id = details.get('handoff_request_id') or details.get('handoff_id')
            if evt.event_type == 'handoff_requested' and handoff_id is not None:
                handoffs_count += 1

            if evt.agent:
                agents_involved.add(evt.agent)

            lifecycle.append({
                'timestamp': evt.created_at.strftime('%Y-%m-%d %H:%M:%S') if evt.created_at else None,
                'event_type': evt.event_type,
                'actor': evt.agent,
                'status_before': from_status,
                'status_after': to_status,
                'claimed_by_before': claimed_by_before,
                'claimed_by_after': claimed_by_after,
                'xp_awarded': xp_awarded,
                'handoff_id': handoff_id,
                'details': details,
            })

        audit_data = {
            'task_id': task.id,
            'task_title': task.title,
            'lifecycle': lifecycle,
            'summary': {
                'total_events': len(lifecycle),
                'total_xp_awarded': total_xp_awarded,
                'agents_involved': sorted(agents_involved) if agents_involved else [],
                'handoffs': handoffs_count,
                'final_status': task.status,
            },
        }
        return render_template('task_audit.html', task=task, audit=audit_data)

    # ── New: GET /tasks (full task list) ───────────────────────
    @app.route('/tasks')
    def tasks_page():
        tasks_query = Task.query.order_by(Task.priority, Task.created_at.desc())
        # Same filters as dashboard
        status_filter = request.args.get('status')
        agent_filter = request.args.get('agent')
        project_filter = request.args.get('project')
        priority_filter = request.args.get('priority')

        if status_filter:
            tasks_query = tasks_query.filter(Task.status == status_filter)
        if agent_filter:
            tasks_query = tasks_query.filter(
                (Task.assigned_to == agent_filter) | (Task.claimed_by == agent_filter)
            )
        if project_filter:
            tasks_query = tasks_query.filter(Task.project == project_filter)
        if priority_filter:
            try:
                tasks_query = tasks_query.filter(Task.priority == int(priority_filter))
            except (ValueError, TypeError):
                pass

        tasks = tasks_query.all()
        agents = Agent.query.all()
        agent_labels = _agent_label_map(agents)
        all_statuses = ['pending', 'assigned', 'claimed', 'in_progress', 'submitted',
                        'in_review', 'completed', 'failed', 'blocked', 'needs_human',
                        'needs_vesper', 'needs_revision', 'timed_out', 'released', 'dead',
                        'triage']
        all_projects = db.session.query(Task.project).distinct().filter(Task.project.isnot(None), Task.project != '').order_by(Task.project).all()
        all_projects = [p[0] for p in all_projects]
        return render_template('tasks.html', tasks=tasks, agents=agents,
                               all_statuses=all_statuses, all_projects=all_projects,
                               agent_labels=agent_labels)

    # ── New: GET /agents (agent grid) ─────────────────────────
    @app.route('/agents')
    def agents_page():
        agents = Agent.query.all()
        # Compute heartbeat ages
        active_statuses_list = ['assigned', 'claimed', 'in_progress', 'submitted', 'in_review', 'needs_revision']
        agent_loads = {}
        agent_heartbeat_ages = {}
        for agent in agents:
            load = Task.query.filter(
                Task.status.in_(active_statuses_list),
                (Task.assigned_to == agent.name) | (Task.claimed_by == agent.name)
            ).count()
            agent_loads[agent.name] = load
            if agent.last_heartbeat:
                hb = agent.last_heartbeat
                if hb.tzinfo is not None:
                    hb = hb.replace(tzinfo=None)
                now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
                delta = now_naive - hb
                seconds = int(delta.total_seconds())
                if seconds < 60:
                    agent_heartbeat_ages[agent.name] = f'{seconds}s ago'
                elif seconds < 3600:
                    agent_heartbeat_ages[agent.name] = f'{seconds // 60}m ago'
                elif seconds < 86400:
                    agent_heartbeat_ages[agent.name] = f'{seconds // 3600}h ago'
                else:
                    agent_heartbeat_ages[agent.name] = f'{seconds // 86400}d ago'
            else:
                agent_heartbeat_ages[agent.name] = 'Never'
        return render_template('agents.html', agents=agents, agent_loads=agent_loads,
                               agent_heartbeat_ages=agent_heartbeat_ages)

    # ── New: GET /stats (overview statistics) ─────────────────
    @app.route('/stats')
    def stats_page():
        all_statuses_list = ['pending', 'assigned', 'claimed', 'in_progress', 'submitted',
                             'in_review', 'completed', 'failed', 'blocked', 'needs_human',
                             'needs_vesper', 'needs_revision', 'timed_out', 'released', 'dead',
                             'triage']
        stats_data = {}
        for s in all_statuses_list:
            count = Task.query.filter_by(status=s).count()
            if count > 0:
                stats_data[s] = count

        agents = Agent.query.all()
        # Agent performance
        agent_perf = []
        for agent in agents:
            total = agent.tasks_completed + agent.tasks_failed + agent.tasks_timed_out + agent.tasks_review_rejected
            agent_perf.append({
                'name': agent.name,
                'display_name': agent.display_name,
                'completed': agent.tasks_completed,
                'failed': agent.tasks_failed,
                'timed_out': agent.tasks_timed_out,
                'review_rejected': agent.tasks_review_rejected,
                'rep': agent.reputation_score,
                'xp': agent.xp,
                'level': agent.level,
                'total': total,
                'status': agent.status,
            })

        # Recent events (last 30)
        events = EventLog.query.order_by(EventLog.created_at.desc()).limit(30).all()

        # System health
        total_tasks = Task.query.count()
        active_count = Task.query.filter(
            Task.status.in_(['assigned', 'claimed', 'in_progress', 'submitted', 'in_review', 'needs_revision'])
        ).count()
        total_agents = Agent.query.count()
        online_agents = Agent.query.filter(Agent.status != 'offline').count()
        total_xp = sum(a.xp for a in agents)
        highest_xp = max((a.xp for a in agents), default=0)
        highest_level = max((a.level for a in agents), default=1)

        return render_template('stats.html', stats=stats_data, agent_perf=agent_perf,
                               events=events, total_tasks=total_tasks, active_count=active_count,
                               total_agents=total_agents, online_agents=online_agents,
                               total_xp=total_xp, highest_xp=highest_xp, highest_level=highest_level)

    return app


def _seed_achievements():
    """Seed the achievement badge definitions."""
    badges = [
        ('🎯', 'First Mission', 'Complete your first task', {'type': 'tasks_completed', 'min': 1}),
        ('🔥', 'On Fire', 'Complete 5 tasks with no failures in last 5', {'type': 'tasks_completed_min_failures', 'min': 5, 'failures_max': 0}),
        ('⚡', 'Speed Demon', 'Complete any task in under 60 seconds', {'type': 'speed_demon', 'max_seconds': 60}),
        ('🛡️', 'Ironclad', 'Complete 10 tasks with zero failures', {'type': 'tasks_completed_min_failures', 'min': 10, 'failures_max': 0}),
        ('🌟', 'Gold Standard', 'Perfect review pass rate across 10+ reviews', {'type': 'gold_standard', 'min_reviews': 10, 'pass_rate': 1.0}),
        ('🔄', 'Phoenix', 'Come back from a failure to complete a task', {'type': 'phoenix', 'min_completed': 1, 'min_failed': 1}),
        ('🏗️', 'Architect', 'Complete 5+ tasks with complexity >= 4', {'type': 'high_complexity', 'min_count': 5, 'min_complexity': 4}),
        ('👀', 'Eagle Eye', 'Review 10+ tasks as a reviewer', {'type': 'reviews_count', 'min_count': 10}),
    ]
    for icon, name, description, criteria in badges:
        existing = Achievement.query.filter_by(name=name).first()
        if not existing:
            db.session.add(Achievement(
                name=name,
                description=description,
                icon=icon,
                criteria=json.dumps(criteria),
            ))
    db.session.commit()
