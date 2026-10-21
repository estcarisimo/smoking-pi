import subprocess

import pytest

import trace
from fakes import trace_json


def test_command_is_paris_icmp_paced_and_skips_the_prefix():
    cmd = trace.command(20, 6, "/tmp/list")
    assert cmd[:5] == ["scamper", "-p", "20", "-O", "json"]
    assert cmd[cmd.index("-c") + 1] == "trace -P icmp-paris -q 1 -w 1 -g 5 -d 32768 -f 6"
    assert cmd[-2:] == ["-f", "/tmp/list"]
    assert "-f" not in trace.command(20, 1, "/x")[trace.command(20, 1, "/x").index("-c") + 1]


def test_parse_keeps_silent_hops_and_skips_other_objects():
    text = "\n".join(['{"type":"cycle-start"}', "not json",
                      trace_json("34.0.0.9", ["192.168.1.1", None, "23.0.0.2", "34.0.0.9"])])
    [t] = trace.parse(text)
    assert t.hops == ["192.168.1.1", None, "23.0.0.2", "34.0.0.9"]
    assert t.reached and t.distance == 4 and t.probes == 4
    assert t.rtts[1] is None and t.rtts[3] == 4.0


def test_parse_started_past_the_prefix_keeps_absolute_ttls():
    [t] = trace.parse(trace_json("34.0.0.9", ["34.0.0.1", "34.0.0.9"], first_hop=6))
    assert t.first_hop == 6
    assert t.hops == [None] * 5 + ["34.0.0.1", "34.0.0.9"]
    assert t.distance == 7


def test_unreached_has_no_distance():
    [t] = trace.parse(trace_json("34.0.0.9", ["192.168.1.1", None, None], reached=False))
    assert not t.reached and t.distance is None


def test_run_hands_the_list_to_scamper_and_raises_on_failure():
    seen = {}

    def runner(cmd, **kw):
        seen["cmd"] = cmd
        seen["list"] = open(cmd[-1]).read()
        return subprocess.CompletedProcess(cmd, 0, stdout=trace_json("34.0.0.9", ["34.0.0.9"]))

    [t] = trace.run(["34.0.0.9", "34.0.0.8"], 20, runner=runner)
    assert seen["list"] == "34.0.0.9\n34.0.0.8\n" and t.dst == "34.0.0.9"
    assert trace.run([], 20, runner=runner) == []

    def failing(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    with pytest.raises(subprocess.CalledProcessError):
        trace.run(["34.0.0.9"], 20, runner=failing)
