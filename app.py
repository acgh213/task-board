# app.py
from flask import Flask, request, jsonify, render_template
from models import db, Task, Agent, Review, EventLog
from config import Config, TestingConfig


def create_app(testing=False):
    app = Flask(__name__)
    app.config.from_object(TestingConfig if testing else Config)

    db.init_app(app)

    with app.app_context():
        db.create_all()
        # Enable WAL mode for concurrent reads + busy timeout for write contention
        db.session.execute(db.text('PRAGMA journal_mode=WAL'))
        db.session.execute(db.text('PRAGMA busy_timeout=5000'))
        db.session.commit()

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
    @app.route('/')
    def dashboard():
        tasks_query = Task.query.order_by(Task.priority, Task.created_at.desc())
        
        # Apply filters from query params
        status_filter = request.args.get('status')
        agent_filter = request.args.get('agent')
        project_filter = request.args.get('project')
        
        if status_filter:
            tasks_query = tasks_query.filter(Task.status == status_filter)
        if agent_filter:
            tasks_query = tasks_query.filter(
                (Task.assigned_to == agent_filter) | (Task.claimed_by == agent_filter)
            )
        if project_filter:
            tasks_query = tasks_query.filter(Task.project == project_filter)
        
        tasks = tasks_query.all()
        agents = Agent.query.all()
        
        # Collect unique statuses and projects for filter dropdowns
        all_statuses = ['pending', 'assigned', 'claimed', 'in_progress', 'submitted',
                        'in_review', 'completed', 'failed', 'blocked', 'needs_human',
                        'needs_vesper', 'needs_revision', 'timed_out', 'released', 'dead']
        all_projects = db.session.query(Task.project).distinct().filter(Task.project.isnot(None), Task.project != '').order_by(Task.project).all()
        all_projects = [p[0] for p in all_projects]
        
        stats = {}
        for s in all_statuses:
            count = Task.query.filter_by(status=s).count()
            if count > 0:
                stats[s] = count
        # Compute agent load: count of active (non-terminal) tasks for each agent
        active_statuses = ['assigned', 'claimed', 'in_progress', 'submitted', 'in_review', 'needs_revision']
        agent_loads = {}
        for agent in agents:
            load = Task.query.filter(
                Task.status.in_(active_statuses),
                (Task.assigned_to == agent.name) | (Task.claimed_by == agent.name)
            ).count()
            agent_loads[agent.name] = load
        # Recent events (last 20)
        events = EventLog.query.order_by(EventLog.created_at.desc()).limit(20).all()
        return render_template('dashboard.html', tasks=tasks, agents=agents, stats=stats,
                               agent_loads=agent_loads, events=events, all_statuses=all_statuses,
                               all_projects=all_projects)

    @app.route('/task/<int:task_id>')
    def task_detail(task_id):
        task = Task.query.get_or_404(task_id)
        reviews = Review.query.filter_by(task_id=task_id).all()
        events = EventLog.query.filter_by(task_id=task_id).order_by(EventLog.created_at).all()
        return render_template('task.html', task=task, reviews=reviews, events=events)

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

    return app
