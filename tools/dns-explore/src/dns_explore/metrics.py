"""Scores, concentration, diversity and churn over a table of queries.

The input is a DataFrame with one row per query and a column per
aggregation level (see :mod:`dns_explore.units`), plus ``ts`` and
``cached``. Every function takes the level as a column name, so the same
measures apply to hostnames, services, CDNs, ASes or organisations.

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
from datetime import date

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
