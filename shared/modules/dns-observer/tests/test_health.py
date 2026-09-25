import json

import pytest

import health

NOW = 1_800_000_000.0
BASE = dict(
    now=NOW,
    started_at=NOW - 7200,
    server_ok=True,
    canary_enabled=True,
    canary_misses=3,
    canary_interval=300,
    quiet_after=1800,
    upstreams={},
)


def canaries(*seen: bool, every: int = 300):
    """Canaries sent every 5 min, the last one 60 s ago."""
    n = len(seen)
    out = []
    for i, ok in enumerate(seen):
        sent = NOW - 60 - (n - 1 - i) * every
        out.append((sent, sent + 0.05 if ok else None))
    return out


def test_observing():
    s = health.evaluate(**BASE, last_query_at=NOW - 5, canaries=canaries(True, True, True))
    assert s["state"] == "observing"


def test_quiet_house_is_not_an_outage():
    s = health.evaluate(**BASE, last_query_at=NOW - 3600, canaries=canaries(True, True, True))
    assert s["state"] == "quiet"
    assert s["state"] in health.LIVE_STATES


def test_router_reverted():
    s = health.evaluate(
        **BASE, last_query_at=NOW - 3600, canaries=canaries(True, False, False, False)
    )
    assert s["state"] == "not_receiving"
    assert "router" in s["fix"]
    assert s["state"] not in health.LIVE_STATES


def test_router_splitting_with_a_secondary():
    s = health.evaluate(**BASE, last_query_at=NOW - 5, canaries=canaries(False, False, False))
    assert s["state"] == "partial"


def test_misses_below_threshold_are_tolerated():
    s = health.evaluate(**BASE, last_query_at=NOW - 3600, canaries=canaries(True, False, False))
    assert s["state"] == "quiet"


def test_in_flight_canary_is_neither_seen_nor_missed():
    sent = NOW - 2
    c = canaries(False, False) + [(sent, None)]
    s = health.evaluate(**BASE, last_query_at=NOW - 3600, canaries=c)
    assert s["state"] != "not_receiving"


def test_never_configured():
    s = health.evaluate(**{**BASE, "canary_enabled": False}, last_query_at=None, canaries=[])
    assert s["state"] == "not_receiving"
    assert "Point the router" in s["fix"]


def test_idle_without_canary_says_it_cannot_tell():
    s = health.evaluate(
        **{**BASE, "canary_enabled": False}, last_query_at=NOW - 3600, canaries=[]
    )
    assert s["state"] == "idle"


def test_starting():
    s = health.evaluate(
        **{**BASE, "started_at": NOW - 20}, last_query_at=None, canaries=[]
    )
    assert s["state"] == "starting"
    s = health.evaluate(**{**BASE, "server_ok": None}, last_query_at=None, canaries=[])
    assert s["state"] == "starting"


def test_server_down_wins():
    s = health.evaluate(
        **{**BASE, "server_ok": False, "port": 5354},
        last_query_at=NOW - 5, canaries=canaries(True),
    )
    assert s["state"] == "server_down" and "5354" in s["reason"]


@pytest.mark.parametrize(
    "ups,state",
    [
        ({"answered": 10, "servfail": 9, "via_fallback": 0}, "upstream_failing"),
        ({"answered": 10, "servfail": 0, "via_fallback": 7}, "upstream_fallback"),
        ({"answered": 3, "servfail": 3, "via_fallback": 0}, "observing"),
    ],
)
def test_upstreams(ups, state):
    s = health.evaluate(
        **{**BASE, "upstreams": ups}, last_query_at=NOW - 5, canaries=canaries(True)
    )
    assert s["state"] == state


def test_summarize_upstreams_skips_cached_and_old():
    entries = [
        {"_ts": NOW - 10, "status": "NOERROR", "upstream": "1.1.1.1:53"},
        {"_ts": NOW - 10, "status": "SERVFAIL", "upstream": ""},
        {"_ts": NOW - 10, "status": "NOERROR", "cached": True, "upstream": "1.1.1.1:53"},
        {"_ts": NOW - 10, "status": "NOERROR", "upstream": "https://1.1.1.1:443/dns-query"},
        {"_ts": NOW - 4000, "status": "SERVFAIL", "upstream": ""},
    ]
    assert health.summarize_upstreams(entries, ["1.1.1.1"], NOW) == {
        "answered": 3, "servfail": 1, "via_fallback": 1,
    }


def test_read_status_missing_stale_stopped(tmp_path):
    path = str(tmp_path / "status.json")
    assert health.read_status(path)["state"] == "down"

    health.write_status(path, {
        "state": "observing", "heartbeat": NOW, "stale_after": NOW + 90,
        "observed_until": NOW - 5,
    })
    fresh = health.read_status(path, now=NOW + 30)
    assert fresh["state"] == "observing" and fresh["live"] is True

    stale = health.read_status(path, now=NOW + 91)
    assert stale["state"] == "down" and stale["live"] is False
    assert stale["observed_until"] == NOW - 5  # the gap has a start

    health.write_status(path, {"state": "stopped", "heartbeat": NOW, "stale_after": NOW})
    assert health.read_status(path, now=NOW + 999)["state"] == "stopped"


def test_read_status_unreadable(tmp_path):
    path = tmp_path / "status.json"
    path.write_text("{half")
    assert health.read_status(str(path))["state"] == "down"


def test_write_status_is_json(tmp_path):
    path = tmp_path / "s" / "status.json"
    health.write_status(str(path), {"state": "quiet"})
    assert json.loads(path.read_text()) == {"state": "quiet"}


def test_restart_after_long_outage_is_not_quiet_until_a_canary_arrives():
    # observed_until restored from before the outage, one canary missed so far.
    restored = NOW - 7200
    s = health.evaluate(
        **{**BASE, "started_at": NOW - 400},
        last_query_at=restored, canaries=canaries(False),
    )
    assert s["state"] == "starting"
    assert s["state"] not in health.LIVE_STATES
    s = health.evaluate(
        **{**BASE, "started_at": NOW - 400},
        last_query_at=restored, canaries=canaries(True),
    )
    assert s["state"] == "quiet"
