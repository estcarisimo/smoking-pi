"""common.diagnosis: each loss episode's class, confidence and evidence.

The shapes below are the ones seen on the reference and staging Pis in
September and October 2026: a hung radio (every target at 100% while the
host heard nothing), one site's own trouble, a lone two-ping miss, a cut on
the line, and the low-grade loss on most paths at night with a normal
first hop.
"""

from datetime import datetime, timezone

import pytest

import backends
import server
from common import diagnosis, diagnosis_query
from test_tools import ExplodingConfigAPI

STEP = 300
T0 = 1_759_300_000 - (1_759_300_000 % STEP)


def ev(target, step, loss, category="topsites"):
    return {"target": target, "category": category, "loss_pct": loss,
            "_epoch": T0 + step * STEP}


def ctx(**extra):
    base = {"step_s": STEP, "reporting": {T0 + i * STEP: 10 for i in range(-5, 40)},
            "cuts": [], "deaf": [], "cpe": True, "wifi": None, "app": {},
            "app_sites": set(), "ipv6": None, "floor": {}}
    base.update(extra)
    return base


def only(result):
    assert len(result["incidents"]) == 1, result["incidents"]
    return result["incidents"][0]


TARGETS = [f"site{i}" for i in range(10)]


def test_a_lone_two_ping_miss_is_a_probe_miss():
    inc = only(diagnosis.diagnose([ev("site1", 3, 20.0)], ctx(), 288))
    assert inc["class"] == "probe_miss"
    assert inc["confidence"] == "medium"
    assert "one point on site1" in inc["evidence"][0]


def test_a_miss_with_its_https_sibling_answering_is_high_confidence():
    c = ctx(app_sites={"site1"}, app={"site1": []})
    inc = only(diagnosis.diagnose([ev("site1", 3, 20.0)], c, 288))
    assert (inc["class"], inc["confidence"]) == ("probe_miss", "high")


def test_a_miss_whose_https_sibling_also_failed_is_the_destination():
    c = ctx(app_sites={"site1"}, app={"site1": [(T0 + 3 * STEP, 1.0)]})
    inc = only(diagnosis.diagnose([ev("site1", 3, 20.0)], c, 288))
    assert inc["class"] == "destination"
    assert any("also failed over TCP/HTTP" in e for e in inc["evidence"])


def test_one_site_for_several_cycles_is_the_destination():
    events = [ev("site1", s, 60.0) for s in range(3, 7)]
    inc = only(diagnosis.diagnose(events, ctx(), 288))
    assert (inc["class"], inc["confidence"]) == ("destination", "high")
    assert inc["minutes"] == 20


def test_dns_only_is_named():
    events = [ev("quad9", s, 40.0, "dns") for s in range(3, 6)]
    inc = only(diagnosis.diagnose(events, ctx(), 288))
    assert (inc["class"], inc["detail"]) == ("destination", "dns")


def test_a_hung_radio_is_this_hosts_wifi_not_an_outage():
    events = [ev(t, s, 100.0) for t in TARGETS for s in range(3, 9)]
    deaf = [{"start_epoch": T0 + 2 * STEP, "seconds": 6 * STEP,
             "deaf": "received nothing"}]
    inc = only(diagnosis.diagnose(events, ctx(deaf=deaf), 288))
    assert (inc["class"], inc["detail"], inc["confidence"]) == (
        "local_wifi", "deaf", "high")
    assert "This host's own Wi-Fi" in inc["summary"]


def test_a_minute_deaf_inside_a_long_episode_is_context_not_the_cause():
    events = [ev(t, s, 100.0) for t in TARGETS for s in range(3, 30)]
    deaf = [{"start_epoch": T0 + 10 * STEP, "seconds": 120, "deaf": "received nothing"}]
    wifi = [{"_epoch": T0 + i * 60, "signal_dbm": -55.0, "associated": 1.0}
            for i in range(0, 30 * 5)]
    inc = only(diagnosis.diagnose(events, ctx(deaf=deaf, wifi=wifi), 288))
    assert inc["class"] == "local_link"
    assert any("heard nothing for 2 of the" in a for a in inc["against"])


def test_everything_lost_while_the_radio_heard_is_the_line():
    events = [ev(t, s, 100.0) for t in TARGETS for s in range(3, 6)]
    wifi = [{"_epoch": T0 + i * 60, "signal_dbm": -55.0, "associated": 1.0}
            for i in range(0, 40)]
    inc = only(diagnosis.diagnose(events, ctx(wifi=wifi), 288))
    assert (inc["class"], inc["detail"], inc["confidence"]) == (
        "local_link", "outage", "high")


def test_everything_lost_while_the_gateway_answered_is_beyond_the_line():
    """Front-8 audit: total loss with the ISP gateway answering at its usual
    rate is past the house, not "the line between the house and the ISP"."""
    events = [ev(t, s, 100.0) for t in TARGETS for s in range(3, 6)]
    wifi = [{"_epoch": T0 + i * 60, "signal_dbm": -55.0, "associated": 1.0}
            for i in range(0, 40)]
    floor = diagnosis.first_hop_floor(
        [{"_epoch": T0 + i * STEP, "_value": 11.0} for i in range(0, 40)], STEP)
    inc = only(diagnosis.diagnose(events, ctx(wifi=wifi, floor=floor), 288))
    assert (inc["class"], inc["detail"], inc["confidence"]) == (
        "upstream", "outage", "high")
    assert any("kept answering" in e for e in inc["evidence"])
    assert "Beyond your line" in inc["summary"]


# Three resolvers reporting in every cycle of the test window.
RESOLVERS = {T0 + i * STEP: 3 for i in range(-5, 40)}


def test_named_sites_lost_while_gateway_and_resolvers_answer_is_the_monitor():
    """2026-08: a frozen resolv.conf; the alerter's monitor_dns."""
    events = [ev(t, s, 100.0) for t in TARGETS for s in range(3, 6)]
    floor = diagnosis.first_hop_floor(
        [{"_epoch": T0 + i * STEP, "_value": 11.0} for i in range(0, 40)], STEP)
    inc = only(diagnosis.diagnose(events, ctx(floor=floor, resolvers=RESOLVERS), 288))
    assert (inc["class"], inc["detail"]) == ("monitor", "names")
    assert "own name resolution" in inc["summary"]
    # The resolvers lost too: the Internet, not names.
    events += [ev("quad9", s, 100.0, "dns") for s in range(3, 6)]
    inc = only(diagnosis.diagnose(events, ctx(floor=floor, resolvers=RESOLVERS), 288))
    assert (inc["class"], inc["detail"]) == ("upstream", "outage")


def test_resolvers_that_reported_elsewhere_in_the_window_do_not_count():
    """Review of #337: the resolvers must have reported in the incident's own
    cycles; a window-wide count would call any total loss the monitor's DNS."""
    events = [ev(t, s, 100.0) for t in TARGETS for s in range(3, 6)]
    floor = diagnosis.first_hop_floor(
        [{"_epoch": T0 + i * STEP, "_value": 11.0} for i in range(0, 40)], STEP)
    earlier = {T0 + i * STEP: 3 for i in range(20, 30)}
    inc = only(diagnosis.diagnose(events, ctx(floor=floor, resolvers=earlier), 288))
    assert inc["class"] == "upstream"


def test_the_shared_thresholds_reach_the_diagnosis(monkeypatch):
    """VERDICT_BROAD_PCT tunes the diagnosis as it tunes the verdict."""
    events = [ev(t, s, 40.0) for t in TARGETS[:5] for s in (4, 5)]
    floor = diagnosis.first_hop_floor(
        [{"_epoch": T0 + i * STEP, "_value": 11.0} for i in range(0, 40)], STEP)
    assert only(diagnosis.diagnose(events, ctx(floor=floor), 288))["class"] != "upstream"
    monkeypatch.setenv("VERDICT_BROAD_PCT", "50")
    assert only(diagnosis.diagnose(events, ctx(floor=floor), 288))["class"] == "upstream"


def test_everything_lost_with_the_gateway_lost_too_is_still_the_line():
    events = [ev(t, s, 100.0) for t in TARGETS for s in range(3, 6)]
    wifi = [{"_epoch": T0 + i * 60, "signal_dbm": -55.0, "associated": 1.0}
            for i in range(0, 40)]
    rows = [{"_epoch": T0 + i * STEP, "_value": 11.0} for i in range(0, 40)]
    for row in rows[3:7]:
        row["_value"] = 100.0
    floor = diagnosis.first_hop_floor(rows, STEP)
    inc = only(diagnosis.diagnose(events, ctx(wifi=wifi, floor=floor), 288))
    assert (inc["class"], inc["detail"]) == ("local_link", "outage")


def test_everything_lost_on_a_host_without_wifi_data_cannot_clear_the_host():
    events = [ev(t, s, 100.0) for t in TARGETS for s in range(3, 6)]
    inc = only(diagnosis.diagnose(events, ctx(), 288))
    assert (inc["class"], inc["confidence"]) == ("local_link", "low")
    assert "cannot be ruled out" in inc["against"][0]


def test_first_hop_cuts_with_several_destinations_are_the_line():
    events = [ev(t, 4, 40.0) for t in TARGETS[:4]]
    cuts = [{"start_epoch": T0 + 3 * STEP + 100, "seconds": 90, "confirmed": True}]
    inc = only(diagnosis.diagnose(events, ctx(cuts=cuts), 288))
    assert (inc["class"], inc["detail"]) == ("local_link", "cuts")


def test_most_destinations_with_a_clean_first_hop_is_upstream():
    events = [ev(t, s, 40.0, "topsites" if i % 2 else "netflix")
              for i, t in enumerate(TARGETS[:8]) for s in (4, 5)]
    floor = diagnosis.first_hop_floor(
        [{"_epoch": T0 + i * STEP, "_value": 11.0} for i in range(0, 40)], STEP)
    inc = only(diagnosis.diagnose(events, ctx(floor=floor), 288))
    assert (inc["class"], inc["confidence"]) == ("upstream", "high")
    assert any("usual 11%" in e for e in inc["evidence"])


def test_upstream_without_first_hop_data_is_never_high():
    events = [ev(t, s, 40.0) for t in TARGETS[:8] for s in (4, 5)]
    inc = only(diagnosis.diagnose(events, ctx(cpe=False), 288))
    assert inc["class"] == "upstream"
    assert inc["confidence"] == "low"


def test_low_grade_loss_on_most_paths_with_a_normal_first_hop_is_spread():
    """The night shape: a ping or two on most paths, never most at once."""
    events = [ev(t, 3 + i, 20.0) for i, t in enumerate(TARGETS[:7])]
    floor = diagnosis.first_hop_floor(
        [{"_epoch": T0 + i * STEP, "_value": 11.0} for i in range(0, 40)], STEP)
    inc = only(diagnosis.diagnose(events, ctx(floor=floor), 288))
    assert (inc["class"], inc["detail"], inc["confidence"]) == (
        "upstream", "spread", "medium")


def test_low_grade_loss_with_a_rising_first_hop_is_the_line():
    events = [ev(t, 3 + i, 20.0) for i, t in enumerate(TARGETS[:7])]
    rows = [{"_epoch": T0 + i * STEP, "_value": 11.0} for i in range(0, 40)]
    for row in rows[3:10]:
        row["_value"] = 35.0
    floor = diagnosis.first_hop_floor(rows, STEP)
    inc = only(diagnosis.diagnose(events, ctx(floor=floor), 288))
    assert (inc["class"], inc["detail"]) == ("local_link", "degraded")


def test_a_few_unrelated_sites_are_unclear_and_low():
    events = [ev(t, 3 + i, 20.0) for i, t in enumerate(TARGETS[:3])]
    inc = only(diagnosis.diagnose(events, ctx(), 288))
    assert (inc["class"], inc["confidence"]) == ("unclear", "low")


def test_the_isp_first_hop_target_is_not_a_destination():
    """CPE_IPv4's ping loss is the gateway's rate-limit floor."""
    result = diagnosis.diagnose([ev("CPE_IPv4", 3, 20.0, "cpe")], ctx(), 288)
    assert result["incidents"] == []


def test_a_target_down_all_window_is_chronic_and_left_out():
    events = [ev("amazon", s, 100.0) for s in range(0, 288)]
    result = diagnosis.diagnose(events, ctx(), 288)
    assert result["chronic"] == ["amazon"]
    assert result["incidents"] == []


def test_a_confirmed_microcut_alone_is_its_own_incident():
    cut = {"start_epoch": T0 + 1000, "seconds": 70, "confirmed": True, "windows": 3,
           "max_loss_pct": 100.0, "total": True}
    inc = only(diagnosis.diagnose([], ctx(cuts=[cut]), 288))
    assert (inc["class"], inc["detail"]) == ("local_link", "microcut")


def test_a_deaf_span_alone_is_this_hosts_wifi():
    span = {"start_epoch": T0 + 1000, "seconds": 190, "deaf": "not associated"}
    inc = only(diagnosis.diagnose([], ctx(deaf=[span]), 288))
    assert (inc["class"], inc["detail"]) == ("local_wifi", "deaf")


def test_hang_cuts_on_every_cpe_target_merge_into_one_span():
    cuts = [{"start_epoch": 100, "seconds": 300, "deaf": "received nothing"},
            {"start_epoch": 120, "seconds": 400, "deaf": "not associated"}]
    assert diagnosis.merge_spans(cuts) == [
        {"start_epoch": 100, "seconds": 420, "deaf": "not associated"}]


def test_site_key_pairs_icmp_with_its_app_layers():
    assert diagnosis.site_key("Cloudflare_h1") == diagnosis.site_key("cloudflare")
    assert diagnosis.site_key("W_x_com_icmp") == diagnosis.site_key("W_x_com_tcp")


def test_wifi_minutes_reads_only_the_uplink_interface():
    rows = [
        {"_epoch": 60, "_field": "uplink", "_value": 1.0, "interface": "wlan0"},
        {"_epoch": 60, "_field": "signal_dbm", "_value": -50.0, "interface": "wlan0"},
        {"_epoch": 60, "_field": "signal_dbm", "_value": -90.0, "interface": "wlan1"},
    ]
    assert diagnosis.wifi_minutes(rows) == [{"_epoch": 60, "signal_dbm": -50.0}]
    assert diagnosis.wifi_minutes([]) is None


# ---------------------------------------------------------------------------
# The MCP tool
# ---------------------------------------------------------------------------


@pytest.fixture()
def no_api(monkeypatch):
    monkeypatch.setattr(backends, "get_config_api", lambda: ExplodingConfigAPI())


def test_diagnose_loss_refuses_long_windows(no_api):
    assert "error" in server.diagnose_loss(hours=0)
    assert "error" in server.diagnose_loss(hours=169)


def test_diagnose_loss_end_to_end(monkeypatch, no_api):
    ts = lambda step: datetime.fromtimestamp(T0 + step * STEP, tz=timezone.utc)  # noqa: E731

    def fake(flux):
        if "distinct(column: \"target\") |> count()" in flux:
            return [{"_time": ts(s), "_value": 10} for s in range(0, 10)]
        if 'r._measurement == "latency"' in flux and "r._value >=" in flux:
            return [{"_time": ts(4), "target": "site1", "category": "topsites",
                     "_value": 0.2}]
        return []

    monkeypatch.setattr(server, "query_influx", fake)
    monkeypatch.setattr(server, "_cadences", lambda: {})
    result = server.diagnose_loss(hours=24)
    assert result["by_class"] == {"probe_miss": 1}
    inc = result["incidents"][0]
    assert inc["start"] == ts(3).isoformat() and inc["end"] == ts(4).isoformat()
    assert result["coverage"] == {"wifi": False, "cpe": False, "app_layer": False,
                                  "breadth_only": False}


def test_diagnose_loss_survives_optional_queries_failing(monkeypatch, no_api):
    def fake(flux):
        if "wifi_link" in flux or "http_latency" in flux:
            raise RuntimeError("boom")
        return []

    monkeypatch.setattr(server, "query_influx", fake)
    monkeypatch.setattr(server, "_cadences", lambda: {})
    result = server.diagnose_loss(hours=24)
    assert result["incidents"] == [] and result["coverage"]["wifi"] is False


def test_diagnose_loss_reports_a_failed_required_query(monkeypatch, no_api):
    def fake(flux):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(server, "query_influx", fake)
    monkeypatch.setattr(server, "_cadences", lambda: {})
    result = server.diagnose_loss(hours=24)
    assert "error" in result and "secret detail" not in str(result)


# ---------------------------------------------------------------------------
# Review cases: no contradictions, wide Wi-Fi rows, missing evidence
# ---------------------------------------------------------------------------


def wifi_rows(n, width=60, **override):
    rows = [{"_epoch": T0 + (i + 1) * width, "signal_dbm": -55.0, "associated": 1.0}
            for i in range(n)]
    for i, fields in override.items():
        rows[int(i)].update(fields)
    return rows


def test_spread_never_says_no_drops_when_the_carrier_dropped():
    events = [ev(t, 3 + i, 20.0) for i, t in enumerate(TARGETS[:7])]
    floor = diagnosis.first_hop_floor(
        [{"_epoch": T0 + i * STEP, "_value": 11.0} for i in range(0, 40)], STEP)
    wifi = wifi_rows(60, **{"20": {"drops": 1}})
    inc = only(diagnosis.diagnose(events, ctx(floor=floor, wifi=wifi), 288))
    said = " ".join(inc["evidence"])
    assert "carrier dropped 1" in " ".join(inc["against"])
    assert "no carrier drop" not in said


def test_outage_never_says_receiving_the_whole_time_when_it_was_not():
    events = [ev(t, s, 100.0) for t in TARGETS for s in range(3, 9)]
    wifi = wifi_rows(60, **{"20": {"associated": 0.0}})
    inc = only(diagnosis.diagnose(events, ctx(wifi=wifi), 288))
    assert inc["class"] == "local_link"
    assert not any("whole time" in e for e in inc["evidence"])
    assert any("not associated for 1 minute" in a for a in inc["against"])


def test_five_minute_wifi_rows_still_cover_a_one_cycle_incident():
    """Past two days the rows are 5 min wide, stamped at their end."""
    lo = T0 + 3 * STEP
    rows = [{"_epoch": lo + 240, "signal_dbm": -55.0, "associated": 0.0}]
    inc = only(diagnosis.diagnose([ev(t, 4, 100.0) for t in TARGETS],
                                  ctx(wifi=rows, wifi_width=300.0), 288))
    assert (inc["class"], inc["detail"]) == ("local_wifi", "disassociated")
    assert "not associated for 5 minute(s)" in inc["evidence"][0]


def test_a_dropped_carrier_in_a_short_incident_is_the_wifi():
    wifi = wifi_rows(60, **{"20": {"drops": 2}})
    inc = only(diagnosis.diagnose([ev(t, 4, 60.0) for t in TARGETS[:5]],
                                  ctx(wifi=wifi), 288))
    assert (inc["class"], inc["detail"]) == ("local_wifi", "carrier")


def test_weak_signal_while_the_first_hop_cut_is_the_wifi():
    wifi = wifi_rows(60, **{str(i): {"signal_dbm": -82.0, "weak": 3}
                            for i in range(17, 21)})
    cuts = [{"start_epoch": T0 + 3 * STEP + 60, "seconds": 90, "confirmed": True}]
    inc = only(diagnosis.diagnose([ev(t, 4, 40.0) for t in TARGETS[:2]],
                                  ctx(wifi=wifi, cuts=cuts), 288))
    assert (inc["class"], inc["detail"]) == ("local_wifi", "weak_signal")
    assert "-82 dBm" in inc["evidence"][0]


def test_a_lone_microcut_without_wifi_data_is_never_high():
    cut = {"start_epoch": T0 + 1000, "seconds": 70, "confirmed": True, "windows": 3,
           "max_loss_pct": 100.0, "total": True}
    inc = only(diagnosis.diagnose([], ctx(cuts=[cut]), 288))
    assert inc["confidence"] == "low"
    assert "cannot be ruled out" in inc["against"][0]


def test_a_lone_point_beside_a_deaf_minute_keeps_the_context():
    deaf = [{"start_epoch": T0 + 3 * STEP + 100, "seconds": 100,
             "deaf": "received nothing"}]
    inc = only(diagnosis.diagnose([ev("site1", 4, 20.0)], ctx(deaf=deaf), 288))
    assert inc["class"] != "probe_miss"
    assert any("heard nothing" in a for a in inc["against"])


def test_chronic_targets_leave_the_denominator():
    events = [ev("amazon", s, 100.0) for s in range(0, 288)]
    events += [ev(t, 50, 40.0) for t in TARGETS[:6]]
    reporting = {T0 + i * STEP: 7 for i in range(0, 300)}
    inc = only(diagnosis.diagnose(events, ctx(reporting=reporting), 288))
    assert inc["targets_reporting"] == 6
    assert inc["class"] == "upstream"


def test_one_silent_cycle_does_not_split_an_incident_two_do():
    one = diagnosis.diagnose([ev("site1", 3, 60.0), ev("site1", 5, 60.0)], ctx(), 288)
    two = diagnosis.diagnose([ev("site1", 3, 60.0), ev("site1", 6, 60.0)], ctx(), 288)
    assert len(one["incidents"]) == 1 and len(two["incidents"]) == 2


def test_ipv6_only_is_named():
    c = ctx(ipv6=diagnosis_query.is_ipv6)
    inc = only(diagnosis.diagnose([ev("google6", s, 100.0) for s in (3, 4)], c, 288))
    assert (inc["class"], inc["detail"]) == ("destination", "ipv6")


def test_diagnose_loss_flux_reads_wifi_by_window_and_leaves_out_the_first_hop(
        monkeypatch, no_api):
    seen = []

    def fake(flux):
        seen.append(flux)
        return []

    monkeypatch.setattr(server, "query_influx", fake)
    monkeypatch.setattr(server, "_cadences", lambda: {})
    server.diagnose_loss(hours=24)
    assert any("aggregateWindow(every: 60s" in f and "wifi_link" in f for f in seen)
    counting = next(f for f in seen if 'distinct(column: "target") |> count()' in f)
    assert 'r.category != "cpe"' in counting
    seen.clear()
    server.diagnose_loss(hours=72)
    assert any("aggregateWindow(every: 300s" in f and "wifi_link" in f for f in seen)


# --- get_incident_history: the incidents the alerter wrote down ----------------

def _stored(start, cls, **over):
    row = {"_time": datetime.fromtimestamp(start, tz=timezone.utc),
           "end_epoch": start + 600, "minutes": 10, "class": cls, "detail": "",
           "confidence": "high", "summary": f"summary of {cls}",
           "targets": "google,amazon", "targets_affected": 2, "targets_reporting": 20,
           "evidence": "first | second", "against": "", "max_loss_pct": 40.0}
    row.update(over)
    return row


def test_history_lists_stored_incidents_newest_first_without_probe_misses(monkeypatch):
    rows = [_stored(T0 + 7200, "upstream"), _stored(T0 + 3600, "probe_miss"),
            _stored(T0, "local_wifi", detail="deaf", targets="", max_loss_pct=-1.0)]
    queries = []

    def fake(f):
        queries.append(f)
        # The second query asks for the oldest stored incident.
        return [{"_time": datetime.fromtimestamp(T0 - 86400, tz=timezone.utc)}] \
            if "first()" in f else rows

    monkeypatch.setattr(server, "query_influx", fake)
    out = server.get_incident_history(days=30)
    assert "loss_incident" in queries[0] and "-30d" in queries[0]
    assert [i["class"] for i in out["incidents"]] == ["upstream", "local_wifi"]
    assert out["by_class"] == {"upstream": 1, "probe_miss": 1, "local_wifi": 1}
    first = out["incidents"][0]
    assert first["targets"] == ["google", "amazon"]
    assert first["evidence"] == ["first", "second"] and first["against"] == []
    assert first["end"] == datetime.fromtimestamp(T0 + 7800, tz=timezone.utc).isoformat()
    assert out["incidents"][1]["detail"] == "deaf" and out["incidents"][1]["targets"] == []
    assert "max_loss_pct" not in out["incidents"][1]  # the -1 of "no loss run"
    assert out["incidents"][0]["max_loss_pct"] == 40.0
    # The start of the whole history, not of the window.
    assert out["stored_from"] == datetime.fromtimestamp(T0 - 86400, tz=timezone.utc).isoformat()
    with_misses = server.get_incident_history(days=30, include_probe_miss=True)
    assert len(with_misses["incidents"]) == 3


def test_history_says_when_nothing_is_stored_yet(monkeypatch):
    monkeypatch.setattr(server, "query_influx", lambda f: [])
    out = server.get_incident_history()
    assert out["incidents"] == [] and out["stored_from"] is None


@pytest.mark.parametrize("days", [0, 366, "x"])
def test_history_refuses_a_bad_window(monkeypatch, days):
    monkeypatch.setattr(server, "query_influx", lambda f: pytest.fail("queried"))
    assert "error" in server.get_incident_history(days=days)


def test_history_query_failure_is_a_static_error(monkeypatch):
    def boom(f):
        raise RuntimeError("secret detail http://influx:8086 token=abc")
    monkeypatch.setattr(server, "query_influx", boom)
    out = server.get_incident_history()
    assert out["error"].startswith("InfluxDB query failed")
    assert "secret" not in str(out)


# ---------------------------------------------------------------------------
# Breadth-only voters (common.breadth): counted per cycle, never named
# ---------------------------------------------------------------------------


def _breadth(steps, reporting=30, lossy=0, lost=0):
    return {T0 + s * STEP: {"reporting": reporting, "lossy": lossy, "lost": lost}
            for s in steps}


def test_breadth_only_voters_make_two_sites_part_of_a_broad_loss():
    """Two pinged sites alone are theirs; with 25 of 30 web services lossy
    in the same cycle they are part of 27 of 40."""
    events = [ev(t, 3, 60.0) for t in ("site1", "site2")]
    assert only(diagnosis.diagnose(events, ctx(), 288))["class"] == "destination"
    inc = only(diagnosis.diagnose(
        events, ctx(breadth=_breadth(range(0, 10), lossy=0) | _breadth([3], lossy=25)),
        288))
    assert inc["class"] == "upstream"
    assert inc["evidence"][0].startswith(
        "27 of 40 destinations lost packets in the same cycle")
    assert inc["targets"] == ["site1", "site2"]
    assert inc["targets_affected"] == 27 and inc["targets_reporting"] == 40


def test_breadth_only_voters_answering_keep_two_sites_theirs():
    events = [ev(t, s, 60.0) for t in ("site1", "site2") for s in (3, 4)]
    inc = only(diagnosis.diagnose(events, ctx(breadth=_breadth(range(0, 10))), 288))
    assert inc["class"] == "destination"
    assert "(2 of 40 destinations)" in inc["evidence"][0]


def test_a_cut_of_the_breadth_alone_is_an_incident_that_names_no_target():
    inc = only(diagnosis.diagnose(
        [], ctx(breadth=_breadth(range(0, 10)) | _breadth([5, 6], lossy=28)), 288))
    assert inc["class"] == "upstream"
    assert inc["targets"] == [] and inc["targets_affected"] == 28
    assert inc["max_loss_pct"] == 0.0


def test_scattered_breadth_loss_never_makes_an_incident_of_its_own():
    """A few web services losing a point here and there: below the broad
    share, so no incident -- and no per-target one either."""
    result = diagnosis.diagnose(
        [], ctx(breadth=_breadth(range(0, 10), lossy=3)), 288)
    assert result["incidents"] == []


def test_breadth_total_loss_with_pings_answering_is_not_this_hosts_dns():
    floor = {"cycles": {T0 + s * STEP: 10.0 for s in range(0, 10)}, "usual": 10.0}
    inc = only(diagnosis.diagnose(
        [], ctx(breadth=_breadth(range(0, 10), reporting=50)
                | _breadth([4, 5, 6], reporting=50, lossy=50, lost=50),
                resolvers={T0 + s * STEP: 3 for s in range(0, 10)}, floor=floor), 288))
    assert (inc["class"], inc["detail"]) == ("upstream", "outage")


def test_diagnose_loss_counts_the_breadth_in_targets_reporting(monkeypatch, no_api):
    ts = lambda step: datetime.fromtimestamp(T0 + step * STEP, tz=timezone.utc)  # noqa: E731

    def fake(flux):
        if "reduce(" in flux:
            return [{"_time": ts(s), "reporting": 30, "lossy": 0, "lost": 0}
                    for s in range(0, 10)]
        if '"http_latency"' in flux and "mean()" in flux:
            return [{"target": f"svc{i}", "_value": 1.0} for i in range(30)]
        if "distinct(column: \"target\") |> count()" in flux:
            return [{"_time": ts(s), "_value": 10} for s in range(0, 10)]
        return []

    monkeypatch.setattr(server, "query_influx", fake)
    monkeypatch.setattr(server, "_cadences", lambda: {})
    result = server.diagnose_loss(hours=24)
    assert result["targets_reporting"] == 40
    assert result["coverage"]["breadth_only"] is True


def test_a_lone_miss_beside_scattered_breadth_loss_is_still_a_probe_miss():
    """Review of #346: a few of 200 web services losing a point in the same
    cycle is their background, not part of the named site's miss."""
    for lossy in (1, 3, 10):
        c = ctx(breadth=_breadth(range(0, 10), reporting=200, lossy=lossy))
        inc = only(diagnosis.diagnose([ev("site1", 3, 20.0)], c, 288))
        assert inc["class"] == "probe_miss" and inc["targets_affected"] == 1


def test_one_site_beside_scattered_breadth_loss_is_still_the_destination():
    c = ctx(breadth=_breadth(range(0, 10), reporting=200, lossy=3))
    inc = only(diagnosis.diagnose([ev("site1", s, 60.0) for s in (3, 4)], c, 288))
    assert inc["class"] == "destination"
    assert inc["evidence"][0] == "only site1 lost packets (1 of 210 destinations)"
    assert inc["targets_affected"] == 1
