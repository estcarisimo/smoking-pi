"""The dashboard's probe-traffic estimate reads each target's probe."""

from app.routes.dashboard import calculate_bandwidth

PROBES = [
    {"name": "FPing", "pings": 10, "step_seconds": 300},
    {"name": "DNS", "pings": 5, "step_seconds": 300},
    {"name": "Fast", "pings": 20, "step_seconds": 60},
]


def _targets(*probes):
    return {"active_targets": {"c": [{"name": f"t{i}", "probe": p}
                                     for i, p in enumerate(probes)]}}


def test_a_default_target_is_what_it_always_was():
    # 10 pings x 64 bytes x 8 bits / 300 s
    got = calculate_bandwidth(_targets("FPing"), PROBES)
    assert got["total_targets"] == 1
    assert got["bandwidth_kbps"] == round(10 * 64 * 8 / 300 / 1000, 1)


def test_a_dns_target_sends_five_queries_not_ten():
    many = ["DNS"] * 600
    fping = calculate_bandwidth(_targets(*["FPing"] * 600), PROBES)
    dns = calculate_bandwidth(_targets(*many), PROBES)
    assert dns["bandwidth_kbps"] * 2 == fping["bandwidth_kbps"]


def test_a_faster_probe_counts_its_own_step_and_pings():
    got = calculate_bandwidth(_targets("Fast"), PROBES)
    assert got["bandwidth_kbps"] == round(20 * 64 * 8 / 60 / 1000, 1)


def test_unknown_probes_and_no_probe_list_fall_back_to_the_default():
    default = calculate_bandwidth(_targets("FPing"), PROBES)
    assert calculate_bandwidth(_targets("Gone"), PROBES) == default
    assert calculate_bandwidth(_targets("FPing")) == default
