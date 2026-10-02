"""The dashboard's traffic card: config-manager's /traffic, what the Pi
sent and received per period, on the interface and to the Internet."""

import pytest
from conftest import login

from app.routes import dashboard as dashboard_module
from app.services import config_api as config_api_module
from test_connection import _stub_dashboard


def period(key, label, rx, tx, coverage=100.0, internet=None, first="2026-10-01",
           last="2026-10-02"):
    return {"period": key, "label": label, "first": first, "last": last,
            "uplink": {"rx": rx, "tx": tx, "total": rx + tx, "seconds": 1.0,
                       "coverage_pct": coverage},
            "internet": internet}


def net(rx, tx, seconds=1.0):
    return {"rx": rx, "tx": tx, "total": rx + tx, "seconds": seconds, "coverage_pct": 100.0}


# config-manager /traffic (traffic.py report()), a meter running since 30/9.
BODY = {
    "available": True, "timezone": "America/Chicago", "interfaces": ["wlan0"],
    "since": "2026-09-30", "internet_available": True,
    "periods": [
        period("today", "today", 450_000_000, 150_000_000, internet=net(400_000_000, 120_000_000)),
        period("yesterday", "yesterday", 900_000_000, 300_000_000,
               internet=net(800_000_000, 250_000_000)),
        period("this_week", "this week", 1_750_000_000, 550_000_000, 44.4,
               internet=net(1_200_000_000, 370_000_000)),
        period("this_month", "this month", 1_350_000_000, 450_000_000,
               internet=net(1_200_000_000, 370_000_000)),
        # The netmeter was not running last month: no Internet figure.
        period("last_month", "last month", 400_000_000, 100_000_000, 1.7,
               internet=net(0, 0, seconds=0.0)),
        period("last_30_days", "last 30 days", 1, 1, 6.7),
    ],
    "days": [],
    "services": {"this_month": [
        {"service": "smokeping", "rx": 600_000_000, "tx": 200_000_000, "total": 800_000_000},
        {"service": "host", "rx": 50_000_000, "tx": 10_000_000, "total": 60_000_000}],
        "last_month": []},
}


def _dashboard(client, monkeypatch, body):
    _stub_dashboard(monkeypatch, {"available": False, "reason": "stubbed"})
    monkeypatch.setattr(dashboard_module.config_api, "get_traffic", lambda: body)
    login(client)
    return client.get("/").get_data(as_text=True)


def test_summary_rows_in_order_with_human_units():
    card = dashboard_module.summarize_traffic(BODY)
    assert [r["period"] for r in card["rows"]] == [
        "today", "yesterday", "this_week", "this_month", "last_month"]
    month = card["month"]
    assert (month["total"], month["rx"], month["tx"]) == ("1.80 GB", "1.35 GB", "450 MB")
    assert month["internet"] == "1.57 GB" and month["partial"] is False
    assert card["interfaces"] == "wlan0"


def test_an_unmeasured_internet_figure_is_none_and_low_coverage_is_partial():
    rows = {r["period"]: r for r in dashboard_module.summarize_traffic(BODY)["rows"]}
    assert rows["last_month"]["internet"] is None
    assert rows["last_month"]["partial"] is True
    assert rows["this_week"]["partial"] is True
    assert rows["today"]["partial"] is False


def test_the_internet_figure_shows_its_own_partial_coverage(client, monkeypatch):
    body = {**BODY, "internet_since": "2026-10-01", "periods": [
        {**p, "internet": {**p["internet"], "coverage_pct": 40.0}}
        if p["period"] == "this_month" else p for p in BODY["periods"]]}
    card = dashboard_module.summarize_traffic(body)
    month = card["month"]
    assert month["internet_partial"] is True and month["internet_coverage"] == 40.0
    assert month["partial"] is False
    html = _dashboard(client, monkeypatch, body)
    assert "(measured for 40% of the month)" in html
    assert "(Internet only since 2026-10-01)" in html


def test_services_use_the_meters_labels():
    services = dashboard_module.summarize_traffic(BODY)["services"]
    assert services[0] == {"service": "smokeping", "total": "800 MB", "rx": "600 MB",
                           "tx": "200 MB"}
    assert services[1]["service"] == "the host, outside the stack"


# The same table pins config-manager's traffic.human() and web-admin's
# human_bytes(): two copies (containers cannot import each other).
HUMAN_BYTES_CASES = [
    (0, "0"), (999, "1 kB"), (512_000, "512 kB"), (999_499, "999 kB"),
    (999_999, "1.0 MB"), (5_500_000, "5.5 MB"), (9_949_999, "9.9 MB"),
    (9_999_999, "10 MB"), (450_000_000, "450 MB"), (999_499_999, "999 MB"),
    (999_999_999, "1.00 GB"), (1_570_000_000, "1.57 GB"),
]


@pytest.mark.parametrize("n, text", HUMAN_BYTES_CASES)
def test_human_bytes_matches_config_manager(n, text):
    assert dashboard_module.human_bytes(n) == text
    assert dashboard_module.human_bytes(None) == "0"


def test_the_card_and_the_top_line(client, monkeypatch):
    html = _dashboard(client, monkeypatch, BODY)
    assert 'id="traffic-card"' in html
    assert "This month: 1.80 GB" in html and "(450 MB sent, 1.35 GB received)" in html
    assert 'id="traffic-month-internet"' in html and "1.57 GB" in html
    assert 'id="top-month"' in html
    assert "This month by service" in html and "the host, outside the stack" in html
    assert "sudo smoking-pi traffic" in html
    # Partial coverage is flagged, and the unmeasured Internet cell is a dash.
    assert '44%<span class="visually-hidden"> (partial)</span>' in html
    assert 'aria-label="Traffic by period"' in html and 'scope="col"' in html
    assert "<td class=\"text-end\">-</td>" in html
    assert "last 30 days" not in html  # the CLI's, not the card's


def test_without_the_netmeter_there_is_no_internet_column(client, monkeypatch):
    body = {**BODY, "internet_available": False,
            "periods": [{**p, "internet": None} for p in BODY["periods"]],
            "services": {"this_month": [], "last_month": []}}
    html = _dashboard(client, monkeypatch, body)
    assert "<th class=\"text-end\">Internet</th>" not in html
    assert 'id="traffic-month-internet"' not in html
    assert 'id="traffic-services"' not in html


def test_no_ledger_says_why_and_the_dashboard_still_renders(client, monkeypatch):
    html = _dashboard(client, monkeypatch, {
        "available": False, "reason": "no traffic ledger yet (Pro only)"})
    assert 'id="traffic-card"' in html and "No figures: no traffic ledger yet" in html
    assert 'id="top-month"' not in html


def test_the_gateway_caches_a_report_but_not_a_failure(monkeypatch):
    gw = config_api_module.ConfigAPIGateway()
    calls = []

    def ok():
        calls.append(1)
        return BODY
    monkeypatch.setattr(gw.client, "get_traffic", ok)
    gw.get_traffic()
    gw.get_traffic()
    assert len(calls) == 1

    gw2 = config_api_module.ConfigAPIGateway()

    def boom():
        calls.append(2)
        raise RuntimeError("down")
    monkeypatch.setattr(gw2.client, "get_traffic", boom)
    assert gw2.get_traffic()["available"] is False
    gw2.get_traffic()
    assert calls.count(2) == 2
