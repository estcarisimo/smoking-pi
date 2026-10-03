"""Scores, concentration, diversity and churn over a table of queries.

The input is a DataFrame with one row per query and a column per
aggregation level (see :mod:`dns_explore.units`), plus ``ts`` and
``cached``. Every function takes the level as a column name, so the same
measures apply to hostnames, services, CDNs, ASes or organizations.

Scores
------
``queries``
    Every query counts once. Rewards chatty telemetry.
``uncached``
    Only queries AdGuard had to ask upstream.
``presence``
    Distinct clock hours the unit appeared in. Robust to bursts and to
    caching (a cache hides volume, not presence).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

SCORES = ("queries", "uncached", "presence")


def scores(df: pd.DataFrame, level: str, score: str) -> pd.Series:
    """Score per unit, highest first.

    Parameters
    ----------
    df : pandas.DataFrame
        Queries, with columns ``level``, ``ts`` and ``cached``.
    level : str
        The column to aggregate by.
    score : str
        One of :data:`SCORES`.

    Returns
    -------
    pandas.Series
        Indexed by unit, sorted descending (ties by name, so it is stable).
    """
    if df.empty:
        return pd.Series(dtype=float)
    if score == "queries":
        s = df.groupby(level).size()
    elif score == "uncached":
        s = df.loc[~df["cached"]].groupby(level).size()
    elif score == "presence":
        s = df.assign(hour=df["ts"].dt.floor("h")).groupby(level)["hour"].nunique()
    else:
        raise ValueError(f"unknown score {score!r}; choose from {', '.join(SCORES)}")
    s = s.astype(float)
    return s.sort_index().sort_values(ascending=False, kind="stable")


@dataclass(frozen=True)
class Diversity:
    """Concentration and diversity of one score distribution.

    Attributes
    ----------
    richness : int
        Distinct units (Hill number of order 0).
    hhi : float
        Herfindahl-Hirschman index, sum of squared shares, in (0, 1].
    effective_hhi : float
        ``1 / hhi``: the number of equally-sized units giving the same
        concentration (Hill number of order 2; weighs the head).
    shannon : float
        Shannon entropy of the shares, in nats.
    effective_shannon : float
        ``exp(shannon)`` (Hill number of order 1; weighs the tail more).
    coverage : dict[int, float]
        Share of the total score covered by the top-K, for each K.
    """

    richness: int
    hhi: float
    effective_hhi: float
    shannon: float
    effective_shannon: float
    coverage: dict[int, float]

    def as_row(self) -> dict[str, float]:
        row = {k: v for k, v in asdict(self).items() if k != "coverage"}
        row.update({f"cov@{k}": v for k, v in self.coverage.items()})
        return row


def diversity(s: pd.Series, ks: tuple[int, ...] = (5, 10, 20, 50)) -> Diversity:
    """Diversity measures of a score series (from :func:`scores`).

    Examples
    --------
    >>> d = diversity(pd.Series([1.0, 1.0, 1.0, 1.0]))
    >>> round(d.effective_hhi, 6), round(d.effective_shannon, 6)
    (4.0, 4.0)
    """
    total = float(s.sum())
    if total <= 0:
        return Diversity(0, float("nan"), 0.0, 0.0, 0.0, {k: float("nan") for k in ks})
    p = (s / total).to_numpy()
    hhi = float(np.sum(p**2))
    shannon = float(-np.sum(p * np.log(p)))
    ranked = np.sort(p)[::-1]
    coverage = {k: float(ranked[:k].sum()) for k in ks}
    return Diversity(len(p), hhi, 1.0 / hhi, shannon, float(np.exp(shannon)), coverage)


def retained(df: pd.DataFrame, by: str, of: str, top: pd.Index) -> int:
    """How many distinct ``of`` units sit behind the ``top`` units of ``by``.

    For example, behind the top-10 services, how many ASes: what choosing
    at the ``by`` level still covers at the ``of`` level, or what
    coalescing to ``of`` would fold together.
    """
    return int(df.loc[df[by].isin(top), of].nunique())


def daily_topk(df: pd.DataFrame, level: str, score: str, k: int) -> dict[date, set[str]]:
    """The top-K units of each calendar day.

    Uses the ``day`` column when present (the log's local calendar day, as
    :func:`dns_explore.cli.load` builds it), otherwise the UTC day of ``ts``.
    """
    days = df["day"] if "day" in df else df["ts"].dt.date
    out: dict[date, set[str]] = {}
    for day, part in df.groupby(days):
        out[day] = set(scores(part, level, score).index[:k])
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    """|a ∩ b| / |a ∪ b|; 1.0 for two empty sets.

    Examples
    --------
    >>> jaccard({"a", "b"}, {"b", "c"})
    0.3333333333333333
    """
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def churn(df: pd.DataFrame, level: str, score: str, k: int) -> pd.DataFrame:
    """Day-over-day churn of the top-K.

    Returns
    -------
    pandas.DataFrame
        One row per day after the first: ``jaccard`` with the previous day
        in the log, ``entered`` and ``left`` (how many units swapped), and
        ``gap_days``, 1 unless days are missing from the log in between.
    """
    days = daily_topk(df, level, score, k)
    rows = []
    ordered = sorted(days)
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        a, b = days[prev], days[cur]
        rows.append(
            {
                "day": cur,
                "jaccard": jaccard(a, b),
                "entered": len(b - a),
                "left": len(a - b),
                "gap_days": (cur - prev).days,
            }
        )
    return pd.DataFrame(rows, columns=["day", "jaccard", "entered", "left", "gap_days"])


def membership(df: pd.DataFrame, level: str, score: str, k: int) -> pd.DataFrame:
    """Which units were in each day's top-K.

    Returns
    -------
    pandas.DataFrame
        One row per unit that was ever in a daily top-K, one column per
        calendar day from the log's first to its last, in order. A day the
        log has no entries for is ``pd.NA`` for every unit: unknown, not out.
    """
    days = daily_topk(df, level, score, k)
    if not days:
        return pd.DataFrame(dtype="boolean")
    first, last = min(days), max(days)
    calendar = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    units = sorted(set().union(*days.values()))
    return pd.DataFrame(
        {day: [(u in days[day]) if day in days else pd.NA for u in units] for day in calendar},
        index=units,
        dtype="boolean",
    )


def ties_at_cut(df: pd.DataFrame, level: str, score: str, k: int) -> float | None:
    """Mean, over days, of how many units share the K-th unit's score.

    1 means a clean cut. Above 1 the top-K is partly decided by the
    tie-break (unit name), not by the score: presence per day saturates at
    24 hours, so a saturated score ranks nothing, and the tie-break then
    makes the same names win every day, which reads as stability. ``None``
    when no day has K units.
    """
    days = df["day"] if "day" in df else df["ts"].dt.date
    counts = []
    for _, part in df.groupby(days):
        s = scores(part, level, score)
        if len(s) >= k:
            counts.append(int((s == s.iloc[k - 1]).sum()))
    return float(np.mean(counts)) if counts else None


def runs(row: pd.Series) -> tuple[int, int]:
    """Re-entries of one unit, and its longest absence before coming back.

    A day missing from the log (``pd.NA``) lengthens an absence already
    seen (it is calendar time away, which is what an exit delay is
    measured in) but never starts one: a unit in on both sides of a hole
    did not leave.

    Examples
    --------
    >>> runs(pd.Series([True, False, False, True, False, True]))
    (2, 2)
    >>> runs(pd.Series([True, pd.NA, True], dtype="boolean"))
    (0, 0)
    >>> runs(pd.Series([True, False, pd.NA, True], dtype="boolean"))
    (1, 2)
    """
    seen, gap, away, reentries, longest = False, 0, False, 0, 0
    for inside in row:
        if pd.isna(inside):
            gap += seen
        elif inside:
            if away:
                reentries += 1
                longest = max(longest, gap)
            seen, gap, away = True, 0, False
        elif seen:
            gap, away = gap + 1, True
    return reentries, longest


def stability(df: pd.DataFrame, level: str, score: str, k: int) -> dict[str, float | None]:
    """How the units of the daily top-K come and go.

    Returns
    -------
    dict
        ``days``: days in the log; ``missing_days``: calendar days between
        its first and last that it has no entries for; ``ever``: units ever
        in a daily top-K; ``always``: in every logged day's; ``once``: in
        exactly one day's; ``reentries``: times a unit came back after
        dropping out; ``max_return_gap``: the longest absence, in calendar
        days, of a unit that came back (an exit rule must wait longer than
        this, or it retires units that return); ``ties_at_cut``: see
        :func:`ties_at_cut`.
    """
    m = membership(df, level, score, k)
    logged = int(m.notna().all(axis=0).sum()) if len(m) else 0
    per_unit = [runs(row) for _, row in m.iterrows()]
    in_days = m.fillna(False).sum(axis=1)
    return {
        "days": logged,
        "missing_days": m.shape[1] - logged,
        "ever": len(m),
        "always": int((in_days == logged).sum()),
        "once": int((in_days == 1).sum()),
        "reentries": sum(r for r, _ in per_unit),
        "max_return_gap": max((g for _, g in per_unit), default=0),
        "ties_at_cut": ties_at_cut(df, level, score, k),
    }


def hysteresis(m: pd.DataFrame, enter: int, leave: int) -> dict[str, int]:
    """Replay the daily top-K through an enter/leave rule.

    The selection starts as the first day's top-K. Afterwards a unit is
    added once it has been in the daily top-K ``enter`` days in a row, and
    removed once it has been out of it ``leave`` days in a row. ``enter=1,
    leave=1`` follows the raw top-K. A day missing from the log changes
    nothing and breaks every streak, as the design freezes the selection
    while the observer is not live.

    Parameters
    ----------
    m : pandas.DataFrame
        From :func:`membership`.

    Returns
    -------
    dict
        ``adds`` and ``drops`` over the log (the first day excluded), and
        ``size`` of the selection on the last day.

    Examples
    --------
    >>> m = pd.DataFrame({1: [True, False], 2: [False, True], 3: [True, True]},
    ...                  index=["a", "b"])
    >>> hysteresis(m, 1, 1), hysteresis(m, 2, 2)
    ({'adds': 2, 'drops': 1, 'size': 2}, {'adds': 1, 'drops': 0, 'size': 2})
    """
    if m.shape[1] == 0:
        return {"adds": 0, "drops": 0, "size": 0}
    cols = list(m.columns)
    first = m[cols[0]].fillna(False).astype(bool)
    selected = set(m.index[first])
    streak_in = {u: int(first[u]) for u in m.index}
    streak_out = {u: int(not first[u]) for u in m.index}
    adds = drops = 0
    for day in cols[1:]:
        for u in m.index:
            inside = m.at[u, day]
            if inside is pd.NA:
                streak_in[u] = streak_out[u] = 0
                continue
            if inside:
                streak_in[u], streak_out[u] = streak_in[u] + 1, 0
            else:
                streak_in[u], streak_out[u] = 0, streak_out[u] + 1
            if u not in selected and streak_in[u] >= enter:
                selected.add(u)
                adds += 1
            elif u in selected and streak_out[u] >= leave:
                selected.discard(u)
                drops += 1
    return {"adds": adds, "drops": drops, "size": len(selected)}


def whole_days(df: pd.DataFrame, slack_minutes: int = 60) -> tuple[pd.DataFrame, list[date]]:
    """Drop the first and last day of the log when it covers them only in part.

    The first day counts as whole if the log has it within ``slack_minutes``
    of its local midnight, the last if it reaches within ``slack_minutes``
    of the next. Uses the ``clock`` column (minutes after the log's local
    midnight) when present, otherwise the UTC time of ``ts``.

    Returns
    -------
    tuple
        The rows of whole days, and the days left out.
    """
    if df.empty:
        return df, []
    days = df["day"] if "day" in df else df["ts"].dt.date
    clock = df["clock"] if "clock" in df else df["ts"].dt.hour * 60 + df["ts"].dt.minute
    first, last = days.min(), days.max()
    partial = []
    if clock[days == first].min() > slack_minutes:
        partial.append(first)
    if last not in partial and clock[days == last].max() < 24 * 60 - slack_minutes:
        partial.append(last)
    return df.loc[~days.isin(partial)], partial
