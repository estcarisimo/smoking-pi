"""Attribute the uplink's traffic to the stack's services.

Loads the counter-only nftables table (ruleset.py), keeps it in step with
the running containers (config-manager's GET /meter/containers: the
privileged container never holds the Docker socket), and every five
minutes reads and zeroes the counters into one interval per service:
InfluxDB ``service_traffic`` (and ``internet_traffic``, the uplink without
the local network), the 24 h state file /budget reads, and the ledger in
the same file that /traffic reads.

    NETMETER              off = load no table, count nothing (default on)
    NETMETER_INTERFACES   comma-separated uplinks (default: every physical
                          interface: one with a device behind it)
    NETMETER_STATE_DIR    state.json and status.json (default /var/lib/netmeter)
    CONFIG_API_URL/TOKEN  config-manager (default http://127.0.0.1:5000)
    TSDB_TYPE, INFLUX_*   where the series go (InfluxDB only)

Runs on the host network with CAP_NET_ADMIN and the host's cgroup
namespace. Stopping it removes the table. See docs/measurement-budget.md.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import meter
import nft
import ruleset

log = logging.getLogger("netmeter")

SYNC_SECONDS = 60
SYS_NET = Path("/sys/class/net")
CGROUP_ROOT = Path("/sys/fs/cgroup")
FETCH_TIMEOUT = 5  # well inside the 15 s stop grace period
DEFAULT_URL = "http://127.0.0.1:5000"


def physical_interfaces(sys_net: Path = SYS_NET) -> list[str]:
    """Interfaces with a device behind them (wlan0, eth0, end0): Docker's
    bridges and veths, VPN tunnels and the loopback have none."""
    try:
        return sorted(p.name for p in sys_net.iterdir() if (p / "device").exists())
    except OSError:
        return []


def uplinks(env: dict) -> list[str]:
    names = [n.strip() for n in (env.get("NETMETER_INTERFACES") or "").split(",") if n.strip()]
    return names or physical_interfaces()


def fetch_containers(base_url: str, token: str = "",
                     opener=urllib.request.urlopen) -> list[ruleset.Service] | None:
    """The stack's containers, or None when config-manager cannot say.
    Only the exception's type is logged: nothing here echoes the token."""
    req = urllib.request.Request(base_url.rstrip("/") + "/meter/containers")
    if token:
        req.add_header("X-API-Token", token)
    try:
        with opener(req, timeout=FETCH_TIMEOUT) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        log.warning("containers not read: HTTP %s", exc.code)
        return None
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        log.warning("containers not read: %s", type(exc).__name__)
        return None
    if not isinstance(body, dict) or not isinstance(body.get("containers"), list):
        return None
    out = []
    for c in body["containers"]:
        if not isinstance(c, dict) or not c.get("service"):
            continue
        out.append(ruleset.Service(
            name=str(c["service"]),
            host_network=bool(c.get("host_network")),
            cgroup=c.get("cgroup") or None,
            cgroup_level=int(c.get("cgroup_level") or 2),
            ipv4=tuple(c.get("ipv4") or ()),
            ipv6=tuple(c.get("ipv6") or ()),
        ))
    return out


def kinds(services: list[ruleset.Service]) -> dict[str, str]:
    k = {s.name: ("host_network" if s.host_network else "bridge") for s in services}
    k.update(host="rest", other_containers="rest")
    return k


def write_status(path: str, body: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(dict(body, updated=time.time()), f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("status not written: errno %s", e.errno)


def influx_writer(env: dict):
    if env.get("TSDB_TYPE", "influxdb") != "influxdb":
        return None
    needed = ("INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG", "INFLUX_BUCKET")
    if not all(env.get(n) for n in needed):
        log.warning("InfluxDB settings missing: the state file only")
        return None
    from influxdb_client import InfluxDBClient
    from influxdb_client.client.write_api import SYNCHRONOUS
    client = InfluxDBClient(url=env["INFLUX_URL"], token=env["INFLUX_TOKEN"],
                            org=env["INFLUX_ORG"], timeout=10_000)
    api = client.write_api(write_options=SYNCHRONOUS)
    return lambda points: api.write(bucket=env["INFLUX_BUCKET"], record=points)


def live_cgroups(services: list[ruleset.Service],
                 root: Path = CGROUP_ROOT) -> tuple[list[ruleset.Service], tuple]:
    """The services whose cgroup exists, and a fingerprint of them.

    ``socket cgroupv2`` stores the cgroup's kernel id when the rule loads,
    not its path. ``docker restart`` keeps the container id, so the path,
    but the cgroup is created anew; the text of the ruleset is unchanged
    while every rule for that service has stopped matching. The inode of
    each cgroup directory is that kernel id: a new one means reload.
    A cgroup that is gone (the container stopped between config-manager's
    answer and now) is left out, since nft refuses a whole table over one
    path that does not exist."""
    kept, fingerprint = [], []
    for s in services:
        if s.host_network and s.cgroup:
            try:
                inode = (root / s.cgroup).stat().st_ino
            except OSError:
                log.info("%s: no cgroup at %s, not counted this time", s.name, s.cgroup)
                continue
            fingerprint.append((s.name, inode))
        kept.append(s)
    return kept, tuple(sorted(fingerprint))


class Meter:
    """The loop's state, separate from the loop so it is testable: what is
    loaded, what was counted before the last reload, the last interval."""

    def __init__(self, env: dict, nft_mod=nft, fetch=fetch_containers,
                 cgroups=live_cgroups):
        self.env = env
        self.nft = nft_mod
        self.fetch = fetch
        self.cgroups = cgroups
        self.services: list[ruleset.Service] = []
        self.loaded: str | None = None
        self.loaded_key: tuple | None = None
        self.counted: list[ruleset.Service] = []
        self.pending: dict[str, tuple[int, int]] = {}
        self.last_mono: float | None = None
        self.state_path = os.path.join(env.get("NETMETER_STATE_DIR") or "/var/lib/netmeter",
                                       "state.json")
        self.state = meter.load_state(self.state_path)
        self.error: str | None = None

    def sync(self, mono: float | None = None) -> None:
        """Reload the table when the containers (or uplinks) changed."""
        found = self.fetch(self.env.get("CONFIG_API_URL") or DEFAULT_URL,
                           self.env.get("CONFIG_API_TOKEN", ""))
        if found is not None:
            self.services = ruleset.services_counted(found)
        links = uplinks(self.env)
        if not links:
            self.error = ("no physical interface to count on: set NETMETER_INTERFACES "
                          "(a bridge, bond, VLAN or PPPoE uplink has no device of its own)")
            return
        services, fingerprint = self.cgroups(self.services)
        try:
            script = ruleset.build(services, links)
        except ValueError as e:
            self.error = f"no ruleset: {e}"
            log.error("%s", self.error)
            return
        key = (script, fingerprint)
        if key == self.loaded_key:
            return
        try:
            if self.loaded is not None:
                # A reload zeroes every counter: keep what they held.
                self.pending = meter.add_counts(self.pending, self.nft.reset_counters())
            self.nft.load(script)
        except nft.NftError as e:
            self.error = f"the ruleset was refused: {e}"
            log.error("%s", self.error)
            self.loaded = self.loaded_key = None
            return
        dropped = {n for n in self.pending if n.startswith("s_")} - {
            ruleset.counter_name(s.name) + d for s in services for d in ("_tx", "_rx")}
        if dropped:
            log.info("counted before the reload but no longer named, so counted as "
                     "host or other containers: %s", ", ".join(sorted(dropped)))
        self.loaded = script
        self.loaded_key = key
        self.counted = services
        self.error = None
        if self.last_mono is None:
            # Counting starts now: the first interval runs from here.
            self.last_mono = time.monotonic() if mono is None else mono
        log.info("counting on %s for %s", ", ".join(links),
                 ", ".join(s.name for s in self.services) or "the totals only")

    def collect(self, now: float, mono: float) -> dict | None:
        """One interval: everything counted since the last one."""
        if self.loaded is None:
            return None
        counts = meter.add_counts(self.pending, self.nft.reset_counters())
        self.pending = {}
        previous, self.last_mono = self.last_mono, mono
        if previous is None or mono <= previous:
            return None
        attributed = ruleset.attribute(counts, self.counted)
        self.state, interval = meter.record(self.state, attributed, kinds(self.counted),
                                            now, mono - previous, ruleset.internet(counts))
        try:
            meter.save_state(self.state_path, self.state)
        except OSError as e:
            log.warning("state not saved: errno %s", e.errno)
        return interval


def run(env: dict) -> int:
    status_path = os.path.join(env.get("NETMETER_STATE_DIR") or "/var/lib/netmeter",
                               "status.json")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if (env.get("NETMETER") or "on").strip().lower() in ("off", "0", "no", "false"):
        log.info("NETMETER=off: no table, nothing counted")
        try:
            nft.teardown()  # one left behind by a container that was killed
        except nft.NftError:
            pass
        while not stop.is_set():
            write_status(status_path, {"state": "off"})
            stop.wait(30)
        return 0

    m = Meter(env)
    write = influx_writer(env)
    next_sync = 0.0
    next_collect = 0.0
    while not stop.is_set():
        mono = time.monotonic()
        if mono >= next_sync:
            try:
                m.sync(mono)
            except Exception as e:
                # Never a crash loop: a container restart loses the state.
                m.error = f"sync failed: {type(e).__name__}"
                m.loaded = m.loaded_key = None
                log.error("%s", m.error)
            next_sync = time.monotonic() + SYNC_SECONDS
        # Read after the sync, which can wait on config-manager.
        now, mono = time.time(), time.monotonic()
        if now >= next_collect:
            try:
                interval = m.collect(now, mono)
            except nft.NftError as e:
                m.error = f"counters not read: {e}"
                log.error("%s", m.error)
                m.loaded = m.loaded_key = None  # load it again on the next sync
                m.last_mono = None
                interval = None
            if interval and write:
                try:
                    write(meter.points(interval))
                except Exception as exc:
                    # The type only: an InfluxDB error's message can carry the token.
                    log.error("traffic not written: %s", type(exc).__name__)
            next_collect = (now // meter.INTERVAL + 1) * meter.INTERVAL
        write_status(status_path, {
            "state": "error" if m.error else ("counting" if m.loaded else "starting"),
            "reason": m.error,
            "services": [s.name for s in m.counted],
            "uplinks": uplinks(env),
        })
        stop.wait(max(0.5, min(next_collect - time.time(), next_sync - time.monotonic(), 30)))
    try:
        nft.teardown()
        log.info("table removed")
    except nft.NftError as e:
        log.error("table not removed: %s", e)
    return 0


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return run(dict(os.environ))


if __name__ == "__main__":
    sys.exit(main())
