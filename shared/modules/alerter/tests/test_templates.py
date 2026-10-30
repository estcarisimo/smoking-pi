"""Tests for message rendering.

Two properties matter more than the wording: the message must FIT the
channel's budget without losing its head, and every interpolated value must
be escaped -- target names are user-editable and go straight into HTML.
"""

from __future__ import annotations

import pytest

import templates
from templates import Section


@pytest.fixture(autouse=True)
def html_mode(monkeypatch):
    monkeypatch.delenv("ALERT_MARKUP", raising=False)


def _alert(target="Cloudflare", **over):
    """An episode's notification (policy.py) naming one destination."""
    event = {
        "type": "alert",
        "rule": "episode",
        "kind": "degradation",
        "severity": "warning",
        "key": "episode:1",
        "message": "\n".join([
            "🟡 Internet degraded",
            f"Cause: just {target} (high confidence)",
            "Range: since 19:30",
            "Scope: 70% of the destinations, 3 categories, mean loss 6.7%",
            f"Signals: {target} lost packets; the other 22 destinations were fine",
        ]),
        "episode": {"key": "episode:1"},
        "links": {
            "graph": "http://h:3000/d/x?var-target=Cloudflare",
            "compare_with_peers": "http://h:3000/d/z",
            "edit": "http://h:8080/targets/?q=Cloudflare",
        },
    }
    event.update(over)
    return event


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


def test_a_full_alert_fits_both_budgets():
    event = _alert()
    assert len(templates.format_message(event)) <= templates.TG_TEXT_LIMIT
    caption = templates.format_message(event, templates.TG_CAPTION_LIMIT)
    assert len(caption) <= templates.TG_CAPTION_LIMIT


def test_a_twinned_alert_still_fits_the_caption_budget():
    """Both link tiers configured must not push an alert past 1024 chars."""
    links = dict(_alert()["links"])
    links.update(
        {
            f"{key}_tunnel": url.replace("http://h:3000", "https://x.example.com")
            .replace("http://h:8080", "https://x.example.com")
            for key, url in links.items()
        }
    )
    caption = templates.format_message(_alert(links=links), templates.TG_CAPTION_LIMIT)
    assert len(caption) <= templates.TG_CAPTION_LIMIT


def test_only_the_graph_gets_an_anywhere_twin():
    """Mirroring all four links would spend a quarter of a caption saying the
    same thing twice; the graph is the one worth reaching from cellular."""
    links = dict(_alert()["links"])
    links["graph_tunnel"] = "https://x.example.com/d/x?var-target=Cloudflare"
    links["edit_tunnel"] = "https://x.example.com/targets/?q=Cloudflare"
    text = templates.format_message(_alert(links=links))
    assert "🌐 anywhere" in text
    assert links["graph_tunnel"] in text
    assert links["edit_tunnel"] not in text


def test_the_head_and_the_cause_always_survive():
    """Priority 0 is never dropped, however tight the budget."""
    text = templates.format_message(_alert(), 120)
    assert "Internet degraded" in text
    assert "Cloudflare" in text


def test_the_signals_line_goes_before_the_links():
    event = _alert()
    full = templates.format_message(event)
    assert "Signals:" in full and "graph" in full
    head = "\n".join(s.text for s in templates.episode_sections(event)
                     if s.priority == 0)
    text = templates.format_message(event, len(head) + 2)
    assert "Signals:" not in text and "Internet degraded" in text


def test_a_huge_message_is_truncated_on_a_line_boundary():
    """A tag cut in half makes Telegram reject the whole message with a 400."""
    event = _alert(message="x" * 5000, links={})
    text = templates.format_message(event, templates.TG_CAPTION_LIMIT)
    assert len(text) <= templates.TG_CAPTION_LIMIT
    assert _tags_balanced(text)


def _tags_balanced(text: str) -> bool:
    import re

    stack = []
    for tag in re.findall(r"<(/?)(\w+)[^>]*>", text):
        closing, name = tag
        if closing:
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack


def test_tags_stay_balanced_at_every_budget():
    event = _alert()
    for limit in range(60, 700, 17):
        assert _tags_balanced(templates.format_message(event, limit)), limit


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


def test_target_names_are_escaped():
    """`a<b&c` is a legal target name today."""
    text = templates.format_message(_alert(target="a<b&c", links={}))
    assert "a&lt;b&amp;c" in text
    assert "<b" not in text.replace("<b>", "")


def test_link_urls_are_attribute_escaped():
    text = templates.format_message(
        _alert(links={"graph": 'http://h/?q="x"&y=1'}, verdict={})
    )
    assert '&quot;' in text or "%22" in text
    assert 'href="http://h/?q="x""' not in text


def test_plain_mode_emits_no_markup(monkeypatch):
    monkeypatch.setenv("ALERT_MARKUP", "plain")
    text = templates.format_message(_alert())
    assert "<b>" not in text and "<a href" not in text and "<i>" not in text
    assert "Cloudflare" in text


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def test_report_events_pass_through_untouched():
    text = templates.format_message(
        {"type": "report", "message": "Daily digest:\n\nall good"}
    )
    assert text.startswith("Daily digest:")


def test_assemble_keeps_everything_when_it_fits():
    sections = [Section(0, "head"), Section(3, "tail")]
    assert templates.assemble(sections, 100) == "head\ntail"


def test_assemble_drops_empty_sections():
    sections = [Section(0, "head"), Section(2, "")]
    assert templates.assemble(sections, 100) == "head"


def test_only_one_anywhere_link_even_if_both_keys_are_present():
    """The docstring promised "exactly one"; nothing enforced it.

    An alert carries graph_tunnel and a digest carries
    grafana_overview_tunnel, so today only one is ever present — but a payload
    with both would render "🌐 anywhere" twice under one label, pointing at
    two different pages, and spend caption budget doing it.
    """
    links = {
        "graph": "http://h/g",
        "graph_tunnel": "https://t/g",
        "grafana_overview": "http://h/o",
        "grafana_overview_tunnel": "https://t/o",
    }
    text = templates.format_message(_alert(links=links))
    assert text.count("🌐 anywhere") == 1


def test_wifi_entry_point_renders_and_its_tunnel_twin_does_not():
    """The digest's link row gains "wi-fi"; the tunnel twin stays out, so the
    one-from-anywhere link is still the overview's."""
    links = {
        "grafana_overview": "http://h/o",
        "grafana_wifi_link": "http://h/d/wifi-link-v1",
        "grafana_wifi_link_tunnel": "https://t/d/wifi-link-v1",
        "grafana_overview_tunnel": "https://t/o",
    }
    text = templates.format_message(_alert(links=links))
    assert 'href="http://h/d/wifi-link-v1">wi-fi</a>' in text
    assert "https://t/d/wifi-link-v1" not in text
    assert text.count("🌐 anywhere") == 1
    assert 'href="https://t/o">🌐 anywhere</a>' in text




# --- episode renderers (front 8, the plain-words messages) -----------------

def _record(**over):
    """An episode record (episodes.summary) for 2026-10-12 19:30-20:10 UTC."""
    record = {
        "key": "episode:7", "start": 1_791_833_400.0, "end": 1_791_835_800.0,
        "duration_s": 2400, "class": "upstream", "detail": "degraded",
        "cause": "your provider, beyond the modem", "confidence": "medium",
        "suspected": None, "down": False, "microcut": False, "longest_cut_s": 0,
        "breadth_pct": 78.0, "categories": 3, "sites": [],
        "measures": {"affected": 14, "reporting": 18, "all_at_once": False,
                     "lost_all_cycles": 0, "hop_loss_pct": 7.0, "hop_usual_pct": 10.0,
                     "dns": [0, 4], "worst": [["Ocean_Reef_Club_Airport_DFW_c567_2", 30.0,
                                               "netflix"], ["Google", 6.0, "topsites"]],
                     "wifi": "receiving", "wifi_dbm": -50.0, "wifi_weak": 0,
                     "radio_blind_s": 0, "longest_cut_s": 0},
    }
    record.update(over)
    return record


def _event(kind="degradation", record=None, **render):
    ctx = {"now": 1_791_834_900.0, "step": 300,
           "names": {"Ocean_Reef_Club_Airport_DFW_c567_2": "Netflix", "Google": "Google",
                     "W_uba_ar_icmp": "uba.ar"}}
    ctx.update(render)
    return {"type": "alert", "rule": "episode", "kind": kind, "key": "episode:7",
            "severity": "warning", "episode": record or _record(), "render": ctx}


@pytest.fixture
def utc_plain(monkeypatch):
    monkeypatch.setenv("DIGEST_TZ", "UTC")
    monkeypatch.setenv("ALERT_MARKUP", "plain")


def test_the_opening_message_of_the_brief(utc_plain):
    """Esteban's example of 2026-10-30, as rendered."""
    assert templates.plain(_event()) == (
        "🟡 Internet with packet loss since 19:30 (25 min)\n"
        "Probable cause: your provider, beyond the modem (medium confidence).\n"
        "• 14 of 18 destinations lost packets; never all at once\n"
        "• your router answered normally (7%, its usual 10%)\n"
        "• this Pi's Wi-Fi was stable (-50 dBm, no cuts)\n"
        "Against: Netflix alone lost 30%.")


def test_below_medium_the_cause_is_undetermined_and_the_hint_a_signal(utc_plain):
    record = _record(**{"class": "undetermined", "confidence": "low",
                        "suspected": "upstream", "cause": "undetermined"})
    assert templates.plain(_event(record=record)) == (
        "🟡 Internet with packet loss since 19:30 (25 min)\n"
        "Cause: undetermined.\n"
        "• the strongest hint: your provider, beyond the modem (low confidence)\n"
        "• 14 of 18 destinations lost packets; never all at once\n"
        "• your router answered normally (7%, its usual 10%)\n"
        "Against: Netflix alone lost 30%.")


def test_a_router_not_measured_is_the_contrary_of_a_network_cause(utc_plain):
    measures = {**_record()["measures"], "hop_loss_pct": None, "worst": []}
    text = templates.plain(_event(record=_record(measures=measures)))
    assert text.endswith("Against: your router is not measured, so your line and "
                         "your provider cannot be told apart.")


def test_a_disagreeing_diagnosis_is_the_contrary_when_nothing_else_is(utc_plain):
    measures = {**_record()["measures"], "worst": []}
    event = _event(record=_record(measures=measures))
    event["diagnosis"] = {"class": "local_wifi", "confidence": "high"}
    assert templates.plain(event).endswith(
        "Against: the last hours' diagnosis points at this Pi's Wi-Fi radio "
        "stopped receiving (high confidence).")
    event["diagnosis"] = {"class": "upstream", "confidence": "high"}  # agrees
    assert "Against" not in templates.plain(event)


def test_one_service_is_named_by_its_label_never_its_target(utc_plain):
    record = _record(**{"class": "destination", "detail": None,
                        "cause": "just W_uba_ar_icmp", "sites": ["W_uba_ar_icmp"],
                        "breadth_pct": 5.0, "categories": 0},
                     measures={**_record()["measures"], "affected": 1,
                               "worst": [["W_uba_ar_icmp", 40.0, "topsites"]]})
    assert templates.plain(_event(record=record)) == (
        "🟡 Packet loss at uba.ar since 19:30 (25 min)\n"
        "Probable cause: just uba.ar (medium confidence).\n"
        "• only 1 of 18 destinations lost packets\n"
        "• your router answered normally (7%, its usual 10%)\n"
        "• this Pi's Wi-Fi was stable (-50 dBm, no cuts)")


def test_an_update_is_one_line(utc_plain):
    record = _record(down=True)
    event = _event("update", record, was_down=False, why=templates.update_why(
        record, {}, {"escalated": True}))
    assert templates.plain(event) == (
        "🔁 Update on the packet loss since 19:30: now the Internet is down.")
    event = _event("update", _record(**{"class": "local_link"}), was_down=False,
                   why=templates.update_why(_record(**{"class": "local_link"}), {},
                                            {"reclassed": True,
                                             "words": "your provider, beyond the modem"}))
    assert templates.plain(event) == (
        "🔁 Update on the packet loss since 19:30: the probable cause is now "
        "your line, the modem or the line to your provider (it was your "
        "provider, beyond the modem).")


def test_the_end_corrects_the_first_message_when_the_class_changed(utc_plain):
    record = _record(**{"class": "local_link", "confidence": "high"})
    event = _event("recovery", record, now=1_791_836_400.0, told_kind="degradation",
                   told_class="upstream",
                   told_words="your provider, beyond the modem", updates=0)
    assert templates.plain(event) == (
        "✅ Packet loss over after 40 min (19:30 to 20:10)\n"
        "Cause: your line, the modem or the line to your provider (high confidence). "
        "This corrects the first message, which said your provider, beyond the modem.\n"
        "Affected: 14 of 18 destinations; the worst, Netflix, lost 30%.")


def test_no_internal_words_reach_a_message(utc_plain):
    """Rule names, target names, ASNs and the old structure's labels."""
    texts = [templates.plain(_event(kind, _record(down=kind == "down")))
             for kind in ("down", "degradation", "recovery", "night", "monitor")]
    for text in texts:
        for word in ("high_loss", "microcut_burst", "outage", "Ocean_Reef", "W_",
                     "AS1", "mute", "Range:", "Scope:", "Signals:", "episode:"):
            assert word not in text, (word, text)
        assert text.count("\n") < 8


def test_the_telegram_rendering_escapes_and_links(monkeypatch):
    monkeypatch.setenv("DIGEST_TZ", "UTC")
    monkeypatch.setenv("PUBLIC_BASE_HOST", "h")
    event = _event(names={"Ocean_Reef_Club_Airport_DFW_c567_2": "<Net&flix>"})
    text = templates.format_message(event)
    assert text.startswith("🟡 <b>Internet with packet loss since 19:30 (25 min)</b>\n")
    assert "Against: &lt;Net&amp;flix&gt; alone lost 30%." in text
    assert text.endswith(f'">19:25–19:55</a>')
    assert "now-6h" not in text


@pytest.mark.parametrize("mode, channel, expected", [
    ("telegram", None, "html"), ("openclaw", None, "html"),
    ("openclaw", "whatsapp", "plain"), ("webhook", None, "plain")])
def test_only_telegram_gets_html(monkeypatch, mode, channel, expected):
    monkeypatch.setenv("NOTIFY_MODE", mode)
    if channel:
        monkeypatch.setenv("OPENCLAW_CHANNEL", channel)
    assert templates.markup() == expected


@pytest.mark.parametrize("target, category, known, expected", [
    ("Google_h2", "topsites", {"Google": "Google"}, "Google"),
    ("Ocean_Reef_Club_Airport_DFW_c567_2", "netflix", {}, "Netflix"),
    ("W_netflix_com_icmp", None, {}, "netflix.com"),
    ("Cloudflare_DNS", "dns", {}, "Cloudflare DNS"),
])
def test_a_destination_is_called_by_its_service(target, category, known, expected):
    import names
    assert names.display(target, category, known) == expected


def test_percentages_and_ranges_read_plainly(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")  # a fallback zone: its name once, at the end
    assert [templates.pct(v) for v in (7.0, 2.5, 30.04, None)] == ["7%", "2.5%", "30%", "?"]
    assert templates.span(1_791_833_400.0, 1_791_835_800.0, 1_791_835_800.0) == \
        "19:30–20:10 UTC"
