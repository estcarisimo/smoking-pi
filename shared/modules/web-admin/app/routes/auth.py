"""
Authentication routes for the web admin
"""

import hmac
import os
import threading
import time
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash

auth_bp = Blueprint('auth', __name__)

# Simple in-process login rate limiter (per source IP).
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300

_failures_lock = threading.Lock()
_login_failures = {}  # ip -> {'count': int, 'locked_until': float}


def _is_locked_out(ip):
    """Return True if the IP is currently locked out."""
    with _failures_lock:
        entry = _login_failures.get(ip)
        if not entry:
            return False
        if entry['locked_until'] > time.time():
            return True
        if entry['locked_until'] and entry['locked_until'] <= time.time():
            # Lockout expired -- start fresh
            del _login_failures[ip]
        return False


def _record_failure(ip):
    """Record a failed login attempt; lock the IP after MAX_FAILURES."""
    with _failures_lock:
        entry = _login_failures.setdefault(ip, {'count': 0, 'locked_until': 0.0})
        entry['count'] += 1
        if entry['count'] >= MAX_FAILURES:
            entry['locked_until'] = time.time() + LOCKOUT_SECONDS


def _reset_failures(ip):
    with _failures_lock:
        _login_failures.pop(ip, None)


def _credentials_configured():
    return bool(
        os.environ.get('WEB_ADMIN_PASSWORD_HASH') or os.environ.get('WEB_ADMIN_PASSWORD')
    )


def _verify_credentials(username, password):
    """Verify username/password against the configured credentials.

    Prefers WEB_ADMIN_PASSWORD_HASH (werkzeug pbkdf2 hash); falls back to a
    constant-time comparison against plaintext WEB_ADMIN_PASSWORD for backward
    compatibility. Returns False when no credentials are configured.
    """
    valid_username = os.environ.get('WEB_ADMIN_USERNAME', 'admin')
    if not hmac.compare_digest(username or '', valid_username):
        return False

    password_hash = os.environ.get('WEB_ADMIN_PASSWORD_HASH')
    if password_hash:
        return check_password_hash(password_hash, password or '')

    plaintext = os.environ.get('WEB_ADMIN_PASSWORD')
    if plaintext:
        return hmac.compare_digest(password or '', plaintext)

    return False


def _safe_next_url(next_url):
    """Only allow relative redirect targets (single leading slash, no netloc)."""
    if not next_url:
        return None
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return None
    if not next_url.startswith('/') or next_url.startswith('//'):
        return None
    return next_url


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

    credentials_configured = _credentials_configured()

    if request.method == 'POST':
        ip = request.remote_addr or 'unknown'

        if _is_locked_out(ip):
            flash('Too many failed login attempts. Try again in a few minutes.', 'error')
            return render_template(
                'login.html',
                credentials_configured=credentials_configured,
                next=request.form.get('next', ''),
            ), 429

        if not credentials_configured:
            flash('Login is not configured. Set WEB_ADMIN_PASSWORD_HASH (or '
                  'WEB_ADMIN_PASSWORD) in the environment and restart.', 'error')
            return render_template(
                'login.html',
                credentials_configured=False,
                next=request.form.get('next', ''),
            )

        username = request.form.get('username')
        password = request.form.get('password')
        remember = request.form.get('remember') == 'on'

        if _verify_credentials(username, password):
            _reset_failures(ip)
            user = User(username)
            login_user(user, remember=remember)

            # Redirect to next page (relative paths only) or dashboard
            next_page = _safe_next_url(request.form.get('next'))
            return redirect(next_page) if next_page else redirect(url_for('dashboard.index'))
        else:
            _record_failure(ip)
            flash('Invalid username or password', 'error')

    return render_template(
        'login.html',
        credentials_configured=credentials_configured,
        next=request.args.get('next', ''),
    )


@auth_bp.route('/logout')
@login_required
def logout():
    """Logout user"""
    logout_user()
    return redirect(url_for('auth.login'))
