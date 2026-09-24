"""Tests for the "is it me or the internet?" verdict.

The verdict is the part of an alert a person acts on, so a confidently wrong
one is worse than none. Two failure modes get the most attention here:
claiming a network fault from an ABSENCE of data, and letting a host that
never answers ICMP inflate breadth until one slow site reads as an outage.
"""

from __future__ import annotations

import time

import pytest

import verdict


def _mean(target, ratio, category=None):
    return {"target": target, "_value": ratio, "category": category}


def _micro(target, protocol, count, cuts=None, possible=0):
    """A folded microcut row (evaluator.microcut_rows); ``cuts=None`` gives
    the older count-of-windows shape, which the verdict still reads."""
    row = {"target": target, "protocol": protocol, "_value": count}
    if cuts is not None:
        row["cuts"], row["possible"] = cuts, possible
    return row


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "VERDICT_BROAD_PCT",
        "VERDICT_MIN_TARGETS",
        "VERDICT_IMPAIRED_LOSS_PCT",
        "VERDICT_STALE_DOWN_HOURS",
        "MICROCUT_BURST_N",
        "WIFI_WEAK_DBM",
        "WIFI_WEAK_SAMPLES",
    ):
        monkeypatch.delenv(var, raising=False)


def _wifi(n=360, weak=0, min_dbm=-52.0, drops=0, uplink=1, iface="wlan0", rx=4053):
    """The evaluator's four wifi_link aggregates, as rows. ``rx=None``
    omits the packets-received row, as a collector without it would."""
    rows = {
        "signal": [{"interface": iface, "n": n, "weak": weak, "min": min_dbm}],
        "drops": [{"interface": iface, "_value": drops}],
        "uplink": [{"interface": iface, "_value": uplink}],
    }
    if rx is not None:
        rows["rx"] = [{"interface": iface, "_value": rx}]
    return rows


UPLINK_DOWN = {"rule": "uplink_down", "severity": "critical", "key": "uplink_down"}


def _healthy(n, prefix="ok", category="top_sites"):
    return [_mean(f"{prefix}{i}", 0.0, category) for i in range(n)]


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


def test_exporter_stale_outranks_a_broad_outage():
    """Reporting "the internet is down" from missing data is the worst case.

    Every target reads as 100% lost precisely BECAUSE nothing is arriving.
    """
    rows = [_mean(f"t{i}", 1.0) for i in range(10)]
    call = verdict.classify(
        [{"rule": "exporter_stale", "severity": "critical"}], rows, []
    )
    assert call["scope"] == "monitoring"
    assert "not the network" in call["line"]


def test_broad_impairment_with_cpe_cutting_is_the_local_link():
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 5)])
    assert call["scope"] == "local_link"
    assert "Your line" in call["line"]


def test_broad_impairment_with_a_clean_cpe_is_upstream():
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [])
    assert call["scope"] == "isp_upstream"
    assert "Not you" in call["line"]


def test_cpe_at_its_rate_limit_floor_never_reads_as_local_link():
    """The CPE gateway rate-limits ICMP: p50 10%, p99 30% loss, always.

    micro_rows only ever contains windows above MICROCUT_LOSS_PCT (50%), so
    the floor cannot appear here -- but if a future change fed raw loss in,
    every broad outage would be blamed on the user's own line.
    """
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [])
    assert call["scope"] == "isp_upstream"
    assert call["cpe_cutting"] == []


def test_a_single_bad_site_with_healthy_peers_is_the_remote_target():
    rows = [_mean("amazon", 0.9, "top_sites"), *_healthy(8)]
    call = verdict.classify([], rows, [])
    assert call["scope"] == "remote_target"
    assert "Just that site" in call["line"]
    assert "top_sites" in call["line"]


def test_ipv6_only_impairment_is_named_as_such():
    rows = [
        _mean("google6", 1.0, "fping6"),
        _mean("cloudflare6", 1.0, "fping6"),
        *_healthy(8),
    ]
    call = verdict.classify([], rows, [])
    assert call["scope"] == "ipv6"


def test_dns_only_impairment_is_named_as_such():
    rows = [
        _mean("quad9", 0.9, "dns_resolvers"),
        _mean("opendns", 0.9, "dns_resolvers"),
        *_healthy(8),
    ]
    call = verdict.classify([], rows, [])
    assert call["scope"] == "dns"


def test_nothing_impaired_claims_nothing():
    call = verdict.classify([], _healthy(8), [])
    assert call["scope"] == "unclear"
    assert call["affected"] == 0


def test_no_measurements_claims_nothing_and_does_not_crash():
    call = verdict.classify([], [], [])
    assert call["scope"] == "unclear"
    assert call["total"] == 0


def test_too_few_targets_is_not_enough_to_call_it_broad():
    """Two of two impaired is 100%, and means nothing."""
    rows = [_mean("a", 0.9, "custom"), _mean("b", 0.9, "custom")]
    call = verdict.classify([], rows, [])
    assert call["scope"] != "isp_upstream"


# ---------------------------------------------------------------------------
# The chronic-target trap
# ---------------------------------------------------------------------------


def _chronic_records(targets, age_hours, now):
    return {
        f"target_down:{t}": {"first_seen": now - age_hours * 3600.0}
        for t in targets
    }


def test_a_chronically_dead_host_does_not_inflate_breadth():
    """REINTRODUCTION TEST for the verdict's most likely wrong answer.

    Bare `amazon.com` does not answer ICMP and charts a permanent flat 100%.
    With six such targets and one genuinely slow site, counting them makes
    7/10 look broad and the verdict announces an ISP outage. Excluding them
    from BOTH numerator and denominator leaves 1 of 4 -- one bad site.

    Delete the _chronic() filter and this flips to isp_upstream.
    """
    now = time.time()
    dead = [f"dead{i}" for i in range(6)]
    rows = [_mean(t, 1.0, "custom") for t in dead]
    rows.append(_mean("amazon", 0.9, "top_sites"))
    rows += _healthy(3)

    call = verdict.classify(
        [], rows, [], records=_chronic_records(dead, 48, now), now=now
    )
    assert call["scope"] != "isp_upstream"
    assert call["total"] == 4
    assert call["affected"] == 1
    assert set(call["evidence"]["excluded_chronic"]) == set(dead)


def test_a_target_that_just_went_down_still_counts():
    """Exclusion is for hosts that never answered, not for a real outage."""
    now = time.time()
    dead = [f"d{i}" for i in range(6)]
    rows = [_mean(t, 1.0, "custom") for t in dead] + _healthy(3)
    call = verdict.classify(
        [], rows, [], records=_chronic_records(dead, 0.5, now), now=now
    )
    assert call["scope"] == "isp_upstream"
    assert call["evidence"]["excluded_chronic"] == []


def test_a_target_with_no_incident_record_is_never_excluded():
    now = time.time()
    rows = [_mean(f"d{i}", 1.0, "custom") for i in range(6)] + _healthy(3)
    call = verdict.classify([], rows, [], records={}, now=now)
    assert call["scope"] == "isp_upstream"


def test_evidence_is_logged_for_every_verdict(caplog):
    """A wrong verdict must be diagnosable from docker logs alone."""
    with caplog.at_level("INFO", logger="alerter.verdict"):
        verdict.classify([], _healthy(8), [])
    assert "verdict inputs:" in caplog.text
    assert "impaired" in caplog.text


def test_thresholds_are_env_tunable(monkeypatch):
    rows = [_mean("a", 0.5), _mean("b", 0.0), _mean("c", 0.0), _mean("d", 0.0)]
    assert verdict.classify([], rows, [])["scope"] != "isp_upstream"
    monkeypatch.setenv("VERDICT_BROAD_PCT", "25")
    assert verdict.classify([], rows, [])["scope"] == "isp_upstream"


# ---------------------------------------------------------------------------
# The Wi-Fi hop
# ---------------------------------------------------------------------------

def test_no_wifi_rows_changes_nothing():
    """A wired host: every verdict is byte-identical to before, and the
    result says the Wi-Fi was not in view."""
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    before = verdict.classify([], rows, [_micro("CPE", "ipv4", 5)])
    after = verdict.classify([], rows, [_micro("CPE", "ipv4", 5)], wifi_rows=None)
    assert after["scope"] == before["scope"] == "local_link"
    assert after["line"] == before["line"]
    assert after["wifi"] is None
    assert verdict.classify([], rows, [], wifi_rows={"signal": [], "drops": [], "uplink": []})["wifi"] is None


def test_cutting_with_a_weak_wifi_hour_is_the_wifi():
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 5)],
                            wifi_rows=_wifi(weak=6, min_dbm=-78.0))
    assert call["scope"] == "wifi"
    assert "Your Wi-Fi" in call["line"] and "-78 dBm" in call["line"]
    assert "not the ISP" in call["line"]
    assert call["wifi"]["degraded"] is True


def test_cutting_with_wifi_drops_names_the_drops():
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 5)],
                            wifi_rows=_wifi(drops=2))
    assert call["scope"] == "wifi"
    assert "dropped 2 times" in call["line"]


def test_wifi_needs_no_breadth_but_does_need_the_cpe_to_be_cutting():
    """A dropped uplink takes everything with it, so breadth is not required;
    but weak Wi-Fi with a clean first hop is not this verdict."""
    healthy = _healthy(10)
    cutting = verdict.classify([], healthy, [_micro("CPE", "ipv4", 5)],
                               wifi_rows=_wifi(weak=10, min_dbm=-80.0))
    assert cutting["scope"] == "wifi"
    clean = verdict.classify([], [_mean(f"t{i}", 0.5) for i in range(10)], [],
                             wifi_rows=_wifi(weak=10, min_dbm=-80.0))
    assert clean["scope"] == "isp_upstream"
    assert clean["wifi"]["degraded"] is True   # reported, not acted on


def test_a_healthy_wifi_hour_leaves_the_local_link_verdict_alone():
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 5)], wifi_rows=_wifi())
    assert call["scope"] == "local_link"
    assert call["wifi"] == {"interface": "wlan0", "uplink": True, "samples": 360,
                            "weak_samples": 0, "min_dbm": -52.0, "disconnects": 0,
                            "rx_packets": 4053, "degraded": False}


def test_one_weak_sample_never_flips_the_verdict():
    """Floor-safety: WIFI_WEAK_SAMPLES (6) below the threshold, not one."""
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 5)],
                            wifi_rows=_wifi(weak=5, min_dbm=-90.0))
    assert call["scope"] == "local_link"


def test_weak_samples_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("WIFI_WEAK_SAMPLES", "3")
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 5)],
                            wifi_rows=_wifi(weak=3, min_dbm=-80.0))
    assert call["scope"] == "wifi"


def test_a_spare_radio_that_is_not_the_uplink_does_not_count():
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 5)],
                            wifi_rows=_wifi(weak=30, min_dbm=-85.0, uplink=0))
    assert call["scope"] == "local_link"
    assert call["wifi"]["uplink"] is False and call["wifi"]["degraded"] is False


def test_monitoring_still_outranks_wifi():
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([{"rule": "exporter_stale"}], rows, [_micro("CPE", "ipv4", 5)],
                            wifi_rows=_wifi(drops=3))
    assert call["scope"] == "monitoring"


# ---------------------------------------------------------------------------
# This host's uplink
# ---------------------------------------------------------------------------


def test_uplink_down_names_this_host_not_the_internet():
    rows = [_mean(f"t{i}", 1.0) for i in range(10)]
    call = verdict.classify([UPLINK_DOWN], rows, [_micro("CPE", "ipv4", 99)])
    assert call["scope"] == "monitor_uplink"
    assert "This host's uplink" in call["line"]
    assert "nothing beyond it can be judged" in call["line"]


def test_a_hung_radio_is_named_as_such():
    """2026-09-20: associated to the AP at -49 dBm, uplink flag set, and
    zero packets received in 20 minutes. Nothing about that looks broken
    from the router's side, so the line has to say what it is."""
    rows = [_mean(f"t{i}", 1.0) for i in range(10)]
    call = verdict.classify([UPLINK_DOWN], rows, [_micro("CPE", "ipv4", 99)],
                            wifi_rows=_wifi(min_dbm=-49.0, rx=0))
    assert call["scope"] == "monitor_uplink"
    assert "Wi-Fi (wlan0)" in call["line"]
    assert "-49 dBm" in call["line"]
    assert "received nothing" in call["line"]
    assert "radio is hung" in call["line"]
    assert "power save" in call["line"]
    assert call["wifi"]["rx_packets"] == 0


def test_a_dropped_association_is_named_as_such():
    rows = [_mean(f"t{i}", 1.0) for i in range(10)]
    call = verdict.classify([UPLINK_DOWN], rows, [], wifi_rows=_wifi(drops=2, rx=0))
    # Drops outrank the hung-radio reading only when the radio did receive
    # something; here it received nothing, so the hung line wins.
    assert "radio is hung" in call["line"]
    call = verdict.classify([UPLINK_DOWN], rows, [], wifi_rows=_wifi(drops=2, rx=120))
    assert "dropped 2 times" in call["line"]
    assert "radio is hung" not in call["line"]


def test_a_collector_without_rx_packets_still_gets_the_generic_line():
    rows = [_mean(f"t{i}", 1.0) for i in range(10)]
    call = verdict.classify([UPLINK_DOWN], rows, [], wifi_rows=_wifi(rx=None))
    assert call["scope"] == "monitor_uplink"
    assert "radio is hung" not in call["line"]
    assert call["wifi"]["rx_packets"] is None


def test_a_wired_host_gets_the_generic_uplink_line():
    rows = [_mean(f"t{i}", 1.0) for i in range(10)]
    call = verdict.classify([UPLINK_DOWN], rows, [], wifi_rows=None)
    assert call["line"].startswith("This host's uplink —")


def test_uplink_down_outranks_the_wifi_and_local_link_scopes():
    rows = [_mean(f"t{i}", 1.0) for i in range(10)]
    call = verdict.classify([UPLINK_DOWN], rows, [_micro("CPE", "ipv4", 99)],
                            wifi_rows=_wifi(weak=30, min_dbm=-80.0, rx=0))
    assert call["scope"] == "monitor_uplink"


def test_monitoring_still_outranks_uplink_down():
    rows = [_mean(f"t{i}", 1.0) for i in range(10)]
    call = verdict.classify([{"rule": "exporter_stale"}, UPLINK_DOWN], rows, [])
    assert call["scope"] == "monitoring"


def test_without_the_uplink_incident_a_total_loss_is_still_the_local_link():
    # The verdict does not infer uplink_down from breadth on its own; the
    # evaluator decides that from the raw cycles. Total loss with the first
    # hop cutting stays "your line" -- which is also true.
    rows = [_mean(f"t{i}", 1.0) for i in range(10)]
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 99)])
    assert call["scope"] == "local_link"


# ---------------------------------------------------------------------------
# The local-link signal reads cuts, not windows
# ---------------------------------------------------------------------------


def test_two_isolated_windows_are_not_a_cutting_first_hop():
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 2, cuts=0, possible=2)])
    assert call["scope"] == "isp_upstream"
    assert call["cpe_cutting"] == []


def test_one_confirmed_cut_is_a_cutting_first_hop():
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 6, cuts=1, possible=0)])
    assert call["scope"] == "local_link"
    assert call["cpe_cutting"] == ["CPE/ipv4"]


def test_enough_possible_cuts_count_and_the_bar_is_env_tunable(monkeypatch):
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 3, cuts=0, possible=3)])
    assert call["scope"] == "local_link"
    monkeypatch.setenv("MICROCUT_BURST_N", "5")
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 3, cuts=0, possible=3)])
    assert call["scope"] == "isp_upstream"


# ---------------------------------------------------------------------------
# An uplink change in the last hour (host_uplink)
# ---------------------------------------------------------------------------

from common import aggregates  # noqa: E402

_T = "2026-09-24T13:02:00+00:00"


@pytest.fixture
def utc(monkeypatch):
    """Clock times in UTC; the zone is restored afterwards, since tzset()
    outlives monkeypatch's env undo."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _change(previous="wlan0", interface="eth0", kind="wired", when=_T):
    return {"time": when, "previous": previous, "interface": interface, "kind": kind}


def test_an_uplink_change_is_named_whatever_the_scope(utc):
    """Latency stepped because the path changed: the line says so, on top of
    whatever the scope is, so nobody calls the ISP about a plugged cable."""
    call = verdict.classify([], _healthy(20), [], uplink_changes=[_change()])
    assert call["line"].endswith(
        "Also: this host's uplink moved from wlan0 to eth0 (wired) at 13:02, "
        "so the measurements before and after crossed different links.")
    assert call["evidence"]["uplink_changes"] == [_change()]


def test_no_change_leaves_the_line_alone():
    plain = verdict.classify([], _healthy(20), [])
    assert "Also:" not in plain["line"]
    assert verdict.classify([], _healthy(20), [], uplink_changes=[])["line"] == plain["line"]


def test_several_changes_name_the_newest_and_count_the_rest(utc):
    changes = [_change(), _change("eth0", "wlan0", "wireless", "2026-09-24T13:40:00+00:00")]
    line = verdict.classify([], _healthy(20), [], uplink_changes=changes)["line"]
    assert "moved from eth0 to wlan0 (wireless) at 13:40 (1 more change this hour)" in line


def test_a_lost_route_sharpens_uplink_down(utc):
    call = verdict.classify([UPLINK_DOWN], [], [],
                            uplink_changes=[_change("wlan0", "", "none")])
    assert call["scope"] == "monitor_uplink"
    assert "this host lost its default route (it was on wlan0) at 13:02" in call["line"]


def test_describe_and_parse_uplink_changes(utc):
    back = _change("none", "wlan0", "wireless")
    assert aggregates.describe_uplink_change(back) == (
        "this host got a default route back, on wlan0 (wireless) at 13:02")
    assert aggregates.describe_uplink_change(_change(), with_time=False) == (
        "this host's uplink moved from wlan0 to eth0 (wired)")
    from datetime import datetime, timezone
    rows = [
        {"_time": datetime(2026, 9, 24, 13, 2, tzinfo=timezone.utc),
         "previous": "wlan0", "interface": "eth0", "kind": "wired"},
        {"_time": datetime(2026, 9, 24, 13, 3, tzinfo=timezone.utc),
         "previous": "eth0", "interface": "", "kind": "none"},
        {"_time": datetime(2026, 9, 24, 13, 4, tzinfo=timezone.utc)},  # heartbeat
    ]
    assert aggregates.parse_uplink_changes(rows) == [
        _change(),
        _change("eth0", "", "none", "2026-09-24T13:03:00+00:00"),
    ]
    flux = aggregates.uplink_changes_flux("-60m")
    assert 'r._measurement == "host_uplink"' in flux and "exists r.previous" in flux
