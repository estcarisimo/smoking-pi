"""Answer for ``<MDNS_NAME>.local`` on the LAN, so the Pi is reachable by a
name that does not depend on its hostname, its DHCP lease or the host's
Avahi keeping its own name.

    MDNS_NAME         the name to claim, without .local (default smoking-pi);
                      off disables the responder
    MDNS_INTERFACES   comma-separated interfaces (default: every LAN
                      interface with IPv4; Docker, VPN and loopback never)
    MDNS_STATE_DIR    where status.json goes (default /run/mdns)

Runs on the host network. See docs/mdns.md.
"""

from __future__ import annotations

import json
import logging
import os
import re
import select
import signal
import sys
import threading
import time

import net
import responder
import wire

log = logging.getLogger("mdns")

DEFAULT_NAME = "smoking-pi"
REFRESH_SECONDS = 30.0
STATUS_SECONDS = 15.0
# A DNS label: letters, digits and inner hyphens, at most 63 bytes, with
# room left for the "-NN" a conflict appends.
NAME_RULE = "MDNS_NAME must be letters, digits and inner hyphens, at most 58"
# Packets read per pass of the loop, so a flood cannot starve the timers,
# the status file or a stop.
BATCH = 64
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,56}[a-z0-9])?$")


def configured_name(env: dict) -> str | None:
    """The base name to claim, None when off. Raises ValueError when the
    setting is not a usable name."""
    raw = (env.get("MDNS_NAME") or "").strip().lower()
    if raw in ("off", "0", "no", "false", "disabled"):
        return None
    if raw.endswith(".local"):
        raw = raw[: -len(".local")]
    name = raw or DEFAULT_NAME
    if not NAME_RE.match(name):
        raise ValueError(NAME_RULE)
    return name


def configured_interfaces(env: dict) -> list[str] | None:
    names = [n.strip() for n in (env.get("MDNS_INTERFACES") or "").split(",") if n.strip()]
    return names or None


def status_path(env: dict) -> str:
    return os.path.join(env.get("MDNS_STATE_DIR") or "/run/mdns", "status.json")


def write_status(path: str, body: dict) -> None:
    body = dict(body, updated=time.time())
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(body, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        # The healthcheck will say so; the name keeps being answered.
        log.warning("status not written to %s: errno %s", path, e.errno)


def idle(path: str, body: dict) -> int:
    """Disabled or misconfigured: say so in the status and stay up, so
    Compose does not restart it in a loop and the CLI can report why.
    An Event, not sleep(): a stop must not wait out the interval."""
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        write_status(path, body)
        stop.wait(STATUS_SECONDS)
    return 0


def accept(source: tuple[str, int], index: int | None, ttl: int | None,
           lan: dict[int, str]) -> bool:
    """Whether a packet is the LAN's: it came in on an interface we answer
    on, and an mDNS packet (from port 5353) has the IP TTL 255 that no
    router forwards (§11). A legacy one-shot query, from another port,
    may have any TTL."""
    if index is not None and index not in lan:
        return False
    if source[1] == responder.MDNS_PORT and ttl is not None and ttl != 255:
        return False
    return True


def run(env: dict) -> int:
    path = status_path(env)
    try:
        base = configured_name(env)
    except ValueError:
        log.error("%s; not answering for any name", NAME_RULE)
        return idle(path, {"state": "invalid", "reason": NAME_RULE,
                           "setting": env.get("MDNS_NAME", "")})
    if base is None:
        log.info("MDNS_NAME=off: not answering for any name")
        return idle(path, {"state": "off"})
    only = configured_interfaces(env)

    sock = net.MulticastSocket()
    interfaces = net.lan_interfaces(only)
    sock.sync(interfaces)
    r = responder.Responder(base, net.addresses(interfaces))
    own = net.own_addresses()
    now = time.monotonic()
    r.start(now)
    log.info("probing for %s", r.name)

    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    by_index = {info["index"]: info["ipv4"] for info in interfaces.values()}
    next_refresh = now + REFRESH_SECONDS
    next_status = now
    last_state, last_name = r.state, r.name

    def send(out: list[responder.Outgoing], arrived: int | None = None) -> None:
        for o in out:
            if o.message.is_response and not o.message.answers:
                continue  # nothing to say: no address yet
            data = wire.build(o.message)
            if o.to is not None:
                sock.send_unicast(data, o.to)
            elif arrived in by_index:
                sock.send_multicast(data, by_index[arrived])
            else:
                for addr in by_index.values():
                    sock.send_multicast(data, addr)

    while not stop["now"]:
        now = time.monotonic()
        send(r.tick(now))
        if (r.state, r.name) != (last_state, last_name):
            if r.name != last_name and r.suffix > 1:
                log.warning("%s is taken by another host; trying %s", last_name, r.name)
            elif r.name != last_name:
                log.info("probing for %s again", r.name)
            elif r.state == "announced":
                log.info("answering for %s at %s", r.name, ", ".join(r.addresses) or "no address")
            elif r.state == "gave_up":
                log.error("every name up to %s is taken; trying %s.local again in %d s",
                          r.name, r.base, int(responder.GIVE_UP_RETRY))
            last_state, last_name = r.state, r.name
            next_status = now  # `smoking-pi url` reads the name held, now
        if now >= next_refresh:
            interfaces = net.lan_interfaces(only)
            sock.sync(interfaces)
            by_index = {info["index"]: info["ipv4"] for info in interfaces.values()}
            own = net.own_addresses()
            send(r.set_addresses(net.addresses(interfaces), now))
            next_refresh = now + REFRESH_SECONDS
        if now >= next_status:
            write_status(path, dict(r.status(), interfaces=sorted(interfaces)))
            next_status = now + STATUS_SECONDS
        due = r.next_due()
        timeout = 1.0 if due is None else max(0.0, min(1.0, due - now))
        ready, _, _ = select.select([sock], [], [], timeout)
        if not ready:
            continue
        for _ in range(BATCH):
            packet = sock.receive()
            if packet is None:
                break
            data, source, index, ttl = packet
            if not accept(source, index, ttl, by_index):
                continue
            try:
                msg = wire.parse(data)
            except wire.WireError:
                continue
            send(r.handle(msg, source, own, time.monotonic()), arrived=index)

    send(r.goodbye())
    log.info("withdrew %s", r.name)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return run(dict(os.environ))


if __name__ == "__main__":
    sys.exit(main())
