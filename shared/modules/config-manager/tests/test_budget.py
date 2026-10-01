"""The measurement budget: pure accounting over the generated SmokePing
config (budget.py), and GET /budget against a fake Docker client."""

import json
from types import SimpleNamespace

import pytest

import api as api_module
import budget

# The generated Probes file's shape: plain probes, then the Curl class with
# its sub-probes (config_generator.generate_probes_file).
PROBES = """*** Probes ***

+ FPing
binary = /usr/sbin/fping
step = 300
pings = 10

+ DNS
binary = /usr/bin/dig
step = 300
pings = 5

+ Curl

++ CurlHTTP2
binary = /usr/local/bin/curl-h3
step = 300
pings = 5

++ WizardHTTP2
binary = /usr/local/bin/curl-h3
step = 300
pings = 5

+ Mystery
step = 60
pings = 3
"""

TARGETS = """*** Targets ***
probe = FPing

+ websites
++ Google
host = google.com
++ NYT
host = nytimes.com

+ DNS_Resolvers
probe = DNS
++ GoogleDNS
host = 8.8.8.8

+ HTTP
++ Google_h2
probe = CurlHTTP2
host = www.google.com

+ Services
++ svc_netflix_h2
probe = WizardHTTP2
host = www.netflix.com
++ odd
probe = Mystery
host = example.com
"""

CPE = """+ CPE
++ CPE_IPv4
host = 192.168.1.1
"""

NO_ENV: dict = {}


def test_sub_probes_belong_to_their_parents_class():
    classes = budget.probe_classes(PROBES)
    assert classes["CurlHTTP2"] == "Curl"
    assert classes["WizardHTTP2"] == "Curl"
    assert classes["FPing"] == "FPing"
    assert classes["Mystery"] == "Mystery"


def test_samples_and_bytes_per_probe():
    body = budget.report(TARGETS, CPE, PROBES, env=NO_ENV)
    rows = {r["probe"]: r for r in body["by_probe"]}
    # FPing: Google, NYT and the CPE (it inherits the file's top probe),
    # 10 pings every 300 s each = 120 samples an hour per target.
    assert rows["FPing"]["targets"] == 3
    assert rows["FPing"]["samples_per_hour"] == 360
    assert rows["FPing"]["mb_per_day"] == pytest.approx(360 * 24 * 168 / 1e6, abs=0.01)
    # One HEAD target: 60 samples an hour at 12 KB.
    assert rows["CurlHTTP2"]["samples_per_hour"] == 60
    assert rows["CurlHTTP2"]["mb_per_day"] == pytest.approx(60 * 24 * 12_000 / 1e6)
    # The wizard's copy is priced as the Curl class it belongs to.
    assert rows["WizardHTTP2"]["bytes_per_sample"] == 12_000
    # Its own step: 3 pings every 60 s = 180 an hour.
    assert rows["Mystery"]["samples_per_hour"] == 180
    assert body["targets"] == 7


def test_most_expensive_first():
    body = budget.report(TARGETS, CPE, PROBES, env=NO_ENV)
    order = [r["probe"] for r in body["by_probe"]]
    assert order[:2] == ["CurlHTTP2", "WizardHTTP2"]
    # Unpriced probes have no bytes: they sort by samples among the rest.
    assert order.index("Mystery") > order.index("FPing")


def test_a_class_without_a_measured_cost_is_named_not_guessed():
    body = budget.report(TARGETS, CPE, PROBES, env=NO_ENV)
    assert body["unpriced"] == ["Mystery"]
    assert {r["probe"]: r for r in body["by_probe"]}["Mystery"]["mb_per_day"] is None
    # Counted in samples, not in bytes.
    assert body["samples_per_hour"] == 360 + 60 + 60 + 60 + 180
    assert "No byte estimate for: Mystery" in budget.render(body)


def test_the_database_defaults_apply_to_a_probe_without_its_own():
    probes = "*** Probes ***\n\n+ FPing\nbinary = /usr/sbin/fping\n"
    targets = "*** Targets ***\nprobe = FPing\n\n+ a\n++ b\nhost = x\n"
    database = "*** Database ***\nstep = 60\npings = 20\n"
    row, = budget.report(targets, "", probes, database, env=NO_ENV)["by_probe"]
    assert (row["step"], row["pings"], row["samples_per_hour"]) == (60, 20, 1200)


def test_usage_against_the_default_ceilings():
    body = budget.report(TARGETS, CPE, PROBES, env=NO_ENV)
    assert body["ceiling"] == {"mb_per_day": budget.DEFAULT_MB_PER_DAY,
                               "samples_per_hour": budget.DEFAULT_SAMPLES_PER_HOUR}
    assert body["used"]["samples_pct"] == round(
        100 * body["samples_per_hour"] / budget.DEFAULT_SAMPLES_PER_HOUR, 1)
    assert body["over"] is False


def test_over_either_ceiling_is_over_budget():
    tight_bytes = {"MEASUREMENT_BUDGET_MB_PER_DAY": "1"}
    body = budget.report(TARGETS, CPE, PROBES, env=tight_bytes)
    assert body["over"] is True and body["used"]["bandwidth_pct"] > 100
    assert "Over budget" in budget.render(body)
    tight_samples = {"MEASUREMENT_BUDGET_SAMPLES_PER_HOUR": "100"}
    assert budget.report(TARGETS, CPE, PROBES, env=tight_samples)["over"] is True


@pytest.mark.parametrize("value", ["", "lots", "0", "-5"])
def test_a_bad_ceiling_falls_back_to_the_default(value):
    env = {"MEASUREMENT_BUDGET_MB_PER_DAY": value}
    assert budget.ceilings(env)["mb_per_day"] == budget.DEFAULT_MB_PER_DAY


def test_the_shipped_probes_all_have_a_cost():
    """Every probe the seed ships is priced, so the seed's figure is whole."""
    import pathlib
    import yaml

    seed = yaml.safe_load((pathlib.Path(budget.__file__).parent / "templates"
                           / "probes.yaml").read_text())["probes"]
    for name, probe in seed.items():
        assert probe.get("module", name) in budget.BYTES_PER_SAMPLE, name


# --- measured: the uplink meter beside the estimate ---------------------------

def _traffic(intervals):
    return json.dumps({"intervals": intervals})


def test_measured_scales_the_covered_time_to_a_day():
    now = 1_000_000.0
    # Two hours of 5-minute intervals, 1 MB in and 0.5 MB out each.
    rows = [{"t": now - 300 * k, "interface": "wlan0", "rx": 1_000_000, "tx": 500_000,
             "seconds": 300} for k in range(24)]
    m = budget.measured(_traffic(rows), now, 1000)
    assert m["interface"] == "wlan0" and m["hours"] == 2.0
    assert m["rx_mb"] == 24.0 and m["tx_mb"] == 12.0
    assert m["mb_per_day"] == 432.0          # 36 MB in 2 h -> x12
    assert m["pct_of_ceiling"] == 43.2
    assert m["kbps"] == 40.0
    assert m["stale"] is False


def test_measured_keeps_only_the_last_day_and_flags_a_stopped_meter():
    now = 1_000_000.0
    rows = [{"t": now - 90_000, "interface": "wlan0", "rx": 9e9, "tx": 9e9, "seconds": 300},
            {"t": now - 1_000, "interface": "wlan0", "rx": 1e6, "tx": 0, "seconds": 300}]
    m = budget.measured(_traffic(rows), now, 1000)
    assert m["rx_mb"] == 1.0 and m["hours"] == 0.1
    assert m["stale"] is True


def test_under_an_hour_is_provisional_and_not_judged():
    now = 1_000_000.0
    # An image pull in the first five minutes: 1.5 GB.
    rows = [{"t": now - 10, "interface": "wlan0", "rx": 1_500_000_000, "tx": 0,
             "seconds": 300}]
    m = budget.measured(_traffic(rows), now, 1000)
    assert m["provisional"] is True and m["pct_of_ceiling"] is None
    assert m["minutes"] == 5
    assert "not yet a daily figure" in budget.render(
        budget.report(TARGETS, CPE, PROBES, traffic_text=_traffic(rows), now=now))


def test_an_uplink_change_names_both_interfaces():
    now = 1_000_000.0
    rows = [{"t": now - 600 - 300 * k, "interface": "wlan0", "rx": 1, "tx": 1,
             "seconds": 300} for k in range(12)]
    rows.append({"t": now - 10, "interface": "eth0", "rx": 1, "tx": 1, "seconds": 300})
    m = budget.measured(_traffic(rows), now, 1000)
    assert m["interface"] == "eth0, wlan0" and m["interfaces"] == ["eth0", "wlan0"]


@pytest.mark.parametrize("text", ["", "not json", "[]", _traffic([]),
                                  _traffic([{"t": 1, "rx": 5, "tx": 5, "seconds": 0}]),
                                  _traffic([{"t": "x", "rx": 5, "tx": 5, "seconds": 300}]),
                                  _traffic([{"t": 999_990, "rx": "a", "tx": 5, "seconds": 300}])])
def test_measured_is_none_without_a_usable_meter(text):
    assert budget.measured(text, 1_000_000.0, 1000) is None


def test_report_and_render_carry_the_measured_figure():
    now = 1_000_000.0
    rows = [{"t": now - 60, "interface": "eth0", "rx": 3_000_000, "tx": 1_000_000,
             "seconds": 300}]
    body = budget.report(TARGETS, CPE, PROBES, traffic_text=_traffic(rows), now=now)
    assert body["measured"]["mb_per_day"] == 1152.0
    text = budget.render(body)
    assert "Measured on eth0: ~1152 MB/day" in text
    assert "over the last 5 min" in text
    assert budget.report(TARGETS, CPE, PROBES)["measured"] is None


# --- GET /budget ------------------------------------------------------------

class FakeContainer:
    def __init__(self, files):
        self.files = files
        self.calls = []

    def exec_run(self, cmd):
        self.calls.append(cmd)
        path = cmd[-1]
        if path in self.files:
            return SimpleNamespace(exit_code=0, output=self.files[path].encode())
        return SimpleNamespace(exit_code=1, output=b"No such file")


@pytest.fixture()
def client(monkeypatch):
    for var in ("CONFIG_API_TOKEN", "MEASUREMENT_BUDGET_MB_PER_DAY",
                "MEASUREMENT_BUDGET_SAMPLES_PER_HOUR"):
        monkeypatch.delenv(var, raising=False)
    api_module.app.config["TESTING"] = True
    with api_module.app.test_client() as c:
        yield c


def _docker(monkeypatch, container=None, error=None):
    def from_env():
        if error:
            raise error
        return SimpleNamespace(containers=SimpleNamespace(get=lambda name: container))
    monkeypatch.setattr(api_module.docker, "from_env", from_env)
    monkeypatch.setattr(api_module, "resolve_container_name", lambda svc: "pro-smokeping-1")


def test_budget_endpoint_includes_the_cpe_targets(client, monkeypatch, tmp_path):
    (tmp_path / "Targets").write_text(TARGETS)
    (tmp_path / "Probes").write_text(PROBES)
    monkeypatch.setattr(api_module, "OUTPUT_DIR", tmp_path)
    container = FakeContainer({"/config/CPE_Targets": CPE})
    _docker(monkeypatch, container)
    body = client.get("/budget").get_json()
    assert body["available"] is True and body["complete"] is True
    assert body["targets"] == 7
    assert ["cat", "/config/Database"] in container.calls
    assert ["cat", "/config/uplink_traffic.json"] in container.calls
    assert body["measured"] is None  # no meter state yet


def test_budget_without_smokeping_still_answers(client, monkeypatch, tmp_path):
    (tmp_path / "Targets").write_text(TARGETS)
    (tmp_path / "Probes").write_text(PROBES)
    monkeypatch.setattr(api_module, "OUTPUT_DIR", tmp_path)
    _docker(monkeypatch, error=RuntimeError("no docker socket"))
    body = client.get("/budget").get_json()
    assert body["available"] is True
    # Without the CPE target, and saying it is incomplete.
    assert body["complete"] is False
    assert body["targets"] == 6


def test_budget_before_the_first_generation(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api_module, "OUTPUT_DIR", tmp_path)
    body = client.get("/budget").get_json()
    assert body == {"available": False, "reason": "no generated Targets file yet"}


def test_budget_needs_the_token_when_one_is_set(client, monkeypatch, tmp_path):
    monkeypatch.setenv("CONFIG_API_TOKEN", "sekrit")
    monkeypatch.setattr(api_module, "OUTPUT_DIR", tmp_path)
    assert client.get("/budget").status_code == 401
    ok = client.get("/budget", headers={"X-API-Token": "sekrit"})
    assert ok.status_code == 200


# --- the command line: python budget.py (smoking-pi budget) -------------------

class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _urlopen(monkeypatch, reply):
    import urllib.request
    seen = []

    def fake(req, timeout=None):
        seen.append(req)
        if isinstance(reply, Exception):
            raise reply
        return _Resp(reply)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def test_render_names_the_totals_and_the_way_back_under_budget():
    body = budget.report(TARGETS, CPE, PROBES, env={"MEASUREMENT_BUDGET_MB_PER_DAY": "1"})
    text = budget.render(body)
    first_row = text.splitlines()[1]
    assert first_row.split()[:3] == ["CurlHTTP2", "Curl", "1"]
    assert "7 targets: 720 samples/h of 20000 (3.6%)" in text
    assert "of 1 (" in text and "Over budget" in text


def test_main_prints_the_report_and_sends_the_token(monkeypatch, capsys):
    import json
    monkeypatch.setenv("CONFIG_API_TOKEN", "sekrit")
    body = {"available": True, **budget.report(TARGETS, CPE, PROBES, env=NO_ENV)}
    seen = _urlopen(monkeypatch, json.dumps(body).encode())
    assert budget.main([]) == 0
    assert "7 targets:" in capsys.readouterr().out
    assert seen[0].full_url == "http://127.0.0.1:5000/budget"
    assert seen[0].get_header("X-api-token") == "sekrit"
    assert budget.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["targets"] == 7


def test_main_says_the_api_refused_rather_than_did_not_answer(monkeypatch, capsys):
    import io
    import urllib.error
    refusal = urllib.error.HTTPError(
        "http://127.0.0.1:5000/budget", 401, "UNAUTHORIZED", {},
        io.BytesIO(b'{"error": "Unauthorized"}'))
    _urlopen(monkeypatch, refusal)
    assert budget.main([]) == 1
    assert capsys.readouterr().err.strip() == "refused: Unauthorized"


def test_main_when_the_api_is_down_or_has_no_budget_yet(monkeypatch, capsys):
    import urllib.error
    _urlopen(monkeypatch, urllib.error.URLError("connection refused"))
    assert budget.main([]) == 1
    assert "did not answer" in capsys.readouterr().err
    _urlopen(monkeypatch, b'{"available": false, "reason": "no generated Targets file yet"}')
    assert budget.main([]) == 1
    assert "No budget yet: no generated Targets file yet" in capsys.readouterr().err
