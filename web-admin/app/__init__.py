"""
SmokePing Web Administration Interface
Flask application for managing SmokePing targets
"""

import os
from flask import Flask, redirect, url_for
from flask_cors import CORS
from flask_caching import Cache
from flask_basicauth import BasicAuth
from flask_login import LoginManager, login_required
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

# Initialize extensions
cache = Cache()
scheduler = BackgroundScheduler()
basic_auth = BasicAuth()
login_manager = LoginManager()

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
    
    # Common configuration
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['CONFIG_DIR'] = os.environ.get('CONFIG_DIR', '/app/config')
    app.config['SMOKEPING_RESTART_CMD'] = os.environ.get('SMOKEPING_RESTART_CMD', 'supervisorctl restart smokeping')
    
    # Basic Auth configuration
    app.config['BASIC_AUTH_USERNAME'] = os.environ.get('WEB_ADMIN_USERNAME', 'admin')
    app.config['BASIC_AUTH_PASSWORD'] = os.environ.get('WEB_ADMIN_PASSWORD', 'smokingpi')
    app.config['BASIC_AUTH_FORCE'] = True  # Require auth for all routes
    
    # Enable CORS for API endpoints
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
    # Initialize cache
    cache.init_app(app, config={'CACHE_TYPE': 'simple', 'CACHE_DEFAULT_TIMEOUT': 86400})
    
    # Initialize Flask-Login (replacing basic auth)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'
    
    @login_manager.user_loader
    def load_user(user_id):
        from app.routes.auth import User
        return User(user_id)
    
    # Initialize scheduler for OCA refresh
    if config_name != 'testing':
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
    
    # Register blueprints
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.targets import targets_bp
    from app.routes.sources import sources_bp
    from app.routes.countries import countries_bp
    from app.routes.api import api_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(targets_bp, url_prefix='/targets')
    app.register_blueprint(sources_bp, url_prefix='/sources')
    app.register_blueprint(countries_bp, url_prefix='/countries')
    app.register_blueprint(api_bp, url_prefix='/api')
    
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
    
    # Add login requirement to all routes except auth
    @app.before_request
    def require_login():
        from flask import request, jsonify
        from flask_login import current_user
        
        # Skip auth for login page and static files
        if request.endpoint and (
            request.endpoint.startswith('auth.') or 
            request.endpoint == 'static'
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
            return redirect(url_for('auth.login', next=request.url))
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return {'error': 'Not found'}, 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return {'error': 'Internal server error'}, 500
    
    return app