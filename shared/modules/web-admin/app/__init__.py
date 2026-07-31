"""
SmokePing Web Administration Interface
Flask application for managing SmokePing targets
"""

import fcntl
import os
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_caching import Cache
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect, CSRFError
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

# Initialize extensions
cache = Cache()
scheduler = BackgroundScheduler()
login_manager = LoginManager()
csrf = CSRFProtect()

# Kept module-level so the lock is held for the lifetime of the worker
_scheduler_lock_file = None

DEFAULT_SECRET_KEY = 'dev-secret-key-change-in-production'
SCHEDULER_LOCK_PATH = os.environ.get(
    'SCHEDULER_LOCK_PATH', '/tmp/web-admin-scheduler.lock'
)


def _acquire_scheduler_lock():
    """Try to acquire an exclusive file lock so only one worker runs the scheduler."""
    global _scheduler_lock_file
    try:
        lock_file = open(SCHEDULER_LOCK_PATH, 'w')
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _scheduler_lock_file = lock_file
        return True
    except OSError:
        return False


def create_app(config_name='production'):
    """Application factory pattern"""
    app = Flask(__name__)

    # Load configuration
    if config_name == 'development':
        app.config['DEBUG'] = True
        app.config['TESTING'] = False
    elif config_name == 'testing':
        app.config['DEBUG'] = False
        app.config['TESTING'] = True
    else:
        app.config['DEBUG'] = False
        app.config['TESTING'] = False

    # Common configuration -- fail fast on missing/placeholder SECRET_KEY
    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key or secret_key == DEFAULT_SECRET_KEY:
        raise RuntimeError(
            'SECRET_KEY environment variable must be set to a unique secret value. '
            'Run ./setup.sh to generate one.'
        )
    app.config['SECRET_KEY'] = secret_key
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)
    app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    # Warn (once, at startup) about the authentication configuration
    if os.environ.get('WEB_ADMIN_PASSWORD_HASH'):
        pass  # preferred configuration
    elif os.environ.get('WEB_ADMIN_PASSWORD'):
        app.logger.warning(
            'WEB_ADMIN_PASSWORD is set as plaintext. Consider setting '
            'WEB_ADMIN_PASSWORD_HASH (werkzeug pbkdf2 hash) instead.'
        )
    else:
        app.logger.error(
            'Neither WEB_ADMIN_PASSWORD_HASH nor WEB_ADMIN_PASSWORD is set: '
            'login is disabled until credentials are configured.'
        )

    # CSRF protection for all state-changing requests
    csrf.init_app(app)

    # Initialize cache
    cache.init_app(app, config={'CACHE_TYPE': 'simple', 'CACHE_DEFAULT_TIMEOUT': 86400})

    # Initialize Flask-Login
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        from app.routes.auth import User
        return User(user_id)

    # Scheduler: opt-in via ENABLE_SCHEDULER and guarded by a file lock so
    # exactly one gunicorn worker runs the background jobs.
    scheduler_enabled = os.environ.get('ENABLE_SCHEDULER', 'false').lower() in (
        '1', 'true', 'yes', 'on'
    )
    if config_name != 'testing' and scheduler_enabled and _acquire_scheduler_lock():
        scheduler.start()
        # Schedule OCA refresh daily at 2 AM
        from app.tasks import refresh_ocas_task, cleanup_cache_task
        scheduler.add_job(
            func=refresh_ocas_task,
            trigger='cron',
            hour=2,
            minute=0,
            id='refresh_ocas_daily',
            replace_existing=True
        )

        # Schedule cache cleanup daily at midnight UTC
        scheduler.add_job(
            func=cleanup_cache_task,
            trigger='cron',
            hour=0,
            minute=0,
            id='cleanup_cache_daily',
            replace_existing=True
        )
        # Shut down the scheduler when exiting the app
        atexit.register(lambda: scheduler.shutdown())
        app.logger.info('Background scheduler started in this worker')

    # Register blueprints
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.targets import targets_bp
    from app.routes.sources import sources_bp
    from app.routes.api import api_bp
    from app.routes.ai import ai_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(targets_bp, url_prefix='/targets')
    app.register_blueprint(sources_bp, url_prefix='/sources')
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(ai_bp, url_prefix='/ai')

    # Unauthenticated health endpoint (used by the container HEALTHCHECK)
    @app.route('/health')
    def health():
        from app.routes.api import config_api
        return jsonify({
            'status': 'ok',
            'app': 'web-admin',
            'config_manager_reachable': config_api.is_available(),
        })

    # Add custom Jinja2 filters
    @app.template_filter('format_category_name')
    def format_category_name(category):
        """Format category names for display"""
        # Special cases for better display
        if category == 'netflix_oca':
            return 'Netflix OCA'
        elif category == 'dns_resolvers':
            return 'DNS Resolvers'
        elif category == 'top_sites':
            return 'Top Sites'
        elif category == 'ipv6_websites':
            return 'IPv6 Websites'
        else:
            # Default transformation
            return category.replace('_', ' ').title()

    # Add login requirement to all routes except auth, health and static files
    @app.before_request
    def require_login():
        from flask_login import current_user

        # Skip auth for login page, health check and static files
        if request.endpoint and (
            request.endpoint.startswith('auth.') or
            request.endpoint in ('static', 'health')
        ):
            return

        # Allow API requests from authenticated sessions (AJAX)
        if request.endpoint and request.endpoint.startswith('api.'):
            # For API endpoints, check if it's an AJAX request from an authenticated session
            if not current_user.is_authenticated:
                # Return 401 for API requests instead of redirect
                return jsonify({'error': 'Authentication required'}), 401
            return

        if not current_user.is_authenticated:
            return redirect(url_for('auth.login', next=request.path))

    # Error handlers: JSON for API paths, HTML pages for browser routes
    def _wants_json():
        return request.path.startswith('/api')

    @app.errorhandler(404)
    def not_found(error):
        if _wants_json():
            return jsonify({'error': 'Not found'}), 404
        return render_template(
            'errors/error.html',
            code=404,
            title='Page Not Found',
            message='The page you requested does not exist.',
        ), 404

    @app.errorhandler(500)
    def internal_error(error):
        if _wants_json():
            return jsonify({'error': 'Internal server error'}), 500
        return render_template(
            'errors/error.html',
            code=500,
            title='Internal Server Error',
            message='Something went wrong. Check the container logs for details.',
        ), 500

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        if _wants_json():
            return jsonify({'error': error.description}), 400
        return render_template(
            'errors/error.html',
            code=400,
            title='Invalid Request',
            message=error.description,
        ), 400

    return app
