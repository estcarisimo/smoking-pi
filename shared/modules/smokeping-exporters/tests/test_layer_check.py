"""layer_check.py: does a host answer a layer at all, and did an RRD ever answer."""

import json
import pathlib
import subprocess
import sys

MODULE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import layer_check  # noqa: E402

HEADER = "  uptime  loss  median  ping1  ping2  ping3  ping4  ping5\n\n"


def done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def fake(outputs):
    """A runner that answers by the command's first two words; records calls."""
    calls = []

    def run(cmd, timeout):
        calls.append(cmd)
        for key, out in outputs.items():
            if key in " ".join(cmd):
                return out
        return done(code=1)

    run.calls = calls
    return run


def test_icmp_counts_any_reply():
    got = fake({"fping": done(stderr="x.example : xmt/rcv/%loss = 3/1/66%")})
    assert layer_check.answers("x.example", "icmp", got)
    silent = fake({"fping": done(stderr="x.example : xmt/rcv/%loss = 3/0/100%", code=1)})
    assert not layer_check.answers("x.example", "icmp", silent)


def test_http_needs_zero_status_and_the_version_asked_for():
    h3 = fake({"--http3-only": done(stdout="3")})
    assert layer_check.answers("x.example", "h3", h3)
    assert h3.calls[0][-1] == "https://x.example/"  # the URL is curl's last argument
    # curl fell back or the server does not speak it: not an answer for h3.
    assert not layer_check.answers("x.example", "h3", fake({"--http3-only": done(stdout="2")}))
    # No TLS at all (a relay or CDN fallback name): curl fails.
    assert not layer_check.answers("x.example", "h1", fake({"--http1.1": done(stdout="0", code=35)}))


def test_tcp_is_a_handshake_on_443():
    seen = []

    class Sock:
        def close(self):
            pass

    def ok(addr, timeout):
        seen.append(addr)
        return Sock()

    def refused(addr, timeout):
        raise ConnectionRefusedError

    assert layer_check.answers("x.example", "tcp", connect=ok)
    assert seen == [("x.example", 443)]
    assert not layer_check.answers("x.example", "tcp", connect=refused)


def test_a_host_that_could_be_an_option_is_never_run():
    run = fake({})
    assert not layer_check.answers("-oProxyCommand=x", "h1", run)
    assert not layer_check.answers("a b", "icmp", run)
    assert run.calls == []


def test_silence_counts_measured_and_answered_rows(tmp_path):
    rows = ("1: nan 5.0 nan nan nan nan nan nan\n"      # measured, nothing came back
            "2: nan 1.0 0.2 0.1 0.2 0.2 0.3 nan\n"      # answered
            "3: nan nan nan nan nan nan nan nan\n")     # not measured (SmokePing down)
    run = fake({"rrdtool fetch": done(stdout=HEADER + rows)})
    assert layer_check.silence(tmp_path / "x.rrd", 86400, run) == {"rows": 2, "answered": 1}


def test_main_reports_both_and_refuses_paths_outside_the_datadir(tmp_path, monkeypatch):
    monkeypatch.setattr(layer_check, "DATADIR", tmp_path)
    (tmp_path / "DNS_Wizard").mkdir()
    (tmp_path / "DNS_Wizard" / "W_x_h1.rrd").write_text("rrd")
    run = fake({"--http1.1": done(stdout="1.1"),
                "rrdtool fetch": done(stdout=HEADER + "1: nan 5.0 nan nan nan nan nan nan\n")})
    report = layer_check.main([json.dumps({
        "preflight": [{"name": "W_x_h1", "host": "x.example", "layer": "h1"}],
        "silence": [{"name": "W_x_h1", "rrd": "DNS_Wizard/W_x_h1.rrd"},
                    {"name": "W_new_h1", "rrd": "DNS_Wizard/W_new_h1.rrd"},
                    {"name": "evil", "rrd": "../../etc/passwd"}],
    })], run)
    assert report["preflight"] == {"W_x_h1": True}
    assert report["silence"] == {"W_x_h1": {"rows": 1, "answered": 0}}  # W_new: no file yet
    assert [e["name"] for e in report["errors"]] == ["evil"]


def test_layers_not_tried_within_the_budget_are_left_out():
    import time as _time

    def slow(cmd, timeout):
        _time.sleep(0.5)
        return done(stdout="1.1")

    report = layer_check.main([json.dumps({"preflight": [
        {"name": f"W_{i}_h1", "host": f"h{i}.example", "layer": "h1"} for i in range(40)]})],
        slow, budget=0.2)
    assert report["preflight"] == {}  # nothing finished in time: no answer, not a "no"
    assert report["untried"] == 40
