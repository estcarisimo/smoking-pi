"""Which diagnosed incident an alert belongs to, with its evidence.

The verdict (verdict.py) answers "is it me or the internet?" from the last
few minutes' rows the rules already fetched. ``diagnose_loss``
(common/diagnosis.py) answers the same question with more: the first hop's
own loss against its usual level, whether this host's radio was deaf, the
app-layer siblings, and a confidence. Only an assistant asked for it, so an
alert could say "your line" about minutes the diagnosis would call a deaf
radio.

Now, when an alert fires, the last few hours are diagnosed once
(common.diagnosis_query, the same code path as the MCP tool) and the alert
carries the incident that covers it. Only when one fires: the queries are
the tool's, about ten, and a quiet minute should cost none of them.

Never a reason to lose an alert: any failure here leaves the alert as it
was, with the verdict alone.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import time

import flux
from common import diagnosis, diagnosis_query

log = logging.getLogger("alerter.diagnosis")

# The rules about loss, which the diagnosis classifies. exporter_stale is
# about the monitor and has no loss to explain.
LOSS_RULES = frozenset({"target_down", "high_loss", "microcut_burst", "outage",
                        "uplink_down", "ipv6_down"})
DEFAULT_ALERT_DIAGNOSIS_HOURS = 3  # 0 turns it off
# An incident still counts as the one an alert is about if it ended this
# many probe steps ago: the alert's window looks back further than its last
# lossy point.
RECENT_STEPS = 4
# Wall-clock budget for the diagnosis. It runs before the alerts are sent,
# and each InfluxDB query may take a minute to time out on a struggling Pi
# -- exactly when an outage alert matters most. Past this, the alerts go
# out with the verdict alone.
BUDGET_S = 20.0
# Rules about everything (no target of their own) and the classes that can
# explain them. A destination incident is about some other target, and
# "unclear" adds nothing to the verdict.
UNTARGETED_RULES = frozenset({"outage", "uplink_down"})
BROAD_CLASSES = frozenset({"local_wifi", "local_link", "upstream"})
# microcut_burst is about the first hop, which no incident names as a
# target: it is explained by a cut on the line or this host's deaf radio.
MICROCUT_CLASSES = frozenset({"local_wifi", "local_link"})

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1,
                                              thread_name_prefix="diagnosis")
_pending: concurrent.futures.Future | None = None


def hours() -> int:
    try:
        value = int(os.environ.get("ALERT_DIAGNOSIS_HOURS", "") or DEFAULT_ALERT_DIAGNOSIS_HOURS)
    except ValueError:
        return DEFAULT_ALERT_DIAGNOSIS_HOURS
    return max(0, min(value, diagnosis_query.MAX_HOURS))


def run(cadences: dict | None, budget_s: float | None = None) -> dict | None:
    """The diagnosis of the last ``hours()``, or None: off, failed, or not
    done within ``budget_s`` (BUDGET_S). A diagnosis still running from an
    earlier iteration is not started again; its late answer is dropped."""
    global _pending
    window = hours()
    if window == 0:
        return None
    if _pending is not None and not _pending.done():
        log.warning("the previous diagnosis is still running; sending the verdict alone")
        return None
    _pending = _pool.submit(diagnosis_query.run, flux.query_influx, window, cadences)
    try:
        return _pending.result(timeout=BUDGET_S if budget_s is None else budget_s)
    except concurrent.futures.TimeoutError:
        log.warning("diagnosis took longer than %.0f s; sending the verdict alone",
                    BUDGET_S if budget_s is None else budget_s)
        return None
    except Exception:  # noqa: BLE001 -- a diagnosis must never cost an alert
        log.warning("diagnosis for the alert failed; sending the verdict alone",
                    exc_info=True)
        return None


def _site(target: str | None) -> str:
    return diagnosis.site_key(target or "")


def for_event(event: dict, result: dict | None, step_s: int,
              now: float | None = None) -> dict | None:
    """The incident that explains ``event``, or None. Attaching the wrong
    one is worse than attaching none: the message would state a cause with
    confidence about something else.

    - An alert about a target gets only a recent incident naming that
      target's site (google_h2 finds google), never another target's.
    - ``microcut_burst`` (the first hop, which no incident names) gets a
      recent cut on the line or a deaf radio.
    - ``ipv6_down`` (IPv6 as a whole) gets only an IPv6-only incident.
    - ``outage`` and ``uplink_down`` (about everything) get the newest
      recent incident of a broad class: this host's Wi-Fi, the line, or
      upstream."""
    rule = event.get("rule")
    if not result or rule not in LOSS_RULES:
        return None
    now = time.time() if now is None else now
    recent = [i for i in result.get("incidents") or []
              if i.get("end_epoch", 0) >= now - RECENT_STEPS * step_s
              and i.get("class") != "probe_miss"]
    if rule == "microcut_burst":
        fits = [i for i in recent if i.get("class") in MICROCUT_CLASSES]
    elif rule == "ipv6_down":
        # About IPv6 as a whole (its target is the word "ipv6"): only an
        # incident that was IPv6 alone.
        fits = [i for i in recent if i.get("detail") == "ipv6"]
    elif rule in UNTARGETED_RULES or not event.get("target"):
        fits = [i for i in recent if i.get("class") in BROAD_CLASSES]
    else:
        site = _site(event.get("target"))
        fits = [i for i in recent
                if site in {_site(t) for t in i.get("targets") or []}]
    if not fits:
        return None
    return _brief(max(fits, key=lambda i: i["end_epoch"]))


def _brief(inc: dict) -> dict:
    """What a message has room for."""
    return {
        "class": inc.get("class"),
        "detail": inc.get("detail"),
        "confidence": inc.get("confidence"),
        "summary": inc.get("summary") or diagnosis.summary(inc),
        "evidence": list(inc.get("evidence") or [])[:3],
        "against": list(inc.get("against") or [])[:2],
        "minutes": inc.get("minutes"),
    }
