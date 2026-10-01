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
    # dns-observer sorts first: mark 1; smokeping: mark 2. Other ct mark bits kept.
    assert (f'socket cgroupv2 level 2 "{DNS.cgroup}" ct mark set ct mark and 0x80ffffff '
            "or 0x1000000 counter name s_dns_observer_tx return") in text
    assert "ct mark and 0x7f000000 == 0x2000000 counter name s_smokeping_rx return" in text
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
