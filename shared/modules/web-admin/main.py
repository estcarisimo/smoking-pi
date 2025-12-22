#!/usr/bin/env python3
"""
SmokePing Web Administration Interface.

Main application entry point for the SmokePing web administration interface.
Provides a Flask-based web UI for managing SmokePing monitoring targets,
categories, and configuration with database backend integration.

This module serves as the WSGI entry point for both development and production
deployments, with automatic configuration from environment variables.

Author: SmokePing Team
Version: 2.0.0

Examples
--------
Development server:
    $ python main.py

Production with gunicorn:
    $ gunicorn main:application

Environment Variables
--------------------
FLASK_ENV : str, optional
    Flask environment mode ('development', 'production'), default 'production'
PORT : int, optional
    Server port number, default 8080
HOST : str, optional
    Server bind address, default '0.0.0.0'
"""

import os
import sys
from pathlib import Path
from typing import Optional

# Add the app directory to the Python path
sys.path.insert(0, str(Path(__file__).parent))

from app import create_app
from flask import Flask


def get_config_from_env() -> tuple[str, int, str]:
    """
    Load configuration from environment variables.
    
    Returns
    -------
    tuple[str, int, str]
        Configuration tuple containing (config_name, port, host)
        
    Examples
    --------
    >>> config_name, port, host = get_config_from_env()
    >>> print(f"Config: {config_name}, Port: {port}, Host: {host}")
    Config: production, Port: 8080, Host: 0.0.0.0
    """
    config_name = os.environ.get('FLASK_ENV', 'production')
    port = int(os.environ.get('PORT', 8080))
    host = os.environ.get('HOST', '0.0.0.0')
    
    return config_name, port, host


def main() -> None:
    """
    Main entry point for development server.
    
    Creates Flask application with environment-based configuration
    and runs the development server with debug mode enabled for
    development environments.
    """
    config_name, port, host = get_config_from_env()
    
    # Create the Flask application
    app = create_app(config_name)
    
    # Run development server
    debug_mode = config_name == 'development'
    app.run(host=host, port=port, debug=debug_mode)


# Load configuration
config_name, port, host = get_config_from_env()

# Create the Flask application
app: Flask = create_app(config_name)

# Production WSGI application (always available for gunicorn)
application: Flask = app

if __name__ == '__main__':
    main()