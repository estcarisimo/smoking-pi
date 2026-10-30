"""October 2026 on the reference Pi, replayed through the whole cycle.

The audit of 2026-10-30 (front 8) counted 72 Telegram messages in October
for about eleven real events, two of them actionable, and several with the
wrong cause. Each case below is one of the audited facts, rebuilt as the
measurements the Pi wrote (loss points per 300 s step, the 10 s first-hop
probe, the Wi-Fi exporter's deaf samples, host_uplink's route changes, the
watchdog's events) and replayed minute by minute through the real
``main.run_iteration`` -- evaluator, signals, episodes, classification,
materiality, policy and delivery -- with InfluxDB faked and the notifier
recording what it would have sent. Each asserts the exact messages.

A point stands for the step that ends at its time, as SmokePing's RRD rows
do, and is readable from that time. Times are the Pi's (America/Chicago),
with the quiet hours it runs, 23:00-07:00.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import alert_diagnosis
import dead_man
import incident_history
import main
import notifier
import reports_watcher
import state
import weekly

CHI = ZoneInfo("America/Chicago")
STEP = 300

TOPSITES = ["Google", "Apple", "Amazon", "NYT", "Facebook", "Cloudflare",
            "Wikipedia", "Reddit", "Github", "Microsoft", "UBA", "Yahoo"]
NETFLIX = [f"Netflix_{i}" for i in range(1, 7)]
DNS = ["GoogleDNS", "CloudflareDNS", "Quad9DNS", "OpenDNS", "ns1_uba"]
DESTINATIONS = TOPSITES + NETFLIX + DNS
CATEGORY = {**{t: "topsites" for t in TOPSITES}, **{t: "netflix" for t in NETFLIX},
            **{t: "dns" for t in DNS}, "CPE_IPv4": "cpe"}
# The taxonomy's service labels (target_labels), as the exporter writes
# them: the Netflix caches are Netflix, UBA's web and name servers uba.ar.
SERVICE = {**{t: "Netflix" for t in NETFLIX}, "UBA": "uba.ar", "ns1_uba": "uba.ar",
           "GoogleDNS": "Google DNS", "CloudflareDNS": "Cloudflare DNS",
           "Quad9DNS": "Quad9", "OpenDNS": "OpenDNS", "NYT": "The New York Times"}


def at(day: int, hhmm: str) -> float:
    hour, minute = hhmm.split(":")[:2]
    second = int(hhmm.split(":")[2]) if hhmm.count(":") == 2 else 0
    return datetime(2026, 10, day, int(hour), int(minute), second,
                    tzinfo=CHI).timestamp()


def _overlap(lo, hi, a, b):
    return max(0.0, min(hi, b) - max(lo, a))


@dataclass
class Case:
    """What the Pi measured. ``loss(target, lo, hi)`` is the loss ratio of
    one step [lo, hi] (None: no point); the spans are (lo, hi) epochs."""

    loss: object = None
    deaf: list = field(default_factory=list)
    route: list = field(default_factory=list)
    cuts: list = field(default_factory=list)
    stalled: list = field(default_factory=list)
    watchdog: list = field(default_factory=list)
    wifi: bool = True

    def ratio(self, target, lo, hi):
        if any(_overlap(lo, hi, a, b) for a, b in self.stalled):
            return None
        deaf = sum(_overlap(lo, hi, a, b) for a, b in self.deaf + self.route)
        if deaf:
            # Nothing comes back while the radio is deaf or the route gone:
            # the step loses its share of the pings.
            return round(min(1.0, deaf / STEP), 1)
        if self.loss is not None:
            return self.loss(target, lo, hi)
        return 0.1 if target == "CPE_IPv4" else 0.0  # the gateway's ICMP floor


class Pi:
    """A fake InfluxDB over a Case, and the clock."""

    def __init__(self, case: Case):
        self.case = case
        self.now = 0.0

    def _steps(self, seconds):
        first = int((self.now - seconds) // STEP + 1) * STEP
        return range(first, int(self.now) + 1, STEP)

    def points(self, seconds):
        rows = []
        for end in self._steps(seconds):
            when = datetime.fromtimestamp(end, tz=CHI)
            for target in DESTINATIONS + ["CPE_IPv4"]:
                value = self.case.ratio(target, end - STEP, end)
                if value is None:
                    continue
                rows.append({"target": target, "_value": value, "_time": when,
                             "category": CATEGORY[target],
                             "_measurement": ("dns_latency" if target in DNS
                                              else "latency")})
        return rows

    def _windows(self, spans, lo):
        """10 s samples inside ``spans`` over [lo, now], oldest first."""
        out = []
        for a, b in spans:
            t = int(max(a, lo) // 10 + 1) * 10
            while t <= min(b, self.now):
                out.append(t)
                t += 10
        return sorted(out)

    def query(self, src: str):
        hour = self.now - 3600
        if "watchdog_event" in src:
            return [{"_time": datetime.fromtimestamp(t, tz=CHI), "_value": msg,
                     "area": "wifi"} for t, msg in self.case.watchdog
                    if t <= self.now]
        if '"upgrade"' in src:
            return []
        if "target_labels" in src:
            return [{"target": t, "_value": v} for t, v in SERVICE.items()]
        if "signal_dbm" in src:  # the weakest signal of the hour
            return ([{"interface": "wlan0", "n": 60, "weak": 0, "min": -50.0}]
                    if self.case.wifi else [])
        if "host_uplink" in src:
            rows = []
            for a, b in self.case.route:
                if hour <= a <= self.now:
                    rows.append({"_time": datetime.fromtimestamp(a, tz=CHI),
                                 "previous": "wlan0", "interface": "", "kind": "lost"})
                if hour <= b <= self.now:
                    rows.append({"_time": datetime.fromtimestamp(b, tz=CHI),
                                 "previous": "none", "interface": "wlan0", "kind": "back"})
            return rows
        if "internal/debug" in src:  # this Pi's deaf samples
            return [{"_time": datetime.fromtimestamp(t, tz=CHI), "interface": "wlan0",
                     "rx_packets": 0, "associated": 1}
                    for t in self._windows(self.case.deaf, hour)]
        if "cpe_latency" in src:  # the 10 s first-hop probe, cut windows only
            return [{"_time": datetime.fromtimestamp(t, tz=CHI), "_value": 100.0,
                     "target": "CPE_IPv4", "protocol": "ipv4"}
                    for t in self._windows(self.case.cuts + self.case.deaf
                                           + self.case.route, hour)]
        if "wifi_link" in src:
            if self.case.wifi and 'r._field == "uplink"' in src:
                return [{"interface": "wlan0", "_value": 1}]
            return []
        if '"http_latency"' in src or 'r._field == "step"' in src:
            return []
        if "first()" in src:
            return [{"_value": 1}]
        seconds = int(re.search(r"range\(start: -(\d+)s\)", src).group(1))
        if "count()" in src:
            n = len([r for r in self.points(seconds) if r["_measurement"] == "latency"])
            return [{"_value": n}] if n else []
        if "mean()" in src:
            by = {}
            for row in self.points(seconds):
                by.setdefault((row["target"], row["category"]), []).append(row["_value"])
            return [{"target": t, "category": c, "_value": sum(v) / len(v)}
                    for (t, c), v in by.items()]
        return self.points(seconds)


@pytest.fixture
def replay(monkeypatch):
    monkeypatch.setenv("QUIET_FROM", "23:00")
    monkeypatch.setenv("QUIET_UNTIL", "07:00")
    monkeypatch.setenv("DIGEST_TZ", "America/Chicago")

    def run(case: Case, start: float, end: float):
        pi = Pi(case)
        current = {"incidents": {}, "reports": {}}
        sent = []
        monkeypatch.setattr(main.evaluator, "_query", pi.query)
        monkeypatch.setattr(time, "time", lambda: pi.now)
        monkeypatch.setattr(state, "load_state", lambda: current)
        monkeypatch.setattr(state, "save_state", lambda s: None)
        monkeypatch.setattr(notifier, "send",
                            lambda payload, image=None: sent.append(
                                (pi.now, dict(payload))) or notifier.SENT)
        monkeypatch.setattr(notifier, "reachable", lambda: True)
        monkeypatch.setattr(alert_diagnosis, "run", lambda *a, **k: None)
        for module, name in ((dead_man, "check_in"), (weekly, "check"),
                             (reports_watcher, "check"),
                             (incident_history, "maybe_record"),
                             (incident_history, "merge")):
            monkeypatch.setattr(module, name, lambda *a, **k: None)
        pi.now = start
        while pi.now <= end:
            main.run_iteration()
            pi.now += 60
        return sent, current

    return run


def kinds(sent):
    return [(p.get("kind") or p.get("type"), p.get("silent")) for _, p in sent]


def closed(current):
    return current["episodes"]["closed"]


def clock(epoch):
    return datetime.fromtimestamp(epoch, tz=CHI).strftime("%H:%M")


# ---------------------------------------------------------------------------
# The audited cases
# ---------------------------------------------------------------------------

def test_1009_the_pi_losing_its_route_for_30s_is_this_pi_and_not_pushed(replay):
    """21:21, 30 s without a default route. Before: a microcut alert about
    "the line to the ISP" and its recovery. Now: recorded, this Pi."""
    route = [(at(9, "21:21:00"), at(9, "21:21:30"))]
    sent, current = replay(Case(route=route), at(9, "21:00"), at(9, "22:15"))
    assert sent == []
    [episode] = closed(current)
    assert episode["class"] == "monitor"
    assert episode["cause"] == "this Pi (it lost its own default route)"
    assert not episode["material"]


def test_1011_eleven_hours_deaf_is_one_silent_monitor_message(replay):
    """03:40-14:35, the radio deaf (no watchdog yet). Before: an outage,
    uplink_down, their re-notifications and recoveries. Now: one silent
    message once it can measure, saying from when to when."""
    deaf = [(at(11, "03:40"), at(11, "14:35"))]
    sent, current = replay(Case(deaf=deaf), at(11, "03:00"), at(11, "15:30"))
    assert kinds(sent) == [("monitor", True)]
    when, message = sent[0]
    assert when >= at(11, "14:35")
    text = message["message"]
    assert "could not measure the network" in text
    assert "03:40" in text and "14:35" in text
    assert "this Pi's Wi-Fi radio stopped receiving" in text
    assert "unknown" in text
    [episode] = closed(current)
    assert episode["class"] == "monitor"


def test_1012_deaf_with_a_stalled_exporter_and_a_reconnection_cut_is_one_message(replay):
    """10:10-16:00 deaf 5 h 40 min; meanwhile the exporter stalled (no points
    at all) and the reconnection made a 10 s cut on the first hop. Before:
    three outage buckets, exporter_stale, two recoveries and a microcut
    "just that site". Now: one monitor message."""
    case = Case(deaf=[(at(12, "10:10"), at(12, "16:00"))],
                stalled=[(at(12, "12:00"), at(12, "12:40"))],
                cuts=[(at(12, "16:00:20"), at(12, "16:00:30"))])
    sent, current = replay(case, at(12, "09:50"), at(12, "17:00"))
    assert kinds(sent) == [("monitor", True)]
    assert "10:10" in sent[0][1]["message"] and "16:00" in sent[0][1]["message"]
    [episode] = closed(current)
    assert episode["class"] == "monitor"
    assert episode["signals"].get("exporter_stale")
    assert episode["signals"].get("microcut_burst")


def test_1014_one_destination_at_a_time_is_never_pushed(replay):
    """Netflix_5 for 10 min, UBA for 10 then 15 min. Before: four high_loss
    alerts and their recoveries. Now: recorded, nothing pushed."""
    spans = {"Netflix_5": [(at(14, "14:00"), at(14, "14:10"))],
             "UBA": [(at(14, "16:00"), at(14, "16:10")),
                     (at(14, "16:40"), at(14, "16:55"))]}

    def loss(target, lo, hi):
        if any(_overlap(lo, hi, a, b) for a, b in spans.get(target, [])):
            return 0.6
        return 0.1 if target == "CPE_IPv4" else 0.0

    sent, current = replay(Case(loss=loss), at(14, "13:50"), at(14, "17:45"))
    assert sent == []
    assert [e["class"] for e in closed(current)] == ["destination"] * len(closed(current))
    assert {s for e in closed(current) for s in e["sites"]} == {"Netflix_5", "UBA"}
    assert not any(e["material"] for e in closed(current))


def test_1015_a_40s_line_cut_on_12_destinations_is_a_recorded_microcut(replay):
    """12:35, the first hop cut for 40 s and 12 destinations lost with it.
    Your line -- and under a minute: recorded for the weekly, not pushed."""
    hit = set(DESTINATIONS[:12])
    cut = (at(15, "12:35:00"), at(15, "12:35:40"))

    def loss(target, lo, hi):
        if target in hit and _overlap(lo, hi, *cut):
            return 0.1
        return 0.1 if target == "CPE_IPv4" else 0.0

    sent, current = replay(Case(loss=loss, cuts=[cut]), at(15, "12:20"), at(15, "13:30"))
    assert sent == []
    [episode] = closed(current)
    assert episode["class"] == "local_link"
    assert episode["cause"].startswith("your line")
    assert episode["microcut"] and episode["longest_cut_s"] < 60


@pytest.mark.parametrize("day, deaf, reload_at", [
    (18, ("14:05", "14:22"), "14:22"),
    (28, ("09:00", "09:15"), "09:15"),
])
def test_deaf_minutes_and_a_driver_reload_are_one_silent_monitor_message(
        replay, day, deaf, reload_at):
    """10-18 and 10-28: deaf 15-20 min, the watchdog reloads the driver,
    then the outage tail, the stale window and the reconnection's microcut.
    Before: four to six messages, the cut as "just that site". Now: one."""
    case = Case(
        deaf=[(at(day, deaf[0]), at(day, deaf[1]))],
        cuts=[(at(day, reload_at) + 20, at(day, reload_at) + 30)],
        watchdog=[(at(day, reload_at), "Wi-Fi uplink wlan0 was deaf for 3 "
                   "checks; reloaded the driver (brcmfmac)")])
    sent, current = replay(case, at(day, deaf[0]) - 1200, at(day, deaf[1]) + 4200)
    assert kinds(sent) == [("monitor", True)]
    text = sent[0][1]["message"]
    assert "reloaded the Wi-Fi driver" in text
    assert "this Pi's Wi-Fi radio stopped receiving" in text
    [episode] = closed(current)
    assert episode["class"] == "monitor"


def test_1006_the_evening_loss_is_one_alert_and_one_silent_recovery(replay):
    """19:30-23:00, most destinations losing 2-8% with the first hop clean.
    No rule saw it then. Now: one material-degradation alert by 19:45,
    "your provider, beyond the modem", and one silent recovery with its
    duration."""
    def loss(target, lo, hi):
        if target == "CPE_IPv4":
            return 0.1
        if not _overlap(lo, hi, at(6, "19:30"), at(6, "23:00")):
            return 0.0
        i = DESTINATIONS.index(target) + int(hi // STEP)
        return 0.1 if i % 3 else 0.0

    sent, current = replay(Case(loss=loss), at(6, "19:00"), at(6, "23:50"))
    assert kinds(sent) == [("degradation", False), ("recovery", True)]
    (alerted, alert), (recovered, recovery) = sent
    assert at(6, "19:40") <= alerted <= at(6, "19:45")
    assert "Internet with packet loss" in alert["message"]
    assert "your provider, beyond the modem" in alert["message"]
    assert recovered > at(6, "23:00")
    assert "19:30 to 23:00" in recovery["message"]
    assert "3h 30m" in recovery["message"]


def test_an_internet_down_of_20_minutes_is_an_alert_and_a_recovery(replay):
    """Invented: the line down 10:00-10:20, the first hop with it, the radio
    receiving. One alert at 15 minutes that sounds, one silent recovery."""
    down = (at(20, "10:00"), at(20, "10:20"))

    def loss(target, lo, hi):
        if _overlap(lo, hi, *down):
            return 1.0
        return 0.1 if target == "CPE_IPv4" else 0.0

    sent, current = replay(Case(loss=loss), at(20, "09:40"), at(20, "11:30"))
    assert kinds(sent) == [("down", False), ("recovery", True)]
    (alerted, alert), (_, recovery) = sent
    assert alerted == at(20, "10:15")
    assert "Internet down" in alert["message"]
    assert "your line" in alert["message"]
    assert "10:00 to 10:20" in recovery["message"]
    assert "20 min" in recovery["message"]


def test_a_night_down_is_alerted_at_seven_if_still_open(replay):
    """Nothing sounds in quiet hours; still going on at 07:00, it is told
    then, as by day."""
    down = (at(21, "06:00"), at(21, "07:30"))

    def loss(target, lo, hi):
        if _overlap(lo, hi, *down):
            return 1.0
        return 0.1 if target == "CPE_IPv4" else 0.0

    sent, _ = replay(Case(loss=loss), at(21, "05:40"), at(21, "08:30"))
    assert kinds(sent) == [("down", False), ("recovery", True)]
    assert clock(sent[0][0]) == "07:00"


def test_a_night_down_that_ended_in_the_night_is_one_silent_bundle_at_seven(replay):
    down = (at(22, "02:00"), at(22, "02:40"))

    def loss(target, lo, hi):
        if _overlap(lo, hi, *down):
            return 1.0
        return 0.1 if target == "CPE_IPv4" else 0.0

    sent, _ = replay(Case(loss=loss), at(22, "01:40"), at(22, "07:10"))
    assert [(p["type"], p.get("silent")) for _, p in sent] == [("held", True)]
    assert clock(sent[0][0]) == "07:00"
