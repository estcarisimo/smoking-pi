"""The readers the weekly summary adds: a calendar week, not "the last N".

Every query is a fake; nothing reaches InfluxDB.
"""

from datetime import datetime, timezone

from common import aggregates, incident_answers

START = datetime(2026, 10, 26, 5, tzinfo=timezone.utc).timestamp()
STOP = datetime(2026, 11, 2, 6, tzinfo=timezone.utc).timestamp()


def test_history_between_reads_the_range_oldest_first_without_probe_misses():
    asked = []
    rows = [
        {"_time": datetime(2026, 10, 30, 1, tzinfo=timezone.utc), "class": "upstream",
         "end_epoch": int(datetime(2026, 10, 30, 1, 40, tzinfo=timezone.utc).timestamp()),
         "minutes": 40, "targets": "a,b,c", "targets_affected": 3,
         "targets_reporting": 10, "max_loss_pct": 80.0},
        {"_time": datetime(2026, 10, 28, 1, tzinfo=timezone.utc), "class": "local_link",
         "minutes": 20, "targets": "a,b", "targets_reporting": 10, "max_loss_pct": -1.0},
        {"_time": datetime(2026, 10, 27, 1, tzinfo=timezone.utc), "class": "probe_miss",
         "minutes": 5, "targets": "a"},
    ]

    def query(flux):
        asked.append(flux)
        return rows

    out = incident_answers.history_between(query, "smokeping", START, STOP)
    [flux] = asked
    assert "range(start: 2026-10-26T05:00:00Z, stop: 2026-11-02T06:00:00Z)" in flux
    assert [i["class"] for i in out] == ["local_link", "upstream"]
    first, second = out
    # No end stored: the start plus its minutes.
    assert first["end_epoch"] - first["start_epoch"] == 20 * 60
    assert first["targets_affected"] == 2 and "max_loss_pct" not in first
    assert second["targets_affected"] == 3 and second["max_loss_pct"] == 80.0


def test_target_stats_reads_a_fixed_window_and_clips_spans_to_it(monkeypatch):
    asked = []

    def query(flux):
        asked.append(flux)
        if "mean()" in flux and "loss" in flux:
            return [{"target": "a", "_measurement": "latency", "_value": 0.1}]
        if "count()" in flux and "union" not in flux:
            return [{"target": "a", "_measurement": "latency", "_value": 2016}]
        return []

    monkeypatch.setattr(aggregates, "query_influx", query)
    spans = [(START - 3600, START + 600), (STOP - 600, STOP + 3600), (STOP + 10, STOP + 20)]
    stats = aggregates.target_stats(START, STOP, spans)
    assert stats[0]["target"] == "a" and stats[0]["avg_loss_pct"] == 10.0
    window = "range(start: 2026-10-26T05:00:00Z, stop: 2026-11-02T06:00:00Z)"
    assert any(window in f for f in asked)
    # The spans are read clipped to the week: the one past it not at all.
    in_spans = [f for f in asked if "2026-10-26T05:10:00Z" in f]
    assert in_spans and all("2026-11-02T06:00:10Z" not in f for f in asked)
    assert not any("-0h" in f for f in asked)
