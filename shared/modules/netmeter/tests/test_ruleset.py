import pytest

import ruleset
from ruleset import Service

SP = Service("smokeping", True, "system.slice/docker-" + "a" * 64 + ".scope")
DNS = Service("dns-observer", True, "system.slice/docker-" + "b" * 64 + ".scope")
GRAFANA = Service("grafana", False, ipv4=("172.18.0.5",), ipv6=("fd00:18::5",))
NOTHING = Service("postgres", False)  # no address: not counted


def test_only_countable_services_sorted():
    assert [s.name for s in ruleset.services_counted([SP, NOTHING, GRAFANA, DNS])] == [
        "dns-observer", "grafana", "smokeping"]


def test_the_table_only_counts_and_marks():
    text = ruleset.build([SP, DNS, GRAFANA], ["wlan0", "eth0"])
    # Atomic replace: create-if-missing, delete, create.
    assert text.startswith("table inet smoking_pi_meter {}\ndelete table inet smoking_pi_meter\n")
    for verb in (" drop", " reject", " accept", " snat", " dnat", " masquerade", "meta mark set"):
        assert verb not in text.replace("policy accept", "")
    assert text.count("policy accept") == 3
    assert 'oifname != { "eth0", "wlan0" } return' in text
    assert "counter name total_tx" in text and "counter name total_rx" in text


def test_host_network_services_by_cgroup_with_their_own_mark_byte():
    text = ruleset.build([SP, DNS], ["wlan0"])
    # Fixed marks: smokeping 1, dns-observer 2. Other ct mark bits kept.
    assert (f'socket cgroupv2 level 2 "{DNS.cgroup}" ct mark set ct mark and 0x80ffffff '
            "or 0x2000000 counter name s_dns_observer_tx return") in text
    assert "ct mark and 0x7f000000 == 0x1000000 counter name s_smokeping_rx return" in text
    # A connection that starts inbound is matched by the receiving socket.
    assert text.count(f'socket cgroupv2 level 2 "{SP.cgroup}"') == 2


def test_bridged_containers_by_address_in_the_forward_hook():
    text = ruleset.build([GRAFANA], ["wlan0"])
    assert 'oifname { "wlan0" } ip saddr 172.18.0.5 counter name s_grafana_tx return' in text
    assert 'iifname { "wlan0" } ip6 daddr fd00:18::5 counter name s_grafana_rx return' in text


def test_same_stack_same_text():
    assert ruleset.build([GRAFANA, SP], ["wlan0"]) == ruleset.build([SP, GRAFANA], ["wlan0"])


@pytest.mark.parametrize("bad", [
    Service("x", True, 'system.slice/docker-a.scope" accept; "'),
    Service("x", False, ipv4=("172.18.0.5; drop",)),
])
def test_hostile_values_never_reach_the_script(bad):
    with pytest.raises(ValueError):
        ruleset.build([bad], ["wlan0"])


def test_no_uplink_no_table():
    with pytest.raises(ValueError):
        ruleset.build([SP], [])


def test_counter_names():
    assert ruleset.counter_name("Web-Admin") == "s_web_admin"


def test_attribute_splits_the_totals():
    counts = {
        "total_tx": (100, 10_000), "total_rx": (90, 9_000),
        "fwd_tx": (20, 3_000), "fwd_rx": (25, 5_000),
        "s_smokeping_tx": (60, 5_000), "s_smokeping_rx": (60, 5_000),
        "s_dns_observer_tx": (10, 1_000), "s_dns_observer_rx": (5, 500),
        "s_grafana_tx": (5, 1_000), "s_grafana_rx": (5, 2_000),
    }
    out = ruleset.attribute(counts, [SP, DNS, GRAFANA])
    assert out["smokeping"] == {"rx": 5_000, "tx": 5_000, "rx_packets": 60, "tx_packets": 60}
    assert out["host"] == {"rx": 3_500, "tx": 4_000, "rx_packets": 25, "tx_packets": 30}
    assert out["other_containers"] == {"rx": 3_000, "tx": 2_000, "rx_packets": 20,
                                       "tx_packets": 15}
    assert out["grafana"]["rx"] == 2_000


def test_attribute_never_negative():
    out = ruleset.attribute({"s_smokeping_tx": (5, 500)}, [SP])
    assert out["host"]["tx"] == 0


def test_marks_do_not_move_when_services_come_and_go():
    alone = ruleset.build([DNS], ["wlan0"])
    together = ruleset.build([SP, DNS, Service("alerter", True, "system.slice/docker-c.scope")],
                             ["wlan0"])
    line = "ct mark and 0x7f000000 == 0x2000000 counter name s_dns_observer_rx return"
    assert line in alone and line in together


def test_an_unknown_host_service_gets_a_stable_hashed_mark():
    m = ruleset.mark_of("something-new")
    assert m == ruleset.mark_of("something-new") and 16 <= m < ruleset.MAX_MARKED


def test_clashing_counter_names_keep_the_first():
    text = ruleset.build([Service("web-admin", False, ipv4=("172.18.0.2",)),
                          Service("web_admin", False, ipv4=("172.18.0.3",))], ["wlan0"])
    assert text.count("counter s_web_admin_tx {}") == 1


def test_internet_counters_skip_the_local_network_before_any_service_returns():
    text = ruleset.build([SP, GRAFANA], ["wlan0"])
    assert "set local_v4 { type ipv4_addr; flags interval; elements = { 0.0.0.0/8, 10.0.0.0/8, " in text
    assert "elements = { ::, fc00::/7, fe80::/10, ff00::/8 }" in text
    out = text[text.index("chain meter_out"):text.index("chain meter_in")]
    # Counted on every uplink packet, so before the first per-service return.
    assert out.index("ip daddr != @local_v4 counter name internet_tx") < out.index("socket cgroupv2")
    assert "ip6 daddr != @local_v6 counter name internet_tx" in out
    inbound = text[text.index("chain meter_in"):text.index("chain meter_forward")]
    assert "ip saddr != @local_v4 counter name internet_rx" in inbound
    fwd = text[text.index("chain meter_forward"):]
    assert ('oifname { "wlan0" } iifname != { "wlan0" } ip daddr != @local_v4 '
            "counter name fwd_internet_tx") in fwd
    assert fwd.index("fwd_internet_rx") < fwd.index("s_grafana")
    # Still nothing but counters.
    assert text.count("policy accept") == 3


def test_internet_adds_host_and_forwarded():
    counts = {"internet_rx": (3, 300), "internet_tx": (2, 200),
              "fwd_internet_rx": (1, 50), "fwd_internet_tx": (1, 5), "total_rx": (9, 999)}
    assert ruleset.internet(counts) == {"rx": 350, "tx": 205}
    assert ruleset.internet({}) == {"rx": 0, "tx": 0}
