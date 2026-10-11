"""Loss incidents are written down once they are over.

diagnose_loss computed incidents on demand and nothing kept them. These
tests pin which incidents get written (over, wholly inside the window), how
one becomes a point (identity = start, every field every time), and that
the background run never costs the alerter.
"""

from __future__ import annotations

import incident_history

NOW = 1_800_000_000.0
STEP = 300


def _inc(start_ago_s, end_ago_s, cls="upstream", **over):
    inc = {"class": cls, "detail": None, "confidence": "high",
           "start_epoch": NOW - start_ago_s, "end_epoch": NOW - end_ago_s,
           "minutes": int((start_ago_s - end_ago_s) / 60),
           "targets": ["google", "amazon"], "targets_affected": 2,
           "targets_reporting": 20, "max_loss_pct": 40.0,
           "evidence": ["most destinations lost packets", "the first hop was clean"],
           "against": [], "summary": "upstream"}
    inc.update(over)
    return inc


def _lines(points):
    return [p.to_line_protocol() for p in points]


def test_only_incidents_that_are_over_and_wholly_inside_the_window():
    window_h = 12
    res = {"incidents": [
        _inc(3600, 1200),                       # ended 20 min ago: may still grow
        _inc(7200, 3600),                       # over: kept
        _inc(12 * 3600 - 300, 11 * 3600),       # starts at the window's edge: cut
        _inc(11 * 3600, 10 * 3600),             # wholly inside: kept
    ]}
    done = incident_history.finished(res, NOW, window_h, STEP)
    assert [i["start_epoch"] for i in done] == [NOW - 11 * 3600, NOW - 7200]


def test_nothing_is_written_when_the_diagnosis_hit_its_row_limit():
    res = {"incidents": [_inc(7200, 3600)], "rows_truncated": True}
    assert incident_history.finished(res, NOW, 12, STEP) == []


def test_identity_is_the_start_and_every_value_is_a_field():
    line = _lines([incident_history.point(_inc(7200, 3600))])[0]
    head, rest = line.split(" ", 1)
    fields, ts = rest.rsplit(" ", 1)
    # No tags: two runs about one incident write the same series and time,
    # so the second overwrites the first.
    assert head == "loss_incident"
    assert ts == str(int(NOW - 7200))
    assert 'class="upstream"' in fields
    assert 'targets="google,amazon"' in fields
    assert 'evidence="most destinations lost packets | the first hop was clean"' in fields
    assert "end_epoch=" in fields and "max_loss_pct=40" in fields


def test_empty_values_are_written_so_an_overwrite_leaves_nothing_stale():
    line = _lines([incident_history.point(_inc(7200, 3600, detail=None, against=[]))])[0]
    assert 'detail=""' in line and 'against=""' in line
    # A cut or deaf span has no loss run: no max_loss_pct, never a fake 0.
    cut = _inc(7200, 7140, cls="local_link", max_loss_pct=None, targets=[])
    assert "max_loss_pct" not in _lines([incident_history.point(cut)])[0]


def test_record_writes_the_finished_incidents(monkeypatch):
    res = {"incidents": [_inc(3600, 1200), _inc(7200, 3600)]}
    monkeypatch.setattr(incident_history.diagnosis_query, "run",
                        lambda query, hours, cadences: res)
    written = []
    n = incident_history.record(12, {}, now=NOW, query=lambda f: [],
                                write=written.extend)
    assert n == 1 and len(written) == 1


def test_due_every_half_hour_and_never_twice_at_once(monkeypatch):
    monkeypatch.setenv("INCIDENT_HISTORY_HOURS", "12")
    started = []

    class Done:
        def done(self):
            return False

    monkeypatch.setattr(incident_history, "_pending", None)
    monkeypatch.setattr(incident_history._pool, "submit",
                        lambda fn, *a: started.append(a) or Done())
    state = {}
    assert incident_history.maybe_record(state, {}, now=NOW) is True
    # Not due yet.
    assert incident_history.maybe_record(state, {}, now=NOW + 60) is False
    # Due, but the last run is still going.
    assert incident_history.maybe_record(state, {}, now=NOW + incident_history.EVERY_S) is False
    assert len(started) == 1
    assert state["incident_history"]["last_start"] == NOW


def test_off_with_zero_hours(monkeypatch):
    monkeypatch.setenv("INCIDENT_HISTORY_HOURS", "0")
    assert incident_history.maybe_record({}, {}, now=NOW) is False


def test_a_failed_run_is_logged_not_raised(monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("influx down")
    monkeypatch.setattr(incident_history, "record", boom)
    incident_history._run(12, {})
    assert "incident history run failed" in caplog.text


def test_a_long_incident_cut_at_the_edge_is_diagnosed_again_with_a_wider_window(monkeypatch):
    """The reference Pi's radio was deaf for 11 hours: a 12 h window sees
    it cut at its edge, so the run widens until the start is inside."""
    long_one = _inc(23 * 3600, 2 * 3600, cls="local_wifi")
    windows = []

    def run(query, hours, cadences):
        windows.append(hours)
        start = NOW - hours * 3600
        cut = dict(long_one, start_epoch=max(long_one["start_epoch"], start))
        return {"incidents": [cut]}

    monkeypatch.setattr(incident_history.diagnosis_query, "run", run)
    written = []
    assert incident_history.record(12, {}, now=NOW, query=lambda f: [],
                                   write=written.extend) == 1
    assert windows == [12, 24]
    assert f" {int(NOW - 23 * 3600)}" in written[0].to_line_protocol()


def test_an_incident_still_going_at_the_edge_does_not_widen_the_window(monkeypatch):
    windows = []

    def run(query, hours, cadences):
        windows.append(hours)
        return {"incidents": [_inc(hours * 3600, 60)]}

    monkeypatch.setattr(incident_history.diagnosis_query, "run", run)
    assert incident_history.record(12, {}, now=NOW, query=lambda f: [],
                                   write=lambda p: None) == 0
    assert windows == [12]
