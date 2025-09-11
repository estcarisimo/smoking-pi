"""
Authentication routes for the web admin
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
import os

auth_bp = Blueprint('auth', __name__)

class User:
    """Simple user class for Flask-Login"""
    def __init__(self, username):
        self.username = username
        self.id = username
        
    @property
    def is_authenticated(self):
        return True
    
    @property
    def is_active(self):
        return True
    
    @property
    def is_anonymous(self):
        return False
    
    def get_id(self):
        return self.id

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Get credentials from environment or use defaults
        valid_username = os.environ.get('WEB_ADMIN_USERNAME', 'admin')
        valid_password = os.environ.get('WEB_ADMIN_PASSWORD', 'smokingpi')
        
        if username == valid_username and password == valid_password:
            user = User(username)
            login_user(user, remember=True)
            
            # Redirect to next page or dashboard
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('dashboard.index'))
        else:
            flash('Invalid username or password', 'error')
    
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    """Logout user"""
    logout_user()
    return redirect(url_for('auth.login'))