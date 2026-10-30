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

import clock
from common import definitions

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
    """``html`` (Telegram's parse mode) or ``plain``. Only Telegram parses
    the HTML -- directly, or through OpenClaw's telegram channel; a webhook
    and any other OpenClaw channel get plain text, whatever ALERT_MARKUP
    says."""
    mode = (os.environ.get("NOTIFY_MODE") or "").strip().lower()
    channel = (os.environ.get("OPENCLAW_CHANNEL") or "telegram").strip().lower()
    if mode == "webhook" or (mode == "openclaw" and channel != "telegram"):
        return "plain"
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
    """An episode's notification (policy.py), rendered by :func:`render`:
    the head in bold, the cause and the signals, and the link to the
    episode's own range. Every line is escaped -- names are user-editable.
    The signals beyond the first go first when a caption budget bites,
    then the link: the head and the cause always survive."""
    rendered = render(event)
    if rendered is None:
        return _prerendered_sections(event)
    head = rendered["head"]
    if len(head) > HEAD_MAX:
        head = head[:HEAD_MAX - 1] + "…"
    # The marker first, as every message here: "🔴 <b>Internet down…</b>".
    marker, _, words = head.partition(" ")
    sections = [Section(0, f"{esc(marker)} {_b(esc(words))}" if words and
                        not marker.isalnum() else _b(esc(head)))]
    for index, line in enumerate(rendered["lines"]):
        # The cause and the first signal stay; the contrary goes after the
        # other signals: it is what says the verdict is contested.
        keep = 0 if index < 2 else 1 if line.startswith("Against:") else 2
        sections.append(Section(keep, esc(line)))
    link = rendered.get("link")
    if link:
        label, url = link
        sections.append(Section(1, f"See {_a(url, esc(label))}" if markup() == "html"
                                else f"See {label} → {url}"))
    return sections


def _prerendered_sections(event: dict) -> list[Section]:
    """A payload with its text already written and no renderer context (a
    message made by hand, an older state's): the first line bold, the rest
    escaped, the last hours' diagnosis, the links row."""
    lines = str(event.get("message", "")).split("\n")
    head = lines[0] if len(lines[0]) <= HEAD_MAX else lines[0][:HEAD_MAX - 1] + "…"
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


# ---------------------------------------------------------------------------
# Episode messages (front 8): what a home user reads
# ---------------------------------------------------------------------------
#
# One renderer per message kind of the push policy (policy.py): the opening
# alert (``down``, ``degradation``), the ``update``, the end (``recovery``),
# the quiet hours' episode (``night``) and the monitor's own (``monitor``).
# Each is a pure function of the payload: ``episode`` (episodes.summary,
# with its ``measures``) and ``render`` (what policy.py knew when it
# decided: ``now``, the cycle ``step``, the service ``names``, the cause
# first told, why an update). No rule names, no target names, no ASNs,
# one marker, the digest's clock (clock.py).

# What each reading is, in a home user's words (the head says what
# happened; the cause line says where).
CAUSE_WORDS = {
    "upstream": "your provider, beyond the modem",
    "local_link": "your line, the modem or the line to your provider",
}
MONITOR_WORDS = {
    "radio": "this Pi's Wi-Fi radio stopped receiving",
    "watchdog": "this Pi's Wi-Fi radio stopped receiving",
    "route": "this Pi lost its own connection to the router",
    "database": "this Pi's database could not be read",
    "exporter": "this Pi's measurements stopped arriving",
    "upgrade": "an upgrade of this Pi's software",
    "dns": "this Pi's own name resolution failed",
}
# The readings the last hours' diagnosis (common.diagnosis) can give, as an
# episode's: a second opinion that disagrees is a contrary signal.
DIAGNOSIS_READING = {"local_wifi": "monitor", "monitor": "monitor",
                     "local_link": "local_link", "upstream": "upstream"}
NETWORK = frozenset({"local_link", "upstream"})
# The first hop's loss this far above its usual level is its own loss.
HOP_RISE_PCT = 5.0
# Without its usual level (no cycle before the episode), the first hop's
# loss is weighed against this: the gateway's ICMP rate-limit floor.
HOP_FLOOR_PCT = 10.0
# One destination "alone" when it lost this many times the next one.
ALONE_RATIO = 2.0


def pct(value: float | None) -> str:
    """``7%``, ``2.5%``: whole above 10 or when whole, else one decimal."""
    if value is None:
        return "?"
    value = float(value)
    if value >= 10 or value == int(value):
        return f"{value:.0f}%"
    return f"{value:.1f}%"


def span(lo: float, hi: float, now: float, sep: str = "–") -> str:
    """``19:30–20:10`` in the digest's zone, its suffix (a fallback zone's
    ``UTC``) once, after the second time."""
    a, b = clock.at(lo, now), clock.at(hi, now)
    head, _, zone = a.rpartition(" ")
    if zone and b.endswith(" " + zone) and not zone[:1].isdigit():
        a = head
    return f"{a}{sep}{b}"


def lasted(seconds: float | None) -> str:
    return _duration(seconds) if seconds and seconds >= 60 else "under a minute"


def _name(target: str, render: dict) -> str:
    return (render.get("names") or {}).get(target) or target


def _names(targets: list[str], render: dict, most: int = 3) -> str:
    """Services, each once, ``A, B and C``; ``A, B and 2 more``."""
    seen: list[str] = []
    for target in targets:
        name = _name(target, render)
        if name not in seen:
            seen.append(name)
    if not seen:
        return "one destination"
    if len(seen) > most:
        return ", ".join(seen[:most - 1]) + f" and {len(seen) - most + 1} more"
    return seen[0] if len(seen) == 1 else ", ".join(seen[:-1]) + " and " + seen[-1]


def reading(record: dict) -> str | None:
    """The reading the evidence is weighed against: the class, or below
    medium confidence the one it suspected (None: nothing fits)."""
    if record.get("class") == "undetermined":
        return record.get("suspected")
    return record.get("class")


def cause_words(record: dict, render: dict, cls: str | None = None) -> str:
    """The cause in a home user's words, for ``cls`` (default: the
    record's class)."""
    cls = cls or record.get("class")
    if cls in CAUSE_WORDS:
        return CAUSE_WORDS[cls]
    if cls == "monitor":
        return MONITOR_WORDS.get(record.get("detail") or "", "this Pi itself")
    if cls == "destination":
        if record.get("detail") == "ipv6":
            return "IPv6 only, IPv4 was fine"
        return "just " + _names(record.get("sites") or [], render)
    return "undetermined"


def what(record: dict, render: dict, down: bool | None = None) -> str:
    """What happened, for a head: Internet down, Internet with packet loss,
    packet loss at one service, IPv6 down."""
    if record.get("down") if down is None else down:
        return "Internet down"
    if record.get("class") == "monitor":
        return "This Pi not measuring"
    if record.get("microcut"):
        return f"Your line cut out for {int(record.get('longest_cut_s') or 0)} s"
    if record.get("class") == "destination":
        if record.get("detail") == "ipv6":
            return "IPv6 down"
        return "Packet loss at " + _names(record.get("sites") or [], render)
    return "Internet with packet loss"


@dataclass(frozen=True)
class Evidence:
    """One signal, and which readings it supports and contradicts."""

    text: str
    supports: frozenset = frozenset()
    contradicts: frozenset = frozenset()


def evidence(record: dict, render: dict) -> list[Evidence]:
    """Every signal the record's numbers allow, in the fixed priority:
    breadth, the first hop, this Pi's radio and route, the resolvers, a
    single destination."""
    m = record.get("measures") or {}
    out: list[Evidence] = []
    affected, reporting = int(m.get("affected") or 0), int(m.get("reporting") or 0)
    broad = (float(record.get("breadth_pct") or 0) >= definitions.MATERIAL_BREADTH_PCT
             or int(record.get("categories") or 0) >= definitions.MATERIAL_MIN_CATEGORIES)
    # 1. Breadth.
    if reporting:
        if record.get("down") or m.get("lost_all_cycles"):
            out.append(Evidence(f"all {reporting} destinations lost every packet",
                                NETWORK, frozenset({"destination"})))
        elif broad:
            out.append(Evidence(
                f"{affected} of {reporting} destinations lost packets"
                + ("" if m.get("all_at_once") else "; never all at once"),
                NETWORK, frozenset({"destination"})))
        elif affected:
            out.append(Evidence(f"only {affected} of {reporting} destinations "
                                "lost packets",
                                frozenset({"destination"}), NETWORK))
    # 2. The first hop: the router this Pi reaches the Internet through.
    hop, usual = m.get("hop_loss_pct"), m.get("hop_usual_pct")
    cut = int(m.get("longest_cut_s") or 0)
    if cut:
        out.append(Evidence("your router stopped answering for "
                            + (lasted(cut) if cut >= 60 else f"{cut} s"),
                            frozenset({"local_link"}),
                            frozenset({"upstream", "destination"})))
    elif hop is not None and hop >= 100.0 * evaluator_down_ratio():
        out.append(Evidence("your router stopped answering too",
                            frozenset({"local_link"}),
                            frozenset({"upstream", "destination"})))
    elif hop is not None and hop - (usual if usual is not None
                                    else HOP_FLOOR_PCT) >= HOP_RISE_PCT:
        out.append(Evidence(f"your router lost {pct(hop)}"
                            + (f", above its usual {pct(usual)}" if usual is not None
                               else ""),
                            frozenset({"local_link"}), frozenset({"upstream"})))
    elif hop is not None:
        out.append(Evidence("your router answered normally ("
                            + (f"{pct(hop)}, its usual {pct(usual)}" if usual is not None
                               else f"{pct(hop)} lost") + ")",
                            frozenset({"upstream", "destination"}),
                            frozenset({"local_link"})))
    elif record.get("class") in NETWORK or record.get("suspected") in NETWORK:
        out.append(Evidence("your router is not measured, so your line and "
                            "your provider cannot be told apart",
                            contradicts=NETWORK))
    # 3. This Pi's own radio and route.
    deaf = int(m.get("radio_blind_s") or 0)
    if deaf >= 60:
        out.append(Evidence(f"this Pi's own Wi-Fi was deaf for {lasted(deaf)} of it",
                            frozenset({"monitor"}),
                            NETWORK | {"destination"}))
    elif m.get("wifi") == "receiving":
        dbm = m.get("wifi_dbm")
        if dbm is not None and int(m.get("wifi_weak") or 0):
            out.append(Evidence(f"this Pi's Wi-Fi signal was weak (down to {dbm:.0f} dBm)",
                                frozenset({"monitor"}), NETWORK))
        else:
            out.append(Evidence("this Pi's Wi-Fi was stable ("
                                + (f"{dbm:.0f} dBm, " if dbm is not None else "")
                                + "no cuts)",
                                NETWORK | {"destination"}))
    # 4. The DNS resolvers.
    dns = m.get("dns")
    if dns and int(dns[1]):
        hit, count = int(dns[0]), int(dns[1])
        if hit and not (record.get("class") == "destination" and not broad):
            out.append(Evidence(f"the DNS resolvers lost packets too ({hit} of {count})",
                                NETWORK, frozenset({"destination"})))
        elif not hit:
            out.append(Evidence("the DNS resolvers answered normally",
                                frozenset({"destination"})))
    # 5. One destination standing out.
    top = standout(m)
    if top and top[1] >= definitions.MATERIAL_LOSS_PCT:
        name = _name(top[0], render)
        if record.get("class") == "destination":
            out.append(Evidence(f"{name} lost {pct(top[1])}",
                                frozenset({"destination"})))
        elif not record.get("down"):
            out.append(Evidence(f"{name} alone lost {pct(top[1])}",
                                frozenset({"destination"}), NETWORK))
    return out


def evaluator_down_ratio() -> float:
    """A first hop losing this share lost everything (evaluator's own)."""
    import evaluator  # late: templates is imported by modules evaluator needs
    return float(evaluator.DOWN_LOSS_RATIO)


def choose(record: dict, render: dict, diagnosis: dict | None = None
           ) -> tuple[list[str], str | None]:
    """Up to three supporting signals and the strongest contrary one, by
    the fixed priority, for the record's reading. With no reading (nothing
    fits), the first three signals and no contrary."""
    cls = reading(record)
    found = evidence(record, render)
    if cls is None:
        return [e.text for e in found[:3]], None
    pro = [e.text for e in found if cls in e.supports][:3]
    con = next((e.text for e in found if cls in e.contradicts), None)
    if con is None and diagnosis and diagnosis.get("confidence") in ("high", "medium"):
        other = DIAGNOSIS_READING.get(diagnosis.get("class") or "")
        if other and other != cls:
            words = (MONITOR_WORDS["radio"] if other == "monitor"
                     else CAUSE_WORDS[other])
            con = (f"the last hours' diagnosis points at {words} "
                   f"({diagnosis['confidence']} confidence)")
    return pro, con


def _link(lo: float, hi: float, render: dict, about: str = "network"
          ) -> tuple[str, str] | None:
    """(``19:30–20:10``, its dashboard URL), or None without links."""
    from common import links  # late: optional in a message

    try:
        url = links.range_link(lo, hi, about)
    except Exception:  # noqa: BLE001 - a link never costs a message
        return None
    return (span(lo, hi, float(render["now"])), url) if url else None


def _cause_line(record: dict, render: dict) -> str:
    if record.get("class") == "undetermined":
        return "Cause: undetermined."
    conf = record.get("confidence")
    return (f"Probable cause: {cause_words(record, render)}"
            + (f" ({conf} confidence)." if conf else "."))


def opening(event: dict) -> dict:
    """``down`` / ``degradation``: what and since when, the probable cause
    with its confidence, up to three signals and the contrary one, and the
    episode's range."""
    record, render = event["episode"], event["render"]
    now, step = float(render["now"]), float(render.get("step") or 300)
    start = float(record["start"])
    marker = "🔴" if event.get("kind") == "down" else STATUS_EMOJI["watch"]
    head = (f"{marker} {what(record, render, event.get('kind') == 'down')} "
            f"since {clock.at(start, now)} ({lasted(now - start)})")
    pro, con = choose(record, render, event.get("diagnosis"))
    lines = [_cause_line(record, render)]
    if record.get("class") == "undetermined" and record.get("suspected"):
        hint = cause_words(record, render, record["suspected"])
        pro = [f"the strongest hint: {hint} (low confidence)"] + pro[:2]
    lines += [f"• {p}" for p in pro]
    if con:
        lines.append(f"Against: {con}.")
    if render.get("assistant"):
        lines.append('Reply "ack" to the assistant to silence this episode.')
    return {"head": head, "lines": lines,
            "link": _link(start - step, now, render)}


def update(event: dict) -> dict:
    """One line: what changed in an episode already told."""
    record, render = event["episode"], event["render"]
    now = float(render["now"])
    why = render.get("why") or "it changed"
    return {"head": f"🔁 Update on {subject(record, render, render.get('was_down'))} "
                    f"since {clock.at(float(record['start']), now)}: {why}",
            "lines": [], "link": None}


def subject(record: dict, render: dict, down: bool | None = None) -> str:
    """What an episode is, after "on" or "about": the Internet outage, the
    packet loss (at one service), the IPv6 outage."""
    if record.get("down") if down is None else down:
        return "the Internet outage"
    if record.get("class") == "destination":
        if record.get("detail") == "ipv6":
            return "the IPv6 outage"
        return "the packet loss at " + _names(record.get("sites") or [], render)
    return "the packet loss"


def update_why(record: dict, render: dict, told: dict) -> str:
    """Why an update is sent, in words: escalated, reclassified, widened."""
    m = record.get("measures") or {}
    if told.get("escalated"):
        return "now the Internet is down."
    if told.get("reclassed"):
        before = told.get("words")
        return (f"the probable cause is now {cause_words(record, render)} "
                + (f"(it was {before})." if before and before != "undetermined"
                   else "(it was undetermined)."))
    return (f"now {m.get('affected', 0)} of {m.get('reporting', 0)} "
            "destinations are losing packets.")


def ending(event: dict) -> dict:
    """``recovery``: the real duration, who was affected, the final cause,
    and that it corrects what was said when the reading changed."""
    record, render = event["episode"], event["render"]
    now, step = float(render["now"]), float(render.get("step") or 300)
    lo, hi = float(record["start"]), float(record["end"])
    told_down = render.get("told_kind") == "down" or record.get("down")
    took = lasted(record.get("duration_s"))
    when = (f"after {took}" if took != "under a minute" else "within a minute") + \
        f" ({span(lo, hi, now, ' to ')})"
    if told_down:
        head = (f"✅ Internet back after {took} down "
                f"({span(lo, hi, now, ' to ')})")
    elif record.get("class") == "destination":
        head = f"✅ {_names(record.get('sites') or [], render)} fine again {when}"
    else:
        head = f"✅ Packet loss over {when}"
    cause = _cause_line(record, render).replace("Probable cause", "Cause")
    if record.get("class") == "undetermined" and record.get("suspected"):
        cause = (f"Cause: undetermined (it looked most like "
                 f"{cause_words(record, render, record['suspected'])}).")
    told = render.get("told_class")
    if (told and told != "undetermined" and told != record.get("class")
            and render.get("told_words")):
        which = "the last update" if render.get("updates") else "the first message"
        cause += f" This corrects {which}, which said {render['told_words']}."
    lines = [cause, "Affected: " + _affected(record, render) + "."]
    return {"head": head, "lines": lines,
            "link": _link(lo - step, hi + step, render)}


def _affected(record: dict, render: dict) -> str:
    m = record.get("measures") or {}
    affected, reporting = int(m.get("affected") or 0), int(m.get("reporting") or 0)
    if record.get("class") == "destination":
        return _names(record.get("sites") or [], render) + " only"
    if record.get("down") or m.get("lost_all_cycles"):
        return f"all {reporting} destinations"
    text = f"{affected} of {reporting} destinations" if reporting else "the destinations"
    top = standout(m)
    if top:
        text += f"; the worst, {_name(top[0], render)}, lost {pct(top[1])}"
    return text


def standout(measures: dict) -> tuple[str, float] | None:
    """The destination that lost clearly more than any other (ALONE_RATIO
    times the next), with its loss; None when none stands out."""
    worst = measures.get("worst") or []
    if worst and (len(worst) == 1
                  or float(worst[0][1]) >= ALONE_RATIO * float(worst[1][1])):
        return worst[0][0], float(worst[0][1])
    return None


def night(event: dict) -> dict:
    """An episode that began and ended in quiet hours: one line of their
    morning summary (quiet.py), and the same text alone if it is sent."""
    record, render = event["episode"], event["render"]
    now = float(render["now"])
    lo, hi = float(record["start"]), float(record["end"])
    step = float(render.get("step") or 300)
    head = (f"🌙 In the night: {what(record, render)}, {span(lo, hi, now, ' to ')} "
            f"({lasted(record.get('duration_s'))})")
    return {"head": head, "lines": [_cause_line(record, render)],
            "link": _link(lo - step, hi + step, render)}


def _watchdog_did(text: str) -> str:
    """What the watchdog did (cli/lib/watchdog.sh), in a user's words."""
    low = text.lower()
    if "reload" in low:
        return "reloaded the Wi-Fi driver"
    if "reboot" in low:
        return "rebooted this Pi"
    if "restart" in low:
        return "restarted the measurements"
    return text


def monitor(event: dict) -> dict:
    """``monitor``: what could not be measured, from when to when, why,
    what the watchdog did, and that the network meanwhile is unknown."""
    record, render = event["episode"], event["render"]
    now, step = float(render["now"]), float(render.get("step") or 300)
    lo, hi = record.get("blind_range") or (record["start"], record["end"])
    lo, hi = float(lo), float(hi)
    head = (f"🛠 This Pi could not measure the network from {span(lo, hi, now, ' to ')} "
            f"({lasted(hi - lo)})")
    detail = (record.get("detail") if record.get("class") == "monitor"
              else render.get("blind_reason"))
    cause = MONITOR_WORDS.get(detail or "", "this Pi itself")
    conf = record.get("confidence") if record.get("class") == "monitor" else None
    lines = [f"Cause: {cause}" + (f" ({conf} confidence)." if conf else ".")]
    actions = [(at, _watchdog_did(str(text)))
               for at, text in record.get("watchdog_actions") or () if text]
    if actions:
        lines.append("The watchdog " + "; then ".join(
            f"{did} at {clock.at(float(at), now)}" if at is not None else did
            for at, did in actions[:2]) + ".")
    else:
        lines.append("The watchdog took no action.")
    lines.append("What the network did meanwhile is unknown.")
    about = "radio" if detail in ("radio", "watchdog", "route") else "network"
    return {"head": head, "lines": lines, "link": _link(lo - step, hi + step, render, about)}


RENDERERS = {"down": opening, "degradation": opening, "update": update,
             "recovery": ending, "night": night, "monitor": monitor}


def render(event: dict) -> dict | None:
    """``{"head", "lines", "link"}`` for an episode payload, or None for
    one this module cannot render (no ``render`` context)."""
    renderer = RENDERERS.get(event.get("kind") or "")
    if renderer is None or not event.get("episode") or not event.get("render"):
        return None
    return renderer(event)


def plain(event: dict) -> str:
    """The rendered message as plain text: the payload's ``message``, which
    the ledger, the outbox's summaries and a webhook read."""
    rendered = render(event)
    if rendered is None:
        return str(event.get("message", ""))
    lines = [rendered["head"], *rendered["lines"]]
    if rendered.get("link"):
        label, url = rendered["link"]
        lines.append(f"See {label} → {url}")
    return "\n".join(lines)


def summary_entry(payload: dict) -> dict:
    """An episode's line in a bundled summary (the outbox's, quiet hours'):
    what happened as its name, the cause as its text, and the marker of
    the episode, not of the message (an end is the end of an outage)."""
    record = payload.get("episode") or {}
    render = payload.get("render") or {"names": {}}
    if payload.get("kind") == "monitor" or record.get("class") == "monitor":
        detail = (record.get("detail") if record.get("class") == "monitor"
                  else render.get("blind_reason"))
        return {"rule": "This Pi could not measure", "target": None,
                "severity": "info",
                "message": "cause: " + MONITOR_WORDS.get(detail or "", "this Pi itself")}
    cause = ("cause undetermined" if record.get("class") == "undetermined"
             else "probable cause: " + cause_words(record, render))
    return {"rule": what(record, render), "target": None, "message": cause,
            "severity": "critical" if record.get("down") else "warning"}
