"""host_facts.py: the JSON config-manager reads through `docker exec`."""

import json
import pathlib
import subprocess
import sys

MODULE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import host_facts  # noqa: E402

ROUTE_HEADER = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"


def _host(tmp_path, *, route="", route6="", wireless=(), resolv="", cpe=None):
    (tmp_path / "route").write_text(ROUTE_HEADER + route)
    (tmp_path / "ipv6_route").write_text(route6)
    net = tmp_path / "net"
    net.mkdir()
    for iface in wireless:
        (net / iface / "phy80211").mkdir(parents=True)
    (tmp_path / "resolv.conf").write_text(resolv)
    if cpe is not None:
        (tmp_path / "cpe.json").write_text(json.dumps(cpe))
    return dict(proc_route=tmp_path / "route", proc_route6=tmp_path / "ipv6_route",
                sys_net=net, resolv_conf=tmp_path / "resolv.conf",
                state_file=tmp_path / "cpe.json")


def test_a_pi_on_wifi(tmp_path):
    paths = _host(
        tmp_path,
        route="wlan0\t00000000\t0156A8C0\t0003\t0\t0\t600\t00000000\n",
        wireless=("wlan0",),
        resolv="# generated\nsearch lan\nnameserver 1.1.1.1\nnameserver 8.8.8.8\n",
        cpe={"ipv4": "136.25.220.1", "ipv6": None, "updated": 1.5, "other": 1},
    )
    assert host_facts.facts(**paths) == {
        "uplink": "wlan0",
        "wireless": True,
        "gateway4": "192.168.86.1",
        "gateway6": None,
        "resolvers": ["1.1.1.1", "8.8.8.8"],
        "cpe": {"ipv4": "136.25.220.1", "ipv6": None, "updated": 1.5},
    }


def test_a_wired_host(tmp_path):
    paths = _host(tmp_path,
                  route="eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\n",
                  wireless=("wlan0",))
    got = host_facts.facts(**paths)
    assert got["uplink"] == "eth0" and got["wireless"] is False


def test_a_v6_only_host_takes_its_uplink_from_the_v6_route(tmp_path):
    row = ("0" * 32 + " 00 " + "0" * 32 + " 00 fe800000000000000000000000000001"
           " 00000400 00000001 00000000 00000003    wlan0\n")
    paths = _host(tmp_path, route6=row, wireless=("wlan0",))
    got = host_facts.facts(**paths)
    assert (got["uplink"], got["wireless"], got["gateway4"], got["gateway6"]) == \
        ("wlan0", True, None, "fe80::1")


def test_nothing_readable_is_all_empty_never_an_error(tmp_path):
    missing = tmp_path / "missing"
    got = host_facts.facts(proc_route=missing, proc_route6=missing, sys_net=missing,
                           resolv_conf=missing, state_file=missing)
    assert got == {"uplink": None, "wireless": False, "gateway4": None,
                   "gateway6": None, "resolvers": [], "cpe": None}


def test_duplicate_nameservers_are_listed_once(tmp_path):
    (tmp_path / "r").write_text("nameserver 1.1.1.1\nnameserver 1.1.1.1\nnameserver ::1\n")
    assert host_facts.resolvers(tmp_path / "r") == ["1.1.1.1", "::1"]


def test_a_corrupt_cpe_state_is_none(tmp_path):
    (tmp_path / "s").write_text("{not json")
    assert host_facts.cpe(tmp_path / "s") is None


def test_the_command_prints_one_json_object():
    """What config-manager parses: a single line of JSON on stdout."""
    out = subprocess.run([sys.executable, str(MODULE_DIR / "host_facts.py")],
                         capture_output=True, text=True, check=True).stdout
    assert set(json.loads(out)) == {"uplink", "wireless", "gateway4", "gateway6",
                                    "resolvers", "cpe"}
