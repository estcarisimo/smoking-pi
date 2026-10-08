"""Results into InfluxDB: replace the window, never duplicate."""

import pytest

import store

W0 = 1_790_000_000


class FakeClient:
    def __init__(self):
        self.deleted, self.written = [], []

    def delete_api(self):
        outer = self

        class D:
            def delete(self, start, stop, predicate, bucket, org):
                outer.deleted.append((start, stop, predicate, bucket))
        return D()

    def write_api(self, write_options=None):
        outer = self

        class W:
            def write(self, bucket, record):
                outer.written.extend(record)
        return W()


def test_a_period_becomes_one_point_with_its_end():
    (pt,) = store.points(store.DEGRADATION, "Google", "topsites",
                         [{"start": W0 + 900, "end": W0 + 4500, "mean_loss_pct": 4.2}], W0)
    line = pt.to_line_protocol()
    assert line.startswith("loss_degradation,category=topsites,target=Google ")
    assert f"end={W0 + 4500}i" in line
    assert "duration_s=3600i" in line
    assert "mean_loss_pct=4.2" in line
    assert line.endswith(str(W0 + 900))


def test_a_period_cut_by_the_window_is_not_written():
    periods = [{"start": W0, "end": W0 + 3600, "mean_loss_pct": 5.0},
               {"start": W0 + 900, "end": W0 + 3600, "mean_loss_pct": 5.0, "truncated": True}]
    assert store.points(store.DEGRADATION, "x", "c", periods, W0) == []


def test_replace_deletes_the_window_then_writes():
    client = FakeClient()
    n = store.replace(store.CONGESTION, "NYT", "topsites",
                      [{"start": W0 + 900, "end": W0 + 9000, "confidence": 0.9}],
                      W0, W0 + 86400, client=client)
    assert n == 1
    (start, stop, predicate, bucket) = client.deleted[0]
    assert predicate == '_measurement="persistent_congestion" AND target="NYT"'
    assert start == "2026-09-21T14:13:20Z"
    assert bucket == "smokeping"
    assert len(client.written) == 1


def test_a_run_that_finds_nothing_still_clears_the_window():
    client = FakeClient()
    assert store.replace(store.CONGESTION, "NYT", "topsites", [], W0, W0 + 60,
                         client=client) == 0
    assert len(client.deleted) == 1
    assert client.written == []


def test_a_quote_never_reaches_the_delete_predicate():
    with pytest.raises(ValueError):
        store.replace(store.CONGESTION, 'x" OR target="y', "c", [], W0, W0 + 60,
                      client=FakeClient())
