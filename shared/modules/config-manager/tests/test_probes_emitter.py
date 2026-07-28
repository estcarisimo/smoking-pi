"""Tests for the SmokePing Probes file emitter."""

from scripts.config_generator import ConfigGenerator, render_probe_value


def _generate(probes: dict) -> str:
    gen = ConfigGenerator()
    gen.probes_config = {"probes": probes, "default_probe": "FPing"}
    return gen.generate_probes_file()


def test_basic_probe_rendering():
    content = _generate({
        "FPing": {"binary": "/usr/sbin/fping", "step": 300, "pings": 10},
        "DNS": {"binary": "/usr/bin/dig", "pings": 5, "step": 300},
    })
    assert "*** Probes ***" in content
    assert "+ FPing" in content
    assert "binary = /usr/sbin/fping" in content
    assert "step = 300" in content
    assert "pings = 10" in content
    assert "+ DNS" in content
    assert "binary = /usr/bin/dig" in content


def test_no_python_literals_in_output():
    content = _generate({
        "EchoPingDNS": {
            "binary": "/usr/bin/echoping",
            "dns": True,
            "pings": 5,
            "step": 300,
        },
    })
    # Python booleans must never leak into SmokePing config
    assert "True" not in content
    assert "False" not in content
    assert "dns = 1" in content


def test_non_probe_keys_are_skipped():
    content = _generate({
        "FPing": {
            "binary": "/usr/sbin/fping",
            "step": 300,
            "pings": 10,
            "metadata": {"version": "1.0"},
            "description": "not a probe var",
            "empty": None,
        },
    })
    assert "metadata" not in content
    assert "description" not in content
    assert "empty" not in content
    assert "{" not in content
    assert "None" not in content


def test_render_probe_value():
    assert render_probe_value(True) == "1"
    assert render_probe_value(False) == "0"
    assert render_probe_value(300) == "300"
    assert render_probe_value("/usr/bin/dig") == "/usr/bin/dig"
    assert render_probe_value(None) is None
    assert render_probe_value({"a": 1}) is None
    assert render_probe_value([1, 2]) is None
