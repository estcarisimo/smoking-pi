"""Closed bins kept in the state volume: a pass reads the whole window once,
then only its tail."""

import json

import pandas as pd

import bins

COLUMNS = ["bin", "v"]
DAY = 86400
T0 = 1_790_000_100 - (1_790_000_100 % 900)


def fetcher(calls):
    def fetch(since, stop):
        calls.append((since, stop))
        first = since
        return pd.DataFrame({"bin": list(range(first, stop - stop % 900, 900)),
                             "v": [stop] * len(range(first, stop - stop % 900, 900))})
    return fetch


def test_the_first_pass_reads_the_window_and_the_next_one_only_its_tail(tmp_path):
    calls = []
    start, now = T0, T0 + 7 * DAY
    first = bins.read("x", start, now, fetcher(calls), COLUMNS, tmp_path)
    assert calls == [(start, now)]
    assert len(first) == 7 * 96
    later = now + 3600
    out = bins.read("x", start + 3600, later, fetcher(calls), COLUMNS, tmp_path)
    assert calls[1] == (now - bins.TAIL_S, later)
    # bins before the tail come from the cache, the tail from the new read,
    # and the window moved on: nothing before its start
    assert out["bin"].min() == start + 3600 and out["bin"].is_unique
    assert set(out[out["bin"] < now - bins.TAIL_S]["v"]) == {now}
    assert set(out[out["bin"] >= now - bins.TAIL_S]["v"]) == {later}
    assert len(out) == 7 * 96


def test_once_a_day_the_whole_window_is_read_again(tmp_path):
    calls = []
    bins.read("x", T0, T0 + 7 * DAY, fetcher(calls), COLUMNS, tmp_path)
    bins.read("x", T0 + DAY, T0 + 8 * DAY, fetcher(calls), COLUMNS, tmp_path)
    assert calls[1] == (T0 + DAY, T0 + 8 * DAY)


def test_a_window_reaching_before_the_cache_reads_it_all(tmp_path):
    calls = []
    bins.read("x", T0, T0 + DAY, fetcher(calls), COLUMNS, tmp_path)
    bins.read("x", T0 - DAY, T0 + DAY + 3600, fetcher(calls), COLUMNS, tmp_path)
    assert calls[1] == (T0 - DAY, T0 + DAY + 3600)


def test_a_cache_that_stopped_long_ago_reads_it_all(tmp_path):
    calls = []
    bins.read("x", T0, T0 + DAY, fetcher(calls), COLUMNS, tmp_path)
    (tmp_path / "x.json").write_text(json.dumps(
        {"from": T0, "through": T0 + 3600, "full_at": T0 + 2 * DAY}))
    bins.read("x", T0 + 2 * DAY, T0 + 3 * DAY, fetcher(calls), COLUMNS, tmp_path)
    assert calls[1] == (T0 + 2 * DAY, T0 + 3 * DAY)


def test_an_unreadable_cache_is_a_miss_not_an_error(tmp_path):
    calls = []
    bins.read("x", T0, T0 + DAY, fetcher(calls), COLUMNS, tmp_path)
    (tmp_path / "x.csv").write_text("garbage\n\x00")
    out = bins.read("x", T0, T0 + DAY + 3600, fetcher(calls), COLUMNS, tmp_path)
    assert calls[1] == (T0, T0 + DAY + 3600) and len(out) == 100
    (tmp_path / "x.json").write_text("{")
    bins.read("x", T0, T0 + DAY + 3600, fetcher(calls), COLUMNS, tmp_path)
    assert calls[2][0] == T0


def test_a_volume_that_cannot_be_written_still_answers(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("")
    out = bins.read("x", T0, T0 + DAY, fetcher([]), COLUMNS, blocked / "sub")
    assert len(out) == 96


def test_a_failed_read_writes_no_cache(tmp_path):
    def boom(since, stop):
        raise RuntimeError("influx away")
    try:
        bins.read("x", T0, T0 + DAY, boom, COLUMNS, tmp_path)
    except RuntimeError:
        pass
    assert not (tmp_path / "x.json").exists()


def test_text_columns_come_back_as_saved(tmp_path):
    def fetch(since, stop):
        return pd.DataFrame({"bin": [since], "target": ["8888"], "category": [""],
                             "v": [0.5]})
    cols = ["bin", "target", "category", "v"]
    bins.read("t", T0, T0 + DAY, fetch, cols, tmp_path, text=("target", "category"))
    out = bins.read("t", T0, T0 + DAY + 900, lambda a, b: pd.DataFrame(columns=cols), cols,
                    tmp_path, text=("target", "category"))
    assert out["target"].tolist() == ["8888"] and out["category"].tolist() == [""]


def test_a_clock_that_stepped_back_reads_it_all(tmp_path):
    calls = []
    bins.read("x", T0, T0 + DAY, fetcher(calls), COLUMNS, tmp_path)
    bins.read("x", T0, T0 + DAY - 7200, fetcher(calls), COLUMNS, tmp_path)
    assert calls[1] == (T0, T0 + DAY - 7200)


def test_an_empty_tail_keeps_the_closed_bins(tmp_path):
    bins.read("x", T0, T0 + DAY, fetcher([]), COLUMNS, tmp_path)
    out = bins.read("x", T0, T0 + DAY + 900, lambda a, b: pd.DataFrame(columns=COLUMNS),
                    COLUMNS, tmp_path)
    assert len(out) == 96 - bins.TAIL_S // 900
