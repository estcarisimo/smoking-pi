"""Recurring windows, replayed: the evening loss as one fact, not 38.

From 2026-08-30 to 2026-10-06 most destinations on the reference Pi lost a
few percent of their pings from 19:30 to 23:00 America/Chicago every
evening. No message named it (0 on 10-01...10-05, then 12 per destination
on 10-06). Replayed here through the real ``main.run_iteration``, as
test_october.py does, over 2026-09-28 -> 2026-10-09: the window every
evening until 10-06, 14 of 18 destinations losing 7-10 % (5-8 % mean over
every destination), then three quiet evenings. Minute by minute from 19:00
to 00:00, every 15 minutes otherwise.

The pattern logic itself is tested on synthetic episodes in
common/tests/test_patterns.py.
"""

from __future__ import annotations

import time
from datetime import datetime
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
from test_october import NETFLIX, STEP, TOPSITES, Case, Pi, _overlap

CHI = ZoneInfo("America/Chicago")
REPORTING = TOPSITES + NETFLIX  # 18; the resolvers do not report here
LOSSY = REPORTING[:14]


def when(month: int, day: int, hhmm: str) -> float:
    hour, minute = hhmm.split(":")
    return datetime(2026, month, day, int(hour), int(minute), tzinfo=CHI).timestamp()


def evenings(*days: tuple[int, int]):
    """The loss of a step: 14 of 18 destinations in 19:30-23:00 on ``days``
    ((month, day)), a level that changes with the day; the first hop clean."""
    def loss(target, lo, hi):
        if target == "CPE_IPv4":
            return 0.1
        if target not in REPORTING:
            return None
        local = datetime.fromtimestamp(lo, tz=CHI)
        key = (local.month, local.day)
        if (key in days and target in LOSSY
                and _overlap(lo, hi, when(*key, "19:30"), when(*key, "23:00"))):
            return 0.07 + 0.01 * (local.day % 4)
        return 0.0
    return loss


class EveningPi(Pi):
    def query(self, src: str):
        if "shared_degradation" in src:
            return []
        return super().query(src)


@pytest.fixture
def replay(monkeypatch):
    monkeypatch.setenv("QUIET_FROM", "23:00")
    monkeypatch.setenv("QUIET_UNTIL", "07:00")
    monkeypatch.setenv("DIGEST_TZ", "America/Chicago")

    def run(case: Case, start: float, end: float):
        pi = EveningPi(case)
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
            hour = datetime.fromtimestamp(pi.now, tz=CHI).hour
            pi.now += 60 if hour >= 19 else 900
        return sent, current

    return run


def day_of(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=CHI).strftime("%m-%d")


def tally(sent):
    return [(day_of(t), p.get("kind"), p.get("silent")) for t, p in sent]


WINDOW = [(9, d) for d in (28, 29, 30)] + [(10, d) for d in range(1, 7)]


# The replay is the slow part (about 6000 cycles): run once, read by every test.
_RESULT = {}


def _nightly(replay):
    if "out" not in _RESULT:
        _RESULT["out"] = replay(Case(loss=evenings(*WINDOW)),
                                when(9, 28, "00:00"), when(10, 9, "23:59"))
    return _RESULT["out"]


def test_the_nightly_window_is_two_alerts_one_pattern_and_its_end(replay):
    sent, current = _nightly(replay)
    assert tally(sent) == [
        ("09-28", "degradation", False), ("09-28", "recovery", True),
        ("09-29", "degradation", False), ("09-29", "recovery", True),
        ("09-30", "pattern_new", True),
        ("10-09", "pattern_gone", True),
    ]
    new = sent[4][1]
    assert new["type"] == "alert" and new["rule"] == "pattern"
    text = new["message"]
    assert text.startswith("🔁 For the 3rd evening in a row, packet loss on most "
                           "destinations from 19:30 to 23:00.")
    assert "Looks like your provider at peak hour (high confidence)." in text
    assert "3 of 3 evenings, 14 of 18 destinations" in text
    assert "5–6 % loss." in text
    assert "With this you can complain to them" in text
    # Announced when the third evening became material, as an alert would.
    assert when(9, 30, "19:40") <= sent[4][0] <= when(9, 30, "19:50")
    gone = sent[5][1]
    assert gone["message"].startswith(
        "✅ The evening loss window did not appear in 3 days; last seen Tuesday.")
    assert "seen on 9 of 12 evenings" in gone["message"]
    assert sent[5][0] > when(10, 9, "23:00")


def test_the_evenings_inside_the_pattern_are_recorded_as_part_of_it(replay):
    _, current = _nightly(replay)
    closed = current["episodes"]["closed"]
    evening = [e for e in closed if e.get("class") == "upstream"]
    assert len(evening) == 9
    first_two, rest = evening[:2], evening[2:]
    assert all(e["material"] and e["pushed"] for e in first_two)
    [pattern] = current["patterns"]["items"]
    assert all(e["pattern"] == pattern["id"] and not e["pushed"]
               and e["unmet"] == ["novelty"] for e in rest)
    assert pattern["id"] == "1930-2300-upstream"
    assert pattern["state"] == "gone"
    assert pattern["days_seen"] == 9 and pattern["days_observed"] == 12
    assert pattern["evidence"] == [e["id"] for e in evening]


def test_the_ledger_says_why_an_evening_was_not_sent(replay, monkeypatch):
    _, current = _nightly(replay)
    ep = current["episodes"]["closed"][-1]
    assert ep["pattern"] == "1930-2300-upstream"
    import policy
    line = policy.recorded({"id": ep["id"], "key": ep["key"], "start": ep["start"],
                            "end": ep["end"], "judgment": {"class": "upstream",
                                                           "cause": ep["cause"]},
                            "pattern": ep["pattern"], "signals": {},
                            "policy": {}, "materiality": {"unmet": ["novelty"]}})
    assert "part of pattern 1930-2300-upstream" in line["message"]


def test_the_weekly_of_0928_names_the_pattern_with_its_count(replay, monkeypatch):
    _, current = _nightly(replay)
    monkeypatch.setenv("DIGEST_TZ", "America/Chicago")
    slot = when(10, 5, "08:00")
    start, stop = when(9, 28, "00:00"), when(10, 5, "00:00")
    windows, gone = weekly.pattern_windows(current["patterns"], start, stop, CHI)
    assert gone == []
    [window] = windows
    assert (window["days"], window["observed"], window["new"]) == (7, 7, True)
    monkeypatch.setattr(weekly.aggregates, "target_stats", lambda *a, **k: [])
    bins = int((stop - start) // weekly.STEP_S)

    def influx(flux):
        if "aggregateWindow" in flux:
            first = start if weekly._rfc3339(start) in flux.split("stop:")[0] else start - 7 * 86400
            return [{"_time": first + (i + 1) * weekly.STEP_S, "_value": 10}
                    for i in range(bins)]
        return []

    payload = weekly.build(slot, influx, current["episodes"], current["patterns"])
    assert payload["send"]
    assert ("19:30–23:00: packet loss on most destinations, 7 evenings of 7 "
            "(your provider; new this week)") in payload["message"]
    # The evenings inside the pattern are its, not "smaller episodes".
    assert "below the bar" not in payload["message"]
    # The week after: two more evenings, then gone.
    windows, gone = weekly.pattern_windows(current["patterns"], stop,
                                           when(10, 12, "00:00"), CHI)
    assert [w["days"] for w in windows] == [2]
    assert len(gone) == 1
    assert "9 evenings of 12 since 28 Sep" in weekly._pattern_line(windows[0], stop)
