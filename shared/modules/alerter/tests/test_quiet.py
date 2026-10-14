"""Quiet hours: held overnight, sent as one message when they end."""

import pytest

import main
import outbox
import quiet

DAY = 1_791_763_200.0  # 2026-10-12 00:00 UTC
NIGHT = DAY + 23.5 * 3600  # 23:30
LATE = DAY + 26 * 3600  # 02:00 the next day
MORNING = DAY + 31 * 3600 + 300  # 07:05 the next day
NOON = DAY + 12 * 3600


def _alert(key="high_loss:Google", rule="high_loss", severity="warning",
           target="Google", message="mean loss 30%", type="alert"):
    return {"type": type, "key": key, "rule": rule, "severity": severity,
            "target": target, "message": message}


@pytest.fixture
def overnight(monkeypatch):
    monkeypatch.setenv("QUIET_FROM", "23:00")
    monkeypatch.setenv("QUIET_UNTIL", "07:00")
    monkeypatch.setenv("DIGEST_TZ", "UTC")


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(main.notifier, "notify",
                        lambda payload, image=None: out.append(payload) or True)
    return out


def test_off_unless_both_times_are_set_and_readable(monkeypatch):
    assert not quiet.active(NIGHT)
    monkeypatch.setenv("QUIET_FROM", "23:00")
    assert not quiet.active(NIGHT)
    monkeypatch.setenv("QUIET_UNTIL", "7am")
    assert not quiet.active(NIGHT)
    monkeypatch.setenv("QUIET_UNTIL", "23:00")
    assert not quiet.active(NIGHT)


def test_a_span_across_midnight(overnight):
    assert quiet.active(NIGHT) and quiet.active(LATE)
    assert not quiet.active(MORNING) and not quiet.active(NOON)
    assert not quiet.active(DAY + 22 * 3600 + 59 * 60)
    assert quiet.active(DAY + 23 * 3600)
    assert not quiet.active(DAY + 31 * 3600)  # 07:00 is the end


def test_a_span_within_one_day(monkeypatch):
    monkeypatch.setenv("QUIET_FROM", "12:00")
    monkeypatch.setenv("QUIET_UNTIL", "14:00")
    assert quiet.active(NOON) and not quiet.active(NIGHT)


def test_in_the_digests_zone(monkeypatch):
    monkeypatch.setenv("QUIET_FROM", "23:00")
    monkeypatch.setenv("QUIET_UNTIL", "07:00")
    monkeypatch.setenv("DIGEST_TZ", "America/Chicago")
    # 23:30 UTC is 18:30 in Chicago; 06:00 UTC is 01:00 there.
    assert not quiet.active(NIGHT)
    assert quiet.active(DAY + 30 * 3600)


def test_the_night_is_held_and_the_morning_gets_one_message(overnight, sent):
    current = {"incidents": {}}
    main._deliver(current, _alert(), now=NIGHT)
    main._deliver(current, {**_alert(type="recovery"), "duration_s": 600},
                  now=NIGHT + 600)
    main._deliver(current, _alert(key="outage:1", rule="outage", target=None,
                                  message="4 of 16 targets lost packets"), now=LATE)
    assert sent == []
    assert [h["delivery"] for h in current["history"]] == ["held"] * 3
    assert len(current["held"]["items"]) == 3
    # Still night: nothing goes.
    assert quiet.release(current, LATE, lambda e: main._deliver(current, e, now=LATE)) is False
    assert quiet.release(current, MORNING,
                         lambda e: main._deliver(current, e, now=MORNING)) is True
    [msg] = sent
    assert msg["type"] == "held" and msg["silent"] is True
    assert "Quiet hours" in msg["message"]
    assert "high_loss Google" in msg["message"] and "✅" in msg["message"]
    assert "outage" in msg["message"]
    assert "held" not in current
    assert current["history"][-1] == {**current["history"][-1],
                                      "type": "held", "delivery": "sent"}


def test_a_total_loss_is_never_held(overnight, sent):
    current = {}
    for rule in ("uplink_down", "internet_down", "monitor_dns"):
        main._deliver(current, _alert(key=rule, rule=rule, severity="critical",
                                      target=None), now=NIGHT)
    assert [p["rule"] for p in sent] == ["uplink_down", "internet_down", "monitor_dns"]
    assert "held" not in current


def test_a_held_summary_that_cannot_be_sent_waits_in_the_outbox(overnight, monkeypatch):
    monkeypatch.setattr(main.notifier, "notify", lambda payload, image=None: False)
    current = {}
    main._deliver(current, _alert(), now=NIGHT)
    quiet.release(current, MORNING, lambda e: main._deliver(current, e, now=MORNING))
    assert "held" not in current
    assert [i["payload"]["type"] for i in current["outbox"]["items"]] == ["held"]


def test_turning_quiet_hours_off_releases_what_was_held(overnight, sent, monkeypatch):
    current = {}
    main._deliver(current, _alert(), now=NIGHT)
    monkeypatch.delenv("QUIET_FROM")
    monkeypatch.delenv("QUIET_UNTIL")
    assert quiet.release(current, LATE, lambda e: main._deliver(current, e, now=LATE))
    assert [p["type"] for p in sent] == ["held"]


def test_the_held_queue_is_bounded(overnight):
    current = {}
    for i in range(quiet.MAX_ITEMS + 3):
        quiet.hold(current, _alert(key=f"k{i}"), NIGHT + i)
    assert len(current["held"]["items"]) == quiet.MAX_ITEMS
    assert current["held"]["dropped"] == 3
    assert "…and" in quiet.build(current, MORNING)["message"]


def test_the_summary_is_markup_safe(overnight):
    current = {}
    quiet.hold(current, _alert(target="<b>x</b>", message="a & b"), NIGHT)
    text = quiet.build(current, MORNING)["message"]
    assert "&lt;b&gt;x" in text and "a &amp; b" in text


def test_templates_send_the_summary_as_written():
    import templates
    event = {"type": "held", "message": "🌙 already rendered"}
    assert templates.format_message(event).startswith("🌙 already rendered")


def test_outbox_constants_are_shared():
    assert quiet.MAX_ITEMS == outbox.MAX_ITEMS
