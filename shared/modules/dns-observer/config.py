"""Configuration for the DNS observer, read once from the environment.

The observer is AdGuard Home (the DNS server) plus this supervisor (the
fallback). Every knob has a default that is safe on a home LAN. The two
rules the defaults encode (docs/dns-observer.md):

* **Upstreams and bootstrap resolvers are public addresses.** The router
  forwards the house's queries to the Pi. An upstream that is the router, or
  a name resolved through the Pi's own resolv.conf (which is the router),
  comes straight back here: a loop. Private addresses are refused unless
  ``DNS_ALLOW_PRIVATE_UPSTREAM=1`` (an internal resolver that does not
  forward to the Pi).
* **Only LAN clients are answered.** A resolver that answers the internet
  (a Pi with a public IPv6 address) is an amplification relay.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_ALLOW_CLIENTS = (
    "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,"
    "169.254.0.0/16,100.64.0.0/10,fc00::/7,fe80::/10"
)
DEFAULT_UPSTREAMS = "https://1.1.1.1/dns-query,https://8.8.8.8/dns-query"
DEFAULT_FALLBACK = "1.1.1.1,8.8.8.8"
DEFAULT_BOOTSTRAP = "1.1.1.1,8.8.8.8"

# RFC 8375 reserves home.arpa for home networks: nothing under it exists
# publicly, so a canary that leaks to a public resolver (the router no
# longer forwards here) is answered NXDOMAIN and reaches nobody's server.
# Not .invalid or .test: routers that follow RFC 6761 answer those
# themselves and never forward them, so the canary could never arrive.
DEFAULT_CANARY_DOMAIN = "canary.smoking-pi.home.arpa"


class ConfigError(ValueError):
    """A setting that would make the observer unsafe or unusable."""


def _split(value: str) -> list[str]:
    if value.strip().lower() == "none":
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _get(env: dict[str, str], name: str, default: str) -> str:
    """An empty value means the default, never "nothing".

    Compose passes ``DNS_ALLOW_CLIENTS=`` when the env file leaves it unset;
    read literally, that is AdGuard's ``allowed_clients: []`` -- which
    AdGuard reads as *everyone*, an open resolver. ``none`` says "empty" on
    purpose (``DNS_FALLBACK=none``: no plain-DNS fallback).
    """
    value = env.get(name, "").strip()
    return value or default


def _host_of(upstream: str) -> str:
    """The host part of an AdGuard upstream spec (URL, host:port or IP)."""
    if "://" in upstream:
        return urlsplit(upstream).hostname or ""
    if upstream.startswith("["):
        return upstream[1:].split("]", 1)[0]
    if upstream.count(":") == 1:
        return upstream.split(":", 1)[0]
    return upstream


def _is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local or (
        ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10")
    )


def _check_public(values: list[str], setting: str, allow_private: bool, *, names_ok: bool) -> None:
    for value in values:
        host = _host_of(value)
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            if names_ok:
                # A name is resolved through the bootstrap resolvers, which
                # are checked to be IPs; that never touches the router.
                continue
            raise ConfigError(
                f"{setting}: {value!r} must be an IP address: resolving a name "
                "would ask the router, which forwards back to the Pi (a loop)."
            ) from None
        if _is_private(ip) and not allow_private:
            raise ConfigError(
                f"{setting}: {host} is a private address. If it is the router, "
                "forwarding there loops back to the Pi. Set "
                "DNS_ALLOW_PRIVATE_UPSTREAM=1 only for an internal resolver that "
                "does not forward to this observer."
            )


def _int(env: dict[str, str], name: str, default: int, minimum: int = 0) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name}: {raw!r} is not an integer") from None
    if value < minimum:
        raise ConfigError(f"{name}: must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class Config:
    bind_hosts: list[str]
    port: int
    upstreams: list[str]
    fallback: list[str]
    bootstrap: list[str]
    allow_clients: list[str]
    anonymize_clients: bool
    retention_hours: int
    upstream_timeout: str
    admin_address: str
    admin_user: str
    admin_password: str
    canary_via: str  # "auto", "off" or an IP address
    canary_port: int
    canary_domain: str
    canary_interval: int
    canary_misses: int
    quiet_after: int
    state_dir: str
    adguard_binary: str
    adguard_conf: str
    adguard_work: str
    # The DNS wizard (wizard.py): what the house uses, summarised.
    wizard_interval: int = 600  # seconds; 0 turns it off
    wizard_top: int = 25
    wizard_exclude: tuple[str, ...] = ()
    # Which services to measure (wizard.select): K comes from the data.
    wizard_score: str = "auto"  # auto | presence | queries
    wizard_coverage: float = 0.8
    wizard_floor: float = 0.005
    wizard_max: int = 60

    @property
    def status_path(self) -> str:
        return os.path.join(self.state_dir, "status.json")

    @property
    def admin_url(self) -> str:
        """Where the supervisor reaches the API: loopback when the UI listens
        on every address (0.0.0.0 or [::]), which is not an address to
        connect to."""
        host, _, port = self.admin_address.rpartition(":")
        if host in ("0.0.0.0", ""):
            host = "127.0.0.1"
        elif host in ("[::]", "::"):
            host = "[::1]"
        return f"http://{host}:{port}"

    @property
    def selftest_host(self) -> str:
        """Where the supervisor asks its own server: loopback of a bound family."""
        for host in self.bind_hosts:
            if host in ("0.0.0.0", "127.0.0.1"):
                return "127.0.0.1"
            if host in ("::", "::1"):
                return "::1"
        return self.bind_hosts[0]

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = dict(os.environ if env is None else env)
        allow_private = _get(env, "DNS_ALLOW_PRIVATE_UPSTREAM", "") in ("1", "true")

        upstreams = _split(_get(env, "DNS_UPSTREAMS", DEFAULT_UPSTREAMS))
        fallback = _split(_get(env, "DNS_FALLBACK", DEFAULT_FALLBACK))
        bootstrap = _split(_get(env, "DNS_BOOTSTRAP", DEFAULT_BOOTSTRAP))
        if not upstreams:
            raise ConfigError("DNS_UPSTREAMS is empty")
        _check_public(upstreams, "DNS_UPSTREAMS", allow_private, names_ok=True)
        _check_public(fallback, "DNS_FALLBACK", allow_private, names_ok=False)
        _check_public(bootstrap, "DNS_BOOTSTRAP", allow_private, names_ok=False)
        if not bootstrap and any(
            not _is_ip(_host_of(u)) for u in upstreams + fallback
        ):
            raise ConfigError("DNS_BOOTSTRAP is empty but an upstream is a hostname")

        allow = _split(_get(env, "DNS_ALLOW_CLIENTS", DEFAULT_ALLOW_CLIENTS))
        if not allow:
            # AdGuard reads an empty list as "allow everyone".
            raise ConfigError("DNS_ALLOW_CLIENTS is empty: that would answer anyone")
        for net in allow:
            try:
                ipaddress.ip_network(net, strict=False)
            except ValueError:
                raise ConfigError(f"DNS_ALLOW_CLIENTS: {net!r} is not a network") from None

        bind = _split(_get(env, "DNS_BIND", "0.0.0.0"))
        if not bind:
            raise ConfigError("DNS_BIND is empty")
        for host in bind:
            if not _is_ip(host):
                raise ConfigError(f"DNS_BIND: {host!r} is not an IP address")

        via = _get(env, "DNS_CANARY_VIA", "auto").strip().lower() or "auto"
        if via not in ("auto", "off") and not _is_ip(via):
            raise ConfigError(f"DNS_CANARY_VIA: {via!r} is not auto, off or an IP address")

        password = _get(env, "DNS_ADMIN_PASSWORD", "")
        if not password:
            raise ConfigError(
                "DNS_ADMIN_PASSWORD is empty; setup generates it (smoking-pi passwords)"
            )

        admin_address = _get(env, "DNS_ADMIN_ADDRESS", "127.0.0.1:3053")
        admin_host, _, admin_port = admin_address.rpartition(":")
        if not admin_host or not admin_port.isdigit():
            raise ConfigError(
                f"DNS_ADMIN_ADDRESS: {admin_address!r} is not host:port "
                "(127.0.0.1:3053, or 0.0.0.0:3053 to open it to the network)"
            )

        timeout_ms = _int(env, "DNS_UPSTREAM_TIMEOUT_MS", 2000, minimum=200)
        return cls(
            bind_hosts=bind,
            port=_int(env, "DNS_PORT", 53, minimum=1),
            upstreams=upstreams,
            fallback=fallback,
            bootstrap=bootstrap,
            allow_clients=allow,
            anonymize_clients=_get(env, "DNS_ANONYMIZE_CLIENTS", "1") not in ("0", "false"),
            retention_hours=24 * _int(env, "DNS_RETENTION_DAYS", 7, minimum=1),
            upstream_timeout=f"{timeout_ms}ms",
            admin_address=admin_address,
            admin_user=_get(env, "DNS_ADMIN_USER", "smokingpi"),
            admin_password=password,
            canary_via=via,
            canary_port=_int(env, "DNS_CANARY_PORT", 53, minimum=1),
            canary_domain=_get(env, "DNS_CANARY_DOMAIN", DEFAULT_CANARY_DOMAIN).strip(".").lower(),
            canary_interval=_int(env, "DNS_CANARY_INTERVAL", 300, minimum=30),
            canary_misses=_int(env, "DNS_CANARY_MISSES", 3, minimum=1),
            quiet_after=_int(env, "DNS_QUIET_AFTER", 1800, minimum=60),
            state_dir=_get(env, "DNS_STATE_DIR", "/var/lib/dns-observer"),
            adguard_binary=env.get("ADGUARD_BINARY", "/opt/adguardhome/AdGuardHome"),
            adguard_conf=env.get("ADGUARD_CONF", "/opt/adguardhome/conf/AdGuardHome.yaml"),
            adguard_work=env.get("ADGUARD_WORK", "/opt/adguardhome/work"),
            wizard_interval=_wizard_interval(env),
            wizard_top=_int(env, "DNS_WIZARD_TOP", 25, minimum=1),
            wizard_exclude=tuple(_split(_get(env, "DNS_WIZARD_EXCLUDE", ""))),
            wizard_score=_choice(env, "DNS_WIZARD_SCORE", "auto", ("auto", "presence", "queries")),
            wizard_coverage=_fraction(env, "DNS_WIZARD_COVERAGE", 0.8),
            wizard_floor=_fraction(env, "DNS_WIZARD_FLOOR", 0.005),
            wizard_max=_int(env, "DNS_WIZARD_MAX", 60, minimum=1),
        )


def _choice(env: dict[str, str], name: str, default: str, allowed: tuple[str, ...]) -> str:
    value = _get(env, name, default).strip().lower()
    if value not in allowed:
        raise ConfigError(f"{name}: {value!r} is not one of {', '.join(allowed)}")
    return value


def _fraction(env: dict[str, str], name: str, default: float) -> float:
    raw = _get(env, name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name}: {raw!r} is not a number") from None
    if not 0 < value <= 1:
        raise ConfigError(f"{name}: must be in (0, 1], got {value}")
    return value


def _wizard_interval(env: dict[str, str]) -> int:
    """0 (off) or at least 60 s: each pass reads what the log gained."""
    value = _int(env, "DNS_WIZARD_INTERVAL", 600, minimum=0)
    if 0 < value < 60:
        raise ConfigError(f"DNS_WIZARD_INTERVAL: 0 (off) or >= 60, got {value}")
    return value


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True
