"""Unit tests for wifi_link.py — pure unit, no iw, sysfs or InfluxDB needed.

The station/info fixtures have the exact shape a Pi 5 (brcmfmac) prints --
the smallest useful set -- with the AP identity replaced by the IANA
documentation MAC range (00-00-5E-00-53-xx) and a made-up SSID; the "rich" fixture is the ath9k/iwlwifi shape with
every optional line, so both ends of what a driver may say are pinned.
"""

import pathlib
import subprocess
import sys

import pytest

MODULE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import wifi_link  # noqa: E402

STATION_BRCMFMAC = """\
Station 00:00:5e:00:53:01 (on wlan0)
\tinactive time:\t0 ms
\trx bytes:\t14004659
\trx packets:\t58791
\ttx bytes:\t50875670
\ttx packets:\t74689
\ttx failed:\t3
\tsignal:  \t-52 dBm
\ttx bitrate:\t433.3 MBit/s
\trx bitrate:\t433.3 MBit/s
\tauthorized:\tyes
\tauthenticated:\tyes
\tassociated:\tyes
\tWMM/WME:\tyes
\tTDLS peer:\tno
\tDTIM period:\t2
\tbeacon interval:100
\tconnected time:\t1648 seconds
\tcurrent time:\t1789780309551 ms
"""

STATION_RICH = """\
Station 00:11:22:33:44:55 (on wlp3s0)
\tinactive time:\t12 ms
\trx bytes:\t1000
\trx packets:\t10
\ttx bytes:\t2000
\ttx packets:\t20
\ttx retries:\t150
\ttx failed:\t4
\tbeacon loss:\t2
\tbeacon rx:\t5000
\trx drop misc:\t7
\tsignal:  \t-61 [-63, -65] dBm
\tsignal avg:\t-60 [-62, -64] dBm
\ttx bitrate:\t866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2
\trx bitrate:\t585.0 MBit/s VHT-MCS 7 80MHz VHT-NSS 2
\texpected throughput:\t512.345Mbps
\tassociated:\tyes
\tconnected time:\t99 seconds
"""

INFO_BRCMFMAC = """\
Interface wlan0
\tifindex 3
\twdev 0x1
\taddr 00:00:5e:00:53:02
\tssid ExampleNet
\ttype managed
\twiphy 0
\tchannel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz
\ttxpower 31.00 dBm
"""

INFO_2G_WITH_SPACES = """\
Interface wlan0
\tssid Casa de Pepe 2.4
\ttype managed
\tchannel 6 (2437 MHz), width: 20 MHz, center1: 2437 MHz
\ttxpower 20.00 dBm
"""

SURVEY = """\
Survey data from wlan0
\tfrequency:\t\t\t5180 MHz [in use]
\tnoise:\t\t\t\t-93 dBm
\tchannel active time:\t\t123456 ms
\tchannel busy time:\t\t23456 ms
\tchannel receive time:\t\t10000 ms
\tchannel transmit time:\t\t5000 ms
Survey data from wlan0
\tfrequency:\t\t\t5200 MHz
\tnoise:\t\t\t\t-95 dBm
"""


class TestParseStationDump:
    def test_brcmfmac_minimal_set(self):
        s = wifi_link.parse_station_dump(STATION_BRCMFMAC)
        assert s["bssid"] == "00:00:5e:00:53:01"
        assert s["signal_dbm"] == -52.0
        assert s["tx_bitrate_mbps"] == 433.3
        assert s["rx_bitrate_mbps"] == 433.3
        assert s["tx_failed"] == 3
        assert s["connected_seconds"] == 1648
        assert s["associated"] is True
        # What this driver does not say must not be invented.
        for absent in ("signal_avg_dbm", "tx_retries", "beacon_loss",
                       "tx_mcs", "expected_throughput_mbps"):
            assert absent not in s

    def test_rich_driver_optional_lines(self):
        s = wifi_link.parse_station_dump(STATION_RICH)
        assert s["signal_dbm"] == -61.0
        assert s["signal_avg_dbm"] == -60.0
        assert s["tx_retries"] == 150
        assert s["beacon_loss"] == 2
        assert s["rx_drop_misc"] == 7
        assert s["tx_mcs"] == 9 and s["tx_nss"] == 2 and s["tx_width_mhz"] == 80
        assert s["rx_mcs"] == 7
        assert s["expected_throughput_mbps"] == 512.345

    def test_he_and_eht_bitrate_lines(self):
        he = "1201.0 MBit/s HE-MCS 11 HE-NSS 2 HE-GI 3.2 HE-DCM 0 40MHz"
        assert wifi_link._parse_bitrate(he) == {"mbps": 1201.0, "mcs": 11, "nss": 2, "width_mhz": 40}
        eht = "2882.0 MBit/s EHT-MCS 13 EHT-NSS 2 EHT-GI 0.8 160MHz"
        assert wifi_link._parse_bitrate(eht) == {"mbps": 2882.0, "mcs": 13, "nss": 2, "width_mhz": 160}

    def test_channel_line_without_width(self):
        i = wifi_link.parse_info("Interface wlan0\n\tchannel 6 (2437 MHz)\n")
        assert i == {"channel": 6, "freq_mhz": 2437, "band_ghz": 2.4}

    def test_unassociated_is_none(self):
        assert wifi_link.parse_station_dump("") is None
        assert wifi_link.parse_station_dump("\n") is None

    def test_only_first_station(self):
        two = STATION_BRCMFMAC + STATION_RICH
        s = wifi_link.parse_station_dump(two)
        assert s["bssid"] == "00:00:5e:00:53:01"
        assert "tx_retries" not in s


class TestParseInfo:
    def test_brcmfmac(self):
        i = wifi_link.parse_info(INFO_BRCMFMAC)
        assert i == {"channel": 36, "freq_mhz": 5180, "width_mhz": 80,
                     "txpower_dbm": 31.0, "ssid": "ExampleNet", "band_ghz": 5.0}

    def test_ssid_with_spaces_and_2g_band(self):
        i = wifi_link.parse_info(INFO_2G_WITH_SPACES)
        assert i["ssid"] == "Casa de Pepe 2.4"
        assert i["band_ghz"] == 2.4
        assert i["channel"] == 6

    def test_not_associated_has_no_channel(self):
        assert wifi_link.parse_info("Interface wlan0\n\ttype managed\n") == {}

    def test_band_boundaries(self):
        assert wifi_link.band_of(2484) == 2.4
        assert wifi_link.band_of(5825) == 5.0
        assert wifi_link.band_of(5955) == 6.0


class TestParseSurvey:
    def test_in_use_block_only(self):
        s = wifi_link.parse_survey(SURVEY)
        assert s == {"noise_dbm": -93.0, "chan_active_ms": 123456,
                     "chan_busy_ms": 23456}

    def test_no_survey_is_empty(self):
        assert wifi_link.parse_survey("") == {}

    def test_busy_pct_between_samples(self):
        prev = {"chan_active_ms": 1000, "chan_busy_ms": 100}
        cur = {"chan_active_ms": 3000, "chan_busy_ms": 600}
        assert wifi_link.busy_pct(prev, cur) == 25.0
        assert wifi_link.busy_pct({}, cur) is None
        assert wifi_link.busy_pct(cur, cur) is None          # no time elapsed
        assert wifi_link.busy_pct(cur, prev) is None         # counter reset


class TestProcAndSysfs:
    def test_read_proc_wireless_drops_unsupported_noise(self, tmp_path):
        p = tmp_path / "wireless"
        p.write_text(
            "Inter-| sta-|   Quality        |   Discarded packets\n"
            " face | tus | link level noise |  nwid  crypt   frag  retry   misc\n"
            " wlan0: 0000   58.  -52.  -256        0      0      0      3      0        0\n")
        assert wifi_link.read_proc_wireless("wlan0", p) == {"link_quality": 58.0, "level": -52.0}
        assert wifi_link.read_proc_wireless("wlan1", p) == {}

    def test_read_proc_wireless_ignores_noise_even_when_present(self, tmp_path):
        p = tmp_path / "wireless"
        p.write_text("h\nh\n wlp3s0: 0000   70.  -40.  -95        0      0      0      0      0        0\n")
        assert "noise" not in wifi_link.read_proc_wireless("wlp3s0", p)

    def test_read_sysfs(self, tmp_path):
        base = tmp_path / "wlan0"
        (base / "statistics").mkdir(parents=True)
        for name, val in (("rx_bytes", 1917478396), ("tx_bytes", 5), ("tx_dropped", 85)):
            (base / "statistics" / name).write_text(f"{val}\n")
        (base / "carrier_down_count").write_text("2\n")
        (base / "carrier").write_text("1\n")
        got = wifi_link.read_sysfs("wlan0", tmp_path)
        assert got == {"rx_bytes": 1917478396, "tx_bytes": 5, "tx_dropped": 85,
                       "carrier_down_count": 2, "carrier": 1}

    def test_read_sysfs_down_interface_carrier_is_zero(self, tmp_path):
        (tmp_path / "wlan0").mkdir()
        assert wifi_link.read_sysfs("wlan0", tmp_path)["carrier"] == 0


class TestInterfaceChoice:
    @pytest.fixture
    def sysnet(self, tmp_path):
        for name, wireless in (("eth0", False), ("wlan0", True), ("wlan1", True), ("docker0", False)):
            d = tmp_path / name
            d.mkdir()
            if wireless:
                (d / "phy80211").mkdir()
        return tmp_path

    def _route(self, tmp_path, iface):
        p = tmp_path / "route"
        p.write_text("Iface\tDestination\tGateway\n"
                     f"{iface}\t00000000\t0156A8C0\n"
                     "docker0\t000011AC\t00000000\n")
        return p

    def test_find_interfaces(self, sysnet):
        assert wifi_link.find_interfaces(sysnet) == ["wlan0", "wlan1"]
        assert wifi_link.find_interfaces(sysnet / "nope") == []

    def test_default_route_interface(self, tmp_path):
        assert wifi_link.default_route_interface(self._route(tmp_path, "wlan0")) == "wlan0"
        assert wifi_link.default_route_interface(tmp_path / "missing") is None

    def test_prefers_the_uplink(self, sysnet, tmp_path):
        assert wifi_link.choose_interface(None, sysnet, self._route(tmp_path, "wlan1")) == "wlan1"

    def test_falls_back_to_first_wireless_when_uplink_is_wired(self, sysnet, tmp_path):
        assert wifi_link.choose_interface(None, sysnet, self._route(tmp_path, "eth0")) == "wlan0"

    def test_override_must_be_wireless(self, sysnet, tmp_path):
        route = self._route(tmp_path, "wlan0")
        assert wifi_link.choose_interface("wlan1", sysnet, route) == "wlan1"
        assert wifi_link.choose_interface("eth0", sysnet, route) is None
        assert wifi_link.choose_interface("nope", sysnet, route) is None

    def test_wired_host_has_none(self, tmp_path):
        (tmp_path / "eth0").mkdir()
        assert wifi_link.choose_interface(None, tmp_path, tmp_path / "route") is None

    def _routes(self, tmp_path, rows):
        """A /proc/net/route with the full column set: (iface, dest, metric)."""
        p = tmp_path / "route-full"
        p.write_text(
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
            + "".join(
                f"{iface}\t{dest}\t0156A8C0\t0003\t0\t0\t{metric}\t00000000\n"
                for iface, dest, metric in rows
            )
        )
        return p

    def test_the_lowest_metric_wins_not_the_first_row(self, tmp_path):
        """Ethernet and Wi-Fi both up: only the lowest metric carries traffic.

        The kernel emits this prefix metric-ascending, so reading the first row
        happens to work — but nothing documents that, and getting it wrong tags
        a Wi-Fi sample uplink=False on a Pi that measures over Wi-Fi.
        """
        route = self._routes(tmp_path, [("wlan0", "00000000", 600),
                                        ("eth0", "00000000", 100)])
        assert wifi_link.default_route_interface(route) == "eth0"

    def test_rows_without_a_metric_column_still_parse(self, tmp_path):
        assert wifi_link.default_route_interface(self._route(tmp_path, "wlan0")) == "wlan0"

    def test_non_default_destinations_are_ignored(self, tmp_path):
        route = self._routes(tmp_path, [("docker0", "000011AC", 0),
                                        ("wlan0", "00000000", 600)])
        assert wifi_link.default_route_interface(route) == "wlan0"

    def _routes_with_flags(self, tmp_path, rows):
        """(iface, destination, flags, metric) — flags as /proc prints them."""
        p = tmp_path / "route-flags"
        p.write_text(
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
            + "".join(
                f"{i}\t{d}\t0156A8C0\t{f}\t0\t0\t{m}\t00000000\n"
                for i, d, f, m in rows
            )
        )
        return p

    def test_an_unreachable_default_route_is_not_an_interface(self, tmp_path):
        """`ip route add unreachable default` is listed here too, and its
        interface name is literally "*" (observed: flags 0201, iface *).
        Returning "*" as the uplink is worse than returning nothing."""
        route = self._routes_with_flags(
            tmp_path, [("*", "00000000", "0201", 100),
                       ("wlan0", "00000000", "0003", 600)])
        assert wifi_link.default_route_interface(route) == "wlan0"

    def test_an_on_link_v4_default_route_counts(self, tmp_path):
        """`ip route add default dev d0` — observed flags 0001, no gateway."""
        route = self._routes_with_flags(
            tmp_path, [("d0", "00000000", "0001", 100)])
        assert wifi_link.default_route_interface(route) == "d0"

    def test_the_default_route_names_its_gateway(self, tmp_path):
        """/proc/net/route prints the gateway in host order: 0156A8C0 on a
        little-endian Pi is 192.168.86.1. The gateway is the router, the
        first thing a "Your connection" card can suggest measuring."""
        route = self._routes(tmp_path, [("wlan0", "00000000", 600)])
        assert wifi_link.default_route4(route) == ("wlan0", "192.168.86.1")

    def test_the_gateway_follows_the_lowest_metric_route(self, tmp_path):
        p = tmp_path / "route-two"
        p.write_text(
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
            "wlan0\t00000000\t0156A8C0\t0003\t0\t0\t600\t00000000\n"
            "eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\n")
        assert wifi_link.default_route4(p) == ("eth0", "192.168.0.1")

    def test_an_on_link_default_route_has_no_gateway(self, tmp_path):
        p = tmp_path / "route-onlink"
        p.write_text(
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
            "wg0\t00000000\t00000000\t0001\t0\t0\t100\t00000000\n")
        assert wifi_link.default_route4(p) == ("wg0", None)

    def test_no_default_route_is_none(self, tmp_path):
        assert wifi_link.default_route4(tmp_path / "missing") is None


class TestIPv6Uplink:
    """A v6-only host has no row in /proc/net/route at all."""

    # dest prefixlen src srclen nexthop metric refcnt use flags iface
    ROW = ("{dest} {plen} " + "0" * 32 + " 00 " + "0" * 32
           + " {metric} 00000001 00000000 {flags}    {iface}")

    def _route6(self, tmp_path, rows):
        p = tmp_path / "ipv6_route"
        p.write_text("\n".join(self.ROW.format(**r) for r in rows) + "\n")
        return p

    def _default(self, iface, metric="00000400", flags="00000003"):
        return {"dest": "0" * 32, "plen": "00", "metric": metric,
                "flags": flags, "iface": iface}

    def test_finds_the_v6_default_route(self, tmp_path):
        route6 = self._route6(tmp_path, [self._default("wlan0")])
        assert wifi_link.default_route_interface6(route6) == "wlan0"

    def test_the_unreachable_lo_entries_are_not_a_default_route(self, tmp_path):
        """::/0 also appears twice on lo as a reject route with metric ffffffff;
        matching on the destination alone would name lo as the uplink."""
        route6 = self._route6(tmp_path, [
            self._default("lo", metric="ffffffff", flags="00200200"),
            self._default("lo", metric="ffffffff", flags="00200200"),
        ])
        assert wifi_link.default_route_interface6(route6) is None

    def test_the_lowest_metric_wins_here_too(self, tmp_path):
        route6 = self._route6(tmp_path, [self._default("wlan0", metric="00000600"),
                                         self._default("eth0", metric="00000100")])
        assert wifi_link.default_route_interface6(route6) == "eth0"

    def test_a_prefix_route_is_not_a_default_route(self, tmp_path):
        route6 = self._route6(tmp_path, [
            {"dest": "fd4cc5a212327e6b" + "0" * 16, "plen": "40",
             "metric": "00000100", "flags": "00000001", "iface": "wlan0"},
        ])
        assert wifi_link.default_route_interface6(route6) is None

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert wifi_link.default_route_interface6(tmp_path / "nope") is None

    def test_the_v6_default_route_names_its_next_hop(self, tmp_path):
        """Router advertisements install a link-local next hop."""
        row = ("0" * 32 + " 00 " + "0" * 32 + " 00 fe800000000000000000000000000001"
               " 00000400 00000001 00000000 00000003    wlan0")
        p = tmp_path / "ipv6_route"
        p.write_text(row + "\n")
        assert wifi_link.default_route6(p) == ("wlan0", "fe80::1")

    def test_a_v6_default_route_without_a_next_hop(self, tmp_path):
        route6 = self._route6(tmp_path, [self._default("wg0", flags="00000001")])
        assert wifi_link.default_route6(route6) == ("wg0", None)

    def test_a_gateway_less_default_route_still_counts(self, tmp_path):
        """wg-quick installs `default dev wg0` with no via. Observed on a real
        kernel: flags 0x00000001 — RTF_UP and NOT RTF_GATEWAY. Requiring a
        gateway dropped it, and every sample went out tagged uplink=0."""
        route6 = self._route6(tmp_path, [
            self._default("wg0", metric="00000064", flags="00000001"),
        ])
        assert wifi_link.default_route_interface6(route6) == "wg0"

    def test_v4_wins_when_both_exist(self, tmp_path):
        v4 = tmp_path / "route"
        v4.write_text("Iface\tDestination\tGateway\n" "eth0\t00000000\t0156A8C0\n")
        v6 = self._route6(tmp_path, [self._default("wlan0")])
        assert wifi_link.uplink_interface(v4, v6) == "eth0"

    def test_v6_is_the_fallback_on_a_v6_only_host(self, tmp_path):
        v6 = self._route6(tmp_path, [self._default("wlan0")])
        assert wifi_link.uplink_interface(tmp_path / "missing", v6) == "wlan0"


class TestUplinkHistory:
    """host_uplink: which interface every measurement crossed, over time."""

    @pytest.fixture
    def sysnet(self, tmp_path):
        net = tmp_path / "net"
        for name, wireless in (("eth0", False), ("wlan0", True), ("tailscale0", False)):
            (net / name).mkdir(parents=True)
            if wireless:
                (net / name / "phy80211").mkdir()
        return net

    def _route(self, tmp_path, *ifaces):
        p = tmp_path / "route"
        p.write_text("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
                     + "".join(f"{i}\t00000000\t0156A8C0\t0003\t0\t0\t{m}\t00000000\n"
                               for i, m in ifaces))
        return p

    def _route6(self, tmp_path, iface=None):
        p = tmp_path / "ipv6_route"
        rows = TestIPv6Uplink.ROW.format(dest="0" * 32, plen="00", metric="00000400",
                                         flags="00000003", iface=iface) if iface else ""
        p.write_text(rows + "\n")
        return p

    def test_classifies_the_uplink(self, sysnet, tmp_path):
        up = lambda *r: wifi_link.current_uplink(self._route(tmp_path, *r),  # noqa: E731
                                                 self._route6(tmp_path), sysnet)
        assert up(("wlan0", 600)) == wifi_link.Uplink("wlan0", "wireless", 4)
        assert up(("eth0", 100)) == wifi_link.Uplink("eth0", "wired", 4)
        assert up(("tailscale0", 5)) == wifi_link.Uplink("tailscale0", "virtual", 4)
        # A cable plugged in: both routes exist, Ethernet's metric is lower.
        assert up(("wlan0", 600), ("eth0", 100)).interface == "eth0"
        assert up() == wifi_link.Uplink("", "none", 0)

    def test_falls_back_to_the_v6_default_route(self, sysnet, tmp_path):
        got = wifi_link.current_uplink(self._route(tmp_path), self._route6(tmp_path, "wlan0"),
                                       sysnet)
        assert got == wifi_link.Uplink("wlan0", "wireless", 6)

    def test_virtual_prefixes_match_the_doctor(self):
        """Two copies by design (see the doctor's sources.py); they must agree,
        or the doctor and the dashboards disagree about the same route."""
        doctor = MODULE_DIR.parent / "doctor" / "doctor" / "sources.py"
        text = doctor.read_text()
        start = text.index("VIRTUAL_IFACE_PREFIXES = (")
        body = text[text.index("(", start):text.index(")", start) + 1]
        import ast
        assert tuple(ast.literal_eval(body)) == wifi_link.VIRTUAL_IFACE_PREFIXES

    def _line(self, u, previous=None, ts=1_790_000_000):
        return wifi_link.build_uplink_point(u, previous, ts).to_line_protocol()

    def test_point_shape(self):
        line = self._line(wifi_link.Uplink("wlan0", "wireless", 4))
        assert line == 'host_uplink family=4i,interface="wlan0",kind="wireless" 1790000000'
        assert 'previous="wlan0"' in self._line(wifi_link.Uplink("eth0", "wired", 4), "wlan0")

    def test_no_route_is_an_empty_string_not_a_missing_field(self):
        """The field must exist on every point, or the no-route state is a gap
        in the series instead of a value."""
        assert 'interface=""' in self._line(wifi_link.Uplink("", "none", 0))

    def test_tracker_heartbeat_and_change(self):
        wlan = wifi_link.Uplink("wlan0", "wireless", 4)
        eth = wifi_link.Uplink("eth0", "wired", 4)
        t = wifi_link.UplinkTracker(last_interface=None)
        first = t.due(wlan, 1000.0)
        assert first is not None and "previous" not in first.to_line_protocol()
        t.written(wlan, 1000.0)
        assert t.due(wlan, 1010.0) is None                       # nothing new
        assert t.due(wlan, 1000.0 + wifi_link.UPLINK_HEARTBEAT) is not None
        change = t.due(eth, 1010.0)                              # at once, not a minute later
        assert change is not None and 'previous="wlan0"' in change.to_line_protocol()

    def test_a_failed_write_keeps_the_change_for_the_next_cycle(self):
        wlan = wifi_link.Uplink("wlan0", "wireless", 4)
        eth = wifi_link.Uplink("eth0", "wired", 4)
        t = wifi_link.UplinkTracker(last_interface="wlan0")
        t.written_at = 1000.0
        assert t.due(eth, 1010.0) is not None                     # write fails: no written()
        again = t.due(eth, 1020.0)
        assert again is not None and 'previous="wlan0"' in again.to_line_protocol()

    def test_a_change_across_a_restart_is_marked(self):
        t = wifi_link.UplinkTracker(last_interface="wlan0")        # from Influx
        pt = t.due(wifi_link.Uplink("eth0", "wired", 4), 1000.0)
        assert 'previous="wlan0"' in pt.to_line_protocol()

    def test_losing_the_route_and_getting_it_back(self):
        t = wifi_link.UplinkTracker(last_interface="wlan0")
        none = wifi_link.Uplink("", "none", 0)
        assert 'previous="wlan0"' in t.due(none, 1000.0).to_line_protocol()
        t.written(none, 1000.0)
        back = t.due(wifi_link.Uplink("wlan0", "wireless", 4), 1010.0)
        assert 'previous="none"' in back.to_line_protocol()

    def test_last_uplink(self):
        class Record:
            def get_value(self):
                return "wlan0"

        class Table:
            records = [Record()]

        class Api:
            def __init__(self, result):
                self.result = result
                self.query_text = None

            def query(self, q):
                self.query_text = q
                if isinstance(self.result, Exception):
                    raise self.result
                return self.result

        api = Api([Table()])
        assert wifi_link.last_uplink(api, "smokeping") == "wlan0"
        assert 'r._measurement == "host_uplink"' in api.query_text
        assert wifi_link.last_uplink(Api([]), "smokeping") is None
        assert wifi_link.last_uplink(Api(RuntimeError("down")), "smokeping") is None


class TestSample:
    """sample() glues the sources; iw is faked via run_iw."""

    @pytest.fixture
    def fake_iw(self, monkeypatch):
        outputs = {"station": STATION_BRCMFMAC, "info": INFO_BRCMFMAC, "survey": ""}

        def run(*args, timeout=5.0):
            return outputs[args[2]]
        monkeypatch.setattr(wifi_link, "run_iw", run)
        monkeypatch.setattr(wifi_link, "read_sysfs",
                            lambda iface, sys_net=None: {"rx_bytes": 10, "carrier": 1,
                                                         "carrier_down_count": 2})
        monkeypatch.setattr(wifi_link, "read_proc_wireless",
                            lambda iface, p=None: {"link_quality": 58.0, "level": -52.0})
        return outputs

    def test_associated_sample_merges_everything(self, fake_iw):
        slow = wifi_link.sample_slow("wlan0", wifi_link.SlowState(), now=100.0)
        s = wifi_link.sample("wlan0", slow, have_iw=True, uplink=True)
        assert s["ssid"] == "ExampleNet" and s["bssid"] == "00:00:5e:00:53:01"
        assert s["channel"] == 36 and s["band_ghz"] == 5.0
        assert s["signal_dbm"] == -52.0 and s["link_quality"] == 58.0
        assert s["rx_bytes"] == 10 and s["carrier_down_count"] == 2
        assert s["uplink"] is True and s["associated"] is True
        assert "noise_dbm" not in s and "snr_db" not in s and "chan_busy_pct" not in s

    def test_unassociated_sample_carries_no_ap_facts(self, fake_iw):
        fake_iw["station"] = ""
        slow = wifi_link.sample_slow("wlan0", wifi_link.SlowState(), now=100.0)
        s = wifi_link.sample("wlan0", slow, have_iw=True, uplink=False)
        assert s["associated"] is False
        for absent in ("ssid", "bssid", "channel", "signal_dbm"):
            assert absent not in s
        assert s["rx_bytes"] == 10                      # counters still flow

    def test_snr_and_busy_when_the_driver_reports_a_survey(self, fake_iw):
        fake_iw["survey"] = SURVEY
        first = wifi_link.sample_slow("wlan0", wifi_link.SlowState(), now=100.0)
        fake_iw["survey"] = (SURVEY.replace("channel active time:\t\t123456", "channel active time:\t\t223456")
                             .replace("channel busy time:\t\t23456", "channel busy time:\t\t48456"))
        second = wifi_link.sample_slow("wlan0", first, now=160.0)
        s = wifi_link.sample("wlan0", second, have_iw=True, uplink=True)
        assert s["noise_dbm"] == -93.0
        assert s["snr_db"] == 41.0
        assert s["chan_busy_pct"] == 25.0

    def test_without_iw_falls_back_to_wext(self, fake_iw):
        s = wifi_link.sample("wlan0", wifi_link.SlowState(), have_iw=False, uplink=True)
        assert s["associated"] is True                  # from carrier
        assert s["signal_dbm"] == -52.0                 # from level
        assert "bssid" not in s and "tx_bitrate_mbps" not in s


class TestBuildPoint:
    def _line(self, s, ts=1_700_000_000):
        return wifi_link.build_point(s, ts).to_line_protocol()

    def test_types_are_fixed_and_flags_are_ints(self):
        s = {"interface": "wlan0", "associated": True, "uplink": True,
             "ssid": "Casa de Pepe 2.4", "bssid": "00:00:5e:00:53:01",
             "signal_dbm": -52.0, "tx_bitrate_mbps": 433.3, "rx_bytes": 10,
             "tx_failed": 3, "channel": 36, "band_ghz": 5.0, "chan_busy_pct": 25.0}
        line = self._line(s)
        assert line.startswith("wifi_link,")
        assert "interface=wlan0" in line and "bssid=00:00:5e:00:53:01" in line
        assert "ssid=Casa\\ de\\ Pepe\\ 2.4" in line            # escaped, not dropped
        assert "associated=1i" in line and "uplink=1i" in line  # int, never boolean
        assert "rx_bytes=10i" in line and "tx_failed=3i" in line and "channel=36i" in line
        assert "signal_dbm=-52," in line or "signal_dbm=-52 " in line  # float: no 'i' suffix
        assert "signal_dbm=-52i" not in line
        assert "tx_bitrate_mbps=433.3" in line and "band_ghz=5" in line
        assert line.endswith(" 1700000000")

    def test_unassociated_point_has_no_ap_tags_but_keeps_counters(self):
        s = {"interface": "wlan0", "associated": False, "uplink": False,
             "rx_bytes": 10, "carrier_down_count": 3}
        line = self._line(s)
        assert line.startswith("wifi_link,interface=wlan0 ")
        assert "ssid=" not in line and "bssid=" not in line
        assert "associated=0i" in line and "carrier_down_count=3i" in line

    def test_bool_values_are_never_written_as_booleans(self):
        # A driver-side bool sneaking through must still serialize as int.
        s = {"interface": "wlan0", "associated": True, "uplink": False}
        line = self._line(s)
        assert "=true" not in line and "=false" not in line


class TestWriteWithRetry:
    def test_gives_up_after_retries_without_raising(self, monkeypatch):
        monkeypatch.setattr(wifi_link.time, "sleep", lambda s: None)

        class Api:
            calls = 0

            def write(self, bucket, record):
                Api.calls += 1
                raise RuntimeError("down")
        assert wifi_link.write_with_retry(Api(), "b", object(), retries=3) is False
        assert Api.calls == 3


class TestRunIw:
    def test_missing_binary_is_empty_not_fatal(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("iw")
        monkeypatch.setattr(wifi_link.subprocess, "run", boom)
        assert wifi_link.run_iw("dev", "wlan0", "info") == ""

    def test_nonzero_exit_returns_stdout(self, monkeypatch):
        monkeypatch.setattr(wifi_link.subprocess, "run",
                            lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="x"))
        assert wifi_link.run_iw("dev", "wlan0", "survey", "dump") == ""
