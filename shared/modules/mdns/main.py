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
        raise ValueError("MDNS_NAME must be letters, digits and hyphens (at most 58)")
    return name


def configured_interfaces(env: dict) -> list[str] | None:
    names = [n.strip() for n in (env.get("MDNS_INTERFACES") or "").split(",") if n.strip()]
    return names or None


def status_path(env: dict) -> str:
    return os.path.join(env.get("MDNS_STATE_DIR") or "/run/mdns", "status.json")


def write_status(path: str, body: dict) -> None:
    body = dict(body, updated=time.time())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(body, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def idle(path: str, body: dict) -> int:
    """Disabled or misconfigured: say so in the status and stay up, so
    Compose does not restart it in a loop and the CLI can report why."""
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    while not stop["now"]:
        write_status(path, body)
        time.sleep(STATUS_SECONDS)
    return 0


def run(env: dict) -> int:
    path = status_path(env)
    try:
        base = configured_name(env)
    except ValueError as e:
        log.error("%s; not answering for any name", e)
        return idle(path, {"state": "invalid", "reason": str(e),
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
            if r.name != last_name:
                log.warning("%s is taken by another host; trying %s", last_name, r.name)
            elif r.state == "announced":
                log.info("answering for %s at %s", r.name, ", ".join(r.addresses) or "no address")
            elif r.state == "gave_up":
                log.error("every name up to %s is taken; giving up", r.name)
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
        while (packet := sock.receive()) is not None:
            data, source, index = packet
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
