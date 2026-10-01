"""The dashboard's measurement budget card: config-manager's /budget,
against the ceilings, most expensive probe first."""

from conftest import login

from app.routes import dashboard as dashboard_module
from app.services import config_api as config_api_module
from test_connection import _stub_dashboard

# config-manager /budget on the shipped seed (docs/measurement-budget.md).
SEED = {
    "available": True, "complete": True, "targets": 21,
    "samples_per_hour": 1404.0, "mb_per_day": 98.42,
    "ceiling": {"mb_per_day": 1000.0, "samples_per_hour": 20000.0},
    "used": {"samples_pct": 7.0, "bandwidth_pct": 9.8},
    "over": False, "unpriced": [],
    "by_probe": [
        {"probe": "CurlHTTP1", "class": "Curl", "targets": 3, "step": 300, "pings": 3,
         "samples_per_hour": 108.0, "bytes_per_sample": 12000, "mb_per_day": 31.1},
        {"probe": "FPing", "class": "FPing", "targets": 6, "step": 300, "pings": 10,
         "samples_per_hour": 720.0, "bytes_per_sample": 168, "mb_per_day": 2.9},
    ],
}


def _dashboard(client, monkeypatch, body):
    _stub_dashboard(monkeypatch, {"available": False, "reason": "stubbed"})
    monkeypatch.setattr(dashboard_module.config_api, "get_budget", lambda: body)
    login(client)
    return client.get("/").get_data(as_text=True)


def test_the_seed_is_a_small_green_share_of_both_ceilings():
    card = dashboard_module.summarize_budget(SEED)
    assert card["bandwidth"] == {"pct": 9.8, "width": 9.8, "badge": "success"}
    assert card["samples"]["badge"] == "success"
    assert card["mb_headroom"] == 901.6
    assert card["samples_headroom"] == 18596
    # 98.42 MB a day is ~9.1 Kbps on average.
    assert card["kbps"] == 9.1


def test_the_badge_turns_yellow_then_red_and_the_bar_stops_at_full():
    def card(pct):
        return dashboard_module.summarize_budget(
            {**SEED, "used": {"bandwidth_pct": pct, "samples_pct": 1.0}})["bandwidth"]
    assert card(74.9)["badge"] == "success"
    assert card(75.0)["badge"] == "warning"
    assert card(100.0)["badge"] == "warning"
    assert card(190.0) == {"pct": 190.0, "width": 100, "badge": "danger"}


def test_over_the_ceiling_headroom_is_zero_not_negative():
    over = {**SEED, "mb_per_day": 1900.0, "over": True,
            "used": {"bandwidth_pct": 190.0, "samples_pct": 7.0}}
    assert dashboard_module.summarize_budget(over)["mb_headroom"] == 0


def test_the_card_shows_the_figures_and_the_most_expensive_probe(client, monkeypatch):
    html = _dashboard(client, monkeypatch, SEED)
    assert 'id="budget-card"' in html
    assert "~98 of 1000 MB/day (9.8%)" in html
    assert "1404 of 20000 per hour (7.0%)" in html
    assert "<td>CurlHTTP1</td>" in html and "31.1" in html
    assert html.index("<td>CurlHTTP1</td>") < html.index("<td>FPing</td>")
    assert 'id="budget-over"' not in html
    assert 'id="budget-incomplete"' not in html
    assert "smoking-pi budget" in html


def test_the_top_card_uses_the_measured_costs(client, monkeypatch):
    # It used to count every sample as 64 bytes, ~10x low on the seed.
    html = _dashboard(client, monkeypatch, SEED)
    assert "~98 MB/day" in html and "9.8% of budget" in html
    assert "~9.1 Kbps on average" in html


def test_over_budget_says_what_brings_it_back(client, monkeypatch):
    body = {**SEED, "over": True, "used": {"bandwidth_pct": 190.0, "samples_pct": 7.0}}
    html = _dashboard(client, monkeypatch, body)
    assert 'id="budget-over"' in html and "longer step" in html
    assert "progress-bar bg-danger" in html and "width: 100%" in html


def test_unpriced_and_incomplete_are_named(client, monkeypatch):
    rows = SEED["by_probe"] + [
        {"probe": "MyProbe", "class": "MyProbe", "targets": 1, "step": 300, "pings": 5,
         "samples_per_hour": 60.0, "bytes_per_sample": None, "mb_per_day": None}]
    body = {**SEED, "complete": False, "unpriced": ["MyProbe"], "by_probe": rows}
    html = _dashboard(client, monkeypatch, body)
    assert "No byte estimate for MyProbe" in html
    assert 'id="budget-incomplete"' in html
    assert '<td class="text-end">?</td>' in html


def test_unreachable_says_so_without_the_exception(client, monkeypatch):
    _stub_dashboard(monkeypatch, {"available": False, "reason": "stubbed"})
    monkeypatch.delattr(dashboard_module.config_api, "get_budget", raising=False)
    monkeypatch.setattr(dashboard_module.config_api, "_budget_cache", None, raising=False)

    def unreachable():
        raise RuntimeError("http://config-manager:5000 secret-token-xyz")
    monkeypatch.setattr(dashboard_module.config_api.client, "get_budget", unreachable)
    login(client)
    html = client.get("/").get_data(as_text=True)
    card = html[html.index('id="budget-card"'):]
    assert "Could not check: config-manager unreachable" in card
    assert "secret-token-xyz" not in html and "config-manager:5000" not in html
    assert "unknown" in html


def test_a_report_is_reused_for_a_minute_and_a_failure_is_not(monkeypatch):
    gw = dashboard_module.config_api
    monkeypatch.setattr(gw, "_budget_cache", None, raising=False)
    answers = [RuntimeError("down"), SEED, {**SEED, "targets": 99}]
    calls = []

    def ask():
        calls.append(1)
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(gw.client, "get_budget", ask)
    now = [1000.0]
    monkeypatch.setattr(config_api_module.time, "monotonic", lambda: now[0])
    assert gw.get_budget()["available"] is False      # failure: not kept
    assert gw.get_budget()["targets"] == 21           # asked again
    now[0] += 59
    assert gw.get_budget()["targets"] == 21           # reused
    assert len(calls) == 2
    now[0] += 2
    assert gw.get_budget()["targets"] == 99           # a minute later: asked
    assert len(calls) == 3


def test_a_body_that_is_not_an_object_is_unavailable(monkeypatch):
    gw = dashboard_module.config_api
    monkeypatch.setattr(gw, "_budget_cache", None, raising=False)
    monkeypatch.setattr(gw.client, "get_budget", lambda: ["not", "a", "dict"])
    body = gw.get_budget()
    assert body["available"] is False and "see web-admin log" in body["reason"]


MEASURED = {"scope": "uplink", "interface": "wlan0", "mb_per_day": 1450.5, "rx_mb": 1200.0,
            "tx_mb": 250.5, "hours": 24.0, "minutes": 1440, "kbps": 134.3, "pct_of_ceiling": 145.1,
            "stale": False}


def test_measured_is_summarized_beside_the_estimate():
    m = dashboard_module.summarize_budget({**SEED, "measured": MEASURED})["measured"]
    assert m["interface"] == "wlan0" and m["mb_per_day"] == 1450.5
    assert (m["pct"], m["width"], m["badge"]) == (145.1, 100, "danger")
    assert dashboard_module.summarize_budget(SEED)["measured"] is None
    assert dashboard_module.summarize_budget({**SEED, "measured": "junk"})["measured"] is None


def test_the_card_shows_the_measured_uplink(client, monkeypatch):
    html = _dashboard(client, monkeypatch, {**SEED, "measured": MEASURED})
    card = html[html.index('id="budget-card"'):]
    assert 'id="budget-measured"' in card
    assert "Measured on wlan0" in card and "~1450 MB/day (145.1% of the ceiling)" in card
    assert "1200 MB in, 250 MB out over the last 24.0 h" in card
    assert "not only the measurements" in card
    assert 'id="top-measured"' in html


def test_no_meter_no_measured_line(client, monkeypatch):
    html = _dashboard(client, monkeypatch, SEED)
    assert 'id="budget-measured"' not in html and 'id="top-measured"' not in html


def test_a_stopped_meter_is_marked_stale(client, monkeypatch):
    html = _dashboard(client, monkeypatch, {**SEED, "measured": {**MEASURED, "stale": True}})
    card = html[html.index('id="budget-measured"'):]
    assert ">stale<" in card[:400]


def test_a_new_meter_says_minutes():
    m = dashboard_module.summarize_budget(
        {**SEED, "measured": {**MEASURED, "hours": 0.1, "minutes": 5}})["measured"]
    assert m["covered"] == "5 min"


def test_minutes_of_data_are_shown_but_not_judged(client, monkeypatch):
    young = {**MEASURED, "hours": 0.1, "minutes": 5, "provisional": True,
             "pct_of_ceiling": None, "mb_per_day": 432000.0}
    m = dashboard_module.summarize_budget({**SEED, "measured": young})["measured"]
    assert (m["badge"], m["width"], m["pct"]) == ("secondary", 0, None)
    html = _dashboard(client, monkeypatch, {**SEED, "measured": young})
    card = html[html.index('id="budget-measured"'):]
    assert "(under an hour of data)" in card and "of the ceiling)" not in card[:600]
    assert 'aria-valuetext="not yet a daily figure"' in card


def test_a_stopped_meter_is_marked_stale_on_the_top_card(client, monkeypatch):
    html = _dashboard(client, monkeypatch, {**SEED, "measured": {**MEASURED, "stale": True}})
    top = html[html.index('id="top-measured"'):]
    assert ">stale<" in top[:300]
