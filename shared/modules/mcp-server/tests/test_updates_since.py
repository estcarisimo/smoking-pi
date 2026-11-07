"""get_updates_since: what changed, checked against what was said.

The alerter's state file and the ledger are written to a temporary
directory; no InfluxDB and no config API (the conftest stubs the one and
the service lookup falls back to base names without the other).
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import server
from common import notification_ledger, updates

CHI = ZoneInfo("America/Chicago")
NOW = time.time()
H = 3600.0


def _closed(i, ago_h, minutes=20, cls="upstream", material=True, pushed=(), **extra):
    end = NOW - ago_h * H
    return {"id": i, "key": f"episode:{i}", "start": end - minutes * 60, "end": end,
            "closed_at": end + 600, "class": cls, "detail": extra.pop("detail", None),
            "cause": extra.pop("cause", "your provider, beyond the modem"),
            "confidence": "high", "material": material,
            "unmet": extra.pop("unmet", [] if material else ["breadth"]),
            "pattern": extra.pop("pattern", None), "loss_pct": 9.4, "down": False,
            "microcut": extra.pop("microcut", False), "blind_s": extra.pop("blind_s", 0),
            "affected": extra.pop("affected", 20), "reporting": 30,
            "breadth_pct": 66.7, "sites": extra.pop("sites", []),
            "pushed": [{"kind": k, "at": end} for k in pushed], **extra}


def _ledger(key, ago_h, kind="alert", outcome="handed", type_="alert"):
    return {"id": f"{key}-{ago_h}", "kind": kind, "type": type_, "key": key,
            "outcome": outcome, "decided_at": NOW - ago_h * H,
            "text": f"<b>{key}</b> said"}


@pytest.fixture()
def files(monkeypatch, tmp_path):
    monkeypatch.setenv("ALERT_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("ALERT_MUTES_FILE", str(tmp_path / "mutes.json"))
    monkeypatch.setenv("DIGEST_TZ", "America/Chicago")

    def write(state=None, ledger=()):
        state = state or {}
        state.setdefault("episodes", {"seq": 50, "open": None, "closed": [],
                                      "since": NOW - 20 * 86400})
        (tmp_path / "state.json").write_text(json.dumps(state))
        with open(tmp_path / notification_ledger.FILENAME, "w") as fh:
            for line in ledger:
                fh.write(json.dumps(line) + "\n")

    return write


def _episodes(*closed, open_=None):
    return {"episodes": {"seq": 50, "open": open_, "closed": list(closed),
                         "since": NOW - 20 * 86400}}


# --- the three verdicts ------------------------------------------------------


def test_nothing_new_on_a_quiet_hour(files):
    files(_episodes(_closed(1, 30)))  # a day ago: outside the window
    out = server.get_updates_since(since="1h")
    assert out["verdict"] == "nothing_new" and out["reasons"] == []
    assert out["material"] == [] and out["non_material"]["total"] == 0
    assert out["tz"] == "America/Chicago"
    assert datetime.fromisoformat(out["as_of"]).utcoffset() is not None


def test_a_material_episode_already_pushed_is_not_repeated(files):
    files(_episodes(_closed(7, 0.5, pushed=("degradation", "recovery"),
                            sites=["Netflix_h3"])),
          ledger=[_ledger("episode:7", 0.8), _ledger("episode:7", 0.5, kind="recovery")])
    out = server.get_updates_since(since="1h")
    assert out["verdict"] == "material_pushed_already"
    [ep] = out["material"]
    assert ep["reported"] is True and ep["pushed"] == ["degradation", "recovery"]
    assert ep["state"] == "closed" and ep["minutes"] == 20
    assert ep["scope"]["destinations"] == "20 of 30"
    # The service, not the internal target name.
    assert ep["scope"]["services"] == ["Netflix"]
    assert [p["key"] for p in out["already_pushed"]] == ["episode:7", "episode:7"]
    assert out["already_pushed"][0]["headline"] == "episode:7 said"


def test_a_cluster_of_small_episodes_is_worth_mentioning(files):
    small = [_closed(10 + i, 2.0 - i * 0.2, minutes=5, cls="destination",
                     material=False, affected=1) for i in range(5)]
    files(_episodes(*small))
    out = server.get_updates_since(since="3h")
    assert out["verdict"] == "worth_mentioning"
    assert out["reasons"] == ["non_material_cluster"]
    nm = out["non_material"]
    assert nm["total"] == 5 and nm["largest_cluster"] == 5
    [row] = nm["by_kind"]
    assert row["kind"] == "packet_loss_destination"
    assert row["text"] == "5 short packet-loss episodes on one or two destinations"
    assert row["below_bar"] == {"breadth": 5}


# --- each reason, and its edges ----------------------------------------------


def test_small_episodes_are_counted_by_the_class_the_alerter_judged(files):
    small = [_closed(40, 5, minutes=4, cls="local_link", material=False, affected=0),
             _closed(41, 9, minutes=6, cls="local_link", material=False, affected=0),
             _closed(42, 12, minutes=3, cls="undetermined", material=False, affected=0),
             _closed(43, 14, minutes=1, cls="local_link", material=False, microcut=True)]
    files(_episodes(*small))
    rows = {r["kind"]: r for r in server.get_updates_since(since="24h")
            ["non_material"]["by_kind"]}
    assert rows["packet_loss_line"]["text"] == "2 short packet-loss episodes on your line"
    assert rows["packet_loss_line"]["minutes"] == 10
    assert rows["packet_loss_unclear"]["count"] == 1
    assert rows["microcut"]["text"] == "1 microcuts (cuts under a minute)"


def test_spread_out_small_episodes_are_background(files):
    # Eight a day, three hours apart: the reference Pi's ordinary day.
    small = [_closed(10 + i, 1 + 3 * i, minutes=5, cls="destination",
                     material=False, affected=1) for i in range(8)]
    files(_episodes(*small))
    out = server.get_updates_since(since="24h")
    assert out["verdict"] == "nothing_new"
    assert out["non_material"]["total"] == 8
    assert out["non_material"]["largest_cluster"] == 1


def test_non_material_listing_can_be_left_out_but_still_counts(files):
    small = [_closed(10 + i, 0.9 - i * 0.1, minutes=3, cls="destination",
                     material=False, affected=1) for i in range(4)]
    files(_episodes(*small))
    out = server.get_updates_since(since="1h", include_non_material=False)
    assert "by_kind" not in out["non_material"]
    assert out["verdict"] == "worth_mentioning"


def test_monitor_blindness_unreported_is_worth_mentioning(files):
    deaf = _closed(20, 0.3, minutes=40, cls="monitor", detail="radio",
                   material=False, blind_s=40 * 60,
                   watchdog_actions=[[NOW - 0.5 * H, "wifi: deaf; reloading the driver"]])
    files(_episodes(deaf))
    out = server.get_updates_since(since="1h")
    assert out["verdict"] == "worth_mentioning"
    assert out["reasons"] == ["monitor_blind_unreported"]
    [blind] = out["monitor"]["blind"]
    assert blind["minutes"] == 40 and blind["cause"] == "the Pi's Wi-Fi radio was deaf"
    assert blind["watchdog_actions"][0]["text"].startswith("wifi: deaf")


def test_monitor_blindness_already_told_or_short_is_not(files):
    told = _closed(20, 0.3, minutes=40, cls="monitor", detail="upgrade",
                   material=False, blind_s=40 * 60)
    short = _closed(21, 0.1, minutes=10, cls="monitor", detail="radio",
                    material=False, blind_s=600)
    files(_episodes(told, short), ledger=[_ledger("episode:20", 0.2, kind="monitor")])
    out = server.get_updates_since(since="1h")
    assert out["verdict"] == "material_pushed_already"
    assert out["monitor"]["blind_minutes"] == 50
    assert {b["cause"] for b in out["monitor"]["blind"]} == {
        "an upgrade of the Pi's software", "the Pi's Wi-Fi radio was deaf"}


def test_a_material_episode_nothing_was_pushed_about_is_worth_mentioning(files):
    files(_episodes(_closed(8, 0.4)))
    out = server.get_updates_since(since="1h")
    assert out["reasons"] == ["material_unreported"]
    assert out["verdict"] == "worth_mentioning"


def test_a_muted_material_episode_is_the_users_choice(files, tmp_path):
    files(_episodes(_closed(8, 0.4)))
    (tmp_path / "mutes.json").write_text(json.dumps({"mutes": [
        {"key": "episode:8", "until": NOW + H, "created_at": NOW - H}]}))
    out = server.get_updates_since(since="1h")
    assert out["material"][0]["muted"] is True
    assert out["verdict"] == "nothing_new"


def _pattern(state="active", **extra):
    return {"id": "1930-2300-upstream", "from": "19:30", "to": "23:00",
            "from_min": 1170, "to_min": 1380, "tz": "America/Chicago",
            "class": "upstream", "cause": "your provider", "confidence": "high",
            "state": state, "created_at": NOW - 5 * 86400, "created_day": "2026-11-01",
            "first_seen": NOW - 8 * 86400, "last_seen": NOW - 20 * H,
            "days_seen": 6, "days_observed": 8, "days": {}, **extra}


def test_a_pattern_that_appeared_unsaid_is_worth_mentioning(files):
    files({**_episodes(), "patterns": {"items": [_pattern(
        state="new", created_at=NOW - 0.5 * H, last_seen=NOW - 0.6 * H)]}})
    out = server.get_updates_since(since="1h")
    assert out["verdict"] == "worth_mentioning" and out["reasons"] == ["pattern_change"]
    [p] = out["patterns"]
    assert p["change"] == "appeared" and p["reported"] is False
    assert p["hours"] == "19:30-23:00 America/Chicago"


def test_a_pattern_change_the_alerter_said_or_queued_is_not(files):
    gone = _pattern(state="gone", gone_at=NOW - 0.5 * H)
    files({**_episodes(), "patterns": {"items": [gone]}},
          ledger=[_ledger("pattern:1930-2300-upstream", 0.4, kind="pattern",
                          type_="recovery")])
    out = server.get_updates_since(since="1h")
    assert out["patterns"][0]["change"] == "disappeared"
    assert out["patterns"][0]["reported"] is True
    assert out["verdict"] == "material_pushed_already"
    # Queued: the alerter says one pattern change a day, this one is next.
    files({**_episodes(), "patterns": {"items": [gone], "pending": [
        {"event": "gone", "id": "1930-2300-upstream"}]}})
    out = server.get_updates_since(since="1h")
    assert out["patterns"][0]["queued"] is True
    assert out["verdict"] == "material_pushed_already"


def test_a_pattern_that_came_back_is_counted_not_news(files):
    files({**_episodes(_closed(30, 0.2, pattern="1930-2300-upstream")),
           "patterns": {"items": [_pattern(last_seen=NOW - 0.2 * H)]}})
    out = server.get_updates_since(since="1h")
    assert [p["change"] for p in out["patterns"]] == ["repeated"]
    # Its evening is the pattern's: not material, not a small episode.
    assert out["material"] == [] and out["non_material"]["total"] == 0
    assert out["verdict"] == "nothing_new"


def test_the_open_episode_is_opened_or_ongoing(files):
    open_ep = {"id": 9, "key": "episode:9", "start": NOW - 0.5 * H, "end": NOW - 60,
               "loss_start": NOW - 0.5 * H, "material": True,
               "judgment": {"class": "local_link", "cause": "your line",
                            "confidence": "medium"},
               "facts": {"affected": 25, "reporting": 30, "breadth_pct": 83.3},
               "policy": {"sent": [{"kind": "degradation", "at": NOW - 0.4 * H}]}}
    files(_episodes(open_=open_ep))
    out = server.get_updates_since(since="1h")
    [ep] = out["material"]
    assert ep["state"] == "opened" and ep["reported"] is True
    assert out["verdict"] == "material_pushed_already"
    out = server.get_updates_since(since="10m")
    assert out["material"][0]["state"] == "ongoing"


def test_a_record_younger_than_since_says_so(files):
    files({"episodes": {"seq": 1, "open": None, "closed": [], "since": NOW - H}})
    out = server.get_updates_since(since="24h")
    assert "earlier minutes are unknown" in out["note"]


def test_heard_half_hours_come_from_the_alerters_marks(files):
    local = datetime.fromtimestamp(NOW, tz=CHI)
    slots = [1] * 48
    files({**_episodes(), "patterns": {"items": [], "heard": {
        local.date().isoformat(): slots}}})
    out = server.get_updates_since(since="1h")
    heard = out["monitor"]["heard"]
    assert heard["of"] in (1, 2) and heard["half_hours"] <= heard["of"]


# --- since ----------------------------------------------------------------


@pytest.mark.parametrize("text, back", [("1h", H), ("90m", 5400), ("24h", 24 * H),
                                         ("2d", 2 * 86400), (" 1 H ", H)])
def test_relative_since(text, back):
    since, problem = updates.parse_since(text, NOW, CHI)
    assert problem is None and since == pytest.approx(NOW - back)


def test_iso_since_with_and_without_an_offset():
    since, _ = updates.parse_since("2026-11-07T08:00:00-06:00", 1794100000.0, CHI)
    assert since == datetime(2026, 11, 7, 14, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    naive, _ = updates.parse_since("2026-11-07T08:00:00", 1794100000.0, CHI)
    assert naive == since
    z, _ = updates.parse_since("2026-11-07T14:00:00Z", 1794100000.0, CHI)
    assert z == since


@pytest.mark.parametrize("text", ["yesterday", "", "8d", "2099-01-01T00:00:00Z"])
def test_bad_since_is_a_plain_error(files, text):
    files()
    out = server.get_updates_since(since=text)
    assert set(out) == {"error"}


def test_it_reads_no_influx(files, monkeypatch):
    files(_episodes(_closed(1, 0.5)))

    def boom(flux):
        raise AssertionError("get_updates_since must not query InfluxDB")

    monkeypatch.setattr(server, "query_influx", boom)
    assert server.get_updates_since(since="1h")["verdict"] == "worth_mentioning"


def test_the_verdict_vocabulary_is_closed():
    assert updates.VERDICTS == ("nothing_new", "worth_mentioning",
                                "material_pushed_already")
    assert updates.verdict([], False) == "nothing_new"
    assert updates.verdict([], True) == "material_pushed_already"
    assert updates.verdict(["pattern_change"], True) == "worth_mentioning"
    assert updates.clusters([0, 100, 3500, 3700, 9000]) == 3
