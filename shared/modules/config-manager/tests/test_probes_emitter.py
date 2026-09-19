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


def test_sub_probes_nest_under_their_module():
    content = _generate({
        "FPing": {"binary": "/usr/sbin/fping", "step": 300, "pings": 10},
        "CurlHTTP1": {"module": "Curl", "binary": "/usr/local/bin/curl-h3",
                      "pings": 5, "step": 300, "expect": "HTTPv=1.1"},
        "CurlHTTP3": {"module": "Curl", "binary": "/usr/local/bin/curl-h3",
                      "pings": 5, "step": 300, "expect": "HTTPv=3"},
        "TCPPing": {"binary": "/usr/bin/tcpping", "pings": 5, "step": 300,
                    "port": 443},
    })
    lines = content.splitlines()
    # one class section, every sub-probe under it, plain probes untouched
    assert lines.count("+ Curl") == 1
    assert lines.index("+ Curl") < lines.index("++ CurlHTTP1") < lines.index("++ CurlHTTP3")
    assert lines.index("++ CurlHTTP3") < lines.index("+ TCPPing")
    assert "+ CurlHTTP1" not in lines and "+ CurlHTTP3" not in lines
    # the class section is a template: no variables of its own
    assert lines[lines.index("+ Curl") + 1] == ""
    # `module` is ours, never SmokePing's
    assert "module" not in content
    assert "expect = HTTPv=3" in content
    assert "port = 443" in content


def test_curl_target_vars_are_emitted_verbatim():
    fmt = "Time: %{time_total} HTTPv=%{http_version}\\n"
    content = _generate({
        "CurlHTTP2": {"module": "Curl", "binary": "/usr/local/bin/curl-h3",
                      "pings": 5, "step": 300, "urlformat": "https://%host%/",
                      "extrare": "/;/", "extraargs": "--http2;-w;" + fmt,
                      "require_zero_status": "yes"},
    })
    assert "urlformat = https://%host%/" in content
    assert "extrare = /;/" in content
    assert "extraargs = --http2;-w;" + fmt in content
    assert "require_zero_status = yes" in content
