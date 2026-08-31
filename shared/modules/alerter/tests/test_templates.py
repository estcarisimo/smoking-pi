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


def _alert(**over):
    event = {
        "type": "alert",
        "rule": "target_down",
        "severity": "critical",
        "target": "Cloudflare",
        "message": "Cloudflare down: 100% loss across all 4 probes",
        "verdict": {
            "scope": "isp_upstream",
            "line": "Not you — 12 of 16 destinations affected but your local "
                    "link is clean, so this is upstream.",
            "affected": 12,
            "total": 16,
            "cpe_cutting": [],
        },
        "links": {
            "graph": "http://h:3000/d/x?var-target=Cloudflare",
            "per_ping_detail": "http://h:3000/d/y",
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


def test_the_headline_and_verdict_always_survive():
    """Priority 0 is never dropped, however tight the budget."""
    text = templates.format_message(_alert(), 120)
    assert "Cloudflare" in text
    assert "critical" in text


def _budget_for(event, keep_priorities):
    """Exact budget that fits priority-0 plus the given optional sections."""
    sections = [
        s
        for s in templates.alert_sections(event)
        if s.priority == 0 or s.priority in keep_priorities
    ]
    return len("\n".join(s.text for s in sections))


def test_sections_are_dropped_in_priority_order():
    """Mute hint goes first, then the breadth recap; links outlive both.

    A caption cannot be explored, so the link out of it is worth more than a
    recap of a number the verdict line already gave. Budgets are derived
    from the rendered sections rather than hardcoded, so rewording a line
    cannot silently turn this into a test of nothing.
    """
    event = _alert()
    full = templates.format_message(event)
    assert "mute:" in full and "graph" in full and "target_down" in full

    # Room for links + breadth but not the mute hint.
    text = templates.format_message(event, _budget_for(event, {1, 2}))
    assert "mute:" not in text
    assert "graph" in text and "target_down" in text

    # Room for links only.
    text = templates.format_message(event, _budget_for(event, {1}))
    assert "mute:" not in text and "target_down" not in text
    assert "graph" in text

    # Room for the head only.
    text = templates.format_message(event, _budget_for(event, set()))
    assert "graph" not in text
    assert "Cloudflare" in text and "critical" in text


def test_a_huge_message_is_truncated_on_a_line_boundary():
    """A tag cut in half makes Telegram reject the whole message with a 400."""
    event = _alert(message="x" * 5000, verdict={}, links={})
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
    text = templates.format_message(_alert(target="a<b&c", links={}, verdict={}))
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


def test_the_rule_name_survives_somewhere():
    """Needed to correlate with docker logs and to target a mute."""
    assert "target_down" in templates.format_message(_alert())


def test_recovery_reports_how_long_it_was_down():
    text = templates.format_message(
        {
            "type": "recovery",
            "rule": "target_down",
            "target": "Cloudflare",
            "message": "cleared",
            "duration_s": 1_380,
        }
    )
    assert "recovered" in text
    assert "23 min" in text


def test_report_events_pass_through_untouched():
    text = templates.format_message(
        {"type": "report", "message": "Daily digest:\n\nall good"}
    )
    assert text.startswith("Daily digest:")


def test_no_verdict_still_renders():
    text = templates.format_message(_alert(verdict={}))
    assert "Cloudflare" in text


def test_assemble_keeps_everything_when_it_fits():
    sections = [Section(0, "head"), Section(3, "tail")]
    assert templates.assemble(sections, 100) == "head\ntail"


def test_assemble_drops_empty_sections():
    sections = [Section(0, "head"), Section(2, "")]
    assert templates.assemble(sections, 100) == "head"
