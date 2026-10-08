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

SCOPE_EMOJI = {
    "monitoring": "🛠",
    "monitor_uplink": "🔌",
    "wifi": "📶",
    "local_link": "🏠",
    "isp_upstream": "🌐",
    "ipv6": "6️⃣",
    "dns": "🔤",
    "remote_target": "🎯",
    "unclear": "❔",
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


# Recoveries: rules where the target or the link answered nothing say "was
# down"; the rest ("high_loss", ...) say "lasted". A windowed rule keeps
# firing while its event is inside the window, so it gets no duration.
DOWN_RULES = frozenset({"target_down", "uplink_down", "ipv6_down", "exporter_stale"})
WINDOWED_RULES = frozenset({"microcut_burst"})


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
    target graph on an alert, the overview on a digest. A caption has 1024
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
        ("per_ping_detail", "per-ping"),
        ("compare_with_peers", "peers"),
        ("edit", "edit"),
        ("graph_tunnel", "🌐 anywhere"),
        # Entry points, from links.entry_point_links() -- what a digest
        # carries, since it is about everything rather than one target.
        ("grafana_overview", "overview"),
        ("grafana_cpe_microcuts", "microcuts"),
        ("grafana_wifi_link", "wi-fi"),
        ("web_admin_targets", "targets"),
        ("grafana_overview_tunnel", "🌐 anywhere"),
    )
    parts: list[str] = []
    seen_labels: set[str] = set()
    for key, label in labels:
        url = links.get(key)
        if not url:
            continue
        # Enforce "one from-anywhere link", rather than merely documenting it.
        # An alert carries graph_tunnel and a digest carries
        # grafana_overview_tunnel, so today only one is ever present -- but a
        # payload holding both would render "🌐 anywhere" twice, pointing at
        # two different pages under one label, and burn caption budget doing
        # it.
        if label in seen_labels:
            continue
        seen_labels.add(label)
        parts.append(_a(url, label))
    return " · ".join(parts)


# One per diagnosis class (common/diagnosis.py), in the verdict's scheme.
DIAGNOSIS_EMOJI = {
    "local_wifi": "📶",
    "local_link": "🏠",
    "destination": "🎯",
    "upstream": "🌐",
    "unclear": "❔",
}


def _diagnosis_leads(diag: dict, verdict: dict) -> bool:
    """Whether the diagnosis, not the verdict, heads the message: it has a
    class worth stating and is at least medium-confident, and the monitor
    is not the one at fault (no measurements arriving outranks everything,
    as in verdict.py)."""
    return bool(
        diag.get("summary")
        # "No single cause fits" says less than the verdict it would replace.
        and diag.get("class") not in (None, "probe_miss", "unclear")
        and diag.get("confidence") in ("high", "medium")
        and verdict.get("scope") != "monitoring"
    )


def alert_sections(event: dict) -> list[Section]:
    """Build the prioritized blocks for one alert or recovery."""
    etype = event.get("type", "alert")
    severity = str(event.get("severity") or "warning")
    target = event.get("target")
    verdict = event.get("verdict") or {}
    links = event.get("links") or {}

    sections: list[Section] = []

    if etype == "recovery":
        head = f"✅ {_b('recovered')}"
        if target:
            head += f" — {esc(target)}"
        sections.append(Section(0, head))
        duration = _duration(event.get("duration_s"))
        detail = esc(event.get("message", ""))
        rule = event.get("rule")
        if rule in WINDOWED_RULES:
            # The rule kept firing while a cut was inside its window, so the
            # alert's open time is the window, not an outage. The message
            # already says what the cuts were.
            duration = None
        verb = "was down" if rule in DOWN_RULES or not rule else "lasted"
        sections.append(
            Section(0, f"{verb} {duration} · {detail}" if duration else detail)
        )
        if event.get("rule"):
            sections.append(Section(2, _i(esc(str(event["rule"])))))
        sections.append(Section(1, _link_line(links)))
        return sections

    emoji = SEVERITY_EMOJI.get(severity, STATUS_EMOJI["watch"])
    head = f"{emoji} {_b(esc(severity))}"
    if target:
        head += f" — {esc(target)}"
    sections.append(Section(0, head))

    diag = event.get("diagnosis") or {}
    leads = _diagnosis_leads(diag, verdict)
    if leads:
        # The diagnosis replaces the verdict line: it saw the same minutes
        # with more evidence (the first hop's usual loss, a deaf radio, the
        # app layer) and says how sure it is. Two answers would read as two
        # opinions.
        emoji = DIAGNOSIS_EMOJI.get(str(diag.get("class")), "")
        sections.append(Section(0, f"{emoji} {esc(diag['summary'])} "
                                   f"{_i(esc('(' + str(diag['confidence']) + ' confidence)'))}"
                                .strip()))
    elif verdict.get("line") and not _mean_saw_nothing(verdict):
        scope_emoji = SCOPE_EMOJI.get(verdict.get("scope", ""), "")
        line = f"{scope_emoji} {esc(verdict['line'])}".strip()
        sections.append(Section(0, line))

    sections.append(Section(0, esc(event.get("message", ""))))

    # The evidence behind the diagnosis: what a reader checks before
    # believing it. Below the numbers, above the context line.
    if leads and diag.get("evidence"):
        sections.append(Section(2, "Why: " + esc("; ".join(diag["evidence"])) + "."))
    if leads and diag.get("against"):
        sections.append(Section(2, "But: " + esc("; ".join(diag["against"])) + "."))

    # Context line: the rule name (for correlating with `docker logs` and for
    # `mute rule:<name>`) plus breadth. Low priority -- it is the first thing
    # dropped when a caption budget bites.
    context = []
    if event.get("rule"):
        context.append(str(event["rule"]))
    total = verdict.get("total")
    if total:
        # The count is the verdict's: targets over the impaired threshold on
        # their 15-minute mean. A brief cut (outage) or one target's run of
        # lost cycles (target_down) fires the rule without moving that mean,
        # and "0 of 16 affected" under an alert reads as "nothing happened".
        # The message carries the rule's own count; zero is left unsaid.
        if verdict.get("affected"):
            context.append(f"{verdict['affected']} of {total} affected")
        # The verdict's reading of the line; when the diagnosis leads it has
        # already said where the fault is, and "local link cutting out" next
        # to "this host's Wi-Fi, not the line" reads as a contradiction.
        if not leads:
            context.append(
                "local link cutting out"
                if verdict.get("cpe_cutting")
                else "local link clean"
            )
    # The Wi-Fi hop, whenever it was checked: a reader should see that a
    # "your line" verdict was reached with the wireless link in view.
    wifi = verdict.get("wifi") or {}
    if wifi.get("min_dbm") is not None:
        wifi_note = f"wi-fi min {wifi['min_dbm']:.0f} dBm"
        if wifi.get("disconnects"):
            wifi_note += f", {wifi['disconnects']} drop{'s' if wifi['disconnects'] != 1 else ''}"
        context.append(wifi_note)
    if context:
        sections.append(Section(2, _i(esc(" · ".join(context)))))

    sections.append(Section(1, _link_line(links)))

    if target:
        sections.append(
            Section(3, esc(f'mute: say "mute {target} for 2h"'))
        )
    return sections


def _mean_saw_nothing(verdict: dict) -> bool:
    """The verdict compared every target's 15-minute mean and found none
    impaired. Under an alert that fired on a shorter span (a brief cut, one
    target's lost cycles), "no target is above 5% mean loss" reads as
    "nothing happened"; the alert's own message is the better account."""
    return (verdict.get("scope") == "unclear" and bool(verdict.get("total"))
            and not verdict.get("affected"))


def format_message(event: dict, limit: int = TG_TEXT_LIMIT) -> str:
    """Render one event to the text that will be delivered."""
    if event.get("type") == "report":
        return assemble([Section(0, str(event.get("message", "")))], limit)
    if event.get("type") == "digest":
        # The body is pre-rendered by digest.render(), which already builds
        # its own sections; splitting the links onto their own Section lets
        # them drop first when a caption budget bites, exactly as on alerts.
        return assemble(
            [
                Section(0, str(event.get("message", ""))),
                Section(1, _link_line(event.get("links"))),
            ],
            limit,
        )
    return assemble(alert_sections(event), limit)
