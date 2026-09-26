import math
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from conftest import T0
from numpy.testing import assert_allclose

from dns_explore import metrics


def frame(rows):
    """rows: (unit, minutes after T0, cached)."""
    return pd.DataFrame(
        {
            "service": [u for u, _, _ in rows],
            "ts": pd.to_datetime([T0 + timedelta(minutes=m) for _, m, _ in rows], utc=True),
            "cached": [c for _, _, c in rows],
        }
    )


def test_presence_counts_hours_not_bursts():
    # A chatty unit (50 queries in one hour) and a steady one (1 per hour, 5 hours).
    chatty = [("chatty.example", 1, False)] * 50
    rows = chatty + [("steady.example", 60 * h, False) for h in range(5)]
    df = frame(rows)
    assert metrics.scores(df, "service", "queries").index[0] == "chatty.example"
    pres = metrics.scores(df, "service", "presence")
    assert pres.index[0] == "steady.example"
    assert pres.to_dict() == {"steady.example": 5.0, "chatty.example": 1.0}


def test_uncached_ignores_cache_hits():
    df = frame([("a.example", 0, True), ("a.example", 1, True), ("b.example", 2, False)])
    assert metrics.scores(df, "service", "uncached").to_dict() == {"b.example": 1.0}


def test_scores_skip_units_that_are_none():
    df = frame([("a.example", 0, False)])
    df.loc[len(df)] = [None, df["ts"].iloc[0], False]
    assert metrics.scores(df, "service", "queries").to_dict() == {"a.example": 1.0}


def test_unknown_score():
    with pytest.raises(ValueError, match="unknown score"):
        metrics.scores(frame([("a", 0, False)]), "service", "bytes")


def test_diversity_uniform_and_concentrated():
    d = metrics.diversity(pd.Series([2.0] * 4), ks=(1, 2, 10))
    assert d.richness == 4
    assert_allclose([d.hhi, d.effective_hhi, d.effective_shannon], [0.25, 4.0, 4.0])
    assert_allclose([d.coverage[1], d.coverage[2], d.coverage[10]], [0.25, 0.5, 1.0])

    d = metrics.diversity(pd.Series([97.0, 1.0, 1.0, 1.0]), ks=(1,))
    # One dominant unit: the effective number is close to 1, the richness is 4.
    assert d.richness == 4
    assert d.effective_hhi < 1.1
    assert 1 < d.effective_shannon < d.richness
    assert_allclose(d.coverage[1], 0.97)


def test_diversity_orders_hill_numbers():
    s = pd.Series(np.arange(1, 21, dtype=float))
    d = metrics.diversity(s)
    # Hill numbers never increase with the order: 0 >= 1 >= 2.
    assert d.richness >= d.effective_shannon >= d.effective_hhi


def test_diversity_of_nothing():
    d = metrics.diversity(pd.Series(dtype=float), ks=(5,))
    assert d.richness == 0 and math.isnan(d.coverage[5])


def test_retained_counts_what_hides_behind_the_top():
    df = frame([("a.example", 0, False), ("a.example", 1, False), ("b.example", 2, False)])
    df["asn"] = ["AS1", "AS2", "AS1"]
    assert metrics.retained(df, "service", "asn", pd.Index(["a.example"])) == 2
    assert metrics.retained(df, "service", "asn", pd.Index(["b.example"])) == 1


def test_churn_day_over_day():
    day = 24 * 60
    rows = (
        [("a.example", 0, False), ("b.example", 1, False)]
        + [("a.example", day, False), ("c.example", day + 1, False)]
        + [("a.example", 2 * day, False), ("c.example", 2 * day + 1, False)]
    )
    c = metrics.churn(frame(rows), "service", "queries", k=2)
    assert list(c["jaccard"].round(3)) == [0.333, 1.0]
    assert list(c["entered"]) == [1, 0] and list(c["left"]) == [1, 0]


def test_churn_needs_two_days():
    assert metrics.churn(frame([("a.example", 0, False)]), "service", "queries", 5).empty
