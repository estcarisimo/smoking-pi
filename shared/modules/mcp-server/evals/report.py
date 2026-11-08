#!/usr/bin/env python3
"""Score the assistant's answers to the question bank and write the report.

shared/scripts/assistant-eval.sh asks each question in questions.yaml
through `openclaw agent` and keeps, per question, three raw files in one
directory:

    <id>.answer.json  what `openclaw agent --json` printed
    <id>.log          `docker logs --timestamps` of the MCP server around
                      the turn (its `mcp.tools: tool=...` lines are the
                      evidence of which tools the agent called)
    <id>.meta         "start=<epoch> end=<epoch> rc=<exit code>"

This turns them into one Markdown report: a row per question with the
tools called and every check that failed, then each answer in full. The
log is windowed here, by Docker's own UTC timestamps, so a line from a
heartbeat just before or after the turn is not credited to it.

    python3 report.py --bank questions.yaml --raw DIR --zone America/Chicago \
        [--judgments judgments.yaml] [--title "..."] > report.md
    python3 report.py --bank questions.yaml --list     # id<TAB>ask, for the runner
    python3 report.py --answered <id>.answer.json      # exit 1: never answered

Needs only the standard library and PyYAML (python3-yaml on Debian).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

# ---------------------------------------------------------------------------
# The MCP server's log
# ---------------------------------------------------------------------------

# Docker's --timestamps prefix: RFC 3339, UTC, nanoseconds.
_DOCKER_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z\s+(.*)$")
# server.logged_tool: "tool=NAME args=ARGS -> RESULT in Nms", or the
# read-only refusal "tool=NAME refused: read-only connector".
_TOOL = re.compile(r"mcp\.tools: tool=(\w+)\s+(?:args=(.*?) -> (.*?) in \d+ms|(refused: .*))\s*$")
_ARG = re.compile(r"(\w+)=(.*?)(?=\s\w+=|$)")


@dataclass
class ToolCall:
    tool: str
    args: dict[str, str]
    result: str
    at: float


def parse_args(text: str) -> dict[str, str]:
    """`hours=6 target=Google` -> {"hours": "6", "target": "Google"}; "-" is none."""
    text = (text or "").strip()
    if not text or text == "-":
        return {}
    return {m.group(1): m.group(2) for m in _ARG.finditer(text)}


def parse_log(text: str, start: float, end: float) -> list[ToolCall]:
    """The tool calls logged between start and end (epoch seconds, inclusive)."""
    calls = []
    for line in text.splitlines():
        stamp = _DOCKER_TS.match(line)
        if not stamp:
            continue
        at = datetime.strptime(stamp.group(1), "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc).timestamp()
        if stamp.group(2):
            at += float("0." + stamp.group(2))
        if not start <= at <= end:
            continue
        tool = _TOOL.search(stamp.group(3))
        if not tool:
            continue
        refused = tool.group(4)
        calls.append(ToolCall(tool=tool.group(1),
                              args=parse_args(tool.group(2) or ""),
                              result=refused or (tool.group(3) or ""),
                              at=at))
    return calls


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


def parse_answer(text: str) -> tuple[str, bool]:
    """(answer text, whether it carried media) from `openclaw agent --json`.

    Anything that is not that JSON (an error, a timeout's partial output)
    is returned as the answer itself, so the report shows what was said.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return (text or "").strip(), False
    payloads = ((data or {}).get("result") or {}).get("payloads") or []
    parts = [p.get("text") or "" for p in payloads if isinstance(p, dict)]
    media = any(p.get("mediaUrl") or p.get("mediaUrls") for p in payloads
                if isinstance(p, dict))
    return "\n\n".join(t for t in parts if t).strip(), media


def _is_error(text: str) -> bool:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return True
    return not isinstance(data, dict) or data.get("ok") is False


_MASKS = [
    # Tunnel hosts name the tailnet; Telegram chats and bot tokens; UUIDs
    # (session and run ids); anything that looks like a bearer or token.
    (re.compile(r"\b[\w-]+\.[\w-]+\.ts\.net\b"), "<host>.<tailnet>.ts.net"),
    # Email addresses, plain or URL-encoded (a mail link's authuser=).
    (re.compile(r"[\w.+-]+(?:@|%40)[\w-]+(?:\.[\w-]+)+"), "<email>"),
    (re.compile(r"\bbot\d+:[\w-]{20,}"), "bot<masked>"),
    (re.compile(r"(?<![\d.])-100\d{6,}\b"), "<chat-id>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"(?i)\b(token|bearer|apikey|api_key|password)([=: ]+)\S+"), r"\1\2<masked>"),
]


def mask(text: str) -> str:
    for pattern, replacement in _MASKS:
        text = pattern.sub(replacement, text)
    return text


_URL = re.compile(r"https?://\S+|<a [^>]*>")


def prose(text: str) -> str:
    """The answer without its links: a link's epoch-ms range and its
    `var-target=Netflix_1_DFW` are right there, and not what the person reads."""
    return _URL.sub(" ", text)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

# Internal ids the person never sees: target names (uba_ar, Netflix_1_DFW,
# W_netflix_com_h2), category keys (top_sites) and tool or field names. No
# word a person reads has an underscore in it, so any snake_case word is one;
# a list of known shapes missed `uba_ar` on the first after-run.
RAW_TARGET_ID = re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*[A-Za-z0-9]\b")
ISO_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
EPOCH = re.compile(r"\b1\d{9}(?:\d{3})?\b")
ALL_CLEAR = re.compile(r"(?i)\ball[ -]clear\b")
CLOCK = re.compile(r"(?i)\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?(?:am|pm|a\.m\.|p\.m\.)(?!\w)")
DRAWN_CHART = re.compile(r"[▁▂▃▄▅▆▇█]{4,}|```")
# Zone labels a reader might see; whichever are not the messages' zone are
# "another zone". Kept to the unambiguous ones (no "AST", no "IST").
ZONE_LABELS = ["UTC", "GMT", "ET", "EST", "EDT", "CT", "CST", "CDT", "MT",
               "MST", "MDT", "PT", "PST", "PDT", "ART", "BRT", "CET", "CEST",
               "BST", "WET", "WEST", "JST", "AEST", "AEDT"]

_ES = {"de", "la", "el", "que", "en", "los", "las", "una", "por", "con", "para",
       "es", "está", "fue", "hubo", "pérdida", "conexión", "anoche", "semana",
       "sin", "nada", "señal", "también", "pero", "del", "al", "lo", "ayer"}
_EN = {"the", "and", "of", "to", "is", "was", "with", "for", "on", "at", "it",
       "no", "your", "loss", "connection", "last", "night", "week", "signal",
       "but", "were", "there", "from", "this", "that", "not", "yesterday"}


def zone_labels(zone: str) -> set[str]:
    """What names the zone in an answer: its abbreviations through the year,
    the generic US form (CST/CDT -> CT), its city, and the key itself."""
    tz = ZoneInfo(zone)
    labels = {zone, zone.split("/")[-1].replace("_", " ")}
    for month in (1, 7):
        abbr = datetime(2026, month, 15, 12, tzinfo=tz).tzname() or ""
        if abbr and not abbr.startswith(("+", "-")):
            labels.add(abbr)
            if len(abbr) == 3 and abbr[1] in "SD" and abbr.endswith("T"):
                labels.add(abbr[0] + "T")
    if zone == "UTC":
        labels |= {"UTC", "GMT", "Z"}
    if zone.startswith("America/") and {"CT", "CST"} & labels:
        labels.add("Central")
    return labels


def language(text: str) -> str:
    words = re.findall(r"[a-záéíóúñü]+", text.lower())
    es = sum(w in _ES for w in words)
    en = sum(w in _EN for w in words)
    return "es" if es > en else "en"


# The checks every question gets; a question opts out by name (`skip`).
GLOBAL_CHECKS = [
    "tool-called", "no-raw-id", "no-iso-time", "no-epoch", "zone-named",
    "no-other-zone", "no-all-clear", "no-image", "no-drawn-chart", "language",
]


@dataclass
class Result:
    id: str
    lang: str
    ask: str
    answer: str
    media: bool
    calls: list[ToolCall]
    rc: int
    failures: list[str] = field(default_factory=list)
    judged: str | None = None   # a human's note when a judgment overrides
    passed: bool = False
    facts: str = ""

    @property
    def unanswered(self) -> bool:
        """The agent never answered (a gateway or model error): a fact about
        the host that run was on, not about the guide."""
        return self.rc != 0 and not self.answer


def score(question: dict, answer: str, media: bool, calls: list[ToolCall],
          zone: str, rc: int = 0) -> list[str]:
    """Every check the answer failed, by name and why. Empty is a pass."""
    fails: list[str] = []
    tools = question.get("tools") or {}
    skip = set(question.get("skip") or [])
    called = [c.tool for c in calls]
    text = prose(answer)

    if rc != 0:
        fails.append(f"agent-failed: openclaw exited {rc}")
    if not answer:
        fails.append("no-answer: the agent said nothing")

    if "tool-called" not in skip and not tools.get("allow_no_call") and not called:
        fails.append("tool-called: no MCP tool was called")
    if tools.get("any") and not set(tools["any"]) & set(called):
        fails.append("tools.any: called none of " + ", ".join(tools["any"]))
    for name in tools.get("all") or []:
        if name not in called:
            fails.append(f"tools.all: {name} was not called")
    forbidden = set(tools.get("none") or [])
    asked_chart = "get_chart" in (tools.get("any") or []) + (tools.get("all") or [])
    if not asked_chart and "no-image" not in skip:
        forbidden.add("get_chart")
    for name in sorted(forbidden & set(called)):
        fails.append(f"tools.none: {name} was called")
    for tool, wanted in (question.get("args") or {}).items():
        ok = any(c.tool == tool and all(re.search(rx, c.args.get(arg, ""))
                                        for arg, rx in wanted.items())
                 for c in calls)
        if not ok:
            seen = [c.args for c in calls if c.tool == tool]
            fails.append(f"args: no {tool} call with {wanted} (saw {seen or 'none'})")

    for rule in question.get("must") or []:
        if not re.search(rule["re"], answer, re.IGNORECASE):
            fails.append(f"must: {rule['name']}")
    for rule in question.get("must_not") or []:
        if re.search(rule["re"], text, re.IGNORECASE):
            fails.append(f"must_not: {rule['name']}")

    def check(name: str, bad: bool, why: str) -> None:
        if name not in skip and bad:
            fails.append(f"{name}: {why}")

    raw = RAW_TARGET_ID.search(text)
    check("no-raw-id", bool(raw), f"internal target id {raw.group(0) if raw else ''!r}")
    check("no-iso-time", bool(ISO_TIMESTAMP.search(text)), "an ISO timestamp is not a time")
    check("no-epoch", bool(EPOCH.search(text)), "an epoch number in the prose")
    labels = zone_labels(zone)
    named = any(re.search(rf"(?<![\w/]){re.escape(label)}(?![\w/])", text)
                for label in labels)
    check("zone-named", bool(CLOCK.search(text)) and not named,
          f"a clock time without the zone ({zone})")
    others = [label for label in ZONE_LABELS if label not in labels
              and re.search(rf"(?<![\w/:.-]){re.escape(label)}(?![\w/:-])", text)]
    check("no-other-zone", bool(others), "names " + ", ".join(others))
    check("no-all-clear", bool(ALL_CLEAR.search(text)), "\"all clear\" filler")
    check("no-image", media and not asked_chart, "sent an image nobody asked for")
    check("no-drawn-chart", bool(DRAWN_CHART.search(answer)),
          "a drawn chart or code block")
    if answer:
        got = language(text)
        check("language", got != question.get("lang", "en"),
              f"answered in {got}, asked in {question.get('lang', 'en')}")
    return fails


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def load_bank(path: Path) -> list[dict]:
    bank = yaml.safe_load(path.read_text())
    questions = bank["questions"]
    ids = [q["id"] for q in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("question ids must be unique")
    return questions


def read_meta(path: Path) -> dict[str, float]:
    meta = {}
    if path.exists():
        for part in path.read_text().split():
            key, _, value = part.partition("=")
            if value:
                meta[key] = float(value)
    return meta


def collect(questions: list[dict], raw: Path, zone: str,
            judgments: dict | None = None) -> list[Result]:
    """One Result per question that was asked (has a .meta), in bank order."""
    judgments = judgments or {}
    results = []
    for q in questions:
        meta = read_meta(raw / f"{q['id']}.meta")
        if not meta:
            continue
        answer_file = raw / f"{q['id']}.answer.json"
        log_file = raw / f"{q['id']}.log"
        answer, media = parse_answer(answer_file.read_text() if answer_file.exists() else "")
        answer = mask(answer)
        calls = parse_log(log_file.read_text() if log_file.exists() else "",
                          meta.get("start", 0), meta.get("end", float("inf")))
        rc = int(meta.get("rc", 0))
        result = Result(id=q["id"], lang=q.get("lang", "en"), ask=q["ask"],
                        answer=answer, media=media, calls=calls, rc=rc,
                        facts=" ".join((q.get("facts") or "").split()))
        result.failures = score(q, answer, media, calls, zone, rc)
        result.passed = not result.failures
        verdict = judgments.get(q["id"])
        if verdict is not None:
            if "pass" in verdict:
                result.passed = bool(verdict["pass"])
                result.judged = verdict.get("note", "")
            if verdict.get("withhold"):
                # An answer that read the owner's private data (mail,
                # calendar) is scored, then never printed.
                result.answer = f"(withheld: {verdict['withhold']})"
        results.append(result)
    return results


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _calls_text(calls: list[ToolCall]) -> str:
    if not calls:
        return "—"
    return ", ".join(c.tool + (f"({' '.join(f'{k}={v}' for k, v in c.args.items())})"
                               if c.args else "") for c in calls)


def render(results: list[Result], title: str, zone: str, notes: str = "") -> str:
    passed = sum(r.passed for r in results)
    unanswered = [r.id for r in results if r.unanswered and not r.passed]
    score = f"**{passed} of {len(results)} pass.**"
    if unanswered:
        score += (f" {len(unanswered)} never answered (the agent failed before "
                  f"replying: {', '.join(unanswered)}); re-ask them with --only.")
    lines = [f"# {title}", "",
             score + f" Zone checked: `{zone}`. "
             "Tools are the MCP server's own log lines inside each turn; "
             "ids, tokens and tunnel hosts are masked.", ""]
    if notes:
        lines += [notes.strip(), ""]
    lines += ["| # | Question | Tools called | Result | Why |",
              "| --- | --- | --- | --- | --- |"]
    for n, r in enumerate(results, 1):
        verdict = ("✅ pass" if r.passed else
                   "⚠️ no answer" if r.unanswered else "❌ fail")
        if r.judged is not None:
            verdict += " (judged)"
        why = "; ".join(r.failures) or "—"
        if r.judged:
            why += f" — judged: {r.judged}"
        lines.append(f"| {n} | `{r.id}` {_cell(r.ask)} | {_cell(_calls_text(r.calls))} "
                     f"| {verdict} | {_cell(why)} |")
    lines.append("")
    for n, r in enumerate(results, 1):
        lines += [f"## {n}. `{r.id}` — {'pass' if r.passed else 'FAIL'}", "",
                  f"**Asked ({r.lang}):** {r.ask}", "",
                  f"**Tools:** {_calls_text(r.calls)}"
                  + (" · **image attached**" if r.media else ""), ""]
        if r.facts:
            lines += [f"**A right answer:** {r.facts}", ""]
        if r.failures:
            lines += ["**Failed checks:**", ""] + [f"- {f}" for f in r.failures] + [""]
        if r.judged:
            lines += [f"**Judged by hand:** {r.judged}", ""]
        body = r.answer or "(no answer)"
        lines += ["**Answer:**", ""] + ["> " + line if line else ">"
                                        for line in body.splitlines()] + [""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bank", type=Path, default=Path(__file__).with_name("questions.yaml"))
    parser.add_argument("--raw", type=Path, help="directory of <id>.answer.json/.log/.meta")
    parser.add_argument("--zone", default="UTC", help="the messages' zone (DIGEST_TZ, else TZ)")
    parser.add_argument("--judgments", type=Path,
                        help="YAML {id: {pass: bool, note: str, withhold: why}}: a human "
                             "verdict over the checks, or an answer not to print")
    parser.add_argument("--title", default="Assistant question bank")
    parser.add_argument("--notes", default="", help="text under the score line")
    parser.add_argument("--list", action="store_true", help="print id<TAB>ask and exit")
    parser.add_argument("--answered", type=Path, metavar="ANSWER_JSON",
                        help="exit 0 if this openclaw --json output has an answer, else 1")
    args = parser.parse_args(argv)
    if args.answered:
        text = args.answered.read_text() if args.answered.exists() else ""
        return 0 if parse_answer(text)[0] and not _is_error(text) else 1
    questions = load_bank(args.bank)
    if args.list:
        for q in questions:
            print(f"{q['id']}\t{q['ask']}")
        return 0
    if not args.raw:
        parser.error("--raw is required unless --list")
    judgments = yaml.safe_load(args.judgments.read_text()) if args.judgments else None
    results = collect(questions, args.raw, args.zone, judgments)
    print(render(results, args.title, args.zone, args.notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
