import json

import pytest

import main
import net
import status


@pytest.mark.parametrize("setting,expected", [
    (None, "smoking-pi"), ("", "smoking-pi"), ("Smoking-Pi-Staging", "smoking-pi-staging"),
    ("lab.local", "lab"), ("off", None), ("no", None),
])
def test_configured_name(setting, expected):
    env = {} if setting is None else {"MDNS_NAME": setting}
    assert main.configured_name(env) == expected


@pytest.mark.parametrize("bad", ["-pi", "pi-", "smoking_pi", "a.b", "x" * 59])
def test_bad_names_are_refused(bad):
    with pytest.raises(ValueError):
        main.configured_name({"MDNS_NAME": bad})


def test_interfaces_setting():
    assert main.configured_interfaces({"MDNS_INTERFACES": " wlan0, eth0 "}) == ["wlan0", "eth0"]
    assert main.configured_interfaces({}) is None


def test_status_round_trip_and_staleness(tmp_path):
    path = str(tmp_path / "s" / "status.json")
    main.write_status(path, {"state": "announced", "name": "smoking-pi.local"})
    body = status.read(path)
    assert body["state"] == "announced" and status.healthy(body)
    stale = status.read(path, now=body["updated"] + status.STALE_SECONDS + 1)
    assert stale["state"] == "down" and not status.healthy(stale)
    assert status.read(str(tmp_path / "missing.json"))["state"] == "down"


def test_status_check_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MDNS_STATE_DIR", str(tmp_path))
    assert status.main(["--check"]) == 1
    main.write_status(str(tmp_path / "status.json"),
                      {"state": "announced", "name": "smoking-pi-2.local",
                       "base": "smoking-pi.local", "addresses": ["192.168.1.10"],
                       "interfaces": ["wlan0"]})
    assert status.main(["--check"]) == 0
    assert status.main([]) == 0
    assert "held by another host" in capsys.readouterr().out
    status.main(["--json"])
    assert json.loads(capsys.readouterr().out)["name"] == "smoking-pi-2.local"


def test_ipv6_addresses_keep_global_and_ula_only(tmp_path):
    f = tmp_path / "if_inet6"
    f.write_text(
        "fd4cc5a212327e6b2ecf67fffe2e7d24 03 40 00 00 wlan0\n"   # ULA, kept
        "fe800000000000002ecf67fffe2e7d24 03 40 20 80 wlan0\n"   # link-local
        "20010db8000000000000000000000001 03 40 00 01 wlan0\n"   # temporary
        "20010db8000000000000000000000002 03 40 00 40 wlan0\n"   # tentative
        "00000000000000000000000000000001 01 80 10 80 lo\n"
    )
    assert net.ipv6_addresses(str(f)) == {"wlan0": ["fd4c:c5a2:1232:7e6b:2ecf:67ff:fe2e:7d24"]}


@pytest.mark.parametrize("name,virtual", [
    ("wlan0", False), ("eth0", False), ("end0", False), ("docker0", True),
    ("br-e6d6d1a9abfa", True), ("veth107118b", True), ("tailscale0", True), ("lo", True),
])
def test_virtual_interfaces(name, virtual):
    assert net.is_virtual(name) is virtual


LAN = {3: "192.168.1.10"}


@pytest.mark.parametrize("source,index,ttl,ok", [
    (("192.168.1.20", 5353), 3, 255, True),
    (("192.168.1.20", 5353), 3, 64, False),      # routed: not from the link
    (("192.168.1.20", 5353), 7, 255, False),     # docker0 / tailscale0
    (("192.168.1.20", 41234), 3, 64, True),      # a legacy one-shot query
    (("192.168.1.20", 5353), None, None, True),  # no ancillary data
])
def test_accept(source, index, ttl, ok):
    assert main.accept(source, index, ttl, LAN) is ok


def test_status_tolerates_a_bad_timestamp(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"state": "announced", "updated": "yesterday"}))
    assert status.read(str(path))["state"] == "down"


def test_invalid_name_reason_is_the_literal_rule(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "idle", lambda path, body: seen.update(body) or 0)
    assert main.run({"MDNS_NAME": "bad_name", "MDNS_STATE_DIR": str(tmp_path)}) == 0
    assert seen == {"state": "invalid", "reason": main.NAME_RULE, "setting": "bad_name"}
