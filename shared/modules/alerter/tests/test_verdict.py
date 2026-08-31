"""Tests for the "is it me or the internet?" verdict.

The verdict is the part of an alert a person acts on, so a confidently wrong
one is worse than none. Two failure modes get the most attention here:
claiming a network fault from an ABSENCE of data, and letting a host that
never answers ICMP inflate breadth until one slow site reads as an outage.
"""

from __future__ import annotations

import time

import pytest

import verdict


def _mean(target, ratio, category=None):
    return {"target": target, "_value": ratio, "category": category}


def _micro(target, protocol, count):
    return {"target": target, "protocol": protocol, "_value": count}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "VERDICT_BROAD_PCT",
        "VERDICT_MIN_TARGETS",
        "VERDICT_IMPAIRED_LOSS_PCT",
        "VERDICT_STALE_DOWN_HOURS",
        "MICROCUT_BURST_N",
    ):
        monkeypatch.delenv(var, raising=False)


def _healthy(n, prefix="ok", category="top_sites"):
    return [_mean(f"{prefix}{i}", 0.0, category) for i in range(n)]


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


def test_exporter_stale_outranks_a_broad_outage():
    """Reporting "the internet is down" from missing data is the worst case.

    Every target reads as 100% lost precisely BECAUSE nothing is arriving.
    """
    rows = [_mean(f"t{i}", 1.0) for i in range(10)]
    call = verdict.classify(
        [{"rule": "exporter_stale", "severity": "critical"}], rows, []
    )
    assert call["scope"] == "monitoring"
    assert "not the network" in call["line"]


def test_broad_impairment_with_cpe_cutting_is_the_local_link():
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [_micro("CPE", "ipv4", 5)])
    assert call["scope"] == "local_link"
    assert "Your line" in call["line"]


def test_broad_impairment_with_a_clean_cpe_is_upstream():
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [])
    assert call["scope"] == "isp_upstream"
    assert "Not you" in call["line"]


def test_cpe_at_its_rate_limit_floor_never_reads_as_local_link():
    """The CPE gateway rate-limits ICMP: p50 10%, p99 30% loss, always.

    micro_rows only ever contains windows above MICROCUT_LOSS_PCT (50%), so
    the floor cannot appear here -- but if a future change fed raw loss in,
    every broad outage would be blamed on the user's own line.
    """
    rows = [_mean(f"t{i}", 0.5) for i in range(10)]
    call = verdict.classify([], rows, [])
    assert call["scope"] == "isp_upstream"
    assert call["cpe_cutting"] == []


def test_a_single_bad_site_with_healthy_peers_is_the_remote_target():
    rows = [_mean("amazon", 0.9, "top_sites"), *_healthy(8)]
    call = verdict.classify([], rows, [])
    assert call["scope"] == "remote_target"
    assert "Just that site" in call["line"]
    assert "top_sites" in call["line"]


def test_ipv6_only_impairment_is_named_as_such():
    rows = [
        _mean("google6", 1.0, "fping6"),
        _mean("cloudflare6", 1.0, "fping6"),
        *_healthy(8),
    ]
    call = verdict.classify([], rows, [])
    assert call["scope"] == "ipv6"


def test_dns_only_impairment_is_named_as_such():
    rows = [
        _mean("quad9", 0.9, "dns_resolvers"),
        _mean("opendns", 0.9, "dns_resolvers"),
        *_healthy(8),
    ]
    call = verdict.classify([], rows, [])
    assert call["scope"] == "dns"


def test_nothing_impaired_claims_nothing():
    call = verdict.classify([], _healthy(8), [])
    assert call["scope"] == "unclear"
    assert call["affected"] == 0


def test_no_measurements_claims_nothing_and_does_not_crash():
    call = verdict.classify([], [], [])
    assert call["scope"] == "unclear"
    assert call["total"] == 0


def test_too_few_targets_is_not_enough_to_call_it_broad():
    """Two of two impaired is 100%, and means nothing."""
    rows = [_mean("a", 0.9, "custom"), _mean("b", 0.9, "custom")]
    call = verdict.classify([], rows, [])
    assert call["scope"] != "isp_upstream"


# ---------------------------------------------------------------------------
# The chronic-target trap
# ---------------------------------------------------------------------------


def _chronic_records(targets, age_hours, now):
    return {
        f"target_down:{t}": {"first_seen": now - age_hours * 3600.0}
        for t in targets
    }


def test_a_chronically_dead_host_does_not_inflate_breadth():
    """REINTRODUCTION TEST for the verdict's most likely wrong answer.

    Bare `amazon.com` does not answer ICMP and charts a permanent flat 100%.
    With six such targets and one genuinely slow site, counting them makes
    7/10 look broad and the verdict announces an ISP outage. Excluding them
    from BOTH numerator and denominator leaves 1 of 4 -- one bad site.

    Delete the _chronic() filter and this flips to isp_upstream.
    """
    now = time.time()
    dead = [f"dead{i}" for i in range(6)]
    rows = [_mean(t, 1.0, "custom") for t in dead]
    rows.append(_mean("amazon", 0.9, "top_sites"))
    rows += _healthy(3)

    call = verdict.classify(
        [], rows, [], records=_chronic_records(dead, 48, now), now=now
    )
    assert call["scope"] != "isp_upstream"
    assert call["total"] == 4
    assert call["affected"] == 1
    assert set(call["evidence"]["excluded_chronic"]) == set(dead)


def test_a_target_that_just_went_down_still_counts():
    """Exclusion is for hosts that never answered, not for a real outage."""
    now = time.time()
    dead = [f"d{i}" for i in range(6)]
    rows = [_mean(t, 1.0, "custom") for t in dead] + _healthy(3)
    call = verdict.classify(
        [], rows, [], records=_chronic_records(dead, 0.5, now), now=now
    )
    assert call["scope"] == "isp_upstream"
    assert call["evidence"]["excluded_chronic"] == []


def test_a_target_with_no_incident_record_is_never_excluded():
    now = time.time()
    rows = [_mean(f"d{i}", 1.0, "custom") for i in range(6)] + _healthy(3)
    call = verdict.classify([], rows, [], records={}, now=now)
    assert call["scope"] == "isp_upstream"


def test_evidence_is_logged_for_every_verdict(caplog):
    """A wrong verdict must be diagnosable from docker logs alone."""
    with caplog.at_level("INFO", logger="alerter.verdict"):
        verdict.classify([], _healthy(8), [])
    assert "verdict inputs:" in caplog.text
    assert "impaired" in caplog.text


def test_thresholds_are_env_tunable(monkeypatch):
    rows = [_mean("a", 0.5), _mean("b", 0.0), _mean("c", 0.0), _mean("d", 0.0)]
    assert verdict.classify([], rows, [])["scope"] != "isp_upstream"
    monkeypatch.setenv("VERDICT_BROAD_PCT", "25")
    assert verdict.classify([], rows, [])["scope"] == "isp_upstream"
