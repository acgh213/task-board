# app.py
from flask import Flask, request, jsonify, render_template
from models import db, Task, Agent
from config import Config, TestingConfig


def create_app(testing=False):
    app = Flask(__name__)
    app.config.from_object(TestingConfig if testing else Config)

    db.init_app(app)

    with app.app_context():
        db.create_all()

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
        tasks = Task.query.order_by(Task.priority, Task.created_at.desc()).all()
        agents = Agent.query.all()
        stats = {
            'pending': Task.query.filter_by(status='pending').count(),
            'claimed': Task.query.filter_by(status='claimed').count(),
            'completed': Task.query.filter_by(status='completed').count(),
            'failed': Task.query.filter_by(status='failed').count(),
        }
        return render_template('dashboard.html', tasks=tasks, agents=agents, stats=stats)

    @app.route('/task/<int:task_id>')
    def task_detail(task_id):
        task = Task.query.get_or_404(task_id)
        return render_template('task.html', task=task)

    return app
