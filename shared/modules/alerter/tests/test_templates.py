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


