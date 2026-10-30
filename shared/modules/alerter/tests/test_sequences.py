"""Whole incidents, replayed minute by minute through evaluate + reconcile.

The rules and the lifecycle each have their own unit tests; what broke in
production was the sequence. After the Pi's radio hung on 12/10/2026, one
event reached Telegram as ten messages in 72 minutes, and a target going
down announced "recovered" while it was still down. These tests drive the
real ``evaluate_with_context`` (InfluxDB faked, one point per target per
300 s step) and ``state.reconcile`` once a minute, and count what would have
been sent.
"""

import re
from datetime import datetime, timedelta, timezone

import evaluator
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

    def run(self, minutes):
        """Evaluate once a minute; return what would have been sent."""
        current = {"incidents": {}}
        sent = []
        for minute in range(minutes):
            self.now = T0 + timedelta(minutes=minute)
            incidents, context = evaluator.evaluate_with_context(
                records=current["incidents"])
            actions = state.reconcile(
                current, incidents, now=self.now.timestamp(),
                min_resolve_after=900, judged=context["judged"])
            sent += [(minute, "alert", e["key"]) for e in actions["alerts"]]
            sent += [(minute, "recovery", e["key"]) for e in actions["recoveries"]]
        return sent


def everyone(loss, targets=TARGETS):
    return {t: loss for t in targets}


def test_a_long_hang_is_one_critical_and_one_recovery(monkeypatch):
    """The 12/10 shape: every target at 100% for an hour, then back.

    Before: after the return, every cycle that slid out of the window opened
    a new ``outage`` warning -- "a brief cut of the link, not a site
    problem" -- just as delivery worked again.
    """
    def loss(i):
        return 1.0 if 3 <= i < 15 else 0.0

    sent = Replay(monkeypatch, everyone(loss)).run(140)
    onset = int((T0 + timedelta(seconds=3 * STEP)).timestamp())
    # A warning at the first lost cycle, the critical at the third, and the
    # recovery 15 minutes after the uplink returned (minute 75) -- once the
    # tail has left the window, and nothing in between.
    assert sent == [
        (15, "alert", f"outage:{onset}"),
        (25, "alert", "uplink_down"),
        (90, "recovery", "uplink_down"),
    ]


def test_a_link_that_comes_back_lossy_is_still_reported(monkeypatch):
    """Review of #332: the hang ends, but the link stays lossy for two hours.

    The uplink_down must recover and the loss that follows must alert: a
    tail that never ends would have kept both silent.
    """
    def loss(i):
        return 1.0 if 3 <= i < 15 else 0.4 if 15 <= i < 39 else 0.0

    sent = Replay(monkeypatch, everyone(loss)).run(240)
    kinds = [(kind, key.split(":")[0]) for _, kind, key in sent]
    assert ("alert", "uplink_down") in kinds
    assert ("recovery", "uplink_down") in kinds
    after = [s for s in sent if s[0] > 75 and s[1] == "alert"]
    assert after and after[0][2].startswith("outage:")


def test_a_lossy_hour_everywhere_is_one_outage(monkeypatch):
    """Widespread loss that is not total, longer than the 20-minute window.

    Before: the outage was keyed by its first cycle still inside the window,
    so a new key -- and a new warning -- every five minutes.
    """
    def loss(i):
        return 0.5 if 3 <= i < 15 else 0.0

    sent = Replay(monkeypatch, everyone(loss)).run(110)
    onset = int((T0 + timedelta(seconds=3 * STEP)).timestamp())
    # One key throughout; the second message is the hourly re-notification
    # of a still-active incident (ALERT_COOLDOWN), not a new incident.
    # Transient: no recovery; per-target high_loss stays suppressed.
    assert sent == [
        (15, "alert", f"outage:{onset}"),
        (75, "alert", f"outage:{onset}"),
    ]


def test_a_target_going_down_escalates_without_a_false_recovery(monkeypatch):
    """high_loss, then target_down: one problem growing worse.

    Before: once the target was down, high_loss was excluded, went missing,
    and announced "recovered" fifteen minutes into the outage.
    """
    timeline = everyone(lambda i: 0.0)
    timeline["NYT"] = lambda i: (0.5 if 2 <= i < 4 else 1.0 if 4 <= i < 14 else 0.0)

    sent = Replay(monkeypatch, timeline).run(110)
    assert [(kind, key) for _, kind, key in sent] == [
        ("alert", "high_loss:NYT"),
        ("alert", "target_down:NYT"),
        ("recovery", "target_down:NYT"),
    ]
    recovered = sent[-1][0]
    assert recovered >= 14 * 5  # after the target answered again, not before


def test_no_data_is_not_a_recovery(monkeypatch):
    """A target is down; then the exporter stalls for an hour.

    Before: the down target's points left the window, the incident went
    missing, and "recovered" went out with no data behind it.
    """
    timeline = everyone(lambda i: None if 6 <= i < 18 else 0.0)
    timeline["NYT"] = lambda i: None if 6 <= i < 18 else (1.0 if i < 18 else 0.0)

    sent = Replay(monkeypatch, timeline).run(130)
    # Data stops at minute 30 and returns at minute 90 with NYT answering.
    # NYT is held open through the stall; it recovers once three points
    # show it up (minute 100) and the grace period has passed.
    assert sent == [
        (5, "alert", "high_loss:NYT"),
        (10, "alert", "target_down:NYT"),
        (45, "alert", "exporter_stale"),
        (105, "recovery", "exporter_stale"),
        (115, "recovery", "target_down:NYT"),
    ]


def test_an_incident_with_no_data_for_a_day_is_dropped_silently():
    current = {"incidents": {}}
    incident = {"rule": "target_down", "severity": "critical",
                "key": "target_down:old", "target": "old", "message": "old down"}
    state.reconcile(current, [incident], now=0.0, judged={"old"})
    for now in (60.0, 3600.0, 23 * 3600.0):
        actions = state.reconcile(current, [], now=now, judged=set())
        assert actions == {"alerts": [], "recoveries": []}
        assert "target_down:old" in current["incidents"]
    actions = state.reconcile(current, [], now=60.0 + 24 * 3600.0, judged=set())
    assert actions == {"alerts": [], "recoveries": []}
    assert current["incidents"] == {}


def test_a_hang_the_alerter_could_not_report_arrives_as_one_summary(monkeypatch):
    """12/10/2026 as it happened: the radio is deaf, so the alerter (which
    still evaluates from the local InfluxDB) cannot deliver anything.

    Before: every alert decided in the dark was lost, and the return
    delivered whatever was decided at that moment. Now the critical waits in
    the outbox, nothing is tried while the network is down, and once it is
    back a single summary says what happened and what is open.
    """
    import main
    import outbox

    def loss(i):
        return 1.0 if 3 <= i < 15 else 0.0

    def online(minute):
        return not 16 <= minute < 76  # deaf from just after the first cut

    replay = Replay(monkeypatch, everyone(loss))
    delivered, attempts = [], []
    clock = {"minute": 0}

    def notify(payload, image=None):
        attempts.append(clock["minute"])
        if online(clock["minute"]):
            delivered.append((clock["minute"], payload))
            return "sent"
        return "failed"

    monkeypatch.setattr(main.notifier, "send", notify)
    current = {"incidents": {}}
    for minute in range(120):
        clock["minute"] = minute
        replay.now = T0 + timedelta(minutes=minute)
        now = replay.now.timestamp()
        incidents, context = evaluator.evaluate_with_context(
            records=current["incidents"])
        actions = state.reconcile(current, incidents, now=now,
                                  min_resolve_after=900, judged=context["judged"])
        for event in actions["alerts"]:
            main._deliver(current, {**event, "type": "alert"}, now=now)
        for event in actions["recoveries"]:
            main._deliver(current, {**event, "type": "recovery"}, now=now)
        outbox.flush(current, now, send=notify,
                     reachable=lambda: online(clock["minute"]))

    kinds = [(m, p.get("type"), p.get("key")) for m, p in delivered]
    # The onset warning went out while the link still worked; the critical
    # failed once (minute 25) and was never sent into the dead link again.
    assert attempts.count(25) == 1 and not any(26 <= m < 76 for m in attempts)
    assert kinds[0][1:] == ("alert", f"outage:{int((T0 + timedelta(seconds=900)).timestamp())}")
    summary = [p for _, p in delivered if p.get("type") == "undelivered"]
    assert len(summary) == 1
    assert "uplink_down" in summary[0]["message"]
    assert "Now open: 🔴 uplink_down" in summary[0]["message"]
    # Back at minute 76; the summary within one backoff step (10 min).
    assert 76 <= next(m for m, p in delivered if p.get("type") == "undelivered") <= 86
    # The recovery, decided after the return, is sent as itself.
    assert kinds[-1][1:] == ("recovery", "uplink_down")
    assert len(delivered) == 3



def test_an_isp_outage_is_named_as_one_and_ends_as_one(monkeypatch):
    """Every destination lost for an hour while the ISP gateway answers.

    Before: "this host's uplink is down", for a fault past the house.
    """
    timeline = everyone(lambda i: 1.0 if 3 <= i < 15 else 0.0,
                        targets=[t for t in TARGETS if t != "CPE_IPv4"])
    timeline["CPE_IPv4"] = lambda i: 0.1  # its rate-limit floor; it answers
    sent = Replay(monkeypatch, timeline, categories={"CPE_IPv4": "cpe"}).run(140)
    onset = int((T0 + timedelta(seconds=3 * STEP)).timestamp())
    assert sent == [
        (15, "alert", f"outage:{onset}"),
        (25, "alert", "internet_down"),
        (90, "recovery", "internet_down"),
    ]
