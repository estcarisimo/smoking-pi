"""Traffic accounting: the meters' ledgers summed into days, weeks and
months (traffic.py), and GET /traffic against a fake Docker client."""

import json
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

import api as api_module
import traffic


@pytest.fixture(autouse=True)
def chicago(monkeypatch):
    # Local dates are the containers' TZ; pin one so the tests do not
    # depend on the machine running them.
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def at(text: str) -> float:
    """A local time as an epoch, in the pinned TZ."""
    return datetime.fromisoformat(text).timestamp()


DAY = 86_400.0


def uplink_state(days: dict) -> str:
    return json.dumps({"intervals": [], "days": days})


def netmeter_state(days: dict, months: dict | None = None) -> str:
    return json.dumps({"intervals": [], "days": days, "months": months or {}})


UPLINK = uplink_state({
    # Thursday 2026-10-01 (all day), Friday the 2nd (until noon), and the
    # last day of September, half measured.
    "2026-09-30": {"rx": 400_000_000, "tx": 100_000_000, "seconds": DAY / 2,
                   "interfaces": ["wlan0"]},
    "2026-10-01": {"rx": 900_000_000, "tx": 300_000_000, "seconds": DAY,
                   "interfaces": ["wlan0"]},
    "2026-10-02": {"rx": 450_000_000, "tx": 150_000_000, "seconds": DAY / 2,
                   "interfaces": ["wlan0"]},
})
NETMETER = netmeter_state(
    {"2026-10-01": {"rx": 1, "tx": 1, "seconds": DAY,
                    "internet": {"rx": 800_000_000, "tx": 250_000_000}},
     "2026-10-02": {"rx": 1, "tx": 1, "seconds": DAY / 2,
                    "internet": {"rx": 400_000_000, "tx": 120_000_000}}},
    {"2026-10": {"seconds": 1.5 * DAY, "services": {
        "smokeping": {"rx": 600_000_000, "tx": 200_000_000},
        "host": {"rx": 50_000_000, "tx": 10_000_000},
        "grafana": {"rx": 900_000_000, "tx": 1}}},
     "2026-09": {"seconds": DAY, "services": {"smokeping": {"rx": 5, "tx": 5}}}})
NOON = at("2026-10-02T12:00:00")


def by_period(body):
    return {p["period"]: p for p in body["periods"]}


def test_periods_start_on_monday_and_at_the_first_of_the_month():
    keys = {k: (first.isoformat(), last.isoformat())
            for k, _l, first, last in traffic.periods(datetime(2026, 10, 2).date())}
    assert keys["today"] == ("2026-10-02", "2026-10-02")
    assert keys["yesterday"] == ("2026-10-01", "2026-10-01")
    assert keys["this_week"] == ("2026-09-28", "2026-10-02")
    assert keys["this_month"] == ("2026-10-01", "2026-10-02")
    assert keys["last_month"] == ("2026-09-01", "2026-09-30")
    assert keys["last_30_days"] == ("2026-09-03", "2026-10-02")
    # January's last month is December of the year before.
    jan = {k: (f, last) for k, _l, f, last in traffic.periods(datetime(2027, 1, 15).date())}
    assert [d.isoformat() for d in jan["last_month"]] == ["2026-12-01", "2026-12-31"]


def test_sums_and_coverage_per_period():
    body = traffic.report(UPLINK, NETMETER, now=NOON)
    p = by_period(body)
    assert body["available"] is True
    assert body["since"] == "2026-09-30" and body["interfaces"] == ["wlan0"]
    # Today, until noon: everything since midnight was measured.
    assert p["today"]["uplink"]["total"] == 600_000_000
    assert p["today"]["uplink"]["coverage_pct"] == 100.0
    # The month: the 1st and half the 2nd, all measured.
    assert p["this_month"]["uplink"]["rx"] == 1_350_000_000
    assert p["this_month"]["uplink"]["tx"] == 450_000_000
    assert p["this_month"]["uplink"]["coverage_pct"] == 100.0
    # This week began on Monday the 28th; the meter only on the 30th, and
    # for half of it: 2 of 4.5 elapsed days.
    assert p["this_week"]["uplink"]["coverage_pct"] == pytest.approx(100 * 2 / 4.5, abs=0.1)
    # Last month: half a day out of thirty, and said so.
    assert p["last_month"]["uplink"]["total"] == 500_000_000
    assert p["last_month"]["uplink"]["coverage_pct"] == pytest.approx(100 * 0.5 / 30, abs=0.1)


def test_the_internet_figure_is_the_netmeters_and_smaller():
    body = traffic.report(UPLINK, NETMETER, now=NOON)
    p = by_period(body)
    assert body["internet_available"] is True
    assert p["this_month"]["internet"] == {
        "rx": 1_200_000_000, "tx": 370_000_000, "total": 1_570_000_000,
        "seconds": 1.5 * DAY, "coverage_pct": 100.0}
    assert p["this_month"]["internet"]["total"] < p["this_month"]["uplink"]["total"]
    # No netmeter (off, or Standard): the interface figure alone.
    alone = traffic.report(UPLINK, "", now=NOON)
    assert alone["internet_available"] is False
    assert by_period(alone)["today"]["internet"] is None
    assert "No Internet-only figure" in traffic.render(alone)


def test_services_by_month_most_first():
    body = traffic.report(UPLINK, NETMETER, now=NOON)
    names = [s["service"] for s in body["services"]["this_month"]]
    assert names == ["grafana", "smokeping", "host"]
    assert body["services"]["this_month"][1] == {
        "service": "smokeping", "rx": 600_000_000, "tx": 200_000_000, "total": 800_000_000}
    assert [s["service"] for s in body["services"]["last_month"]] == ["smokeping"]


def test_days_newest_first_with_their_internet_part():
    days = traffic.report(UPLINK, NETMETER, now=NOON)["days"]
    assert [d["date"] for d in days] == ["2026-10-02", "2026-10-01", "2026-09-30"]
    assert days[0]["internet"] == {"rx": 400_000_000, "tx": 120_000_000}
    assert days[2]["internet"] is None


def test_without_the_uplink_meter_the_netmeters_totals_stand_in():
    body = traffic.report("", NETMETER, now=NOON)
    assert body["available"] is True
    assert by_period(body)["today"]["uplink"]["total"] == 2


@pytest.mark.parametrize("damaged", ["", "not json", "[]", '{"days": []}',
                                     '{"days": {"2026-10-02": "x", "junk": {"rx": "a"}}}'])
def test_no_ledger_or_a_damaged_one_is_said_not_raised(damaged):
    body = traffic.report(damaged, damaged, now=NOON)
    if body["available"]:
        # Junk rows are skipped or read as zero, never an exception.
        assert by_period(body)["today"]["uplink"]["total"] == 0
    else:
        assert "no traffic ledger yet" in body["reason"]
    assert traffic._ledger('{"days": {"junk": {"rx": 1}, "2026-10-02": {}}}', "days") == {
        "2026-10-02": {}}
    assert traffic._ledger('{"months": {"2026-13": {}, "2026-10": {}}}', "months") == {
        "2026-10": {}}


def test_render():
    text = traffic.render(traffic.report(UPLINK, NETMETER, now=NOON))
    assert "Traffic on wlan0" in text and "measured since 2026-09-30" in text
    assert "this month" in text and "1.80 GB" in text and "Internet" in text
    assert "This month by service:" in text and "smokeping" in text
    # Last month the netmeter was not running: no Internet figure, not 0.
    last_month = next(line for line in text.splitlines() if line.startswith("last month"))
    assert last_month.rstrip().endswith("-")
    assert traffic.human(0) == "0"
    assert traffic.human(512_000) == "512 kB"
    assert traffic.human(5_500_000) == "5.5 MB"
    assert traffic.human(1_570_000_000) == "1.57 GB"


# --- GET /traffic ----------------------------------------------------------------

class FakeContainer:
    def __init__(self, files):
        self.files = files

    def exec_run(self, cmd):
        path = cmd[-1]
        if path in self.files:
            return SimpleNamespace(exit_code=0, output=self.files[path].encode())
        return SimpleNamespace(exit_code=1, output=b"No such file")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as c:
        yield c


def _docker(monkeypatch, containers=None, error=None):
    def get(name):
        if name not in (containers or {}):
            raise RuntimeError("no such container")
        return containers[name]

    def from_env():
        if error:
            raise error
        return SimpleNamespace(containers=SimpleNamespace(get=get))
    monkeypatch.setattr(api_module.docker, "from_env", from_env)
    monkeypatch.setattr(api_module, "resolve_container_name", lambda svc: f"pro-{svc}-1")


def test_traffic_endpoint_reads_both_ledgers(client, monkeypatch):
    _docker(monkeypatch, {
        "pro-smokeping-1": FakeContainer({"/config/uplink_traffic.json": UPLINK}),
        "pro-netmeter-1": FakeContainer({"/var/lib/netmeter/state.json": NETMETER}),
    })
    body = client.get("/traffic").get_json()
    assert body["available"] is True and body["internet_available"] is True
    assert body["since"] == "2026-09-30"


def test_traffic_without_netmeter_or_docker_answers(client, monkeypatch):
    _docker(monkeypatch, {"pro-smokeping-1": FakeContainer(
        {"/config/uplink_traffic.json": UPLINK})})
    body = client.get("/traffic").get_json()
    assert body["available"] is True and body["internet_available"] is False
    _docker(monkeypatch, error=RuntimeError("no docker socket"))
    body = client.get("/traffic").get_json()
    assert body["available"] is False


def test_traffic_needs_the_token_when_one_is_set(client, monkeypatch):
    monkeypatch.setenv("CONFIG_API_TOKEN", "sekrit")
    _docker(monkeypatch, error=RuntimeError("no docker socket"))
    assert client.get("/traffic").status_code == 401
    assert client.get("/traffic", headers={"X-API-Token": "sekrit"}).status_code == 200
