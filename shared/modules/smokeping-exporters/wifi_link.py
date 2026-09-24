#!/usr/bin/env python3
"""
Wi-Fi link collector — samples the host's wireless uplink and writes it to
InfluxDB as measurement "wifi_link".

Why: on a Pi whose default route is wlan0, every latency and loss figure the
rest of this stack records crossed a Wi-Fi hop first, and nothing recorded
the state of that hop. A microcut that coincides with a signal dip or a burst
of failed transmissions is a Wi-Fi problem, not an ISP one — but only if the
dip was recorded.

Sources, in order of authority:
  * ``iw dev <if> station dump`` (nl80211, unprivileged): signal, PHY bitrate,
    failures, association state. Drivers differ in what they fill in —
    brcmfmac on a Pi 5 gives signal, bitrates, tx failed and connected time
    and nothing else; ath9k/iwlwifi add signal avg, retries, beacon loss,
    MCS/NSS/width. Every optional line is optional here too.
  * ``iw dev <if> info``: channel, frequency, width, tx power. Sampled on the
    slow cadence, it changes only on a roam.
  * ``iw dev <if> survey dump``: noise floor and channel busy time, when the
    driver reports a survey at all (brcmfmac does not).
  * sysfs: byte/packet counters that survive re-association (the station
    counters reset on every association), carrier state and the number of
    times the carrier dropped.
  * /proc/net/wireless: link quality, and the whole fallback when ``iw`` is
    not installed.

Field types are fixed for the life of the measurement: counters and flags
are always int, levels and rates always float. InfluxDB rejects a write whose
field type differs from the first one it saw, and the client serializes a
Python bool as a *boolean* field, so flags are cast with int() explicitly.

Rates (bytes/s, failures/s) are left to Flux ``derivative()``; the only value
computed here is ``chan_busy_pct``, because Flux arithmetic across two fields
needs a pivot and the doctor's dashboard checks cannot follow one.

Pro edition only (requires InfluxDB). Idles, without exiting, on a host that
has no wireless interface.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("wifi_link")

REQUIRED_ENV = ("INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET")
DEFAULT_SAMPLE_INTERVAL = 10   # seconds between station samples
DEFAULT_SLOW_INTERVAL = 60     # seconds between info/survey samples
IDLE_RESCAN = 60               # seconds between interface rescans when idle
WRITE_RETRIES = 3

SYS_NET = Path("/sys/class/net")
PROC_ROUTE = Path("/proc/net/route")
PROC_IPV6_ROUTE = Path("/proc/net/ipv6_route")
PROC_WIRELESS = Path("/proc/net/wireless")

# Route flags, as /proc/net/route and /proc/net/ipv6_route print them
# (linux/route.h). RTF_UP alone decides: a default route installed on-link,
# with no gateway -- wg-quick's, and a PPP peer's -- is a real default route
# and does not set RTF_GATEWAY, while the entries that must be excluded
# (::/0 unreachable on lo, an `unreachable default`) do not set RTF_UP.
RTF_UP = 0x0001
RTF_REJECT = 0x0200

# Counters read from sysfs; their names are the field names.
SYSFS_COUNTERS = ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets",
                  "tx_dropped", "carrier_down_count")


def _env_int(name: str, default: int) -> int:
    """A blank value in .env exports as an empty string; treat it as unset."""
    return int(os.environ.get(name, "") or default)


# ───────────────────────── interface discovery ─────────────────────────
def find_interfaces(sys_net: Path = SYS_NET) -> list[str]:
    """Names of wireless interfaces: those with a phy80211 link in sysfs."""
    if not sys_net.is_dir():
        return []
    return sorted(p.name for p in sys_net.iterdir() if (p / "phy80211").exists())


def default_route4(proc_route: Path = PROC_ROUTE) -> tuple[str, str | None] | None:
    """The IPv4 default route as (interface, gateway), from /proc/net/route
    (destination 00000000), so there is no dependency on iproute2. The
    gateway is None for a route installed on-link (wg-quick's, a PPP peer's).

    With Ethernet and Wi-Fi both up there are two default routes and only the
    lowest metric is used, so the metric is compared rather than the file's
    order. The kernel happens to emit the prefix's routes metric-ascending —
    verified by adding the high-metric route first in a throwaway namespace
    and reading the file back — but that ordering is not documented anywhere,
    and picking the wrong one would tag a Wi-Fi sample `uplink=False` on a Pi
    that is measuring over Wi-Fi, which is exactly the case the Wi-Fi verdict
    exists for. Rows without a metric column (older kernels, and the test
    fixtures) count as metric 0.
    """
    try:
        lines = proc_route.read_text().splitlines()[1:]
    except OSError:
        return None
    best: tuple[int, str, str | None] | None = None
    for line in lines:
        parts = line.split()
        if len(parts) < 2 or parts[1] != "00000000":
            continue
        # `ip route add unreachable default` is listed here too, with the
        # interface name literally "*" and RTF_REJECT set. Returning "*" as
        # the uplink would be worse than returning nothing.
        if parts[0] == "*":
            continue
        if len(parts) >= 4:
            try:
                flags = int(parts[3], 16)
            except ValueError:
                flags = RTF_UP
            if not flags & RTF_UP or flags & RTF_REJECT:
                continue
        try:
            metric = int(parts[6]) if len(parts) >= 7 else 0
        except ValueError:
            metric = 0
        if best is None or metric < best[0]:
            best = (metric, parts[0], _gateway4(parts[2]) if len(parts) >= 3 else None)
    return (best[1], best[2]) if best else None


def _gateway4(field: str) -> str | None:
    """/proc/net/route prints the gateway as a host-order 32-bit word: the
    network-order address read as a native integer, so 0156A8C0 is
    192.168.86.1 on a little-endian Pi and would read the other way round
    on a big-endian host."""
    try:
        value = int(field, 16)
    except ValueError:
        return None
    return str(ipaddress.IPv4Address(value.to_bytes(4, sys.byteorder))) if value else None


def default_route_interface(proc_route: Path = PROC_ROUTE) -> str | None:
    """The interface carrying the IPv4 default route (default_route4)."""
    route = default_route4(proc_route)
    return route[0] if route else None


def default_route6(proc_route6: Path = PROC_IPV6_ROUTE) -> tuple[str, str | None] | None:
    """The IPv6 default route as (interface, next hop), from /proc/net/ipv6_route.

    A v6-only host has no row in /proc/net/route at all, so without this the
    uplink reads as unknown and every Wi-Fi sample is tagged `uplink=False` —
    the verdict requires the uplink, so it would never say "it's your Wi-Fi"
    on such a host. ::/0 also appears as two unreachable entries on `lo` with
    metric ffffffff, which is why the flags are checked rather than just the
    destination: a real default route is up and has a gateway. The next hop
    is normally link-local (fe80::/10), learned from router advertisements.
    """
    try:
        lines = proc_route6.read_text().splitlines()
    except OSError:
        return None
    best: tuple[int, str, str | None] | None = None
    for line in lines:
        parts = line.split()
        # dest, prefixlen, src, srclen, nexthop, metric, refcnt, use, flags, iface
        if len(parts) < 10 or parts[0] != "0" * 32 or parts[1] != "00":
            continue
        try:
            metric, flags = int(parts[5], 16), int(parts[8], 16)
        except ValueError:
            continue
        if not flags & RTF_UP or flags & RTF_REJECT:
            continue
        if best is None or metric < best[0]:
            best = (metric, parts[9], _gateway6(parts[4]))
    return (best[1], best[2]) if best else None


def _gateway6(field: str) -> str | None:
    try:
        raw = bytes.fromhex(field)
    except ValueError:
        return None
    if len(raw) != 16 or not any(raw):
        return None
    return str(ipaddress.IPv6Address(raw))


def default_route_interface6(proc_route6: Path = PROC_IPV6_ROUTE) -> str | None:
    """The interface carrying the IPv6 default route (default_route6)."""
    route = default_route6(proc_route6)
    return route[0] if route else None


def uplink_interface(proc_route: Path = PROC_ROUTE,
                     proc_route6: Path = PROC_IPV6_ROUTE) -> str | None:
    """The interface the host's traffic actually leaves by, v4 first then v6."""
    return default_route_interface(proc_route) or default_route_interface6(proc_route6)


def choose_interface(override: str | None, sys_net: Path = SYS_NET,
                     proc_route: Path = PROC_ROUTE,
                     proc_route6: Path = PROC_IPV6_ROUTE) -> str | None:
    """WIFI_INTERFACE if set and wireless; else the wireless interface that
    carries the default route; else the first wireless interface; else None."""
    wireless = find_interfaces(sys_net)
    if override:
        if override in wireless:
            return override
        # Silence here used to look like "no wireless hardware". Say which it
        # is: a typo, or a wired interface asked to report Wi-Fi statistics.
        log.warning(
            "WIFI_INTERFACE=%s is not a wireless interface (wireless here: %s) "
            "— no Wi-Fi statistics will be collected",
            override, ", ".join(wireless) or "none",
        )
        return None
    if not wireless:
        return None
    uplink = uplink_interface(proc_route, proc_route6)
    if uplink in wireless:
        return uplink
    return wireless[0]


# ───────────────────────── sysfs and procfs ─────────────────────────
def read_sysfs(iface: str, sys_net: Path = SYS_NET) -> dict[str, int]:
    """Interface counters and carrier state. Missing files are skipped."""
    base = sys_net / iface
    out: dict[str, int] = {}
    for name in SYSFS_COUNTERS:
        path = base / "statistics" / name if name != "carrier_down_count" else base / name
        try:
            out[name] = int(path.read_text().strip())
        except (OSError, ValueError):
            continue
    try:
        out["carrier"] = int((base / "carrier").read_text().strip())
    except (OSError, ValueError):
        # Reading carrier of a down interface raises EINVAL; that is "no".
        out["carrier"] = 0
    return out


def read_proc_wireless(iface: str, proc_wireless: Path = PROC_WIRELESS) -> dict[str, float]:
    """Link quality and signal level from the legacy WEXT file. Its noise
    column is deliberately ignored: -256 on every driver seen so far, and the
    survey is the source that means something when a driver has one."""
    try:
        lines = proc_wireless.read_text().splitlines()[2:]
    except OSError:
        return {}
    for line in lines:
        name, _, rest = line.strip().partition(":")
        if name.strip() != iface:
            continue
        cols = rest.split()
        if len(cols) < 4:
            return {}
        return {"link_quality": float(cols[1].rstrip(".")),
                "level": float(cols[2].rstrip("."))}
    return {}


# ───────────────────────── iw parsing ─────────────────────────
_NUMBER_RE = re.compile(r"-?[\d.]+")
_BITRATE_RE = re.compile(r"^([\d.]+)\s+MBit/s(.*)$")
_MCS_RE = re.compile(r"\bMCS\s+(\d+)")
_NSS_RE = re.compile(r"\b(?:VHT-NSS|HE-NSS|EHT-NSS|NSS)\s+(\d+)")
_WIDTH_RE = re.compile(r"\b(\d+)MHz")


def _parse_bitrate(value: str) -> dict[str, Any]:
    """'433.3 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2' → rate and, when the
    driver spells them out, MCS / NSS / width."""
    m = _BITRATE_RE.match(value.strip())
    if not m:
        return {}
    out: dict[str, Any] = {"mbps": float(m.group(1))}
    rest = m.group(2)
    if (mm := _MCS_RE.search(rest)):
        out["mcs"] = int(mm.group(1))
    if (nm := _NSS_RE.search(rest)):
        out["nss"] = int(nm.group(1))
    if (wm := _WIDTH_RE.search(rest)):
        out["width_mhz"] = int(wm.group(1))
    return out


def parse_station_dump(text: str) -> dict[str, Any] | None:
    """The first station block of ``iw dev <if> station dump``. None when the
    interface is not associated (the command prints nothing)."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("Station "):
        return None
    out: dict[str, Any] = {"bssid": lines[0].split()[1].lower()}
    for raw in lines[1:]:
        if raw.startswith("Station "):
            break
        if ":" not in raw:
            continue
        key, _, value = raw.strip().partition(":")
        key, value = key.strip(), value.strip()
        try:
            if key == "signal":
                # "-52 dBm" or, on some drivers, "-52 [-55, -50] dBm"
                out["signal_dbm"] = float(value.split()[0])
            elif key == "signal avg":
                out["signal_avg_dbm"] = float(value.split()[0])
            elif key == "tx bitrate":
                for k, v in _parse_bitrate(value).items():
                    out[f"tx_{k}" if k != "mbps" else "tx_bitrate_mbps"] = v
            elif key == "rx bitrate":
                for k, v in _parse_bitrate(value).items():
                    out[f"rx_{k}" if k != "mbps" else "rx_bitrate_mbps"] = v
            elif key == "tx retries":
                out["tx_retries"] = int(value)
            elif key == "tx failed":
                out["tx_failed"] = int(value)
            elif key == "beacon loss":
                out["beacon_loss"] = int(value)
            elif key == "rx drop misc":
                out["rx_drop_misc"] = int(value)
            elif key == "connected time":
                out["connected_seconds"] = int(value.split()[0])
            elif key == "expected throughput":
                # "512.345Mbps" — no space before the unit on real output
                out["expected_throughput_mbps"] = float(_NUMBER_RE.match(value).group(0))
            elif key == "associated":
                out["associated"] = value == "yes"
        except (ValueError, IndexError):
            log.debug("unparsed station line: %r", raw)
    out.setdefault("associated", True)
    return out


_CHANNEL_RE = re.compile(
    r"channel\s+(\d+)\s+\((\d+)\s+MHz\)(?:,\s+width:\s+(\d+)\s+MHz)?")
_TXPOWER_RE = re.compile(r"txpower\s+([\d.]+)\s+dBm")
# An SSID may legally begin with spaces; this loses them, as iw's own output
# makes them indistinguishable from the separator. Trailing ones are stripped.
_SSID_RE = re.compile(r"^\s*ssid\s+(.*)$", re.MULTILINE)


def parse_info(text: str) -> dict[str, Any]:
    """Channel, frequency, width, tx power and SSID from ``iw dev <if> info``."""
    out: dict[str, Any] = {}
    if (m := _CHANNEL_RE.search(text)):
        out["channel"] = int(m.group(1))
        out["freq_mhz"] = int(m.group(2))
        if m.group(3):
            out["width_mhz"] = int(m.group(3))
    if (m := _TXPOWER_RE.search(text)):
        out["txpower_dbm"] = float(m.group(1))
    if (m := _SSID_RE.search(text)):
        out["ssid"] = m.group(1).strip()
    if "freq_mhz" in out:
        out["band_ghz"] = band_of(out["freq_mhz"])
    return out


def band_of(freq_mhz: int) -> float:
    if freq_mhz < 3000:
        return 2.4
    if freq_mhz < 5900:
        return 5.0
    return 6.0


_SURVEY_KEYS = {
    "noise": ("noise_dbm", float),
    "channel active time": ("chan_active_ms", int),
    "channel busy time": ("chan_busy_ms", int),
}


def parse_survey(text: str) -> dict[str, Any]:
    """The ``[in use]`` block of ``iw dev <if> survey dump``. Empty when the
    driver reports no survey (brcmfmac) or nothing is in use."""
    out: dict[str, Any] = {}
    in_use = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Survey data"):
            in_use = False
        elif line.startswith("frequency:") and "[in use]" in line:
            in_use = True
        elif in_use and ":" in line:
            key, _, value = line.partition(":")
            spec = _SURVEY_KEYS.get(key.strip())
            if spec:
                name, cast = spec
                try:
                    out[name] = cast(float(value.split()[0]))
                except (ValueError, IndexError):
                    pass
    return out


def busy_pct(prev: dict[str, Any], cur: dict[str, Any]) -> float | None:
    """Channel busy share between two survey samples; None when either lacks
    the counters or no time elapsed."""
    try:
        active = cur["chan_active_ms"] - prev["chan_active_ms"]
        busy = cur["chan_busy_ms"] - prev["chan_busy_ms"]
    except KeyError:
        return None
    if active <= 0 or busy < 0:
        return None
    return round(100.0 * busy / active, 2)


# ───────────────────────── sampling ─────────────────────────
def run_iw(*args: str, timeout: float = 5.0) -> str:
    """Stdout of ``iw <args>``; empty on any failure (unassociated stations
    and unsupported surveys both exit non-zero with nothing on stdout)."""
    try:
        proc = subprocess.run(["iw", *args], capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("iw %s failed: %s", " ".join(args), exc)
        return ""
    return proc.stdout


@dataclass
class SlowState:
    """What the slow cadence learned last time, reused between fast samples."""
    info: dict[str, Any] = field(default_factory=dict)
    survey: dict[str, Any] = field(default_factory=dict)
    busy_pct: float | None = None
    sampled_at: float = 0.0


def sample_slow(iface: str, state: SlowState, now: float) -> SlowState:
    info = parse_info(run_iw("dev", iface, "info"))
    survey = parse_survey(run_iw("dev", iface, "survey", "dump"))
    busy = busy_pct(state.survey, survey) if state.survey else None
    return SlowState(info=info, survey=survey, busy_pct=busy, sampled_at=now)


def sample_fast(iface: str, have_iw: bool, uplink: bool) -> dict[str, Any]:
    """The per-interval part: station dump, sysfs counters, WEXT quality."""
    s: dict[str, Any] = {"interface": iface, "uplink": uplink}
    s.update(read_sysfs(iface))
    wext = read_proc_wireless(iface)
    if "link_quality" in wext:
        s["link_quality"] = wext["link_quality"]

    station = parse_station_dump(run_iw("dev", iface, "station", "dump")) if have_iw else None
    if station:
        s.update(station)
    elif have_iw:
        s["associated"] = False
    else:
        # No iw: WEXT is all there is. Level is the signal; carrier is the
        # association state.
        s["associated"] = bool(s.get("carrier"))
        if "level" in wext and s["associated"]:
            s["signal_dbm"] = wext["level"]
    return s


def merge_slow(s: dict[str, Any], slow: SlowState) -> dict[str, Any]:
    """Fold the slow-cadence facts into a fast sample -- only while
    associated, so a disconnect never carries the old AP's channel along."""
    if not s.get("associated"):
        return s
    s.update({k: v for k, v in slow.info.items() if k != "ssid"})
    if slow.info.get("ssid"):
        s["ssid"] = slow.info["ssid"]
    s.update(slow.survey)
    if slow.busy_pct is not None:
        s["chan_busy_pct"] = slow.busy_pct
    if "noise_dbm" in s and "signal_dbm" in s:
        s["snr_db"] = round(s["signal_dbm"] - s["noise_dbm"], 1)
    return s


def sample(iface: str, slow: SlowState, have_iw: bool, uplink: bool) -> dict[str, Any]:
    """One complete sample, untyped: fast facts plus the cached slow ones."""
    return merge_slow(sample_fast(iface, have_iw, uplink), slow)


# ───────────────────────── point ─────────────────────────
INT_FIELDS = ("tx_mcs", "tx_nss", "tx_width_mhz", "rx_mcs", "rx_nss",
              "rx_width_mhz", "channel", "freq_mhz", "width_mhz",
              "tx_retries", "tx_failed", "beacon_loss", "rx_drop_misc",
              "connected_seconds", "chan_active_ms", "chan_busy_ms",
              *SYSFS_COUNTERS)
FLOAT_FIELDS = ("signal_dbm", "signal_avg_dbm", "noise_dbm", "snr_db",
                "tx_bitrate_mbps", "rx_bitrate_mbps", "expected_throughput_mbps",
                "band_ghz", "txpower_dbm", "chan_busy_pct", "link_quality")


def build_point(s: dict[str, Any], ts: int) -> Point:
    """A wifi_link point. Tags: interface always; ssid and bssid only while
    associated, so an unassociated sample is a series of its own rather than
    a lie about the last AP. Every field is cast to its fixed type."""
    pt = Point("wifi_link").tag("interface", s["interface"])
    if s.get("associated"):
        if s.get("ssid"):
            pt.tag("ssid", s["ssid"])
        if s.get("bssid"):
            pt.tag("bssid", s["bssid"])
    pt.field("associated", int(bool(s.get("associated"))))
    pt.field("uplink", int(bool(s.get("uplink"))))
    for name in INT_FIELDS:
        if name in s:
            pt.field(name, int(s[name]))
    for name in FLOAT_FIELDS:
        if name in s:
            pt.field(name, float(s[name]))
    return pt.time(ts, WritePrecision.S)


# ───────────────────────── influx write ─────────────────────────
def write_with_retry(write_api, bucket: str, point: Point, retries: int = WRITE_RETRIES) -> bool:
    delay = 1.0
    for attempt in range(1, retries + 1):
        try:
            write_api.write(bucket=bucket, record=point)
            return True
        except Exception as exc:
            if attempt < retries:
                log.warning("Influx write failed (attempt %d/%d): %s — retrying in %.0fs",
                            attempt, retries, exc, delay)
                time.sleep(delay)
                delay *= 2
            else:
                log.error("Influx write failed after %d attempts, dropping sample: %s",
                          retries, exc)
    return False


# ───────────────────────── main loop ─────────────────────────
def main() -> int:
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        return 1

    override = os.environ.get("WIFI_INTERFACE", "").strip() or None
    interval = _env_int("WIFI_SAMPLE_INTERVAL", DEFAULT_SAMPLE_INTERVAL)
    slow_interval = _env_int("WIFI_SLOW_INTERVAL", DEFAULT_SLOW_INTERVAL)
    bucket = os.environ["INFLUX_BUCKET"]

    client = InfluxDBClient(url=os.environ["INFLUX_URL"], token=os.environ["INFLUX_TOKEN"],
                            org=os.environ["INFLUX_ORG"], timeout=10_000)
    write_api = client.write_api(write_options=SYNCHRONOUS)

    have_iw = shutil.which("iw") is not None
    if not have_iw:
        log.warning("iw not installed: signal from /proc/net/wireless only, "
                    "no bitrate, failures or channel data")

    iface: str | None = None
    idle_logged = False
    slow = SlowState()
    last_bssid: str | None = None
    log.info("Wi-Fi link collector started (interval %ds, slow %ds, interface %s)",
             interval, slow_interval, override or "auto")

    while True:
        started = time.time()
        try:
            if iface is None or not (SYS_NET / iface / "phy80211").exists():
                iface = choose_interface(override)
                slow, last_bssid = SlowState(), None
                if iface is None:
                    if not idle_logged:
                        log.info("no wireless interface%s; wifi_link idle, rescanning every %ds",
                                 f" named {override!r}" if override else "", IDLE_RESCAN)
                        idle_logged = True
                    time.sleep(IDLE_RESCAN)
                    continue
                idle_logged = False
                log.info("collecting from %s", iface)

            uplink = uplink_interface() == iface
            s = sample_fast(iface, have_iw, uplink)
            # Channel, width and SSID change only on a roam -- so a new BSSID
            # re-reads them at once instead of lagging a slow interval behind.
            roamed = s.get("bssid") != last_bssid
            if have_iw and (roamed or started - slow.sampled_at >= slow_interval):
                slow = sample_slow(iface, slow, started)
            last_bssid = s.get("bssid")
            write_with_retry(write_api, bucket, build_point(merge_slow(s, slow), int(started)))
        except Exception as exc:  # never let one bad cycle end the collector
            log.error("cycle failed: %s", exc)

        time.sleep(max(0.0, interval - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
