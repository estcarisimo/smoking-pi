"""Whole events, replayed minute by minute through the cycle's pipeline.

The rules and the lifecycle each have their own unit tests; what broke in
production was the sequence. After the Pi's radio hung on 12/10/2026, one
event reached Telegram as ten messages in 72 minutes, and a target going
down announced "recovered" while it was still down. These tests drive the
real ``evaluate_with_context`` (InfluxDB faked, one point per target per
300 s step), ``state.track``, ``episodes.update`` and ``policy.decide`` once
a minute, and count what the policy would have sent. The October cases
from the audit are test_october.py; these are the synthetic shapes.
"""

import re
from datetime import datetime, timedelta, timezone

import episodes
import evaluator
import main
import policy
import state

STEP = 300
T0 = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
TARGETS = ["Google", "Apple", "Amazon", "NYT", "Facebook", "cloudflare",
           "GoogleDNS", "CloudflareDNS", "Quad9DNS", "CPE_IPv4"]


class Replay:
    """A fake InfluxDB over a per-target timeline of loss ratios.

    ``timeline[target]`` is a function of the step index returning the loss
    ratio written for that step, or None for no point. A step's point is
    readable from the moment the step starts, as an RRD row is.
    """

    def __init__(self, monkeypatch, timeline, categories=None):
        self.timeline = timeline
        self.categories = categories or {}
        self.now = T0
        monkeypatch.setattr(evaluator, "_query", self.query)

    def points(self, seconds):
        start = self.now - timedelta(seconds=seconds)
        rows = []
        i = 0
        while T0 + timedelta(seconds=STEP * i) <= self.now:
            at = T0 + timedelta(seconds=STEP * i)
            if at > start:
                for target, loss in self.timeline.items():
                    value = loss(i)
                    if value is not None:
                        rows.append({"target": target, "_value": value,
                                     "_time": at, "_measurement": "latency",
                                     "category": self.categories.get(target, "topsites")})
            i += 1
        return rows

    def query(self, flux_src):
        if '"http_latency"' in flux_src:
            return []  # measurement liveness: nothing else was ever written
        if any(m in flux_src for m in ("cpe_latency", "wifi_link", "host_uplink")):
            return []
        if 'r._field == "step"' in flux_src:  # cadence: the default step
            return []
        seconds = int(re.search(r"range\(start: -(\d+)s\)", flux_src).group(1))
        if "first()" in flux_src:  # ever measured: yes
            return [{"_value": 1}]
        if "count()" in flux_src:
            return [{"_value": len(self.points(seconds))}] if self.points(seconds) else []
        if "mean()" in flux_src:
            by_target = {}
            for row in self.points(seconds):
                by_target.setdefault(row["target"], []).append(min(1.0, row["_value"]))
            return [{"target": t, "category": "topsites", "_value": sum(v) / len(v)}
                    for t, v in by_target.items()]
        return self.points(seconds)  # the raw down-window points

    def run(self, minutes, deliver=None):
        """Evaluate once a minute; return what the policy would have sent,
        as (minute, kind), and the state."""
        current = {"incidents": {}}
        sent = []
        for minute in range(minutes):
            self.now = T0 + timedelta(minutes=minute)
            now = self.now.timestamp()
            incidents, context = evaluator.evaluate_with_context(
                records=current["incidents"])
            state.track(current, incidents, now=now, min_resolve_after=900,
                        judged=context["judged"])
            events = episodes.update(
                current, main._cycle(current, incidents, context, [], now))
            for payload in policy.decide(current, events, now):
                sent.append((minute, payload["kind"]))
                if deliver:
                    deliver(current, payload, minute, now)
        self.current = current
        return sent


def everyone(loss, targets=TARGETS):
    return {t: loss for t in targets}


def test_a_long_hang_is_one_alert_and_one_recovery(monkeypatch):
    """The 12/10 shape: every target at 100% for an hour, then back, with no
    Wi-Fi telemetry and no first hop to tell the monitor from the line.

    Before: an outage warning, the critical, re-notifications, and after
    the return a new ``outage`` every cycle that slid out of the window.
    Now: one episode. Nothing says the monitor could not hear, so it is the
    Internet down -- with its cause undetermined, as the alert says.
    """
    def loss(i):
        return 1.0 if 3 <= i < 15 else 0.0

    replay = Replay(monkeypatch, everyone(loss))
    sent = replay.run(140)
    assert [kind for _, kind in sent] == ["down", "recovery"]
    assert sent[0][0] == 25  # the third lost cycle
    [episode] = replay.current["episodes"]["closed"]
    assert episode["cause"] == "undetermined"
    assert episode["duration_s"] == 60 * 60


def test_a_link_that_comes_back_lossy_is_one_episode(monkeypatch):
    """Review of #332: the hang ends, but the link stays lossy for two hours.
    The loss that follows is the same episode; its end is told once, when
    the loss is over."""
    def loss(i):
        return 1.0 if 3 <= i < 15 else 0.4 if 15 <= i < 39 else 0.0

    replay = Replay(monkeypatch, everyone(loss))
    sent = replay.run(240)
    assert [kind for _, kind in sent] == ["down", "recovery"]
    assert sent[-1][0] > 39 * 5
    [episode] = replay.current["episodes"]["closed"]
    assert episode["duration_s"] == 36 * 300


def test_a_lossy_hour_everywhere_is_one_degradation(monkeypatch):
    """Widespread loss that is not total, longer than the 20-minute window.

    Before: a new outage key -- and a new warning -- every five minutes,
    then an hourly re-notification. Now: one alert, one recovery.
    """
    def loss(i):
        return 0.5 if 3 <= i < 15 else 0.0

    sent = Replay(monkeypatch, everyone(loss)).run(110)
    assert [kind for _, kind in sent] == ["degradation", "recovery"]


def test_a_target_going_down_is_one_destination_and_not_pushed(monkeypatch):
    """high_loss, then target_down: one problem growing worse -- about one
    destination, which is recorded and never pushed."""
    timeline = everyone(lambda i: 0.0)
    timeline["NYT"] = lambda i: (0.5 if 2 <= i < 4 else 1.0 if 4 <= i < 14 else 0.0)

    replay = Replay(monkeypatch, timeline)
    assert replay.run(110) == []
    [episode] = replay.current["episodes"]["closed"]
    assert episode["class"] == "destination"
    assert episode["sites"] == ["NYT"]
    assert set(episode["signals"]) == {"high_loss", "target_down"}


def test_no_data_is_not_an_end(monkeypatch):
    """A target is down; then the exporter stalls for an hour.

    Before: the stall was its own critical and its own recovery. Now the
    stall is the monitor's blind span inside the same episode; the target
    is held, not called fine, while there is no data.
    """
    timeline = everyone(lambda i: None if 6 <= i < 18 else 0.0)
    timeline["NYT"] = lambda i: None if 6 <= i < 18 else (1.0 if i < 18 else 0.0)

    replay = Replay(monkeypatch, timeline)
    sent = replay.run(130)
    # One episode: NYT, the stall inside it. The NYT loss is one site's and
    # never pushed; the hour the monitor could not measure is told once.
    assert [kind for _, kind in sent] == ["monitor"]
    [episode] = replay.current["episodes"]["closed"]
    assert episode["signals"].get("exporter_stale")
    assert episode["class"] == "destination"
    assert episode["blind_s"] >= 1200


def test_a_hang_the_alerter_could_not_report_arrives_as_one_summary(monkeypatch):
    """The alert decided while delivery is down waits in the outbox; once
    the network is back a single summary says what happened and what is
    open, and nothing is tried into the dead link meanwhile."""
    import outbox

    def loss(i):
        return 1.0 if 3 <= i < 15 else 0.0

    def online(minute):
        return not 20 <= minute < 76

    delivered, attempts = [], []
    clock = {"minute": 0}

    def notify(payload, image=None):
        attempts.append(clock["minute"])
        if online(clock["minute"]):
            delivered.append((clock["minute"], payload))
            return "sent"
        return "failed"

    monkeypatch.setattr(main.notifier, "send", notify)

    def deliver(current, payload, minute, now):
        clock["minute"] = minute
        main._deliver(current, payload, now=now)

    replay = Replay(monkeypatch, everyone(loss))
    replay.run(76, deliver=deliver)
    for minute in range(76, 90):
        clock["minute"] = minute
        outbox.flush(replay.current, (T0 + timedelta(minutes=minute)).timestamp(),
                     send=notify, reachable=lambda: online(clock["minute"]))
    # The alert (minute 25) failed once and was never sent into the dead
    # link again; it arrives once the link is back, as itself or summarized.
    assert attempts.count(25) == 1 and not any(26 <= m < 76 for m in attempts)
    assert len(delivered) == 1 and 76 <= delivered[0][0] <= 86
    assert "Internet down" in delivered[0][1]["message"]


def test_an_isp_outage_is_named_as_one_and_ends_as_one(monkeypatch):
    """Every destination lost for an hour while the ISP gateway answers.

    Before: "this host's uplink is down", for a fault past the house, and
    an outage warning before it.
    """
    timeline = everyone(lambda i: 1.0 if 3 <= i < 15 else 0.0,
                        targets=[t for t in TARGETS if t != "CPE_IPv4"])
    timeline["CPE_IPv4"] = lambda i: 0.1  # its rate-limit floor; it answers
    replay = Replay(monkeypatch, timeline, categories={"CPE_IPv4": "cpe"})
    sent = replay.run(140)
    assert [kind for _, kind in sent] == ["down", "recovery"]
    [episode] = replay.current["episodes"]["closed"]
    assert episode["class"] == "upstream"
    assert episode["cause"] == "your provider, beyond the modem"
