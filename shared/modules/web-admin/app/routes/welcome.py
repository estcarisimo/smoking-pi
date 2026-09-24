"""The welcome tour: the first thing a new install's web admin shows.

Stage C of the first-run flow. The install still seeds its targets, so it
measures from the first minute; the tour shows what was set up rather than
replacing it (a decision of 2026-09-24). Three steps, each built on what
already exists: is it measuring (stage A), what this host's own network
suggests (stage B), and the seeded targets, each of which can be paused.
Adding and pausing go through the same endpoints as the Targets page, so
the tour has no write path of its own. Nothing is added, paused or removed
unless the person chooses it.

The dashboard sends a login here until the tour is finished or skipped,
which config-manager remembers (/first-run). It can be reopened from the
dashboard at any time.
"""

from collections import OrderedDict

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.routes.dashboard import config_api, summarize_connection, summarize_measurements

welcome_bp = Blueprint('welcome', __name__)

# How the seeded categories are introduced, in the order the tour lists
# them. Anything else (a category of your own) follows, by name.
CATEGORY_INTROS = OrderedDict([
    ('top_sites', 'Popular sites, pinged: the everyday Internet.'),
    ('dns_resolvers', 'Public DNS resolvers: every page load starts with a lookup.'),
    ('http', 'The same sites fetched over HTTP/1.1, HTTP/2 and HTTP/3.'),
    ('tcp', 'A bare TCP handshake to port 443: the floor under the HTTP times.'),
    ('netflix_oca', "Netflix's caches serving your network, found for you."),
    ('custom', 'Targets of your own.'),
])


def group_targets(targets):
    """[(category, intro, [target, ...]), ...] in CATEGORY_INTROS order."""
    groups = OrderedDict((name, []) for name in CATEGORY_INTROS)
    for target in targets:
        groups.setdefault(target.get('category') or 'custom', []).append(target)
    return [(name, CATEGORY_INTROS.get(name, ''), sorted(items, key=lambda t: t.get('name', '')))
            for name, items in groups.items() if items]


@welcome_bp.route('/')
def index():
    using_database = config_api.is_database_available()
    targets = []
    if using_database:
        try:
            targets = config_api.get_all_targets_from_db().get('targets', [])
        except Exception:
            current_app.logger.warning("Welcome tour: could not list targets", exc_info=True)
    return render_template(
        'welcome.html',
        measurements=summarize_measurements(config_api.get_measurements()),
        connection=summarize_connection(config_api.get_recommendations()),
        groups=group_targets(targets),
        using_database=using_database,
    )


@welcome_bp.route('/finish', methods=['POST'])
def finish():
    outcome = request.form.get('outcome')
    if outcome not in ('done', 'skipped'):
        flash("Unknown choice; the tour is still open.", 'warning')
        return redirect(url_for('welcome.index'))
    try:
        config_api.set_first_run(outcome)
    except Exception:
        current_app.logger.error("Could not record the welcome tour", exc_info=True)
        flash("Could not record that; the tour may show again at the next login.", 'warning')
        return redirect(url_for('dashboard.index', tour='off'))
    if outcome == 'done':
        flash("All set. The dashboard below is where to come back to.", 'success')
    return redirect(url_for('dashboard.index'))


@welcome_bp.route('/again', methods=['POST'])
def again():
    """Reopen the tour from the dashboard."""
    try:
        config_api.set_first_run('reset')
    except Exception:
        current_app.logger.warning("Could not reset the welcome tour", exc_info=True)
    return redirect(url_for('welcome.index'))
