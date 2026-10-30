"""Render events into the message that actually reaches a phone.

Two things make this more than string formatting.

**Two budgets, not one.** A plain Telegram message may be 4096 characters; a
message carrying an image gets a 1024-character CAPTION. So an alert with a
chart has a quarter of the room, and the answer is not to truncate -- cutting
the middle out of an alert loses the part that mattered. Sections carry a
priority and the lowest-value ones are DROPPED until the message fits, so
what survives is always the headline, the verdict and the numbers.

**Telegram HTML, escaped.** Outbound text is parsed as HTML (verified against
this deployment, not assumed), which means an unescaped target name is both a
rendering bug and an injection: target names are user-editable and ``a<b&c``
is a legal one today. Every interpolated value goes through html.escape.

``ALERT_MARKUP=plain`` disables markup entirely, as an escape hatch for a
deployment whose channel does not parse HTML.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass

# Telegram's two limits. A caption is a quarter of a message.
TG_TEXT_LIMIT = 4096
TG_CAPTION_LIMIT = 1024
# An episode's head line (policy.py) is a few words; this bounds a broken one.
HEAD_MAX = 200

# One traffic light, used everywhere a state is stated -- alerts here, and the
# summaries the agent writes from the MCP tools (see the OpenClaw skill). A
# reader scanning a phone should not have to learn a second color vocabulary
# halfway down a report, so 🟢/🟡/🔴 means the same thing in both.
STATUS_EMOJI = {"ok": "🟢", "watch": "🟡", "bad": "🔴"}

SEVERITY_EMOJI = {
    "critical": STATUS_EMOJI["bad"],
    "warning": STATUS_EMOJI["watch"],
    "info": "🔵",
}

@dataclass(frozen=True)
class Section:
    """One block of a message.

    ``priority`` is DROP ORDER, highest number first: 0 is never dropped,
    3 goes first. So links (1) outlive the breadth recap (2), which outlives
    the mute hint (3) -- the verdict line already states the breadth, and a
    link is the only way out of a static image caption.
    """

    priority: int
    text: str


def markup() -> str:
    return (os.environ.get("ALERT_MARKUP") or "html").strip().lower()


def esc(value: object) -> str:
    """Escape for Telegram HTML, or pass through in plain mode."""
    text = str(value)
    if markup() != "html":
        return text
    return html.escape(text, quote=False)


def _b(text: str) -> str:
    return f"<b>{text}</b>" if markup() == "html" else text


def _i(text: str) -> str:
    return f"<i>{text}</i>" if markup() == "html" else text


def _a(url: str, label: str) -> str:
    if markup() != "html":
        return url
    return f'<a href="{html.escape(url, quote=True)}">{label}</a>'


def assemble(sections: list[Section], limit: int) -> str:
    """Join sections, dropping the least important until it fits.

    Drops whole sections rather than truncating mid-message, because the
    tail of an alert (links, a mute hint) is worth far less than its head.
    Only when priority-0 alone still overflows does it truncate, and then on
    a line boundary so HTML tags are never cut in half -- a half-tag makes
    Telegram reject the whole message with a 400.
    """
    kept = [s for s in sections if s.text]
    while True:
        body = "\n".join(s.text for s in kept)
        if len(body) <= limit or not kept:
            break
        droppable = max((s.priority for s in kept), default=0)
        if droppable == 0:
            break
        # Drop one at a time, lowest value first, so we keep as much as fits.
        for index in range(len(kept) - 1, -1, -1):
            if kept[index].priority == droppable:
                del kept[index]
                break

    body = "\n".join(s.text for s in kept)
    if len(body) <= limit:
        return body

    lines = body.split("\n")
    out: list[str] = []
    for line in lines:
        candidate = "\n".join([*out, line])
        if len(candidate) + 1 > limit:
            break
        out.append(line)
    trimmed = "\n".join(out)
    if not trimmed:
        trimmed = body[: max(0, limit - 1)]
    return (trimmed + "…")[:limit]


def _duration(seconds: float | None) -> str | None:
    if not seconds or seconds < 0:
        return None
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _link_line(links: dict | None) -> str:
    """The links row: the primary set, plus ONE from-anywhere link.

    Exactly one tunnel twin is emitted, whichever applies to the event: the
    target graph on an alert, the overview on an entry-point message. A caption has 1024
    characters and a Grafana deep link runs to ~120 of them, so mirroring the
    whole row would cost a quarter of the budget to say the same thing twice.
    Whoever is on cellular wants the picture; from there the rest of the
    dashboard is one tap away.
    """
    if not links:
        return ""
    labels = (
        # Per-target links, from links.target_links().
        ("graph", "graph"),
        ("compare_with_peers", "peers"),
        ("edit", "edit"),
        ("graph_tunnel", "🌐 anywhere"),
        # Entry points, from links.entry_point_links(): about everything
        # rather than one target.
        ("grafana_overview", "overview"),
        ("grafana_cpe_microcuts", "microcuts"),
        ("grafana_wifi_link", "wi-fi"),
        ("web_admin_targets", "targets"),
        ("grafana_overview_tunnel", "🌐 anywhere"),
        # The weekly summary's one link: its week, exactly.
        ("grafana_week", "the week in Grafana"),
    )
    parts: list[str] = []
    seen_labels: set[str] = set()
    for key, label in labels:
        url = links.get(key)
        if not url:
            continue
        # Enforce "one from-anywhere link", rather than merely documenting it.
        # An alert carries graph_tunnel and an entry point
        # grafana_overview_tunnel, so today only one is ever present -- but a
        # payload holding both would render "🌐 anywhere" twice, pointing at
        # two different pages under one label, and burn caption budget doing
        # it.
        if label in seen_labels:
            continue
        seen_labels.add(label)
        parts.append(_a(url, label))
    return " · ".join(parts)


def format_message(event: dict, limit: int = TG_TEXT_LIMIT) -> str:
    """Render one event to the text that will be delivered."""
    if event.get("type") in ("report", "undelivered", "held"):
        # Pre-rendered: a report by ai-insights, an outbox summary by
        # outbox.build, a quiet-hours one by quiet.build (already escaped
        # for the markup).
        return assemble([Section(0, str(event.get("message", "")))], limit)
    if event.get("type") == "weekly":
        # The body is pre-rendered by weekly.render(), which already builds
        # its own sections; splitting the links onto their own Section lets
        # them drop first when a caption budget bites, exactly as on alerts.
        return assemble(
            [
                Section(0, str(event.get("message", ""))),
                Section(1, _link_line(event.get("links"))),
            ],
            limit,
        )
    return assemble(episode_sections(event), limit)


def episode_sections(event: dict) -> list[Section]:
    """An episode's notification (policy.py): its first line as the head,
    the structured lines below it (cause, range, scope, signals), and the
    last hours' diagnosis when one ran. Minimal on purpose: the wording is
    the next change's. Every line is escaped -- target names are
    user-editable."""
    lines = str(event.get("message", "")).split("\n")
    # The head is bold; one too long for a caption is cut before the markup
    # goes on, never inside it.
    head = lines[0] if len(lines[0]) <= HEAD_MAX else lines[0][:HEAD_MAX - 1] + "…"
    # The light first, as every message here: "🔴 <b>Internet down</b>".
    light, _, words = head.partition(" ")
    if words and not light.isalnum():
        sections = [Section(0, f"{esc(light)} {_b(esc(words))}")]
    else:
        sections = [Section(0, _b(esc(head)))]
    sections += [Section(0 if i < 3 else 2, esc(line))
                 for i, line in enumerate(lines[1:]) if line]
    diag = event.get("diagnosis") or {}
    if diag.get("summary") and diag.get("confidence") in ("high", "medium"):
        sections.append(Section(2, _i(esc(
            f"Diagnosis of the last hours: {diag['summary']} "
            f"({diag['confidence']} confidence)"))))
    sections.append(Section(1, _link_line(event.get("links"))))
    return sections
