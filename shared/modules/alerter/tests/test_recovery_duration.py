"""#274: a recovery says how long the problem lasted, not how long the alert
stayed open.

The alert stays open past the problem twice over: the resolve grace period
(ALERT_RESOLVE_AFTER, 900 s by default) after the rule stops firing, and for
a windowed rule the window itself. microcut_burst keeps firing while any cut
is inside its 60 min window, so a 2 min cut read "was down 1h 15m".
"""

import main
import state
import templates

T0 = 1_790_000_000.0  # a real epoch: 0 would read as "no first_seen"


def _cycle(st, incidents, now):
    return state.reconcile(st, incidents, now=now)


def _recovery_text(st, incident, active_until, step=300):
    """Fire ``incident`` at t=0, keep it firing every ``step`` until
    ``active_until``, then let it go and run until the recovery is sent.
    Returns the rendered recovery message."""
    t = T0
    while t <= T0 + active_until:
        _cycle(st, [incident], t)
        t += step
    for _ in range(20):
        actions = _cycle(st, [], t)
        if actions["recoveries"]:
            event = actions["recoveries"][0]
            payload = {**event, "type": "recovery", "duration_s": main._duration_of(event)}
            return templates.format_message(payload)
        t += step
    raise AssertionError("no recovery was sent")


def test_a_target_down_recovery_ends_when_the_rule_stopped_firing(state_file):
    """Fired at 0, last seen at 1200, gone at 1500: down 25 min, not the
    40 min that adds the grace period."""
    st = state.load_state()
    incident = {"rule": "target_down", "severity": "critical", "key": "target_down:x",
                "target": "x", "message": "x down: 100% loss", "value": 100.0}
    text = _recovery_text(st, incident, active_until=1200)
    assert "was down 25 min" in text
    assert "40 min" not in text


def test_a_microcut_recovery_does_not_call_the_window_an_outage(state_file):
    """One 2 min 40 s cut keeps microcut_burst firing for its 60 min window.
    The recovery says what the cuts were, never "was down 1h"."""
    st = state.load_state()
    incident = {"rule": "microcut_burst", "severity": "warning",
                "key": "microcut_burst:CPE_IPv4/ipv4", "target": "CPE_IPv4",
                "message": "CPE_IPv4 (ipv4): 1 cut of 2 min 40 s over 50% loss in the last 60m",
                "value": 1}
    text = _recovery_text(st, incident, active_until=3600)
    assert "was down" not in text
    assert "1h" not in text
    assert "1 cut of 2 min 40 s" in text


def test_a_high_loss_recovery_says_lasted_not_down(state_file):
    st = state.load_state()
    incident = {"rule": "high_loss", "severity": "warning", "key": "high_loss:x",
                "target": "x", "message": "x: 12% loss over 15m", "value": 12.0}
    text = _recovery_text(st, incident, active_until=1200)
    assert "lasted 25 min" in text
    assert "was down" not in text


def test_a_record_saved_before_this_change_still_gets_a_duration():
    """State written by the previous version has no ended_at: fall back to
    cleared_at rather than dropping the clause."""
    event = {"rule": "target_down", "state": {"first_seen": T0, "cleared_at": T0 + 2400}}
    assert main._duration_of(event) == 2400.0
