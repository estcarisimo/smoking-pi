"""Shared degradation: one series across destinations, qualified by tags.

The change-point step is faked (``find``): a period wherever the pooled
series is at least 2 %, so these tests check what goes into the series and
how a period is qualified, not BCP."""

import pandas as pd

import shared
import tags

T0 = 1_790_000_100 - (1_790_000_100 % 900)  # a bin start
BINS = 7 * 96
OPERATORS = {  # 12 voters on 6 operators of 4 kinds, plus the gateway
    "a1": ("Fastly", tags.CDN), "a2": ("Fastly", tags.CDN),
    "b1": ("Cloudflare", tags.CDN), "b2": ("Cloudflare", tags.CDN),
    "c1": ("Google", tags.CONTENT), "c2": ("Google", tags.CONTENT),
    "d1": ("Meta", tags.CONTENT), "d2": ("Meta", tags.CONTENT),
    "e1": ("Netflix", tags.CACHE), "e2": ("Netflix", tags.CACHE),
    "f1": ("AWS", tags.CLOUD), "f2": ("AWS", tags.CLOUD),
}


def of(target):
    return OPERATORS.get(target, (tags.UNKNOWN, tags.UNKNOWN))


def fake_find(df):
    """A period wherever the pooled loss is >= 2 %, contiguous bins merged."""
    hot = df[df["loss_pct"] >= 2.0]["epoch"].tolist()
    periods, start, prev = [], None, None
    for e in hot:
        if start is None:
            start = e
        elif e != prev + 900:
            periods.append({"start": start, "end": prev + 900, "mean_loss_pct": 0.0})
            start = e
        prev = e
    if start is not None:
        periods.append({"start": start, "end": prev + 900, "mean_loss_pct": 0.0})
    return {"periods": periods, "change_points": 2 * len(periods)}


def rows(loss_by_target, count=3, categories=None):
    """aggregateWindow-shaped rows: _time is the window's END."""
    categories = categories or {}
    loss, counts = [], []
    for t, values in loss_by_target.items():
        for i, v in enumerate(values):
            if v is None:
                continue
            when = pd.Timestamp(T0 + (i + 1) * 900, unit="s", tz="UTC")
            cat = categories.get(t, "custom")
            loss.append({"_time": when, "_value": v, "target": t, "category": cat})
            counts.append({"_time": when, "_value": count, "target": t, "category": cat})
    return loss, counts


def quiet(n=BINS):
    return [0.0] * n


def with_window(values, start, length, value):
    out = list(values)
    for i in range(start, start + length):
        out[i] = value
    return out


def base():
    data = {t: quiet() for t in OPERATORS}
    data["CPE_IPv4"] = quiet()
    return data


CATS = {"CPE_IPv4": "cpe"}


def run(data, deaf=frozenset()):
    loss, counts = rows(data, categories=CATS)
    return shared.detect(loss, counts, set(deaf), of, find=fake_find)


def test_window_labels_are_turned_into_bin_starts():
    loss, _ = rows({"a1": [0.0, 0.5]})
    df, cats = shared.frame(loss)
    assert list(df.index) == [T0, T0 + 900]
    assert cats == {"a1": "custom"}


def test_an_evening_on_every_destination_is_shared():
    data = base()
    for t in OPERATORS:
        data[t] = with_window(data[t], 300, 12, 0.05)
    out = run(data)
    assert out["voters"] == 12
    [p] = out["periods"]
    assert (p["start"], p["end"]) == (T0 + 300 * 900, T0 + 312 * 900)
    assert p["scope"] == "shared"
    assert p["lossy_operator_share"] == 1.0
    assert p["kinds"] == ",".join(sorted({tags.CDN, tags.CONTENT, tags.CACHE, tags.CLOUD}))
    assert p["coverage"] == 1.0


def test_one_lossy_destination_is_dominated_not_shared():
    data = base()
    data["a1"] = with_window(data["a1"], 300, 12, 0.6)  # pooled ~5 %
    [p] = run(data)["periods"]
    assert p["scope"] == "dominated"
    assert p["dominant"] == "a1"
    assert p["dominant_share"] == 1.0


def test_loss_on_one_kind_of_infrastructure_only_is_not_shared():
    data = base()
    for t in ("a1", "a2", "b1", "b2"):   # two CDNs: a third of the operators, one kind
        data[t] = with_window(data[t], 300, 12, 0.1)
    [p] = run(data)["periods"]
    assert p["lossy_operator_share"] == 2 / 6
    assert p["kinds"] == tags.CDN
    assert p["scope"] == "dominated"


def test_the_radio_s_deaf_bins_are_out_of_the_series():
    data = base()
    for t in data:
        data[t] = with_window(data[t], 300, 16, 1.0)
    deaf = {T0 + i * 900 for i in range(300, 316)}
    assert run(data, deaf)["periods"] == []


def test_a_cut_the_gateway_shares_is_the_first_hop_s():
    data = base()
    for t in data:  # every destination AND the gateway lose ~5 % for 30 min
        data[t] = with_window(data[t], 300, 2, 0.05)
    out = run(data)
    assert out["periods"] == []
    assert out["monitor_bins"] >= 2


def test_a_single_lost_gateway_ping_does_not_blank_an_evening():
    data = base()
    for t in OPERATORS:
        data[t] = with_window(data[t], 300, 12, 0.05)
    data["CPE_IPv4"] = with_window(data["CPE_IPv4"], 305, 1, 0.033)  # one bin only
    [p] = run(data)["periods"]
    assert p["scope"] == "shared"


def test_a_dead_destination_does_not_make_the_network_lossy():
    data = base()
    data["c1"] = with_window(data["c1"], 200, 40, 1.0)  # 10 h at 100 %
    assert run(data)["periods"] == []


def test_a_host_that_always_loses_does_not_vote():
    data = base()
    data["rate_limited"] = [0.33] * BINS
    out = run(data)
    assert out["voters"] == 12
    assert out["periods"] == []


def test_a_rotating_slot_is_not_a_member():
    data = base()
    data["Ocean_Reef_x"] = [None] * 600 + [0.5] * (BINS - 600)  # here for under a day
    out = run(data)
    assert out["voters"] == 12
    assert out["periods"] == []


def test_too_few_voters_say_nothing():
    data = {t: with_window(quiet(), 300, 12, 0.05) for t in list(OPERATORS)[:5]}
    out = run(data)
    assert out["voters"] == 5
    assert out["periods"] == []


def test_a_bin_with_too_few_reporting_voters_is_left_empty():
    data = base()
    for t in list(OPERATORS)[:5]:   # 7 of 12 report: 58 %, below the 60 % quorum
        data[t] = with_window(data[t], 300, 12, None)
    for t in list(OPERATORS)[5:]:
        data[t] = with_window(data[t], 300, 12, 0.05)
    assert run(data)["periods"] == []


def test_untagged_targets_are_not_a_second_kind():
    data = base()
    data["x1"] = quiet()
    for t in ("a1", "a2", "x1"):
        data[t] = with_window(data[t], 300, 12, 0.3)
    [p] = run(data)["periods"]
    assert tags.UNKNOWN not in p["kinds"].split(",")
    assert p["scope"] == "dominated"


def test_the_queries_are_storage_engine_shapes_on_aligned_bins():
    for fn in ("mean", "count"):
        flux = shared.bins_flux(1_790_000_123, 1_790_600_456, fn)
        # aggregateWindow straight after the filter: what InfluxDB pushes down
        assert ('r._field == "loss") '
                f'|> aggregateWindow(every: 15m, fn: {fn}, createEmpty: false)') in flux
        assert "map(" not in flux and "reduce(" not in flux and "pivot(" not in flux
        # both ends on a bin boundary: the newest window is never a clipped one
        assert "range(start: 1790000100, stop: 1790600400)" in flux
    assert "last()" in shared.hosts_flux()


def test_run_reads_bins_hosts_and_tags():
    data = base()
    for t in OPERATORS:
        data[t] = with_window(data[t], 300, 12, 0.05)
    loss, counts = rows(data, categories=CATS)

    def query(flux):
        if "fn: mean" in flux:
            return loss
        if "fn: count" in flux:
            return counts
        return [{"target": t, "_value": f"{t}.example"} for t in data]

    class FakeTags:
        def refresh(self, hosts):
            self.hosts = hosts

        def of(self, target, hosts):
            assert hosts[target] == f"{target}.example"
            return of(target)

    store = FakeTags()
    out = shared.run(T0, set(), query=query, tag_store=store, find=fake_find)
    assert store.hosts["a1"] == "a1.example"
    assert out["voters"] == 12
