"""Traffic accounting: what the Pi sent and received, today, this week,
this month and last month.

The measurement budget (budget.py) answers "how fast is it spending, now";
this answers "how much has it spent", in the periods a data plan is billed
in. It reads two ledgers the meters keep in their state files, per local
date (the containers' TZ):

* the uplink meter (smokeping-exporters/uplink_traffic.py,
  /config/uplink_traffic.json ``days``): everything on the interface the
  default route leaves through, the local network included;
* the netmeter (netmeter/meter.py, /var/lib/netmeter/state.json): the
  Internet-only part per day (``days.*.internet``: the same uplink without
  traffic to private, link-local and multicast addresses), and each
  service's bytes per month (``months``).

Every figure says how much of its period was measured (``coverage_pct``):
a meter that started mid-month, a reboot (one five-minute interval is
lost) or a stopped container all leave time uncounted, and that is
reported, not filled in.

Pure: api.py supplies the file contents; ``python traffic.py`` asks the
running API, for ``smoking-pi traffic``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

# How many of the newest days the body lists one by one.
DAYS_LISTED = 62
# The meters write nothing dated earlier (their LEDGER_EPOCH).
LEDGER_FIRST = date(2020, 1, 1)


def _day(d: str) -> Optional[date]:
    try:
        return date.fromisoformat(d)
    except ValueError:
        return None


def _ledger(text: str, key: str, today: date) -> Dict[str, dict]:
    """``days`` or ``months`` of a meter's state file; {} when the file is
    missing or damaged (the report then says the meter has nothing yet)."""
    try:
        state = json.loads(text) if text else None
    except ValueError:
        return {}
    rows = state.get(key) if isinstance(state, dict) else None
    if not isinstance(rows, dict):
        return {}
    # A date (days) or a month (months) between 2020 and tomorrow, nothing
    # else: a damaged key, or a row a wrong clock wrote (a Pi without a
    # real-time clock before it synced, a clock stepped forward), must not
    # become the "measured since" or this month's traffic.
    latest = today + timedelta(days=1)

    def valid(k):
        d = _day(k) if key == "days" else _day(f"{k}-01")
        return d is not None and LEDGER_FIRST <= d <= latest
    return {k: v for k, v in rows.items()
            if isinstance(k, str) and isinstance(v, dict) and valid(k)}


def _int(v) -> int:
    try:
        return max(int(v or 0), 0)
    except (TypeError, ValueError):
        return 0


def _float(v) -> float:
    try:
        return max(float(v or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def periods(today: date) -> List[Tuple[str, str, date, date]]:
    """(key, label, first day, last day) of each reported period. Weeks
    start on Monday."""
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    return [
        ("today", "today", today, today),
        ("yesterday", "yesterday", today - timedelta(days=1), today - timedelta(days=1)),
        ("this_week", "this week", today - timedelta(days=today.weekday()), today),
        ("this_month", "this month", month_start, today),
        ("last_month", "last month", last_month_end.replace(day=1), last_month_end),
        ("last_30_days", "last 30 days", today - timedelta(days=29), today),
    ]


def _elapsed(first: date, last: date, now: float) -> float:
    """Real seconds of the period that have happened. Local midnights
    through epoch seconds, not naive datetimes: a daylight-saving day is
    23 or 25 hours, and the meters count real seconds."""
    start = datetime.combine(first, datetime.min.time()).timestamp()
    end = min(datetime.combine(last + timedelta(days=1), datetime.min.time()).timestamp(), now)
    return max(end - start, 0.0)


def _sum(days: Dict[str, dict], first: date, last: date, internet: bool = False) -> dict:
    rx = tx = 0
    seconds = 0.0
    for key, row in days.items():
        d = _day(key)
        if d is None or not first <= d <= last:
            continue
        if internet:
            part = row.get("internet")
            if not isinstance(part, dict):
                continue
            rx += _int(part.get("rx"))
            tx += _int(part.get("tx"))
        else:
            rx += _int(row.get("rx"))
            tx += _int(row.get("tx"))
        seconds += _float(row.get("seconds"))
    return {"rx": rx, "tx": tx, "total": rx + tx, "seconds": round(seconds, 1)}


def _coverage(measured: float, elapsed: float) -> Optional[float]:
    if elapsed <= 0:
        return None
    return round(min(100.0, 100 * measured / elapsed), 1)


def _services(months: Dict[str, dict], month_key: str) -> List[dict]:
    month = months.get(month_key) or {}
    services = month.get("services") if isinstance(month.get("services"), dict) else {}
    rows = [{"service": str(name), "rx": _int(v.get("rx")), "tx": _int(v.get("tx"))}
            for name, v in services.items() if isinstance(v, dict)]
    for r in rows:
        r["total"] = r["rx"] + r["tx"]
    rows.sort(key=lambda r: (-r["total"], r["service"]))
    return rows


def report(uplink_text: str = "", netmeter_text: str = "",
           now: Optional[float] = None) -> dict:
    """The JSON body of GET /traffic. Bytes are integers; the clients
    format them."""
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now).date()
    uplink_days = _ledger(uplink_text, "days", today)
    net_days = _ledger(netmeter_text, "days", today)
    months = _ledger(netmeter_text, "months", today)
    # The interface's figure is the uplink meter's; without it (the
    # exporter has not run yet), the netmeter's own totals of the same
    # interface.
    days = uplink_days or net_days
    has_internet = any(isinstance(r.get("internet"), dict) for r in net_days.values())
    if not days:
        return {"available": False,
                "reason": "no traffic ledger yet: the meters write their first "
                          "interval five minutes after they start (Pro only)"}

    interfaces = sorted({i for r in uplink_days.values()
                         for i in (r.get("interfaces") or []) if isinstance(i, str)})
    out_periods = []
    for key, label, first, last in periods(today):
        elapsed = _elapsed(first, last, now)
        uplink = _sum(days, first, last)
        uplink["coverage_pct"] = _coverage(uplink["seconds"], elapsed)
        internet = None
        if has_internet:
            internet = _sum(net_days, first, last, internet=True)
            internet["coverage_pct"] = _coverage(internet["seconds"], elapsed)
        out_periods.append({"period": key, "label": label, "first": first.isoformat(),
                            "last": last.isoformat(), "uplink": uplink,
                            "internet": internet})

    listed = []
    for key in sorted(days, reverse=True)[:DAYS_LISTED]:
        row = days[key]
        net = (net_days.get(key) or {}).get("internet")
        listed.append({"date": key, "rx": _int(row.get("rx")), "tx": _int(row.get("tx")),
                       "seconds": _float(row.get("seconds")),
                       "internet": ({"rx": _int(net.get("rx")), "tx": _int(net.get("tx"))}
                                    if isinstance(net, dict) else None)})

    month_key = today.strftime("%Y-%m")
    last_month_key = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    return {
        "available": True,
        "timezone": os.environ.get("TZ") or time.tzname[0],
        "interfaces": interfaces,
        "since": min(days),
        "internet_available": has_internet,
        # The netmeter can have started later than the uplink meter (it
        # came in v2.18.0) or been off for a while: its own start.
        "internet_since": (min(k for k, r in net_days.items()
                               if isinstance(r.get("internet"), dict))
                           if has_internet else None),
        "periods": out_periods,
        "days": listed,
        "services": {
            "this_month": _services(months, month_key),
            "last_month": _services(months, last_month_key),
        },
    }


def human(n: int) -> str:
    """Decimal units, as ISPs bill: 1 GB = 10^9 bytes."""
    if n <= 0:
        return "0"
    if n >= 1e9:
        return f"{n / 1e9:.2f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB" if n >= 1e7 else f"{n / 1e6:.1f} MB"
    return f"{n / 1e3:.0f} kB"


def render(body: dict) -> str:
    """The report as text, for ``smoking-pi traffic``."""
    on = ", ".join(body.get("interfaces") or []) or "the uplink"
    since = f"measured since {body.get('since')}"
    if body.get("internet_since") and body["internet_since"] != body.get("since"):
        since += f", Internet only since {body['internet_since']}"
    lines = [f"Traffic on {on} (local time, {body.get('timezone')}), {since}.", ""]

    def cov(part):
        return "-" if part["coverage_pct"] is None else f"{part['coverage_pct']:.0f}%"

    head = f"{'':<13} {'received':>10} {'sent':>10} {'total':>10} {'covered':>8}"
    if body.get("internet_available"):
        head += f" {'Internet':>10} {'covered':>8}"
    lines.append(head)
    for p in body["periods"]:
        u = p["uplink"]
        line = (f"{p['label']:<13} {human(u['rx']):>10} {human(u['tx']):>10} "
                f"{human(u['total']):>10} {cov(u):>8}")
        if body.get("internet_available"):
            # Nothing measured is not zero bytes: the netmeter was not running.
            net = p["internet"]
            line += (f" {human(net['total']):>10} {cov(net):>8}" if net["seconds"]
                     else f" {'-':>10} {'-':>8}")
        lines.append(line)
    lines += ["",
              "received/sent/total: everything on the interface, the local network "
              "included."]
    if body.get("internet_available"):
        lines.append("Internet: the same without traffic to and from the local "
                     "network (what a data plan counts).")
    else:
        lines.append("No Internet-only figure: the netmeter is off or has not counted yet.")
    lines.append("covered: how much of the period each meter was running; nothing is "
                 "extrapolated.")
    services = body.get("services", {}).get("this_month") or []
    if services:
        lines += ["", "This month by service:"]
        for s in services:
            lines.append(f"  {s['service']:<18} {human(s['total']):>10}  "
                         f"({human(s['rx'])} in, {human(s['tx'])} out)")
    return "\n".join(lines)


def main(argv=None) -> int:
    """``python traffic.py [--json]``: ask the running API (GET /traffic).

    Runs inside the config-manager container (smoking-pi traffic), so the
    API token comes from the container's own environment.
    """
    import urllib.error
    import urllib.request

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the JSON report")
    args = parser.parse_args(argv)
    req = urllib.request.Request("http://127.0.0.1:5000/traffic")
    token = os.environ.get("CONFIG_API_TOKEN", "")
    if token:
        req.add_header("X-API-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
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
        print(f"No traffic figures yet: {body.get('reason')}", file=sys.stderr)
        return 1
    print(json.dumps(body, indent=2) if args.json else render(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
