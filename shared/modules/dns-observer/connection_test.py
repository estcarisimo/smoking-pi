"""Check the DNS path end to end: ``python connection_test.py [--json]``.

What ``smoking-pi dns test`` runs right after pointing the router at the
Pi, when the canary (one name every 5 minutes) would take a quarter of an
hour to say anything. Each check has its own fix:

1. the Pi's DNS server answers, on loopback and on the LAN address the
   router will use;
2. it resolves a real name through the encrypted upstreams;
3. the router resolves at all;
4. the router forwards to the Pi: ``--count`` unique names asked of the
   router, counted as they arrive in the Pi's query log. All, some or
   none is the verdict. None, with the router flagging its answers as
   authoritative (``aa``), means it answers the canary's suffix itself
   and never forwards it (routers do that with ``.invalid`` and ``.test``,
   RFC 6761).

Exit 0 when nothing failed (a warning is not a failure), 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import socket
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass

import dns.asyncquery
import dns.flags
import dns.message
import dns.rcode

import adguard
from config import Config, ConfigError
from main import default_gateway

# How long a forwarded name may take to show up in AdGuard's query log.
ARRIVAL_WAIT = 2.0
QUERY_TIMEOUT = 3.0


@dataclass
class Check:
    name: str
    result: str  # ok | warn | fail | skip
    detail: str
    fix: str | None = None


@dataclass
class Answer:
    rcode: str
    answers: int
    authoritative: bool
    ms: float


Query = Callable[[str, str, str, int], Awaitable[Answer]]


async def udp_query(name: str, rdtype: str, server: str, port: int) -> Answer:
    start = time.monotonic()
    resp = await dns.asyncquery.udp(
        dns.message.make_query(name, rdtype), server, port=port, timeout=QUERY_TIMEOUT
    )
    return Answer(
        rcode=dns.rcode.to_text(resp.rcode()),
        answers=len(resp.answer),
        authoritative=bool(resp.flags & dns.flags.AA),
        ms=(time.monotonic() - start) * 1000,
    )


def lan_address(via: str | None) -> str | None:
    """The address this host uses towards the router: the one the router
    must forward to. A UDP connect sends nothing."""
    target = via or "1.1.1.1"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((target, 53))
            return s.getsockname()[0]
    except OSError:
        return None


def forwarding_verdict(
    via: str, lan: str | None, sent: int, arrived: int, authoritative: int, domain: str
) -> Check:
    """The router check's verdict from the counts alone (unit-tested)."""
    name = "router forwards to the Pi"
    detail = f"{arrived}/{sent} test names asked of {via} arrived here"
    if sent == 0:
        return Check(name, "fail", f"the router at {via} did not answer any test name",
                     "Check that this host reaches the router (smoking-pi doctor).")
    if arrived == sent:
        return Check(name, "ok", detail)
    if arrived:
        return Check(
            name, "warn", detail,
            "The router also sends queries to another server: a secondary DNS, or "
            "IPv6 DNS servers. Expected if you kept a secondary; the observer then "
            "sees part of the house (state 'partial').",
        )
    if authoritative:
        return Check(
            name, "fail",
            f"{detail}; the router answers *.{domain} itself (authoritative) and "
            "never forwards it",
            "Set a canary name the router forwards: smoking-pi config set "
            "DNS_CANARY_DOMAIN canary.smoking-pi.home.arpa, then run this again.",
        )
    target = lan or "this Pi's LAN address"
    return Check(
        name, "fail", detail,
        "The router is not using the Pi. Open the router's DNS setting again and "
        f"check it saved (many apps only save on an explicit Save); the primary "
        f"must be {target}. The router's DNS setting, "
        "not the DNS it hands devices by DHCP.",
    )


async def run(
    cfg: Config,
    *,
    via: str | None,
    count: int,
    name: str,
    query: Query = udp_query,
    querylog: Callable[..., Awaitable[list[dict]]] | None = None,
    lan: str | None = None,
) -> list[Check]:
    checks: list[Check] = []
    port = cfg.port

    async def ask(qname: str, rdtype: str, server: str, p: int = port) -> Answer | None:
        try:
            return await query(qname, rdtype, server, p)
        except Exception:  # timeout, refused, unreachable
            return None

    # 1. The server answers. ANY is refused by AdGuard itself, without an
    # upstream, so this says "listening", not "the internet works".
    local = cfg.selftest_host
    a = await ask(f"selftest-{secrets.token_hex(4)}.{cfg.canary_domain}", "ANY", local)
    if a is None:
        checks.append(Check(
            "Pi DNS server answers", "fail", f"no answer from {local}:{port}",
            "smoking-pi dns status; smoking-pi logs dns-observer",
        ))
        return checks
    checks.append(Check("Pi DNS server answers", "ok", f"{local}:{port}"))

    if lan and lan != local:
        a = await ask(f"selftest-{secrets.token_hex(4)}.{cfg.canary_domain}", "ANY", lan)
        if a is None:
            checks.append(Check(
                "answers on the LAN", "fail", f"no answer from {lan}:{port}",
                f"The router could not reach it either. DNS_BIND is "
                f"{','.join(cfg.bind_hosts)}; it must include {lan} or 0.0.0.0. "
                "A firewall on the Pi dropping port 53 does the same.",
            ))
        else:
            checks.append(Check("answers on the LAN", "ok", f"{lan}:{port}"))

    # 2. A real name through the encrypted upstreams.
    a = await ask(name, "A", local)
    if a is None or a.rcode != "NOERROR" or not a.answers:
        got = "timeout" if a is None else f"{a.rcode}, {a.answers} answers"
        checks.append(Check(
            "resolves through the upstreams", "fail", f"{name}: {got}",
            "The Pi cannot reach its encrypted upstreams: check its internet "
            "(smoking-pi doctor) and DNS_UPSTREAMS.",
        ))
    else:
        checks.append(Check(
            "resolves through the upstreams", "ok", f"{name} in {a.ms:.0f} ms"
        ))

    if via is None:
        checks.append(Check(
            "router forwards to the Pi", "skip",
            "no router: no default gateway here and DNS_CANARY_VIA is off",
            "Give it one: smoking-pi dns test --via <router address>",
        ))
        return checks

    # 3. The router resolves at all.
    a = await ask(name, "A", via, cfg.canary_port)
    if a is None or a.rcode != "NOERROR" or not a.answers:
        got = "timeout" if a is None else f"{a.rcode}, {a.answers} answers"
        checks.append(Check(
            "router resolves", "fail", f"{name} via {via}: {got}",
            "The house has no working DNS through the router right now. If the "
            "router points only at the Pi, fix the checks above first; otherwise "
            "set the router's DNS back to automatic.",
        ))
    else:
        checks.append(Check("router resolves", "ok", f"{name} via {via} in {a.ms:.0f} ms"))

    # 4. The router forwards here: unique names, counted on arrival.
    nonce = f"t{secrets.token_hex(5)}"
    names = [f"{nonce}-{i}.{cfg.canary_domain}" for i in range(count)]
    answers = await asyncio.gather(*(ask(n, "A", via, cfg.canary_port) for n in names))
    replied = [x for x in answers if x is not None]
    authoritative = sum(1 for x in replied if x.authoritative)
    arrived: set[str] = set()
    if querylog is not None:
        deadline = time.monotonic() + ARRIVAL_WAIT
        while True:
            await asyncio.sleep(0.5)
            for e in await querylog(search=nonce, limit=count * 4):
                qn = str((e.get("question") or {}).get("name", "")).rstrip(".").lower()
                if qn in names:
                    arrived.add(qn)
            if len(arrived) == count or time.monotonic() >= deadline:
                break
    checks.append(forwarding_verdict(
        via, lan, count if replied else 0, len(arrived), authoritative, cfg.canary_domain
    ))
    return checks


def router_address(cfg: Config, override: str | None) -> str | None:
    if override:
        return override
    if cfg.canary_via == "off":
        return None
    if cfg.canary_via == "auto":
        return default_gateway()
    return cfg.canary_via


async def _main(args: argparse.Namespace) -> list[Check]:
    cfg = Config.from_env()
    via = router_address(cfg, args.via)
    api = adguard.AdGuardAPI(cfg)
    try:
        return await run(
            cfg, via=via, count=args.count, name=args.name,
            querylog=api.querylog, lan=lan_address(via),
        )
    finally:
        await api.close()


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="smoking-pi dns test")
    p.add_argument("--via", help="the router's address (default: the default gateway)")
    p.add_argument("--count", type=int, default=10, help="test names to send (default 10)")
    p.add_argument("--name", default="example.com", help="real name to resolve")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    args.count = max(1, min(args.count, 50))
    try:
        checks = asyncio.run(_main(args))
    except ConfigError as exc:
        print(f"configuration refused: {exc}", file=sys.stderr)
        return 2
    failed = any(c.result == "fail" for c in checks)
    if args.json:
        json.dump({"ok": not failed, "checks": [asdict(c) for c in checks]},
                  sys.stdout, indent=1)
        print()
        return 1 if failed else 0
    for c in checks:
        print(f"{c.result.upper():<5} {c.name}: {c.detail}")
        if c.fix and c.result != "ok":
            print(f"      fix: {c.fix}")
    print()
    if failed:
        print("FAILED: fix the first failing check, then run this again.")
    elif any(c.result == "warn" for c in checks):
        print("Works, with the warning above.")
    else:
        print("The DNS path works: devices -> router -> Pi -> upstreams.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
