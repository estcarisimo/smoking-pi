"""The weekly message, byte for byte, as it was before its data moved.

The week's reading and judging moved to ``common/weekly.py`` so that the
MCP server's ``get_weekly_summary`` returns the same week as data. The
message the alerter sends was not to change by one byte: these cases were
rendered by the alerter as it was before the move (v2.51.0) and stored in
``data/weekly_golden.json``. Set ``WEEKLY_GOLDEN_WRITE=1`` to rewrite the
file -- only for a change that means to change the message.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_weekly import SLOT, START, STOP, Influx, _episode, _incident, _record, _t

import weekly

GOLDEN = Path(__file__).parent / "data" / "weekly_golden.json"


@pytest.fixture(autouse=True)
def zone(monkeypatch):
    monkeypatch.setenv("DIGEST_TZ", "America/Chicago")


def _stats(*a, **k):
    return [
        {"target": "Netflix_h3", "avg_loss_pct_measurable": 7.2, "median_ms": 31.0},
        {"target": "uba_ar", "avg_loss_pct_measurable": 12.5},
        {"target": "Google", "avg_loss_pct_measurable": 4.9},
    ]


def _patterns(state="active", gone_at=None):
    first = _t(12, 19, 30).timestamp()
    item = {"id": "1930-2300-upstream", "from": "19:30", "to": "23:00",
            "tz": "America/Chicago", "class": "upstream", "cause": "your provider",
            "part": "evening", "state": state, "created_day": "2026-10-14",
            "created_at": _t(14, 23).timestamp(), "first_seen": first,
            "last_seen": _t(30, 22).timestamp(), "days_seen": 17,
            "days_observed": 21, "affected": 22, "reporting": 30,
            "days": {f"2026-10-{d:02d}": ("seen" if d % 3 else "clear")
                     for d in range(12, 32)}}
    if gone_at is not None:
        item["gone_at"] = gone_at
    return {"since": START - 30 * 86400, "items": [item]}


def _cases():
    reboot = [{"_time": _t(27, 3), "_value": "wifi: deaf; reloading the brcmfmac driver"},
              {"_time": _t(30, 4), "_value": "wifi: deaf; rebooting"},
              {"_time": _t(31, 5), "_value": "smokeping: stopped; restarting it"}]
    upgrade = [{"_time": _t(29, 10), "phase": "start"},
               {"_time": _t(29, 10, 12), "phase": "end"}]
    shared = [{"_time": _t(d, 20), "end": _t(d, 21, 50).timestamp(),
               "_measurement": "shared_degradation", "class": "loss"}
              for d in (26, 27, 29, 30)]
    slow = [{"_time": _t(28, 19), "end": _t(28, 20).timestamp(),
             "_measurement": "shared_congestion", "class": ""}]
    return {
        "line_cut": dict(query=Influx(incidents=[_incident(_t(28, 20, 40), 31)])),
        "quiet": dict(query=Influx()),
        "deaf_and_no_data": dict(query=Influx(
            incidents=[_incident(_t(27, 1), 5 * 60 + 40, cls="local_wifi")],
            bins=int((STOP - START) // weekly.STEP_S) - 36, watchdog=reboot,
            upgrades=upgrade)),
        "recurring_new": dict(query=Influx(shared=shared)),
        "recurring_gone": dict(query=Influx(before=[
            _incident(_t(d - 7, 20), 60, max_loss=30.0) for d in (26, 27, 28, 29)])),
        "slowdown_and_tops": dict(query=Influx(
            shared=slow, medians=[0.050, 0.020],
            incidents=[_incident(_t(29, 9), 95, cls="upstream", affected=25),
                       _incident(_t(30, 13), 20, cls="destination", affected=12),
                       _incident(_t(30, 15), 40, cls="unclear", affected=10),
                       _incident(_t(31, 2), 18, cls="local_link")],
            before=[_incident(_t(21, 9), 300, cls="upstream", affected=25)]),
            stats=True),
        "alerter_record": dict(query=Influx(), record=_record(
            _episode(_t(28, 19, 30), 210),
            _episode(_t(29, 16), 10, cls="destination", material=False,
                     broad_cycles=0),
            _episode(_t(27, 3, 40), 65, cls="monitor", detail="radio",
                     material=False),
            _episode(_t(30, 12), 40, cls="local_link", loss_pct=11.0))),
        "pattern_active": dict(query=Influx(), record=_record(
            _episode(_t(28, 19, 30), 210, pattern="1930-2300-upstream")),
            known=_patterns()),
        "pattern_gone": dict(query=Influx(), record=_record(),
                             known=_patterns("gone", _t(31, 9).timestamp())),
    }


def _render_all(monkeypatch) -> dict:
    monkeypatch.setenv("PUBLIC_BASE_HOST", "192.168.1.10")
    out = {}
    for name, case in _cases().items():
        monkeypatch.setattr(weekly.aggregates, "target_stats",
                            _stats if case.get("stats") else (lambda *a, **k: []))
        payload = weekly.build(SLOT, case["query"], case.get("record"),
                               case.get("known"))
        out[name] = {"send": payload["send"], "message": payload.get("message"),
                     "links": payload.get("links")}
    return out


def test_the_weekly_message_is_byte_identical_to_v2_51_0(monkeypatch):
    got = _render_all(monkeypatch)
    if os.environ.get("WEEKLY_GOLDEN_WRITE") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        pytest.skip("golden file rewritten")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(got) == set(want)
    for name in want:
        assert got[name] == want[name], name
    # The cases mean something: most of them send, one does not.
    assert sum(1 for v in want.values() if v["send"]) >= 7
    assert not want["quiet"]["send"]


def test_the_cases_cover_every_section(monkeypatch):
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    text = "\n".join(v["message"] or "" for v in want.values())
    for section in ("What happened", "Recurring", "The monitor", "Destinations",
                    "below the bar", "did not come back", "gone (",
                    "The watchdog reloaded", "upgrade", "with no measurement"):
        assert section in text, section
