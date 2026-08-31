"""Answer the question an alert actually raises: is it me, or the internet?

Every notification poses it. Answering it is what turns "amazon: mean loss
22.4% over 15m" into something a person can act on -- or correctly ignore.

The verdict is DETERMINISTIC and costs no extra queries. Everything it needs
is already fetched by :func:`evaluator.evaluate_with_context` and was
previously discarded:

- ``mean_rows``  -- mean clamped loss per target over 15m, for EVERY target.
  This is the breadth signal: how much of the internet looks broken.
- ``micro_rows`` -- count of cpe_latency windows above MICROCUT_LOSS_PCT.
  This is the local-link signal, and it is floor-safe by construction: the
  CPE rate-limits ICMP, giving a constant p50 10% / p99 30% loss floor that
  a 50% threshold cannot see. Never read raw CPE loss here.
- ``stale_rows`` -- exporter liveness, via the incident list.

The ordering below is a precedence, not a scoring function: the first scope
that matches wins, and `monitoring` outranks everything unconditionally.
Announcing "the internet is down" on the strength of an ABSENCE of data is
the worst thing this module could do.
"""

from __future__ import annotations

import logging
import os

from evaluator import _is_ipv6_target

log = logging.getLogger("alerter.verdict")

# Share of measurable targets that must be impaired before a problem counts
# as broad rather than site-specific.
DEFAULT_BROAD_PCT = 60.0  # VERDICT_BROAD_PCT
# Below this many measurable targets, breadth means nothing and we say so.
DEFAULT_MIN_TARGETS = 3  # VERDICT_MIN_TARGETS
# Mean loss percent above which a target counts as impaired.
DEFAULT_IMPAIRED_LOSS_PCT = 10.0  # VERDICT_IMPAIRED_LOSS_PCT
# A target stuck at 100% loss for longer than this is treated as chronic and
# excluded from breadth entirely. See _measurable().
DEFAULT_STALE_DOWN_HOURS = 6.0  # VERDICT_STALE_DOWN_HOURS

CHRONIC_LOSS_RATIO = 0.999


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _mean_by_target(mean_rows: list[dict]) -> dict[str, tuple[float, str | None]]:
    """target -> (mean loss ratio, category). Averages duplicate rows."""
    sums: dict[str, list[float]] = {}
    categories: dict[str, str | None] = {}
    for row in mean_rows:
        target = row.get("target")
        value = row.get("_value")
        if target is None or value is None:
            continue
        sums.setdefault(target, []).append(float(value))
        categories.setdefault(target, row.get("category"))
    return {t: (sum(v) / len(v), categories.get(t)) for t, v in sums.items()}


def _chronic(target: str, ratio: float, records: dict, now: float,
             max_age_s: float) -> bool:
    """A host that never answers ICMP at all, as opposed to one that broke.

    ``www.amazon.com`` answers; bare ``amazon.com`` does not, and a target
    pointed at one of those charts a permanent flat 100%. Counting those in
    breadth turns "one site is slow" into "the ISP is down" -- the single
    most likely way for this verdict to be confidently wrong.

    Detected without new state: state.py already persists ``first_seen`` per
    incident, so a target_down older than VERDICT_STALE_DOWN_HOURS that is
    still at 100% has been that way since before anything happened today.
    """
    if ratio < CHRONIC_LOSS_RATIO:
        return False
    record = records.get(f"target_down:{target}")
    if not record:
        return False
    first_seen = record.get("first_seen")
    if not first_seen:
        return False
    return (now - float(first_seen)) > max_age_s


def _cpe_cutting(micro_rows: list[dict], burst_n: int) -> list[str]:
    """CPE target/protocol pairs showing a burst of real microcuts."""
    cutting = []
    for row in micro_rows:
        value = row.get("_value")
        if value is None:
            continue
        if int(value) >= burst_n:
            target = row.get("target") or "?"
            cutting.append(f"{target}/{row.get('protocol') or '?'}")
    return sorted(cutting)


def classify(
    incidents: list[dict],
    mean_rows: list[dict],
    micro_rows: list[dict],
    records: dict | None = None,
    now: float | None = None,
) -> dict:
    """Return ``{scope, line, affected, total, cpe_cutting, evidence}``.

    Pure: rows in, verdict out, no network and no clock unless one is given.
    """
    import time

    if now is None:
        now = time.time()
    records = records or {}

    broad_pct = _env_float("VERDICT_BROAD_PCT", DEFAULT_BROAD_PCT)
    min_targets = _env_int("VERDICT_MIN_TARGETS", DEFAULT_MIN_TARGETS)
    impaired_pct = _env_float(
        "VERDICT_IMPAIRED_LOSS_PCT", DEFAULT_IMPAIRED_LOSS_PCT
    )
    stale_hours = _env_float(
        "VERDICT_STALE_DOWN_HOURS", DEFAULT_STALE_DOWN_HOURS
    )
    burst_n = _env_int("MICROCUT_BURST_N", 2)

    means = _mean_by_target(mean_rows)
    excluded = [
        t
        for t, (ratio, _) in means.items()
        if _chronic(t, ratio, records, now, stale_hours * 3600.0)
    ]
    measurable = {t: v for t, v in means.items() if t not in excluded}

    impaired = sorted(
        t for t, (ratio, _) in measurable.items() if ratio * 100.0 > impaired_pct
    )
    total = len(measurable)
    affected = len(impaired)
    cutting = _cpe_cutting(micro_rows, burst_n)
    share = (100.0 * affected / total) if total else 0.0
    broad = total >= min_targets and share >= broad_pct

    evidence = {
        "impaired": impaired,
        "measurable": total,
        "excluded_chronic": sorted(excluded),
        "share_pct": round(share, 1),
        "cpe_cutting": cutting,
    }
    # Logged every iteration: a wrong verdict must be diagnosable from
    # `docker logs` alone, without reproducing the moment it was made.
    log.info(
        "verdict inputs: %d/%d impaired (%.1f%%), cpe_cutting=%s, "
        "excluded_chronic=%s",
        affected, total, share, cutting or "none", excluded or "none",
    )

    def _out(scope: str, line: str) -> dict:
        return {
            "scope": scope,
            "line": line,
            "affected": affected,
            "total": total,
            "cpe_cutting": cutting,
            "evidence": evidence,
        }

    # 1. Monitoring first, unconditionally. If the exporter stalled we are
    #    reasoning about missing data, not about the network.
    if any(i.get("rule") == "exporter_stale" for i in incidents):
        return _out(
            "monitoring",
            "The monitor, not the network — no measurements are arriving, "
            "so treat everything below as unknown.",
        )

    if total == 0:
        return _out("unclear", "No comparable measurements in the last 15m.")

    # 2. Local link: the first hop is dropping AND the damage is broad.
    #    Breadth matters -- CPE microcuts alone can coexist with a fine
    #    connection, since a rate-limited gateway is not a broken one.
    if cutting and broad:
        return _out(
            "local_link",
            f"Your line — {affected} of {total} destinations affected and "
            f"the first hop is cutting out.",
        )

    # 3. Broad damage with a clean first hop: upstream.
    if broad:
        return _out(
            "isp_upstream",
            f"Not you — {affected} of {total} destinations affected but "
            f"your local link is clean, so this is upstream.",
        )

    if affected == 0:
        return _out(
            "unclear",
            f"No target is above {impaired_pct:.0f}% mean loss over 15m.",
        )

    # 4/5. A uniform impaired set names its own cause.
    if all(_is_ipv6_target(t, measurable[t][1]) for t in impaired):
        healthy_v4 = any(
            not _is_ipv6_target(t, cat) and ratio * 100.0 <= impaired_pct
            for t, (ratio, cat) in measurable.items()
        )
        if healthy_v4:
            return _out(
                "ipv6",
                f"IPv6 only — all {affected} affected targets are IPv6 "
                f"while IPv4 is healthy.",
            )

    if impaired and all(
        (measurable[t][1] or "").lower().startswith("dns") for t in impaired
    ):
        return _out(
            "dns",
            f"DNS only — all {affected} affected targets are resolvers; "
            f"everything else is fine.",
        )

    # 6. One or two sites, peers fine: theirs, not yours.
    if affected <= 2:
        peers = _healthy_peers(impaired, measurable, impaired_pct)
        if peers:
            return _out(
                "remote_target",
                f"Just that site — {affected} of {total} affected; its peers "
                f"in {peers} are fine.",
            )

    return _out(
        "unclear",
        f"{affected} of {total} destinations affected — not a clear pattern.",
    )


def _healthy_peers(
    impaired: list[str],
    measurable: dict[str, tuple[float, str | None]],
    impaired_pct: float,
) -> str | None:
    """Name the impaired targets' category when its other members are fine."""
    categories = {measurable[t][1] for t in impaired if measurable[t][1]}
    if len(categories) != 1:
        return None
    category = categories.pop()
    siblings = [
        t
        for t, (ratio, cat) in measurable.items()
        if cat == category and t not in impaired
    ]
    if not siblings:
        return None
    if all(measurable[t][0] * 100.0 <= impaired_pct for t in siblings):
        return category
    return None
