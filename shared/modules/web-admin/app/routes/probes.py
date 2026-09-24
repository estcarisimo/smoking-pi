"""Probes: how often each probe measures, and how many pings it sends.

Stage D of the first-run flow, part 3. The analysis reads each target's
real cycle (common.cadence) and counts loss in pings lost, so a probe's
step and pings can change without breaking an alert. What cannot change in
place is SmokePing's own RRD files: each is made for one step and one ping
count. config-manager's RRD guard moves the old files aside at the reload,
so every target of the probe starts a new SmokePing history. This page says
so before anything is saved, and asks for a confirmation.
"""

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.routes.dashboard import PACKET_BYTES, config_api
from app.routes.targets import describe_cadence, probe_unit

probes_bp = Blueprint('probes', __name__)

# Mirrors config-manager's PROBE_STEPS / PROBE_MIN_PINGS / PROBE_MAX_PINGS,
# which are what is enforced; these only build the form.
STEP_CHOICES = (60, 120, 300, 600, 900, 1800, 3600)
MIN_PINGS, MAX_PINGS = 3, 20


def step_label(step):
    return 'Every ' + describe_cadence(1, step).split(' every ', 1)[1]


def kbps(pings, step, targets):
    """Estimated probe traffic in kbit/s, the dashboard's formula."""
    return round(pings * PACKET_BYTES * 8 / step * targets / 1000, 2)


def _probes():
    probes = config_api.get_probes_from_db().get('probes', [])
    for probe in probes:
        probe['unit'] = probe_unit(probe.get('name'), probe.get('module'))
        probe['cadence'] = describe_cadence(
            probe['pings'], probe['step_seconds'], probe['unit'])
        probe['kbps'] = kbps(probe['pings'], probe['step_seconds'],
                             probe.get('active_targets', 0))
    return probes


@probes_bp.route('/')
def index():
    try:
        probes = _probes()
    except Exception as e:
        current_app.logger.error(f"Failed to get probes: {e}")
        flash('Could not load the probes from config-manager.', 'error')
        probes = []
    return render_template('probes/index.html', probes=probes)


@probes_bp.route('/<name>/edit', methods=['GET', 'POST'])
def edit(name):
    try:
        probe = next((p for p in _probes() if p['name'] == name), None)
    except Exception as e:
        current_app.logger.error(f"Failed to get probes: {e}")
        flash('Could not load the probes from config-manager.', 'error')
        return redirect(url_for('probes.index'))
    if probe is None:
        flash(f'No probe named {name}.', 'error')
        return redirect(url_for('probes.index'))

    if request.method == 'POST':
        try:
            step = int(request.form.get('step_seconds', ''))
            pings = int(request.form.get('pings', ''))
        except ValueError:
            flash('Choose a step and a number of pings.', 'error')
            return render_template('probes/edit.html', probe=probe, **_form(probe))
        if request.form.get('confirm') != 'yes':
            flash('Confirm that the probe\'s targets start a new SmokePing history.', 'error')
            return render_template('probes/edit.html', probe=probe,
                                   **_form(probe, step, pings))
        try:
            result = config_api.update_probe(name, {'step_seconds': step, 'pings': pings})
        except ValueError as e:
            flash(str(e), 'error')
            return render_template('probes/edit.html', probe=probe,
                                   **_form(probe, step, pings))
        except Exception as e:
            current_app.logger.error(f"Failed to update probe {name}: {e}")
            flash('config-manager did not save the change.', 'error')
            return render_template('probes/edit.html', probe=probe,
                                   **_form(probe, step, pings))
        _report(name, result, probe['unit'])
        return redirect(url_for('probes.index'))

    return render_template('probes/edit.html', probe=probe, **_form(probe))


def _form(probe, step=None, pings=None):
    return {
        'steps': [(s, step_label(s)) for s in STEP_CHOICES],
        'min_pings': MIN_PINGS, 'max_pings': MAX_PINGS,
        'step': step or probe['step_seconds'],
        'pings': pings or probe['pings'],
    }


def _report(name, result, unit):
    """Say what the change did, including what the RRD guard moved."""
    if not result.get('changed'):
        flash(f'{name} already measures that way; nothing changed.', 'info')
        return
    current = result['current']
    message = (f"{name} now measures "
               f"{describe_cadence(current['pings'], current['step_seconds'], unit)}.")
    guard = result.get('rrd_guard') or {}
    moved = len(guard.get('archived', []))
    if guard.get('ran'):
        if moved:
            message += (f" {moved} old SmokePing file{'s were' if moved != 1 else ' was'} "
                        "moved to /data/.archive/; Grafana keeps the full history.")
        errors = guard.get('errors') or []
        if errors:
            flash(f"The RRD guard could not check {len(errors)} file(s); "
                  "see the config-manager log.", 'warning')
    else:
        flash('The RRD guard did not run. If SmokePing stopped measuring, '
              'see Measurement frequency in the docs.', 'warning')
    if not result.get('reloaded'):
        flash('Saved, but SmokePing did not confirm the reload.', 'warning')
    flash(message, 'success')
