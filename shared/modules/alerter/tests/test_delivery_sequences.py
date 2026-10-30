"""Whole deliveries, replayed minute by minute: nothing is said twice.

test_sequences.py counts what the rules decide; these count what reaches the
phone. October 2026 on the reference Pi, from Telegram: six identical copies
of one microcut alert in one second (10/09), and twice an outage arriving as
a pair of identical messages one minute apart, minutes after the outbox
summary that had already told it (10/18, 10/28).

The real ``main.run_iteration`` runs once a minute, with the evaluator
scripted, the state file round-tripped through JSON, and the real notifier
talking to a fake OpenClaw gateway that keeps every request it receives in
its own durable queue and replays it later -- as the real one does, which is
where every copy came from.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

import main

T0 = datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc)  # 13:00 CDT
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class Answer:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = {"ok": True} if body is None else body
        self.text = ""

    def json(self):
        return self._body


class Gateway:
    """OpenClaw's /tools/invoke, and the phone behind it.

    ``mode(minute)`` says what it does with a request:

    - ``up``: delivers it to the phone and answers ok;
    - ``down``: unreachable, nothing received (ConnectError);
    - ``stalled``: receives it, keeps it in its queue, and answers after
      the alerter's 10 s timeout (ReadTimeout);
    - ``deaf``: Telegram is out of reach: it keeps it, and answers the
      first request after the timeout, the rest at once with ok:false;
    - ``flaky``: delivers it, and answers ok:false all the same.

    ``replay_at`` lists the minutes it replays its queue, one copy per
    minute; ``replay_all_at``, the minute it replays all of it at once.
    """

    def __init__(self, monkeypatch, mode, replay_at=(), replay_all_at=None):
        self.mode = mode
        self.minute = 0
        self.queue = []
        self.phone = []
        self.replay_at = list(replay_at)
        self.replay_all_at = replay_all_at
        self._deaf_answered = False
        monkeypatch.setattr(main.notifier.httpx, "post", self.post)
        monkeypatch.setattr(main.notifier.time, "sleep", lambda s: None)

    @staticmethod
    def text(payload):
        args = payload["args"]
        return args.get("message") or args.get("caption") or ""

    def post(self, url, json=None, headers=None, timeout=None):
        mode = self.mode(self.minute)
        if mode == "down":
            raise httpx.ConnectError("refused")
        if mode == "up":
            self.phone.append((self.minute, self.text(json)))
            return Answer()
        if mode == "flaky":
            self.phone.append((self.minute, self.text(json)))
            return Answer(200, {"ok": False, "error": {"message": "timeout"}})
        self.queue.append(self.text(json))
        if mode == "stalled" or not self._deaf_answered:
            self._deaf_answered = mode == "deaf"
            raise httpx.ReadTimeout("no answer in 10 s")
        return Answer(200, {"ok": False, "error": {"message": "network"}})

    def tick(self, minute):
        self.minute = minute
        if self.mode(minute) != "deaf":
            self._deaf_answered = False
        if minute == self.replay_all_at:
            self.phone += [(minute, text) for text in self.queue]
            self.queue = []
        elif minute in self.replay_at and self.queue:
            self.phone.append((minute, self.queue.pop(0)))


class Loop:
    """run_iteration once a minute over a script of incidents."""

    def __init__(self, monkeypatch, tmp_path, incidents, reachable,
                 watchdog=None, start=T0):
        self.incidents = incidents
        self.watchdog = watchdog or (lambda minute: [])
        self.start = start
        self.minute = 0
        monkeypatch.setenv("ALERT_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("NOTIFY_MODE", "openclaw")
        monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "t")
        monkeypatch.setenv("OPENCLAW_TO", "telegram:1")
        monkeypatch.setattr(main.evaluator, "evaluate_with_context", self.evaluate)
        monkeypatch.setattr(main.verdict, "classify", lambda *a, **k: {})
        monkeypatch.setattr(main.alert_diagnosis, "run", lambda cadences: None)
        monkeypatch.setattr(
            main, "_chart_for",
            lambda event, peers: PNG if event.get("target") else None)
        monkeypatch.setattr(main.watchdog_events, "collect",
                            lambda current, query: self.watchdog(self.minute))
        monkeypatch.setattr(main.notifier, "reachable",
                            lambda: reachable(self.minute))
        monkeypatch.setattr(main.time, "time", lambda: self.now)
        monkeypatch.setattr(main.dead_man, "check_in", lambda ok, now=None: None)
        for module in (main.incident_history, main.digest, main.reports_watcher):
            for name in ("merge", "maybe_record", "check"):
                if hasattr(module, name):
                    monkeypatch.setattr(module, name, lambda *a, **k: None)

    @property
    def now(self):
        return (self.start + timedelta(minutes=self.minute)).timestamp()

    def evaluate(self, **_):
        incidents = [dict(i) for i in self.incidents(self.minute)]
        return incidents, {
            "mean_rows": [], "micro_rows": [], "wifi_rows": {},
            "uplink_changes": [], "cadences": {},
            "windows": {"mean": 900, "step": 300, "stale": 1200},
            "judged": {"NYT", "136.25.220.1"},
        }

    def run(self, minutes, gateway):
        for minute in range(minutes):
            self.minute = minute
            gateway.tick(minute)
            main.run_iteration()


def told(phone, message):
    """How many messages on the phone say ``message``, alone or in a summary."""
    return sum(1 for _, text in phone if message in text)


def _outage(key, message):
    return {"rule": "outage", "severity": "warning", "key": key, "target": None,
            "transient": True, "message": message, "value": 10}


# --- 2026-10-09: six copies of one alert in one second ----------------------

def test_a_stalled_gateway_gets_one_copy_not_six(monkeypatch, tmp_path):
    """The microcut alert went out with its chart while the gateway could
    not reach Telegram and answered after the alerter's timeout. Before:
    three attempts with the chart, then three text-only, each one kept in
    the gateway's queue -- six identical messages the moment it drained
    it, and the outbox's own copy after them."""
    message = "CPE 136.25.220.1: 14 cuts over 50% loss in 15 min"
    microcut = {"rule": "microcut_burst", "severity": "warning",
                "key": "microcut_burst:136.25.220.1/ipv4",
                "target": "136.25.220.1", "message": message, "value": 14}
    loop = Loop(monkeypatch, tmp_path,
                incidents=lambda m: [microcut] if 2 <= m < 60 else [],
                reachable=lambda m: not 2 <= m < 30)
    gateway = Gateway(monkeypatch,
                      mode=lambda m: "stalled" if m < 30 else "up",
                      replay_all_at=30)
    loop.run(45, gateway)

    assert told(gateway.phone, message) == 1, gateway.phone
    assert len(gateway.phone) == 1


# --- 2026-10-18 13:43 CDT: the summary, then the same outage twice ----------

def test_a_summary_then_nothing_it_or_the_gateway_already_told(monkeypatch, tmp_path):
    """The radio went deaf; two outages and the watchdog's driver reload
    were decided while nothing could leave. The outbox summary told them
    when the Pi was back -- and 4-5 minutes later the first outage arrived
    again, twice, identical, one minute apart: the gateway had kept the
    attempt that opened the outbox, and its in-cycle retry.

    Then a new outage, sent while the gateway delivered and still answered
    ok:false: before, the outbox sent it again a minute later -- the same
    pair, identical text, consecutive messages one minute apart."""
    first = "10 of 10 targets lost every packet in the same 10-minute span at 18:06"
    second = "9 of 10 targets lost packets in the same 5-minute span at 18:18"
    later = "8 of 10 targets lost packets in the same 5-minute span at 18:40"
    reload = "the watchdog on this host, at 18:22 UTC: wifi: driver reloaded"

    def incidents(m):
        out = []
        if 6 <= m < 16:
            out.append(_outage("outage:1", first))
        if 18 <= m < 25:
            out.append(_outage("outage:2", second))
        if 40 <= m < 45:
            out.append(_outage("outage:3", later))
        return out

    def watchdog(m):
        if m != 23:
            return []
        return [{"type": "alert", "rule": "watchdog", "severity": "info",
                 "key": "watchdog:wifi:1", "target": None, "message": reload}]

    def mode(m):
        if 5 <= m < 31:
            return "deaf"
        if m == 40:
            return "flaky"
        return "up"

    loop = Loop(monkeypatch, tmp_path, incidents,
                reachable=lambda m: not 5 <= m < 31, watchdog=watchdog)
    gateway = Gateway(monkeypatch, mode, replay_at=(36, 37))
    loop.run(60, gateway)

    summaries = [t for _, t in gateway.phone if "Not delivered" in t]
    assert len(summaries) == 1
    assert second in summaries[0] and reload in summaries[0]
    for message in (first, second, reload, later):
        assert told(gateway.phone, message) == 1, (message, gateway.phone)


# --- a plain recovery after a held alert --------------------------------------

def test_a_recovery_after_a_held_alert_arrives_once(monkeypatch, tmp_path):
    """NYT goes down at night: held, and hourly re-notifications held with
    it. The morning summary tells it. NYT answers again after 07:00: the
    recovery of an incident told only in a summary still goes, once --
    even when the gateway delivers it and answers ok:false."""
    monkeypatch.setenv("QUIET_FROM", "23:00")
    monkeypatch.setenv("QUIET_UNTIL", "07:00")
    monkeypatch.setenv("DIGEST_TZ", "UTC")
    start = datetime(2026, 10, 18, 22, 50, tzinfo=timezone.utc)
    down = {"rule": "target_down", "severity": "critical", "key": "target_down:NYT",
            "target": "NYT", "message": "NYT down: 100% loss in 3 cycles", "value": 1.0}
    back = 8 * 60 + 15  # 07:05

    loop = Loop(monkeypatch, tmp_path,
                incidents=lambda m: [down] if 40 <= m < back else [],
                reachable=lambda m: True, start=start)
    recovery_minute = []

    def mode(m):
        return "flaky" if recovery_minute and m == recovery_minute[0] else "up"

    gateway = Gateway(monkeypatch, mode)
    for minute in range(back + 60):
        loop.minute = minute
        gateway.tick(minute)
        if minute >= back + 15 and not recovery_minute:
            recovery_minute.append(minute)  # the recovery is decided here
        main.run_iteration()

    summaries = [t for _, t in gateway.phone if "Quiet hours" in t]
    assert len(summaries) == 1 and down["message"] in summaries[0]
    recoveries = [t for _, t in gateway.phone if t.startswith("✅")]
    assert len(recoveries) == 1, gateway.phone
    assert len(gateway.phone) == 2


@pytest.fixture(autouse=True)
def _no_mutes(monkeypatch):
    monkeypatch.setattr(main.state.mutes, "load", lambda: [])
