"""What the phone shows: every message kind, rendered from October.

The episodes are the replays of tests/test_october.py (the same fake Pi,
the real ``main.run_iteration``); here the assertion is the exact text a
home user reads (templates.py), not only how many messages went. Plain
text unless a test says otherwise, so a failure diff reads as the message.

Esteban's rules (2026-10-30), which these hold the renderers to: the head
says what and since when; the probable cause with its confidence, or
"Cause: undetermined" below medium; up to three signals with numbers and
the strongest contrary one; a link to the episode's own range; no rule
names, target names or ASNs; one marker; one clock.
"""

from __future__ import annotations

import charts
import main
import notifications
import templates
from test_october import (  # noqa: F401 - replay is a fixture
    DESTINATIONS,
    STEP,
    Case,
    _overlap,
    at,
    replay,
)


def text(payload: dict) -> str:
    return templates.format_message(payload)


def evening(netflix_alone: bool = False):
    """10-06, 19:30-23:00: most destinations losing 2-8%, the first hop
    clean; with ``netflix_alone`` one Netflix cache losing 30% meanwhile."""
    def loss(target, lo, hi):
        if target == "CPE_IPv4":
            return 0.1
        if not _overlap(lo, hi, at(6, "19:30"), at(6, "23:00")):
            return 0.0
        if netflix_alone and target == "Netflix_3":
            return 0.3
        i = DESTINATIONS.index(target) + int(hi // STEP)
        return 0.1 if i % 3 else 0.0
    return Case(loss=loss), at(6, "19:00"), at(6, "23:50")


def line_down():
    """Invented: the line down 10:00-10:20, the first hop with it."""
    down = (at(20, "10:00"), at(20, "10:20"))

    def loss(target, lo, hi):
        if _overlap(lo, hi, *down):
            return 1.0
        return 0.1 if target == "CPE_IPv4" else 0.0
    return Case(loss=loss), at(20, "09:40"), at(20, "11:30")


def test_the_evening_alert_and_its_end(replay, monkeypatch):
    monkeypatch.setenv("ALERT_MARKUP", "plain")
    sent, _ = replay(*evening())
    (_, alert), (_, end) = sent
    assert text(alert) == (
        "🟡 Internet with packet loss since 19:30 (15 min)\n"
        "Probable cause: your provider, beyond the modem (high confidence).\n"
        "• 16 of 23 destinations lost packets; never all at once\n"
        "• your router answered normally (10%, its usual 10%)\n"
        "• this Pi's Wi-Fi was stable (-50 dBm, no cuts)")
    assert text(end) == (
        "✅ Packet loss over after 3h 30m (19:30 to 23:00)\n"
        "Cause: your provider, beyond the modem (high confidence).\n"
        "Affected: 16 of 23 destinations.")


def test_one_destination_standing_out_is_the_contrary_signal(replay, monkeypatch):
    monkeypatch.setenv("ALERT_MARKUP", "plain")
    sent, _ = replay(*evening(netflix_alone=True))
    (_, alert), (_, end) = sent
    # The service (the taxonomy's label), never the cache's own name.
    assert text(alert) == (
        "🟡 Internet with packet loss since 19:30 (15 min)\n"
        "Probable cause: your provider, beyond the modem (high confidence).\n"
        "• 17 of 23 destinations lost packets; never all at once\n"
        "• your router answered normally (10%, its usual 10%)\n"
        "• this Pi's Wi-Fi was stable (-50 dBm, no cuts)\n"
        "Against: Netflix alone lost 30%.")
    assert text(end).endswith(
        "Affected: 17 of 23 destinations; the worst, Netflix, lost 30%.")


def test_telegram_gets_the_bold_head_and_the_range_as_a_link(replay, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_HOST", "smoking-pi.local")
    sent, _ = replay(*evening())
    (_, alert), (_, end) = sent
    overview = "http://smoking-pi.local:3000/d/smoking-pi-overview-v1"
    # From one cycle before the loss to the alert; the end's, one cycle
    # each side. Never now-6h.
    assert text(alert) == (
        "🟡 <b>Internet with packet loss since 19:30 (15 min)</b>\n"
        "Probable cause: your provider, beyond the modem (high confidence).\n"
        "• 16 of 23 destinations lost packets; never all at once\n"
        "• your router answered normally (10%, its usual 10%)\n"
        "• this Pi's Wi-Fi was stable (-50 dBm, no cuts)\n"
        f'See <a href="{overview}?from={int(at(6, "19:25")) * 1000}&amp;'
        f'to={int(at(6, "19:45")) * 1000}">19:25–19:45</a>')
    assert text(end).endswith(
        f'See <a href="{overview}?from={int(at(6, "19:25")) * 1000}&amp;'
        f'to={int(at(6, "23:05")) * 1000}">19:25–23:05</a>')
    # The plain text the ledger and the summaries read carries the URL.
    assert alert["message"].endswith(
        f"See 19:25–19:45 → {overview}?from={int(at(6, '19:25')) * 1000}"
        f"&to={int(at(6, '19:45')) * 1000}")


def test_the_line_down_alert_and_its_end(replay, monkeypatch):
    monkeypatch.setenv("ALERT_MARKUP", "plain")
    sent, _ = replay(*line_down())
    (_, alert), (_, end) = sent
    assert text(alert) == (
        "🔴 Internet down since 10:00 (15 min)\n"
        "Probable cause: your line, the modem or the line to your provider "
        "(high confidence).\n"
        "• all 23 destinations lost every packet\n"
        "• your router stopped answering too\n"
        "• this Pi's Wi-Fi was stable (-50 dBm, no cuts)")
    assert text(end) == (
        "✅ Internet back after 20 min down (10:00 to 10:20)\n"
        "Cause: your line, the modem or the line to your provider "
        "(high confidence).\n"
        "Affected: all 23 destinations.")


def test_with_an_assistant_the_alert_says_how_to_silence_it(replay, monkeypatch):
    monkeypatch.setenv("ALERT_MARKUP", "plain")
    monkeypatch.setenv("NOTIFY_MODE", "openclaw")
    sent, _ = replay(*line_down())
    (_, alert), (_, end) = sent
    assert text(alert).endswith(
        '\nReply "ack" to the assistant to silence this episode.')
    assert "Reply" not in text(end)


def test_the_chart_rides_only_on_the_opening_message(replay, monkeypatch):
    drawn, images = [], []
    monkeypatch.setattr(charts, "render_incident_chart",
                        lambda target, **kw: drawn.append((target, kw)) or b"png")
    deliver = main._deliver
    monkeypatch.setattr(main, "_deliver", lambda current, payload, image=None, now=None: (
        images.append((payload["kind"], image)), deliver(current, payload, image, now)))
    replay(*line_down())
    assert images == [("down", b"png"), ("recovery", None)]
    [(target, kw)] = drawn
    assert target in DESTINATIONS and kw["first_seen"] == at(20, "10:00")


def test_the_monitor_message_after_eleven_deaf_hours(replay, monkeypatch):
    monkeypatch.setenv("ALERT_MARKUP", "plain")
    sent, _ = replay(Case(deaf=[(at(11, "03:40"), at(11, "14:35"))]),
                     at(11, "03:00"), at(11, "15:30"))
    [(_, message)] = sent
    assert text(message) == (
        "🛠 This Pi could not measure the network from 03:40 to 14:35 (10h 55m)\n"
        "Cause: this Pi's Wi-Fi radio stopped receiving (high confidence).\n"
        "The watchdog took no action.\n"
        "What the network did meanwhile is unknown.")


def test_the_monitor_message_says_what_the_watchdog_did(replay, monkeypatch):
    """10-12: deaf 10:10-16:00 with the exporter stalled; 10-18: deaf 17
    minutes, then the watchdog reloads the driver."""
    monkeypatch.setenv("ALERT_MARKUP", "plain")
    case = Case(deaf=[(at(12, "10:10"), at(12, "16:00"))],
                stalled=[(at(12, "12:00"), at(12, "12:40"))],
                cuts=[(at(12, "16:00:20"), at(12, "16:00:30"))])
    [(_, message)], _ = replay(case, at(12, "09:50"), at(12, "17:00"))
    assert text(message) == (
        "🛠 This Pi could not measure the network from 10:10 to 16:00 (5h 50m)\n"
        "Cause: this Pi's Wi-Fi radio stopped receiving (high confidence).\n"
        "The watchdog took no action.\n"
        "What the network did meanwhile is unknown.")
    reload_at = at(18, "14:22")
    case = Case(deaf=[(at(18, "14:05"), reload_at)],
                cuts=[(reload_at + 20, reload_at + 30)],
                watchdog=[(reload_at, "wifi: nothing answers on wlan0 (link up); "
                           "reloading the brcmfmac driver")])
    [(_, message)], _ = replay(case, at(18, "13:45"), at(18, "15:32"))
    # Blind until a cycle after the reload: the reload's own drop.
    assert text(message) == (
        "🛠 This Pi could not measure the network from 14:05 to 14:27 (22 min)\n"
        "Cause: this Pi's Wi-Fi radio stopped receiving (high confidence).\n"
        "The watchdog reloaded the Wi-Fi driver at 14:22.\n"
        "What the network did meanwhile is unknown.")


def test_monitor_messages_link_the_wifi_dashboard(replay, monkeypatch):
    monkeypatch.setenv("ALERT_MARKUP", "plain")
    monkeypatch.setenv("PUBLIC_BASE_HOST", "smoking-pi.local")
    [(_, message)], _ = replay(Case(deaf=[(at(11, "03:40"), at(11, "14:35"))]),
                               at(11, "03:00"), at(11, "15:30"))
    assert text(message).endswith(
        "See 03:35–14:40 → http://smoking-pi.local:3000/d/wifi-link-v1"
        f"?from={int(at(11, '03:35')) * 1000}&to={int(at(11, '14:40')) * 1000}")


def test_the_line_microcut_is_recorded_in_plain_words(replay, monkeypatch):
    """10-15 12:35: the first hop cut for 40 s, 12 destinations with it.
    Not sent; the ledger's line says what and why."""
    monkeypatch.setenv("ALERT_MARKUP", "plain")
    recorded = []
    real = notifications.record
    monkeypatch.setattr(notifications, "record", lambda payload, outcome, *a, **k: (
        recorded.append(payload) if outcome == "recorded"
        else real(payload, outcome, *a, **k)))
    hit = set(DESTINATIONS[:12])
    cut = (at(15, "12:35:00"), at(15, "12:35:40"))

    def loss(target, lo, hi):
        if target in hit and _overlap(lo, hi, *cut):
            return 0.1
        return 0.1 if target == "CPE_IPv4" else 0.0

    sent, _ = replay(Case(loss=loss, cuts=[cut]), at(15, "12:20"), at(15, "13:30"))
    assert sent == []
    [line] = recorded
    assert line["message"] == (
        "📝 Recorded, not sent (under a minute): Your line cut out for 40 s, "
        "12:35 to 12:40\n"
        "Cause: your line, the modem or the line to your provider")


def test_the_night_bundle_is_one_plain_line_per_episode(replay, monkeypatch):
    monkeypatch.setenv("ALERT_MARKUP", "plain")
    down = (at(22, "02:00"), at(22, "02:40"))

    def loss(target, lo, hi):
        if _overlap(lo, hi, *down):
            return 1.0
        return 0.1 if target == "CPE_IPv4" else 0.0

    [(_, bundle)], _ = replay(Case(loss=loss), at(22, "01:40"), at(22, "07:10"))
    assert text(bundle) == (
        "🌙 Quiet hours, 02:55 to 07:00 — held overnight:\n"
        "🟢 Nothing open now.\n"
        "🔴 Internet down · 02:00 → ✅ 02:40 — probable cause: your line, "
        "the modem or the line to your provider")
