"""The aggregate stream and Jitterbug on it (aggregate.py).

Jitterbug itself is faked (``find``): these tests check what the stream
holds and what reaches the analyzer, not the analyzer."""

import inspect

import numpy as np
import pandas as pd

import aggregate
import congestion

DAY = 86400
T0 = 1_790_006_400 - (1_790_006_400 % DAY)  # a midnight, so bins and cycles align
VOTERS = [f"v{i}" for i in range(10)]
BASE_MS = {t: 2.0 + 20 * i for i, t in enumerate(VOTERS)}  # 2 ms to 182 ms away
BASE_MS["outsider"] = 50.0


def ts(epoch):
    return pd.Timestamp(epoch, unit="s", tz="UTC")


class FakeInflux:
    """Answers the floor and the pings queries from a function of
    (target, cycle epoch) -> list of ping RTTs in ms (empty: nothing answered)."""

    def __init__(self, rtts, targets=VOTERS):
        self.rtts, self.targets, self.queries = rtts, targets, []

    @staticmethod
    def _range(flux):
        part = flux.split("range(start: ")[1]
        start, stop = part.split(", stop: ")
        return int(start), int(stop.split(")")[0])

    def __call__(self, flux):
        self.queries.append(flux)
        start, stop = self._range(flux)
        out = []
        if 'r._field == "ping1"' in flux:
            for b in range(start, stop, 900):
                for t in self.targets:
                    lows = [min(self.rtts(t, c)) for c in range(b, b + 900, 300) if self.rtts(t, c)]
                    if lows:
                        out.append({"_time": ts(b + 900), "_value": min(lows) / 1000, "target": t})
        else:
            for c in range(start, stop, 300):
                for t in self.targets:
                    out += [{"_time": ts(c), "_value": v / 1000, "target": t}
                            for v in self.rtts(t, c)]
        return out


def steady(t, c):
    """Ten pings at the target's base, 0..0.9 ms above it."""
    return [BASE_MS[t] + k / 10 for k in range(10)]


def test_jitterbug_is_the_per_target_one_unchanged():
    assert inspect.signature(aggregate.run).parameters["find"].default is congestion.detect


def test_the_floor_is_trailing_and_leaves_the_bin_itself_out():
    idx_start = T0
    rows = []
    for i in range(3 * 96):
        v = 0.001 if 200 <= i < 220 else 0.010  # twenty bins far below the others
        rows.append({"_time": ts(idx_start + (i + 1) * 900), "_value": v, "target": "a"})
    f = aggregate.floors(rows, idx_start, idx_start + 3 * DAY)
    assert f["a"].iloc[: aggregate.FLOOR_MIN_BINS].isna().all()  # a day of history first
    bin200 = idx_start + 200 * 900
    assert f.loc[bin200, "a"] == 10.0  # its own bin is not in its floor
    assert f.loc[bin200 + 20 * 900, "a"] < 10.0  # later bins' floors see them


def test_a_cycle_is_the_deciles_of_pings_over_each_voters_floor():
    fake = FakeInflux(steady)
    floor = aggregate.floors(fake(aggregate.floor_flux(T0, T0 + 3 * DAY)), T0, T0 + 3 * DAY)
    c = T0 + 2 * DAY
    out = aggregate.cycles(fake(aggregate.pings_flux(c, c + 300)), floor, VOTERS, set())
    assert len(out) == 1 and out["n"].iloc[0] == 10
    # 2 ms and 182 ms away, one scale: the deciles span the 0..0.9 ms jitter.
    deciles = out[[f"d{i}" for i in range(1, 11)]].iloc[0].to_numpy()
    assert np.all(np.diff(deciles) >= 0)
    assert -0.01 <= deciles[0] < 0.1 and 0.8 < deciles[-1] <= 0.91


def test_a_cycle_needs_a_quorum_of_voters_and_ignores_the_others():
    def sparse(t, c):
        return steady(t, c) if t in VOTERS[:7] or t == "outsider" else []

    fake = FakeInflux(sparse, VOTERS + ["outsider"])
    floor = aggregate.floors(fake(aggregate.floor_flux(T0, T0 + 3 * DAY)), T0, T0 + 3 * DAY)
    c = T0 + 2 * DAY
    out = aggregate.cycles(fake(aggregate.pings_flux(c, c + 300)), floor, VOTERS, set())
    assert out.empty  # 7 voters answered; the outsider does not count


def test_the_monitors_bins_are_not_part_of_the_stream():
    fake = FakeInflux(steady)
    floor = aggregate.floors(fake(aggregate.floor_flux(T0, T0 + 3 * DAY)), T0, T0 + 3 * DAY)
    c = T0 + 2 * DAY
    out = aggregate.cycles(fake(aggregate.pings_flux(c, c + 1800)), floor, VOTERS, {c})
    assert sorted(out["epoch"]) == [c + 900, c + 1200, c + 1500]


def test_a_shared_rise_moves_every_decile_and_one_route_barely_does():
    def evening(t, c):
        bump = 5.0 if c >= T0 + 2 * DAY + 3600 else 0.0
        return [v + bump for v in steady(t, c)]

    def one_route(t, c):
        bump = 30.0 if t == "v0" and c >= T0 + 2 * DAY + 3600 else 0.0
        return [v + bump for v in steady(t, c)]

    c = T0 + 2 * DAY
    for rtts, low, high in ((evening, 4.9, 5.0), (one_route, -0.1, 0.1)):
        fake = FakeInflux(rtts)
        floor = aggregate.floors(fake(aggregate.floor_flux(T0, T0 + 3 * DAY)), T0, T0 + 3 * DAY)
        out = aggregate.cycles(fake(aggregate.pings_flux(c + 3600, c + 3900)), floor, VOTERS, set())
        assert low <= out["d5"].iloc[0] - 0.45 <= high  # the median, minus its jitter


def test_the_stream_grows_by_the_new_cycles_only():
    fake = FakeInflux(steady)
    now = T0 + 9 * DAY + 600
    d_start = T0 + 2 * DAY
    stream, added = aggregate.extend(pd.DataFrame(columns=aggregate.COLUMNS), VOTERS,
                                     set(), d_start, now, fake)
    stop = now // 900 * 900 - 900  # the last two bins wait for the next pass
    assert stream["epoch"].min() == d_start and stream["epoch"].max() == stop - 300
    assert added == len(stream) == (stop - d_start) // 300
    raw = [q for q in fake.queries if "=~ /^ping" in q]
    assert len(raw) == 7  # a day at a time on the first pass
    fake.queries.clear()
    later, added = aggregate.extend(stream, VOTERS, set(), d_start, now + 3600, fake)
    assert added == 12 and len(later) == len(stream) + 12
    raw = [q for q in fake.queries if "=~ /^ping" in q]
    assert len(raw) == 1 and FakeInflux._range(raw[0]) == (stop, stop + 3600)


def test_a_gap_longer_than_the_window_restarts_at_the_known_monitor_bins():
    fake = FakeInflux(steady)
    old = pd.DataFrame([[T0, 10] + [0.0] * 10], columns=aggregate.COLUMNS)
    d_start = T0 + 2 * DAY
    stream, _ = aggregate.extend(old, VOTERS, set(), d_start, d_start + 7200, fake)
    assert stream["epoch"].iloc[1] == d_start


def test_jitterbug_reads_ten_positive_samples_per_cycle():
    stream = pd.DataFrame([[T0, 9] + [float(k) for k in range(10)],
                           [T0 + 300, 9] + [-200.0] + [1.0] * 9],
                          columns=aggregate.COLUMNS)
    s = aggregate.samples(stream)
    assert list(s.columns) == ["epoch", "values"]
    assert (s["epoch"] == T0).sum() == 10 and (s["epoch"] == T0 + 300).sum() == 9
    assert s["values"].min() == aggregate.OFFSET_MS and (s["values"] > 0).all()


def test_run_keeps_the_stream_between_passes_and_feeds_jitterbug(tmp_path):
    fake = FakeInflux(steady)
    seen = []

    def find(df):
        seen.append(df)
        start = int(df["epoch"].iloc[0])
        return {"periods": [{"start": start + 3600, "end": start + 7200, "confidence": 0.9,
                             "jump_ms": 1.0, "ks_p": 0.01}],
                "change_points": 2, "inferences": 3}

    shared_result = {"voters": 10, "voter_targets": VOTERS, "monitor": set()}
    path = tmp_path / "aggregate.csv"
    c_start, d_start, now = T0 + DAY, T0 + 2 * DAY, T0 + 3 * DAY
    r = aggregate.run(shared_result, c_start, d_start, now, fake, find, path)
    assert path.exists() and r["added"] == r["cycles"] > 0
    assert r["periods"][0]["coverage"] == 1.0
    assert len(seen[0]) == 10 * r["cycles"]
    again = aggregate.run(shared_result, c_start, d_start, now + 3600, fake, find, path)
    assert again["added"] == 12 and again["cycles"] == r["cycles"] + 12
    # Older than the congestion window: dropped from the file.
    moved = aggregate.run(shared_result, d_start + 3600, d_start, now + 3600, fake, find, path)
    assert moved["cycles"] == again["cycles"] - 12


def test_a_stream_file_from_another_version_starts_over(tmp_path):
    path = tmp_path / "aggregate.csv"
    path.write_text("epoch,whatever\n1,2\n")
    assert aggregate.load(path).empty
    assert aggregate.load(tmp_path / "missing.csv").empty
