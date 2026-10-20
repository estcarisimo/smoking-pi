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


def test_a_period_carries_its_class_coverage_and_deaf_share():
    (pt,) = store.points(store.DEGRADATION, "Google", "topsites",
                         [{"start": W0 + 900, "end": W0 + 4500, "mean_loss_pct": 96.0,
                           "class": "monitor_deaf", "deaf_share": 1.0, "coverage": 0.75}], W0)
    line = pt.to_line_protocol()
    assert 'class="monitor_deaf"' in line
    assert "deaf_share=1," in line and "coverage=0.75" in line


def test_a_pass_becomes_one_point_with_its_cost_and_counts():
    report = {"seconds": 63.8, "errors": 1,
              "targets": {"a": {"congestion": {"periods": 1}, "degradation": {"periods": 2}},
                          "b": {"error": True}}}
    line = store.pass_point(report, W0).to_line_protocol()
    assert line.startswith("inference_pass ")
    assert "seconds=63.8" in line and "targets=2i" in line and "errors=1i" in line
    assert "congestion_periods=1i" in line and "degradation_periods=2i" in line
    assert line.endswith(str(W0))


def test_shared_points_carry_the_qualification_and_skip_a_cut_period():
    periods = [
        {"start": 100, "end": 200, "truncated": True},
        {"start": 1000, "end": 4600, "mean_loss_pct": 4.2, "coverage": 1.0, "voters": 40,
         "operators": 15, "lossy_operator_share": 0.6, "kinds": "cdn,content_network",
         "dominant": "NYT", "dominant_share": 0.1, "scope": "shared"},
    ]
    [pt] = store.shared_points(periods, window_start=500)
    line = pt.to_line_protocol()
    assert line.startswith("shared_degradation ")
    for part in ('scope="shared"', "voters=40i", 'kinds="cdn,content_network"', "end=4600i"):
        assert part in line


def test_the_pass_point_counts_shared_periods():
    line = store.pass_point({"targets": {}, "shared": {"periods": 3}}, 1000).to_line_protocol()
    assert "shared_periods=3i" in line
    assert "shared_periods=0i" in store.pass_point({"targets": {}}, 1000).to_line_protocol()


def test_shared_congestion_points_carry_jitterbug_s_numbers():
    periods = [{"start": 1000, "end": 8200, "confidence": 0.8, "jump_ms": 1.2, "ks_p": 0.01,
                "coverage": 0.9, "mean_loss_pct": 3.0}]
    [pt] = store.shared_points(periods, 500, store.SHARED_CONGESTION)
    line = pt.to_line_protocol()
    assert line.startswith("shared_congestion ")
    for part in ("confidence=0.8", "jump_ms=1.2", "ks_p=0.01", "coverage=0.9", "end=8200i"):
        assert part in line
    assert "mean_loss_pct" not in line and "target=" not in line


def test_shared_degradation_points_carry_the_attribution():
    p = {"start": 1000, "end": 4600, "by_operator": "Fastly 9.0 %", "by_kind": "cdn 4.5 %",
         "diverged": "b1 0.0 %"}
    line = store.shared_points([p], 500)[0].to_line_protocol()
    for part in ('by_operator="Fastly 9.0 %"', 'by_kind="cdn 4.5 %"', 'diverged="b1 0.0 %"'):
        assert part in line


def test_replacing_shared_congestion_deletes_only_its_own_measurement():
    client = FakeClient()
    store.replace_shared([], 500, 9000, client=client, measurement=store.SHARED_CONGESTION)
    assert '_measurement="shared_congestion"' in client.deleted[0][2]


def test_the_pass_point_counts_shared_congestion_periods():
    line = store.pass_point({"targets": {}, "shared_congestion": {"periods": 2}},
                            1000).to_line_protocol()
    assert "shared_congestion_periods=2i" in line
