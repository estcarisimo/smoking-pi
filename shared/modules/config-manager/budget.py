"""What the configured measurements cost, against a ceiling.

The first step of the measurement budget: accounting, not admission. It
reads the same generated files SmokePing loads (Targets, CPE_Targets,
Probes, the Database defaults), so what it counts is what runs, and
reports samples per hour and approximate bytes per day per probe, the
totals, and how much of each ceiling they use. Nothing is throttled yet;
the point is that a target list that grows (the DNS wizard, a future
discovery step) is visible before it is expensive.

Deliberately approximate: the purpose is guardrails and capacity planning,
not byte-perfect accounting. The per-sample costs below were measured, not
derived from a spec: averages over real sites, checked against the
netmeter's per-service counters.

Everything here is pure, so it is tested without Docker; api.py and the
command line below supply the file contents.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from typing import Dict, Optional, Set

import freshness

# Bytes on the wire per sample, both directions, IP headers included,
# measured 2026-09-30:
# - FPing / FPing6: one echo request and its reply; fping's default 56
#   data bytes make an 84-byte IPv4 packet (104 over IPv6).
# - DNS: one dig lookup, about 80 bytes up and 80-165 down with dig's
#   EDNS cookie; rounded up.
# - TCPPing: tcptraceroute's SYN, the SYN/ACK and the kernel's RST.
# - Curl: one HEAD over TLS 1.3. The handshake (certificate chain) and the
#   response headers, and curl's own lookup of the name. Re-measured
#   2026-10-02 per sample on a test Pi, 23 sites: 9-17 KB over HTTP/1.1 and
#   HTTP/2, 12.5 KB on average.
# - Curl over HTTP/3 (HTTP3_BYTES_PER_SAMPLE): 15-22 KB, 17-18 KB on
#   average. QUIC pads every client Initial to 1200 bytes and acknowledges
#   on its own, so the same HEAD costs about 45% more than over TCP. Priced
#   at 12 KB until then, the estimate sat ~29% under the meter.
BYTES_PER_SAMPLE: Dict[str, int] = {
    "FPing": 168,
    "FPing6": 208,
    "DNS": 300,
    "TCPPing": 200,
    "Curl": 12_500,
}
HTTP3_BYTES_PER_SAMPLE = 18_000

# Default ceilings, about 30 GB a month. The shipped seed uses 12% of the
# bandwidth (~117 MB/day, nearly all of it TLS handshakes) and 7% of the
# samples; a DNS wizard adoption of 60 services over HTTP/1.1, 2 and 3
# alone is ~2.2 GB/day. So they flag a target list that grew several-fold,
# not normal use. MEASUREMENT_BUDGET_MB_PER_DAY and
# MEASUREMENT_BUDGET_SAMPLES_PER_HOUR override them.
DEFAULT_MB_PER_DAY = 1000
DEFAULT_SAMPLES_PER_HOUR = 20_000

_SECTION = re.compile(r"^(\+{1,2})\s*(\S+)\s*$")


def probe_classes(probes_text: str) -> Dict[str, str]:
    """SmokePing class per probe name. A top-level ``+ Name`` is its own
    class; a ``++ Sub`` under it (config_generator's sub-probes, e.g.
    ``+ Curl`` / ``++ CurlHTTP2``) belongs to the parent's class."""
    classes: Dict[str, str] = {}
    parent: Optional[str] = None
    for raw in probes_text.splitlines():
        m = _SECTION.match(raw.strip())
        if not m:
            continue
        if len(m.group(1)) == 1:
            parent = m.group(2)
            classes[parent] = parent
        elif parent is not None:
            classes[m.group(2)] = parent
    return classes


def http3_probes(probes_text: str) -> Set[str]:
    """Probe names whose curl speaks HTTP/3 (``--http3`` or
    ``--http3-only`` in their ``extraargs``), priced apart from HTTP/1.1
    and HTTP/2. Each section's own ``extraargs`` only, which is how
    config_generator emits them: a ``+ Curl`` parent never carries one."""
    found: Set[str] = set()
    current: Optional[str] = None
    for raw in probes_text.splitlines():
        line = raw.strip()
        m = _SECTION.match(line)
        if m:
            current = m.group(2)
            continue
        if current and line.startswith("extraargs") and "--http3" in line:
            found.add(current)
    return found


def ceilings(env: Optional[Dict[str, str]] = None) -> Dict[str, float]:
    """The configured ceilings; an empty, malformed or non-positive value
    falls back to the default rather than disabling the check."""
    env = os.environ if env is None else env

    def read(name: str, default: float) -> float:
        try:
            value = float(env.get(name, "") or default)
        except ValueError:
            return default
        return value if value > 0 else default

    return {
        "mb_per_day": read("MEASUREMENT_BUDGET_MB_PER_DAY", DEFAULT_MB_PER_DAY),
        "samples_per_hour": read("MEASUREMENT_BUDGET_SAMPLES_PER_HOUR",
                                 DEFAULT_SAMPLES_PER_HOUR),
    }


# The meter's state is written every 5 minutes; older than this, the
# exporter has stopped and its figure is not "now".
MEASURED_STALE_SECONDS = 900


# Less than this much data is not yet a daily figure: shown, but not
# judged against the ceiling (an image pull in the first five minutes
# would read as hundreds of times the budget).
MEASURED_MIN_SECONDS = 3600


def measured(state_text: str, now: float, mb_ceiling: float) -> Optional[dict]:
    """What the uplink actually carried over the last 24 h, from the
    smokeping-exporters uplink_traffic.py state file; None without one.

    Everything on the interface, not only the measurements. ``mb_per_day``
    scales the covered time to a day, so a meter that started an hour ago
    still reads as a daily figure (``hours`` says how much it rests on;
    ``provisional`` says it is under an hour, and then there is no
    percentage of the ceiling).
    """
    try:
        state = json.loads(state_text) if state_text else None
        if not isinstance(state, dict):
            return None
        rows = []
        for i in state.get("intervals") or []:
            if not isinstance(i, dict):
                continue
            row = (float(i.get("t") or 0), float(i.get("seconds") or 0),
                   int(i.get("rx") or 0), int(i.get("tx") or 0), i.get("interface"))
            if now - row[0] <= 86_400:
                rows.append(row)
    except (TypeError, ValueError):
        # A damaged state file costs the measured line, never the report.
        return None
    seconds = sum(r[1] for r in rows)
    if not rows or seconds <= 0:
        return None
    rx = sum(r[2] for r in rows)
    tx = sum(r[3] for r in rows)
    per_day = (rx + tx) / seconds * 86_400 / 1e6
    newest = max(r[0] for r in rows)
    interfaces = sorted({r[4] for r in rows if r[4]})
    provisional = seconds < MEASURED_MIN_SECONDS
    return {
        "scope": "uplink",
        # An uplink change within the day: both, since the sum spans both.
        "interface": ", ".join(interfaces) or None,
        "interfaces": interfaces,
        "mb_per_day": round(per_day, 2),
        "rx_mb": round(rx / 1e6, 2),
        "tx_mb": round(tx / 1e6, 2),
        "hours": round(seconds / 3600, 1),
        "minutes": round(seconds / 60),
        "kbps": round((rx + tx) * 8 / seconds / 1000, 1),
        "provisional": provisional,
        "pct_of_ceiling": (round(100 * per_day / mb_ceiling, 1)
                           if mb_ceiling and not provisional else None),
        "stale": now - newest > MEASURED_STALE_SECONDS,
    }


def by_service(state_text: str, now: float) -> Optional[dict]:
    """What each service sent and received on the uplink over the last
    24 h, from the netmeter container's state file (netmeter/meter.py);
    None without one. Exact attribution, not an estimate: nftables counters
    keyed on each container. ``host`` is everything on the uplink that no
    container sent (apt, an assistant, sshd); ``other_containers`` are
    containers this stack does not name. Most traffic first."""
    try:
        state = json.loads(state_text) if state_text else None
        if not isinstance(state, dict):
            return None
        rows = []
        for i in state.get("intervals") or []:
            if not isinstance(i, dict) or now - float(i.get("t") or 0) > 86_400:
                continue
            services = i.get("services") if isinstance(i.get("services"), dict) else {}
            rows.append((float(i.get("t") or 0), float(i.get("seconds") or 0), {
                str(k): (int(v.get("rx") or 0), int(v.get("tx") or 0), str(v.get("kind") or ""))
                for k, v in services.items() if isinstance(v, dict)}))
    except (TypeError, ValueError, AttributeError):
        return None
    seconds = sum(r[1] for r in rows)
    if not rows or seconds <= 0:
        return None
    totals: Dict[str, list] = {}
    for _t, _s, services in rows:
        for name, (rx, tx, kind) in services.items():
            acc = totals.setdefault(name, [0, 0, kind])
            acc[0] += rx
            acc[1] += tx
            acc[2] = kind or acc[2]
    services = [{
        "service": name,
        "kind": kind,
        "rx_mb": round(rx / 1e6, 2),
        "tx_mb": round(tx / 1e6, 2),
        "mb_per_day": round((rx + tx) / seconds * 86_400 / 1e6, 2),
    } for name, (rx, tx, kind) in totals.items()]
    services.sort(key=lambda r: (-r["mb_per_day"], r["service"]))
    return {
        "hours": round(seconds / 3600, 1),
        "minutes": round(seconds / 60),
        "provisional": seconds < MEASURED_MIN_SECONDS,
        "stale": now - max(r[0] for r in rows) > MEASURED_STALE_SECONDS,
        "mb_per_day": round(sum(r["mb_per_day"] for r in services), 2),
        "services": services,
    }


def report(
    targets_text: str,
    cpe_text: str,
    probes_text: str,
    database_text: str = "",
    env: Optional[Dict[str, str]] = None,
    traffic_text: str = "",
    now: Optional[float] = None,
    services_text: str = "",
) -> dict:
    """The JSON body of GET /budget."""
    step, pings = freshness.parse_database_defaults(database_text)
    steps = freshness.parse_probe_var(probes_text, "step", step)
    counts = freshness.parse_probe_var(probes_text, "pings", pings)
    classes = probe_classes(probes_text)
    http3 = http3_probes(probes_text)
    per_probe = Counter(e.probe for e in freshness.parse_targets(targets_text, cpe_text))

    rows = []
    for probe, targets in sorted(per_probe.items()):
        p_step = steps.get(probe, step)
        p_pings = counts.get(probe, pings)
        cls = classes.get(probe, probe)
        per_sample = (HTTP3_BYTES_PER_SAMPLE if cls == "Curl" and probe in http3
                      else BYTES_PER_SAMPLE.get(cls))
        samples_h = targets * p_pings * 3600 / p_step
        rows.append({
            "probe": probe,
            "class": cls,
            "targets": targets,
            "step": p_step,
            "pings": p_pings,
            "samples_per_hour": round(samples_h, 1),
            "bytes_per_sample": per_sample,
            "mb_per_day": (None if per_sample is None
                           else round(samples_h * 24 * per_sample / 1e6, 2)),
        })
    # Most expensive first: what to cut is at the top.
    rows.sort(key=lambda r: (-(r["mb_per_day"] or 0), -r["samples_per_hour"], r["probe"]))

    samples = sum(r["samples_per_hour"] for r in rows)
    mb = sum(r["mb_per_day"] or 0 for r in rows)
    ceiling = ceilings(env)
    used = {
        "samples_pct": round(100 * samples / ceiling["samples_per_hour"], 1),
        "bandwidth_pct": round(100 * mb / ceiling["mb_per_day"], 1),
    }
    return {
        "targets": sum(per_probe.values()),
        "samples_per_hour": round(samples, 1),
        "mb_per_day": round(mb, 2),
        "ceiling": ceiling,
        "used": used,
        "over": used["samples_pct"] > 100 or used["bandwidth_pct"] > 100,
        # Probes whose class has no measured cost: counted in samples, not
        # in bytes, and named so the bandwidth figure is not read as whole.
        "unpriced": sorted(r["probe"] for r in rows if r["bytes_per_sample"] is None),
        "by_probe": rows,
        # The meter beside the estimate: what the uplink really carried.
        "measured": measured(traffic_text, time.time() if now is None else now,
                             ceiling["mb_per_day"]),
        # The same uplink, attributed per service (the netmeter container).
        "measured_by_service": by_service(services_text,
                                          time.time() if now is None else now),
    }


def _mb(value: float) -> str:
    return f"{value:.1f}" if value < 10 else f"{value:.0f}"


def covered(m: dict) -> str:
    """How much time a measured figure rests on, as a person says it."""
    if (m.get("hours") or 0) >= 1:
        return f"{m['hours']} h"
    return f"{m.get('minutes') or 0} min"


def render(body: dict) -> str:
    """The report as text, for ``smoking-pi budget``."""
    lines = [f"{'probe':<14} {'class':<8} {'targets':>7} {'step':>5} {'pings':>5} "
             f"{'samples/h':>10} {'MB/day':>9}"]
    for r in body["by_probe"]:
        mb = "?" if r["mb_per_day"] is None else f"{r['mb_per_day']:.1f}"
        lines.append(f"{r['probe']:<14} {r['class']:<8} {r['targets']:>7} {r['step']:>5} "
                     f"{r['pings']:>5} {r['samples_per_hour']:>10.0f} {mb:>9}")
    c, u = body["ceiling"], body["used"]
    lines += [
        "",
        f"{body['targets']} targets: {body['samples_per_hour']:.0f} samples/h of "
        f"{c['samples_per_hour']:.0f} ({u['samples_pct']}%), "
        f"~{body['mb_per_day']:.0f} MB/day of {c['mb_per_day']:.0f} ({u['bandwidth_pct']}%).",
    ]
    m = body.get("measured")
    if m:
        lines.append(
            f"Measured on {m.get('interface') or 'the uplink'}: ~{m['mb_per_day']:.0f} MB/day "
            f"({_mb(m['rx_mb'])} MB in, {_mb(m['tx_mb'])} MB out over the last {covered(m)}), "
            "everything the Pi sent and received there, not only the measurements."
            + (" Under an hour of data: not yet a daily figure." if m.get("provisional") else "")
            + (" Stale: the meter has stopped." if m.get("stale") else ""))
    b = body.get("measured_by_service")
    if b and b.get("services"):
        lines.append(f"By service, over the last {covered(b)}"
                     + (" (not yet a daily figure)" if b.get("provisional") else "")
                     + (" (stale: the meter has stopped)" if b.get("stale") else "") + ":")
        for r in b["services"]:
            lines.append(f"  {r['service']:<18} ~{r['mb_per_day']:>8.1f} MB/day  "
                         f"({_mb(r['rx_mb'])} MB in, {_mb(r['tx_mb'])} MB out)")
    if body["unpriced"]:
        lines.append("No byte estimate for: " + ", ".join(body["unpriced"])
                     + " (counted in samples only).")
    if body["over"]:
        lines.append("Over budget: fewer targets, fewer pings or a longer step brings it "
                     "back; the ceilings are MEASUREMENT_BUDGET_MB_PER_DAY and "
                     "MEASUREMENT_BUDGET_SAMPLES_PER_HOUR.")
    lines.append("The estimate is approximate: SmokePing measurements only, from the generated config.")
    return "\n".join(lines)


def main(argv=None) -> int:
    """``python budget.py [--json]``: ask the running API (GET /budget).

    Runs inside the config-manager container (smoking-pi budget), so the
    API token comes from the container's own environment.
    """
    import urllib.error
    import urllib.request

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the JSON report")
    args = parser.parse_args(argv)
    req = urllib.request.Request("http://127.0.0.1:5000/budget")
    token = os.environ.get("CONFIG_API_TOKEN", "")
    if token:
        req.add_header("X-API-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # It answered, and said no (a wrong token, a failure): say what.
        try:
            reason = json.loads(exc.read() or b"{}").get("error", exc.reason)
        except ValueError:
            reason = exc.reason
        print(f"refused: {reason}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"the config-manager API did not answer: {type(exc).__name__}", file=sys.stderr)
        return 1
    if not body.get("available"):
        print(f"No budget yet: {body.get('reason')}", file=sys.stderr)
        return 1
    print(json.dumps(body, indent=2) if args.json else render(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
