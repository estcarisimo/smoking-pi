"""The alert line and the diagnosis attached to it count the same targets.

v2.33.0 let the HTTP, TCP and DNS wizard targets vote on the widespread
rule (common.definitions.breadth_voters), but the diagnosis went on
counting the ICMP and resolver targets alone: an alert could read "180 of
243 targets lost packets" over a diagnosis that said "19 of 19". These
tests drive the real ``evaluate_with_context`` and the real
``diagnosis_query.run`` (as ``alert_diagnosis.run`` calls it) over one fake
InfluxDB, which answers the alerter's raw-point queries and the diagnosis's
Flux aggregates from the same per-target timeline.
"""

import re
from datetime import datetime, timedelta, timezone

import alert_diagnosis
import evaluator
from common import breadth, cadence, diagnosis_query

STEP = 300
T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
NOW_STEP = 36  # three hours of points: the alert diagnosis's window
NAMED = ["Google", "Apple", "Amazon", "NYT"]


class Influx:
    """``named``: ICMP targets in ``latency``; ``breadth``: breadth-only
    targets, each ``(measurement, pings, step, loss(i))``. ``loss(i)`` is
    the ratio written at step ``i``, None for no point."""

    def __init__(self, named, breadth_targets):
        self.named = named
        self.breadth = breadth_targets
        self.now = T0 + timedelta(seconds=STEP * NOW_STEP)

    def _window(self, src):
        m = re.search(r"range\(start: -(\d+)([smhd])\)", src)
        if m is None:  # range(start: 0): ever measured
            return 10**9
        return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]

    def _points(self, series, seconds, step=STEP):
        start = self.now - timedelta(seconds=seconds)
        out = []
        for i in range(NOW_STEP + 1):
            at = T0 + timedelta(seconds=STEP * i)
            if at <= start or at > self.now or (step > STEP and i % (step // STEP)):
                continue
            value = series(i)
            if value is not None:
                out.append((at, value))
        return out

    def _breadth_points(self, seconds):
        for target, (measurement, pings, step, loss) in self.breadth.items():
            for at, value in self._points(loss, seconds, step):
                yield target, measurement, pings, step, at, value

    def query(self, src):  # noqa: C901 -- one dispatcher, read top to bottom
        seconds = self._window(src)
        if '|> count() |> keep(columns: ["target", "_value"])' in src:
            bar = re.search(r"r\._value >= ([0-9.]+)\)", src)  # breadth.count_flux
            counts = {}
            for target, *_rest, value in self._breadth_points(seconds):
                if bar is None or value >= float(bar.group(1)):
                    counts[target] = counts.get(target, 0) + 1
            return [{"target": t, "_value": n} for t, n in counts.items()]
        for end in ("first", "last"):  # breadth.span_flux
            if f'|> {end}() |> keep(columns: ["target", "_time"])' in src:
                spans = {}
                for target, *_rest, at, _v in self._breadth_points(seconds):
                    spans.setdefault(target, []).append(at)
                pick = min if end == "first" else max
                return [{"target": t, "_time": pick(v)} for t, v in spans.items()]
        if "aggregateWindow(" in src:  # breadth.cycle_count_flux
            bar = float(re.search(r"r\._value >= ([0-9.]+)\) \|> filter", src).group(1))
            voting = set(re.findall(r'"([^"]+)"', src.split("set: [")[1].split("]")[0]))
            per_cycle = {}
            for t, *_rest, at, v in self._breadth_points(seconds):
                if t in voting and v >= bar:
                    per_cycle[at] = per_cycle.get(at, 0) + 1
            return [{"_time": at, "_value": n} for at, n in per_cycle.items()]
        if '"http_latency"' in src and "last()" in src:  # breadth.cadence_flux
            return [row for t, (_m, pings, step, _l) in self.breadth.items()
                    for row in ({"target": t, "_field": "pings", "_value": pings},
                                {"target": t, "_field": "step", "_value": step})]
        if '"http_latency"' in src and 'r._field == "pings"' in src:  # points_flux
            rows = []
            for target, measurement, pings, step, at, value in \
                    self._breadth_points(seconds):
                for field, v in (("loss", value), ("pings", pings), ("step", step)):
                    rows.append({"target": target, "_field": field, "_value": v,
                                 "_time": at, "_measurement": measurement,
                                 "category": "dns_wizard"})
            return rows
        if '"http_latency"' in src:  # liveness, the app layer: nothing else
            return []
        if any(m in src for m in ("cpe_latency", "wifi_link", "host_uplink")):
            return []
        if 'r._field == "step"' in src:  # the named targets' cadence: default
            return []
        if "first()" in src:
            return [{"_value": 1}]
        named = [(t, at, v) for t, loss in self.named.items()
                 for at, v in self._points(loss, seconds)]
        if "distinct(" in src:  # targets reporting per point time
            if 'r._measurement == "latency"' not in src:
                return []  # the resolvers: none here
            times = {}
            for _t, at, _v in named:
                times[at] = times.get(at, 0) + 1
            return [{"_time": at, "_value": n} for at, n in times.items()]
        if "count()" in src:
            return [{"_value": len(named)}]
        if "mean()" in src:
            sums = {}
            for t, _at, v in named:
                sums.setdefault(t, []).append(min(1.0, v))
            return [{"target": t, "category": "topsites", "_value": sum(v) / len(v)}
                    for t, v in sums.items()]
        rows = [{"target": t, "_value": v, "_time": at, "_measurement": "latency",
                 "category": "topsites", "_field": "loss"} for t, at, v in named]
        if "r._value >=" in src:  # the diagnosis's loss events
            rows = [r for r in rows if r["_value"] >= 0.15]
            rows.sort(key=lambda r: r["_time"], reverse=True)
        return rows


def _lossy_from(step, value):
    return lambda i: value if i >= step else 0.0


def _run(monkeypatch, influx):
    monkeypatch.setattr(evaluator, "_query", influx.query)
    monkeypatch.setattr(evaluator.time, "time", lambda: influx.now.timestamp())
    incidents, context = evaluator.evaluate_with_context()
    result = diagnosis_query.run(influx.query, alert_diagnosis.hours(),
                                 context["cadences"])
    return incidents, context, result


def test_the_alert_and_its_diagnosis_count_the_same_breadth(monkeypatch):
    """Four pinged sites and ten of twelve web services lose packets in the
    last two cycles. A CDN that never answers and a probe on a slower step
    vote in neither."""
    services = {f"svc{i}_h2": ("http_latency", 3, STEP,
                               _lossy_from(NOW_STEP - 1, 2 / 3 if i < 10 else 0.0))
                for i in range(12)}
    services["dead_cdn"] = ("latency", 10, STEP, lambda i: 1.0)
    services["slow_tcp"] = ("tcp_latency", 3, 2 * STEP, _lossy_from(NOW_STEP - 1, 1.0))
    influx = Influx({t: _lossy_from(NOW_STEP - 1, 0.6) for t in NAMED}, services)

    incidents, context, result = _run(monkeypatch, influx)

    [outage] = [i for i in incidents if i["rule"] == "outage"]
    assert outage["message"].startswith("14 of 16 targets lost packets")
    # The same voters on both sides.
    alert_voters = {r["target"] for r in context["breadth_mean_rows"]}
    assert alert_voters == {f"svc{i}_h2" for i in range(12)}
    cad = cadence.by_target(influx.query(breadth.cadence_flux()))
    def counted(bar=None):
        return {r["target"]: r["_value"] for r in influx.query(breadth.count_flux(3, bar))}

    totals = counted()
    events = {t: counted(cadence.event_ratio(cadence.of(cad, t).pings)).get(t, 0)
              for t in totals}
    diag_voters = breadth.voters(totals, events, cad, STEP)
    assert diag_voters == alert_voters

    attached = alert_diagnosis.for_event(outage, result, STEP,
                                         now=influx.now.timestamp())
    assert attached is not None and attached["class"] == "upstream"
    assert attached["evidence"][0].startswith(
        "14 of 16 destinations lost packets in the same cycle")
    [inc] = [i for i in result["incidents"] if i["class"] == "upstream"]
    # Counted, never named: the per-target view is the pinged sites.
    assert inc["targets"] == sorted(NAMED)
    assert inc["targets_reporting"] == 16
    assert result["targets_reporting"] == 16
    assert result["coverage"]["breadth_only"] is True


def test_a_cut_of_the_web_services_alone_is_one_incident_naming_none(monkeypatch):
    """Every ping answers; sixteen web services lose packets at once. The
    alerter calls it a cut (16 of 20); the diagnosis must see the same
    incident, and name none of the services."""
    services = {f"svc{i}_h3": ("http_latency", 3, STEP,
                               _lossy_from(NOW_STEP - 1, 2 / 3))
                for i in range(16)}
    influx = Influx({t: lambda i: 0.0 for t in NAMED}, services)

    incidents, _context, result = _run(monkeypatch, influx)

    [outage] = [i for i in incidents if i["rule"] == "outage"]
    assert outage["message"].startswith("16 of 20 targets lost packets")
    attached = alert_diagnosis.for_event(outage, result, STEP,
                                         now=influx.now.timestamp())
    assert attached is not None and attached["class"] == "upstream"
    assert attached["evidence"][0].startswith(
        "16 of 20 destinations lost packets in the same cycle")
    [inc] = result["incidents"]
    assert inc["targets"] == [] and inc["targets_affected"] == 16


def test_one_lossy_web_service_is_neither_an_alert_nor_an_incident(monkeypatch):
    """Breadth-only targets never get a per-target view: one service losing
    every other cycle all afternoon is in no incident of its own."""
    services = {f"svc{i}_h2": ("http_latency", 3, STEP,
                               (lambda i: 2 / 3 if i % 2 else 0.0) if i == 0
                               else (lambda i: 0.0))
                for i in range(12)}
    influx = Influx({t: lambda i: 0.0 for t in NAMED}, services)

    incidents, _context, result = _run(monkeypatch, influx)

    assert not [i for i in incidents if i["rule"] == "outage"]
    assert result["incidents"] == []
