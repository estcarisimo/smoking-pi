"""Results into InfluxDB: correct the window, keep what reached its edge,
never duplicate."""

import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import store

W0 = 1_790_000_000
NOW = W0 + 14 * store.DAY
HOUR, DAY = store.HOUR, store.DAY


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeClient:
    """InfluxDB with ``records`` (start, end) already written: what the
    store reads before it deletes anything."""

    def __init__(self, records=()):
        self.records = list(records)
        self.deleted, self.written, self.queries = [], [], []

    def query_api(self):
        outer = self

        class Q:
            def query(self, flux):
                outer.queries.append(flux)
                return [SimpleNamespace(records=[
                    SimpleNamespace(values={
                        "_time": datetime.fromtimestamp(s, tz=timezone.utc), "_value": e})
                    for s, e in outer.records])]
        return Q()

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


def test_replace_reads_then_deletes_after_the_settled_edge_then_writes():
    client = FakeClient()
    n = store.replace(store.CONGESTION, "NYT", "topsites",
                      [{"start": W0 + 4 * DAY, "end": W0 + 4 * DAY + 9000, "confidence": 0.9}],
                      W0, NOW, client=client)
    assert n == 1
    (start, stop, predicate, bucket) = client.deleted[0]
    assert predicate == '_measurement="persistent_congestion" AND target="NYT"'
    assert start == _iso(W0 + 3 * DAY + 1) and stop == _iso(NOW)
    assert bucket == "smokeping"
    assert len(client.written) == 1
    (flux,) = client.queries
    assert 'r._measurement == "persistent_congestion" and r.target == "NYT"' in flux
    assert 'r._field == "end"' in flux


@pytest.mark.parametrize("measurement, guard", [
    (store.CONGESTION, 3 * DAY), (store.DEGRADATION, store.GUARD[store.DEGRADATION]),
    (store.SHARED, store.GUARD[store.DEGRADATION]),
    (store.SHARED_SEGMENT, store.GUARD[store.DEGRADATION]),
    (store.SHARED_CONGESTION, 3 * DAY)])
def test_the_delete_starts_a_guard_after_the_window_start(measurement, guard):
    client = FakeClient()
    if measurement in (store.CONGESTION, store.DEGRADATION):
        store.replace(measurement, "NYT", "topsites", [], W0, NOW, client=client)
    else:
        store.replace_shared([], W0, NOW, client=client, measurement=measurement)
    (start, stop, predicate, _) = client.deleted[0]
    assert start == _iso(W0 + guard + 1) and stop == _iso(NOW)
    assert f'_measurement="{measurement}"' in predicate


def test_degradation_settles_sooner_than_congestion():
    # BCP loses a degradation within hours of the edge, Jitterbug a
    # congestion within days: one guard for both would freeze degradation
    # far longer than it needs or erase congestion.
    assert store.GUARD[store.DEGRADATION] < store.GUARD[store.CONGESTION]
    assert store.GUARD[store.SHARED_CONGESTION] == store.GUARD[store.CONGESTION]
    assert store.GUARD[store.SHARED] == store.GUARD[store.SHARED_SEGMENT] \
        == store.GUARD[store.DEGRADATION]


def test_a_period_starting_inside_the_guard_is_neither_deleted_nor_written():
    # An earlier pass wrote it at W0 + 1 day; today Jitterbug, with the
    # window's start a day before it, places it two bins later.
    record = (W0 + DAY, W0 + DAY + 4 * HOUR)
    client = FakeClient([record])
    n = store.replace(store.CONGESTION, "uba_ar", "topsites",
                      [{"start": W0 + DAY + 1800, "end": W0 + DAY + 4 * HOUR}], W0, NOW,
                      client=client)
    assert n == 0 and client.written == []
    assert client.deleted[0][0] == _iso(W0 + 3 * DAY + 1)  # after the record


def test_a_period_the_detector_no_longer_finds_near_the_edge_stays():
    client = FakeClient([(W0 + 2 * DAY, W0 + 3 * DAY)])
    assert store.replace(store.CONGESTION, "uba_ar", "topsites", [], W0, NOW,
                         client=client) == 0
    assert client.deleted[0][0] > _iso(W0 + 2 * DAY)


def test_a_truncated_period_keeps_its_earlier_record():
    client = FakeClient([(W0 - 3 * HOUR, W0 + 5 * HOUR)])
    n = store.replace(store.DEGRADATION, "UBA", "academic",
                      [{"start": W0, "end": W0 + 5 * HOUR, "truncated": True}], W0, NOW,
                      client=client)
    assert n == 0 and client.written == []
    assert client.deleted[0][0] == _iso(W0 + store.GUARD[store.DEGRADATION] + 1)


def test_a_period_after_the_guard_is_replaced_as_before():
    # Written at 5 days in; today the detector moves its start a bin later:
    # the old point is in the deleted range and the new one is written.
    start = W0 + 5 * DAY
    client = FakeClient([(start, start + 3 * HOUR)])
    n = store.replace(store.CONGESTION, "Facebook", "topsites",
                      [{"start": start + 900, "end": start + 3 * HOUR, "jump_ms": 6.0}],
                      W0, NOW, client=client)
    assert n == 1
    assert client.deleted[0][0] <= _iso(start)
    assert client.written[0].to_line_protocol().endswith(str(start + 900))


def test_a_period_after_the_guard_the_detector_no_longer_finds_is_deleted():
    client = FakeClient([(W0 + 6 * DAY, W0 + 6 * DAY + HOUR)])
    store.replace(store.CONGESTION, "Facebook", "topsites", [], W0, NOW, client=client)
    assert client.deleted[0][0] <= _iso(W0 + 6 * DAY)


def test_a_start_moved_later_across_the_edge_is_not_a_second_point():
    # The record starts just inside the guard; today the detector places
    # the same period two bins later, just after it: no second point.
    edge = W0 + 3 * DAY
    client = FakeClient([(edge - 900, edge + 6 * HOUR)])
    n = store.replace(store.CONGESTION, "uba_ar", "topsites",
                      [{"start": edge + 900, "end": edge + 6 * HOUR}], W0, NOW, client=client)
    assert n == 0 and client.written == []


def test_a_start_moved_back_across_the_edge_keeps_its_record():
    # The record starts just after the settled edge; today the detector
    # places the same period before it: the edge moves past the record
    # instead of deleting it.
    edge = W0 + 3 * DAY
    record = (edge + 900, edge + 6 * HOUR)
    client = FakeClient([record])
    n = store.replace(store.CONGESTION, "uba_ar", "topsites",
                      [{"start": edge - 900, "end": edge + 6 * HOUR}], W0, NOW, client=client)
    assert n == 0
    assert client.deleted[0][0] == _iso(record[0] + 1)


def test_moving_the_edge_keeps_every_record_it_brings_before_it():
    # Two records after the edge; today the detector places the first
    # before it and stretches the second back to the first's start.
    edge = W0 + 3 * DAY
    first, second = (edge + HOUR, edge + 3 * HOUR), (edge + 5 * HOUR, edge + 8 * HOUR)
    settled, write = store.settle(
        [{"start": edge - HOUR, "end": edge + 2 * HOUR},
         {"start": edge + HOUR, "end": edge + 8 * HOUR}],
        [first, second], W0, W0, 3 * DAY)
    assert settled == second[0] and write == []


def test_settle_never_writes_twice_for_one_period():
    # Whatever the detector reports, a written period never overlaps a
    # record that stays.
    edge = W0 + 3 * DAY
    records = [(edge - 2 * HOUR, edge + HOUR), (edge + 2 * HOUR, edge + 4 * HOUR),
               (edge + 6 * DAY, edge + 6 * DAY + HOUR)]
    for shift in range(-12, 13):
        p = [{"start": edge + shift * 900, "end": edge + 4 * HOUR},
             {"start": edge + 6 * DAY + shift * 900, "end": edge + 6 * DAY + 2 * HOUR}]
        settled, write = store.settle(p, records, W0, W0, 3 * DAY)
        kept = [r for r in records if r[0] <= settled]
        for w in write:
            assert w["start"] > edge
            assert not any(r[0] < w["end"] and w["start"] < r[1] for r in kept)


def test_a_new_period_after_the_edge_beside_a_kept_one_is_written():
    # Adjacent is not overlapping: a kept record ending where a new period
    # starts does not hide it.
    edge = W0 + 3 * DAY
    client = FakeClient([(edge - HOUR, edge + 2 * HOUR)])
    n = store.replace(store.CONGESTION, "uba_ar", "topsites",
                      [{"start": edge + 2 * HOUR, "end": edge + 5 * HOUR}], W0, NOW,
                      client=client)
    assert n == 1


def test_records_are_read_back_a_window_before_the_edge():
    client = FakeClient()
    store.replace(store.CONGESTION, "NYT", "topsites", [], W0, NOW, client=client)
    assert f"range(start: {W0 - 14 * DAY}, stop: {NOW + 1})" in client.queries[0]


def test_a_window_pinned_at_its_start_date_corrects_everything_in_it():
    # INFERENCE_SINCE three days ago: the window does not slide toward its
    # periods, so nothing in it is settled yet.
    since = NOW - 3 * DAY
    client = FakeClient([(since + HOUR, since + 2 * HOUR)])
    n = store.replace(store.CONGESTION, "NYT", "topsites",
                      [{"start": since + 2 * HOUR, "end": since + 3 * HOUR}],
                      since, NOW, client=client, edge=NOW - 14 * DAY)
    assert n == 1
    assert client.deleted[0][0] == _iso(since + 1)


def test_a_window_pinned_inside_the_guard_still_corrects_from_its_start():
    # INFERENCE_SINCE one day after the edge, inside the three-day guard: a
    # period just after the start date is written, not left out for good.
    since = NOW - 13 * DAY
    client = FakeClient()
    n = store.replace(store.CONGESTION, "NYT", "topsites",
                      [{"start": since + HOUR, "end": since + 3 * HOUR}],
                      since, NOW, client=client, edge=NOW - 14 * DAY)
    assert n == 1
    assert client.deleted[0][0] == _iso(since + 1)


def test_a_window_that_slides_settles_from_the_edge():
    # Pinned until the edge reaches the start date, then sliding: settled
    # from the edge like any other window.
    since = NOW - 14 * DAY
    client = FakeClient()
    store.replace(store.CONGESTION, "NYT", "topsites", [], since, NOW, client=client,
                  edge=NOW - 14 * DAY)
    assert client.deleted[0][0] == _iso(NOW - 11 * DAY + 1)


def test_an_aggregate_stream_a_cycle_after_its_edge_slides_with_it():
    # aggregate.run trims the stream at the window's start, so its first
    # cycle is up to five minutes after the edge: still sliding, still guarded.
    edge = NOW - 14 * DAY
    client = FakeClient()
    store.replace_shared([], edge + 300, NOW, client=client,
                         measurement=store.SHARED_CONGESTION, edge=edge)
    assert client.deleted[0][0] == _iso(edge + 3 * DAY + 1)


def test_a_failed_read_deletes_nothing():
    class Broken(FakeClient):
        def query_api(self):
            raise ConnectionError("influxdb down")

    client = Broken()
    with pytest.raises(ConnectionError):
        store.replace(store.CONGESTION, "NYT", "topsites", [], W0, NOW, client=client)
    assert client.deleted == [] and client.written == []


def test_a_run_that_finds_nothing_still_clears_after_the_edge():
    client = FakeClient()
    assert store.replace(store.CONGESTION, "NYT", "topsites", [], W0, NOW,
                         client=client) == 0
    assert len(client.deleted) == 1
    assert client.written == []


def test_shared_periods_settle_the_same_way():
    guard = store.GUARD[store.SHARED]
    kept = (W0 + guard - 900, W0 + guard + 2 * HOUR)
    client = FakeClient([kept, (W0 + 3 * DAY, W0 + 3 * DAY + HOUR)])
    n = store.replace_shared(
        [{"start": W0 + guard + 900, "end": W0 + guard + 2 * HOUR, "scope": "shared"},
         {"start": W0 + 4 * DAY, "end": W0 + 4 * DAY + HOUR, "scope": "shared"}],
        W0, NOW, client=client)
    assert n == 1  # the moved one is the kept record; the new one is written
    assert client.written[0].to_line_protocol().endswith(str(W0 + 4 * DAY))
    (start, _, predicate, _) = client.deleted[0]
    assert start == _iso(W0 + guard + 1) and predicate == '_measurement="shared_degradation"'
    assert "r.target" not in client.queries[0]


def test_shared_congestion_settles_a_moved_start_like_per_target_congestion():
    edge = W0 + 3 * DAY
    client = FakeClient([(edge + 900, edge + 6 * HOUR)])
    store.replace_shared([{"start": edge - 900, "end": edge + 6 * HOUR}], W0, NOW,
                         client=client, measurement=store.SHARED_CONGESTION)
    assert client.deleted[0][0] == _iso(edge + 901)


def test_a_quote_never_reaches_the_delete_predicate():
    with pytest.raises(ValueError):
        store.replace(store.CONGESTION, 'x" OR target="y', "c", [], W0, W0 + 60,
                      client=FakeClient())


def test_a_period_carries_its_class_coverage_and_both_deaf_shares():
    (pt,) = store.points(store.DEGRADATION, "Google", "topsites",
                         [{"start": W0 + 900, "end": W0 + 4500, "mean_loss_pct": 96.0,
                           "class": "monitor_deaf", "deaf_share": 0.25, "deaf_loss_share": 1.0,
                           "coverage": 0.75}], W0)
    line = pt.to_line_protocol()
    assert 'class="monitor_deaf"' in line
    assert "deaf_share=0.25" in line and "coverage=0.75" in line
    assert re.search(r"[ ,]deaf_loss_share=1[, ]", line)


def test_a_period_carries_how_its_loss_was_spread():
    """A burst: one bin of 31 % in a segment of 14. The counts and the
    peak's epoch are integer fields, the peak a float."""
    (pt,) = store.points(store.DEGRADATION, "UBA", "custom",
                         [{"start": W0 + 900, "end": W0 + 900 + 14 * 900, "mean_loss_pct": 2.19,
                           "bins": 14, "lossy_bins": 1, "peak_pct": 30.67,
                           "peak_start": W0 + 4500, "burst": 1}], W0)
    line = pt.to_line_protocol()
    assert "lossy_bins=1i" in line and "burst=1i" in line
    assert f"peak_start={W0 + 4500}i" in line
    assert "peak_pct=30.67" in line
    # ``bins`` stays the detector's own, as before: not a field.
    assert not re.search(r"[ ,]bins=", line)


def test_a_period_written_before_the_spread_existed_has_none_of_it():
    (pt,) = store.points(store.DEGRADATION, "x", "c",
                         [{"start": W0 + 900, "end": W0 + 4500, "mean_loss_pct": 4.2}], W0)
    line = pt.to_line_protocol()
    assert "lossy_bins" not in line and "peak" not in line and "burst" not in line


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
    store.replace_shared([], W0, NOW, client=client, measurement=store.SHARED_CONGESTION)
    assert '_measurement="shared_congestion"' in client.deleted[0][2]


def test_a_guard_past_now_deletes_nothing():
    client = FakeClient()
    store.replace_shared([], NOW - HOUR, NOW, client=client,
                         measurement=store.SHARED_CONGESTION, edge=NOW - HOUR)
    assert client.deleted == []


def test_the_pass_point_counts_shared_congestion_periods():
    line = store.pass_point({"targets": {}, "shared_congestion": {"periods": 2}},
                            1000).to_line_protocol()
    assert "shared_congestion_periods=2i" in line


def test_shared_segment_points_carry_the_hypothesis_and_its_counts():
    h = {"start": 1000, "end": 4600, "mean_loss_pct": 3.0, "affected": 9, "unaffected": 20,
         "mild": 1, "traced": 29, "untraced": 0, "coverage": 1.0, "common_hops": 5,
         "path_changed": 0, "confidence": "strong", "segment": "AS6939 (HURRICANE)",
         "segment_kind": "as", "affected_through": 9, "unaffected_through": 0,
         "recall": 1.0, "spill": 0.0, "alternatives": "", "hypothesis": "9 lost, 20 did not",
         "target_loss": {"a": 4.0}}
    [pt] = store.shared_points([h, {"start": 100, "end": 200}], 500, store.SHARED_SEGMENT)
    line = pt.to_line_protocol()
    assert line.startswith("shared_segment ")
    for part in ('confidence="strong"', 'segment="AS6939 (HURRICANE)"', "affected=9i",
                 "unaffected_through=0i", "recall=1", "common_hops=5i", "end=4600i",
                 'hypothesis="9 lost, 20 did not"'):
        assert part in line
    assert "target_loss" not in line and "voters" not in line
    assert store.measurement_name(store.SHARED_SEGMENT) == "shared_segment"


def test_the_triage_fields_are_written_with_their_types_and_do_not_change_the_key():
    p = {"start": W0 + 900, "end": W0 + 4500, "confidence": 0.9,
         "triage": "ICMP, TCP and HTTP to NYT rose together", "tri_tcp": "up",
         "tri_http": "up", "tri_dns": "flat", "tri_hop": "flat", "tri_wifi": "nodata",
         "tri_path": "unchanged", "tri_others": 0, "tri_version": 1,
         "icmp_rise_ms": 10.2, "tcp_rise_ms": 9.0, "http_rise_ms": 49}
    (pt,) = store.points(store.CONGESTION, "NYT", "topsites", [p], W0)
    line = pt.to_line_protocol()
    assert line.startswith("persistent_congestion,category=topsites,target=NYT ")
    assert 'triage="ICMP, TCP and HTTP to NYT rose together"' in line
    assert 'tri_tcp="up"' in line and 'tri_path="unchanged"' in line
    assert "tri_others=0i" in line and "tri_version=1i" in line
    assert "http_rise_ms=49" in line and "http_rise_ms=49i" not in line
    assert line.endswith(f" {W0 + 900}")


def test_a_period_without_the_triage_has_none_of_its_fields():
    (pt,) = store.points(store.CONGESTION, "NYT", "topsites",
                         [{"start": W0 + 900, "end": W0 + 4500}], W0)
    assert "tri_" not in pt.to_line_protocol() and "triage" not in pt.to_line_protocol()


def test_the_triage_runs_only_on_the_periods_that_are_written():
    # A record an earlier pass wrote before the settled edge stays as it was:
    # its period is not written, so it is not triaged either.
    kept = (W0 + 2 * DAY, W0 + 3 * DAY + 3600)
    client = FakeClient([kept])
    seen = []
    periods = [{"start": kept[0] + 900, "end": kept[1]},
               {"start": W0 + 5 * DAY, "end": W0 + 5 * DAY + 3600}]
    store.replace(store.CONGESTION, "NYT", "topsites", periods, W0, NOW, client=client,
                  enrich=lambda w: seen.extend(p["start"] for p in w))
    assert seen == [W0 + 5 * DAY]


def test_the_triage_fields_reach_the_written_point():
    client = FakeClient()

    def enrich(write):
        for p in write:
            p["triage"] = "only NYT rose."
    store.replace(store.CONGESTION, "NYT", "topsites",
                  [{"start": W0 + 5 * DAY, "end": W0 + 5 * DAY + 3600}], W0, NOW,
                  client=client, enrich=enrich)
    assert 'triage="only NYT rose."' in client.written[0].to_line_protocol()


def test_the_pass_records_the_triage_cost():
    line = store.pass_point({"seconds": 70.0, "targets": {},
                             "triage": {"seconds": 1.25, "errors": 2}}, NOW).to_line_protocol()
    assert "triage_s=1.25" in line and "triage_errors=2i" in line
