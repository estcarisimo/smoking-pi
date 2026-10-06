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


def hours() -> int:
    try:
        value = int(os.environ.get("ALERT_DIAGNOSIS_HOURS", "") or DEFAULT_ALERT_DIAGNOSIS_HOURS)
    except ValueError:
        return DEFAULT_ALERT_DIAGNOSIS_HOURS
    return max(0, min(value, diagnosis_query.MAX_HOURS))


def run(cadences: dict | None) -> dict | None:
    """The diagnosis of the last ``hours()``, or None (off, or it failed)."""
    window = hours()
    if window == 0:
        return None
    try:
        return diagnosis_query.run(flux.query_influx, window, cadences)
    except Exception:  # noqa: BLE001 -- a diagnosis must never cost an alert
        log.warning("diagnosis for the alert failed; sending the verdict alone",
                    exc_info=True)
        return None


def _site(target: str | None) -> str:
    return diagnosis.site_key(target or "")


def for_event(event: dict, result: dict | None, step_s: int,
              now: float | None = None) -> dict | None:
    """The incident that explains ``event``: the newest recent one naming
    its target (by site, so google_h2 finds google), else -- for a rule
    about everything, or a target the incidents do not name -- the newest
    recent incident of any kind except a lone probe miss."""
    if not result or event.get("rule") not in LOSS_RULES:
        return None
    now = time.time() if now is None else now
    recent = [i for i in result.get("incidents") or []
              if i.get("end_epoch", 0) >= now - RECENT_STEPS * step_s]
    if not recent:
        return None
    target = event.get("target")
    if target:
        mine = [i for i in recent
                if _site(target) in {_site(t) for t in i.get("targets") or []}]
        if mine:
            return _brief(max(mine, key=lambda i: i["end_epoch"]))
    broad = [i for i in recent if i.get("class") != "probe_miss"]
    if not broad:
        return None
    return _brief(max(broad, key=lambda i: i["end_epoch"]))


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
