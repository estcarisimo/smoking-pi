"""IPv6 reachability gate: address classification, routing, probes, filtering."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import ipv6_check  # noqa: E402
from scripts.config_generator import (  # noqa: E402
    build_category_context,
    filter_ipv6_targets,
)


# The shape seen on a host with no IPv6 service: a ULA prefix from the router
# and a Tailscale address, both of which Linux reports as "scope global".
ULA_ONLY = """
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536
    inet6 ::1/128 scope host
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet6 fd4c:c5a2:1232:7e6b:143a:4965:3c8:f7af/64 scope global dynamic
    inet6 fe80::ba27:ebff:fe12:3456/64 scope link
3: tailscale0: <POINTOPOINT,MULTICAST,NOARP,UP,LOWER_UP> mtu 1280
    inet6 fd7a:115c:a1e0::2c01:c1a7/128 scope global
"""

WITH_GUA = ULA_ONLY + """
    inet6 2803:9800:a004:8b00:ba27:ebff:fe12:3456/64 scope global dynamic
"""


class TestGlobalAddresses:
    def test_ula_and_link_local_are_not_global(self):
        # fd00::/8 is RFC 4193 private space (Tailscale included) and
        # fe80::/10 is link-local — neither can reach the IPv6 internet,
        # even though `ip -6 addr show scope global` lists them.
        assert ipv6_check.global_addresses(ULA_ONLY) == []

    def test_finds_global_unicast(self):
        found = ipv6_check.global_addresses(WITH_GUA)
        assert found == ["2803:9800:a004:8b00:ba27:ebff:fe12:3456"]

    def test_empty_and_garbage_input(self):
        assert ipv6_check.global_addresses("") == []
        assert ipv6_check.global_addresses("inet6 not-an-address/64") == []


class TestDefaultRoute:
    def test_no_route(self):
        assert ipv6_check.has_default_route("") is False

    def test_default_via(self):
        assert ipv6_check.has_default_route(
            "default via fe80::1 dev eth0 metric 1024 pref medium") is True

    def test_unreachable_default_does_not_count(self):
        assert ipv6_check.has_default_route(
            "unreachable default dev lo metric 1024") is False


class TestPingResult:
    def test_network_unreachable(self):
        assert ipv6_check.ping_succeeded("ping: connect: Network is unreachable", 2) is False

    def test_all_lost(self):
        out = "2 packets transmitted, 0 received, 100% packet loss, time 1002ms"
        assert ipv6_check.ping_succeeded(out, 1) is False

    def test_replies(self):
        out = "2 packets transmitted, 2 received, 0% packet loss, time 1001ms"
        assert ipv6_check.ping_succeeded(out, 0) is True

    def test_nonzero_exit_is_failure(self):
        out = "2 packets transmitted, 2 received, 0% packet loss"
        assert ipv6_check.ping_succeeded(out, 1) is False


class TestEvaluate:
    def test_ula_only_host_is_unavailable(self):
        result = ipv6_check.evaluate(ULA_ONLY, "", [])
        assert result["available"] is False
        assert "no global IPv6 address" in result["reason"]

    def test_global_address_without_route(self):
        result = ipv6_check.evaluate(WITH_GUA, "", [])
        assert result["available"] is False
        assert "default IPv6 route" in result["reason"]

    def test_configured_but_unreachable(self):
        result = ipv6_check.evaluate(
            WITH_GUA, "default via fe80::1 dev eth0",
            [("2 packets transmitted, 0 received, 100% packet loss", 1)])
        assert result["available"] is False
        assert "no probe host replied" in result["reason"]

    def test_fully_working(self):
        result = ipv6_check.evaluate(
            WITH_GUA, "default via fe80::1 dev eth0",
            [("2 packets transmitted, 0 received, 100% packet loss", 1),
             ("2 packets transmitted, 2 received, 0% packet loss", 0)])
        assert result["available"] is True
        assert result["addresses"] == ["2803:9800:a004:8b00:ba27:ebff:fe12:3456"]


class TestCheckModes:
    def _fail_runner(self, argv):
        raise AssertionError("runner must not be called")

    def test_off_short_circuits(self, monkeypatch):
        monkeypatch.setenv("IPV6_MODE", "off")
        result = ipv6_check.check(self._fail_runner)
        assert result["available"] is False

    def test_force_short_circuits(self, monkeypatch):
        monkeypatch.setenv("IPV6_MODE", "force")
        result = ipv6_check.check(self._fail_runner)
        assert result["available"] is True

    def test_runner_failure_is_flagged_as_error(self, monkeypatch):
        monkeypatch.setenv("IPV6_MODE", "auto")

        def boom(argv):
            raise RuntimeError("container not running")

        result = ipv6_check.check(boom)
        # `error` distinguishes "could not tell" from "no IPv6", so the caller
        # keeps the previous verdict instead of dropping targets.
        assert result["error"] is True

    def test_auto_skips_ping_when_structurally_impossible(self, monkeypatch):
        monkeypatch.setenv("IPV6_MODE", "auto")
        calls = []

        def runner(argv):
            calls.append(argv)
            if argv[:3] == ["ip", "-6", "addr"]:
                return ULA_ONLY, 0
            return "", 0

        result = ipv6_check.check(runner)
        assert result["available"] is False
        assert not any(c[0] == "ping" for c in calls)


class TestStatusCache:
    @pytest.fixture(autouse=True)
    def isolated_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IPV6_STATE_FILE", str(tmp_path / "ipv6-status.json"))
        ipv6_check._status = None
        yield
        ipv6_check._status = None

    def test_unknown_until_published(self):
        assert ipv6_check.get_status()["available"] is None
        # Unknown must not gate targets — nobody has looked yet.
        assert ipv6_check.measurements_allowed() is True

    def test_published_unavailable_gates(self):
        ipv6_check.set_status({"available": False, "reason": "no v6"})
        assert ipv6_check.measurements_allowed() is False

    def test_published_available_allows(self):
        ipv6_check.set_status({"available": True, "reason": "ok"})
        assert ipv6_check.measurements_allowed() is True

    def test_verdict_survives_into_a_fresh_process(self):
        # The regression this guards: the nightly OCA refresh regenerates
        # config in a SUBPROCESS. With a process-local cache that subprocess
        # saw "unknown", fell through to allowed, and wrote IPv6 targets back
        # into the config every night while the gate reported them disabled.
        ipv6_check.set_status({"available": False, "reason": "no global IPv6"})

        ipv6_check._status = None  # stand in for a freshly started process

        assert ipv6_check.get_status()["available"] is False
        assert ipv6_check.measurements_allowed() is False

    def test_fresh_process_sees_an_available_verdict_too(self):
        ipv6_check.set_status({"available": True, "reason": "ok"})
        ipv6_check._status = None
        assert ipv6_check.get_status()["available"] is True

    def test_corrupt_state_file_falls_open(self, tmp_path, monkeypatch):
        bad = tmp_path / "corrupt.json"
        bad.write_text("{not json")
        monkeypatch.setenv("IPV6_STATE_FILE", str(bad))
        ipv6_check._status = None
        # Unreadable state is "nobody has checked", not "no IPv6".
        assert ipv6_check.get_status()["available"] is None
        assert ipv6_check.measurements_allowed() is True

    def test_unwritable_state_file_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("IPV6_STATE_FILE", "/proc/nope/ipv6-status.json")
        ipv6_check._status = None
        ipv6_check.set_status({"available": False, "reason": "no v6"})
        # The in-memory verdict still applies to this process.
        assert ipv6_check.measurements_allowed() is False


class TestTargetFiltering:
    TARGETS = [
        {"name": "Google", "host": "google.com", "probe": "FPing"},
        {"name": "Google6", "host": "www.google.com", "probe": "FPing6"},
        {"name": "Amazon", "host": "www.amazon.com"},
    ]

    def test_allowed_keeps_everything(self):
        assert filter_ipv6_targets(self.TARGETS, True) == self.TARGETS

    def test_blocked_drops_only_ipv6_probes(self):
        kept = filter_ipv6_targets(self.TARGETS, False)
        assert [t["name"] for t in kept] == ["Google", "Amazon"]

    def test_category_context_omits_ipv6_targets(self):
        categories = build_category_context(
            {"top_sites": self.TARGETS}, ipv6_allowed=False)
        names = [t["name"] for t in categories[0]["targets"]]
        assert names == ["Google", "Amazon"]

    def test_category_context_default_keeps_ipv6(self):
        categories = build_category_context({"top_sites": self.TARGETS})
        assert len(categories[0]["targets"]) == 3

    def test_all_ipv6_category_is_dropped_entirely(self):
        # An empty section would render a header with no targets under it.
        categories = build_category_context(
            {"top_sites": [self.TARGETS[1]]}, ipv6_allowed=False)
        assert categories == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
