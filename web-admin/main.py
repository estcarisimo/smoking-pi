#!/usr/bin/env python3
"""
SmokePing Web Administration Interface
Main application entry point
"""

import os
import sys
from pathlib import Path

# Add the app directory to the Python path
sys.path.insert(0, str(Path(__file__).parent))

from app import create_app

# Configuration from environment
config_name = os.environ.get('FLASK_ENV', 'production')
port = int(os.environ.get('PORT', 8080))
host = os.environ.get('HOST', '0.0.0.0')

# Create the Flask application
app = create_app(config_name)

# Production WSGI application (always available for gunicorn)
application = app

if __name__ == '__main__':
    # Development server
    app.run(host=host, port=port, debug=(config_name == 'development'))