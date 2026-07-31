"""Turn collected InfluxDB aggregates into a plain-language health report.

Flow: render the collector's dict into a compact text block, send it to
Claude with a fixed analyst system prompt, and write the returned markdown
to REPORTS_DIR (``report-YYYYMMDD-HHMMSS.md`` plus ``latest.md``).

Guardrails:
- Missing ANTHROPIC_API_KEY is not an error: log and skip (exit 0) so a
  cron/loop deployment without a key never crash-loops.
- AI_MAX_INPUT_CHARS (default 20000) caps the rendered payload; anything
  beyond is truncated with an explicit note so the model knows.
- AI_REPORTS_PER_DAY (default 8) caps API spend via a small JSON state
  file in REPORTS_DIR.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path

from jinja2 import Template

logger = logging.getLogger("ai-insights")

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_REPORTS_DIR = "/reports"
DEFAULT_MAX_INPUT_CHARS = 20_000
DEFAULT_REPORTS_PER_DAY = 8
MAX_OUTPUT_TOKENS = 2048

STATE_FILENAME = ".reporter-state.json"

TRUNCATION_NOTE = (
    "\n[NOTE: the measurement summary above was truncated to fit the "
    "input limit; some targets/windows are omitted.]\n"
)

SYSTEM_PROMPT = (
    "You are a network health analyst for a home/SMB SmokePing monitoring "
    "system. You receive aggregated latency, packet-loss, and CPE microcut "
    "statistics for a time window. Produce a plain-language report in "
    "Markdown with these sections: overall health, notable degradations "
    "and microcuts, trends versus the numbers given, and concrete action "
    "suggestions. Be concrete and specific -- cite target names and "
    "numbers from the data. No fluff, no generic advice, no preamble. "
    "If the data shows nothing abnormal, say so briefly."
)

_SUMMARY_TEMPLATE = Template(
    """\
SmokePing measurement summary
Window: last {{ data.window_hours }}h (generated {{ data.generated_at }})
Targets with data: {{ data.target_total }}\
{% if data.targets_truncated %} (showing worst {{ data.targets|length }} by loss){% endif %}

Per-target stats (latency in ms; loss in %; loss_events = samples with >=5% loss):
{% for t in data.targets -%}
- {{ t.target }} [{{ t.measurement }}]: median={{ t.median_ms|default('n/a') }}ms \
p95={{ t.p95_ms|default('n/a') }}ms avg_loss={{ t.avg_loss_pct }}% \
max_loss={{ t.max_loss_pct|default(0) }}% loss_events={{ t.loss_events }}
{% else -%}
(no latency/dns data points in this window)
{% endfor %}
CPE microcuts (high-frequency local-link probing; loss in %):
{% for c in data.cpe.stats -%}
- {{ c.target }}/{{ c.protocol }}: lossy_windows={{ c.lossy_windows }} \
max_loss={{ c.max_loss_pct|default(0) }}% \
median_jitter={{ c.median_jitter_ms|default('n/a') }}ms
{% else -%}
(no cpe_latency data in this window)
{% endfor %}
{% if data.cpe.worst_windows -%}
Worst CPE windows:
{% for w in data.cpe.worst_windows -%}
- {{ w.time }} {{ w.target }}/{{ w.protocol }}: {{ w.loss_pct }}% loss
{% endfor %}
{%- endif %}
"""
)


def reports_dir() -> Path:
    return Path(os.environ.get("REPORTS_DIR", DEFAULT_REPORTS_DIR))


def render_summary(data: dict) -> str:
    """Render the collected dict into the compact text block sent to Claude.

    Truncates to AI_MAX_INPUT_CHARS with an explicit note.
    """
    max_chars = int(os.environ.get("AI_MAX_INPUT_CHARS", DEFAULT_MAX_INPUT_CHARS))
    text = _SUMMARY_TEMPLATE.render(data=data)
    if len(text) > max_chars:
        keep = max(0, max_chars - len(TRUNCATION_NOTE))
        text = text[:keep] + TRUNCATION_NOTE
    return text


# ---------------------------------------------------------------------------
# Reports-per-day cap (state file)
# ---------------------------------------------------------------------------


def _state_path() -> Path:
    return reports_dir() / STATE_FILENAME


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _reports_today(state: dict) -> int:
    if state.get("date") != date.today().isoformat():
        return 0
    try:
        return int(state.get("count", 0))
    except (TypeError, ValueError):
        return 0


def _record_report() -> None:
    state = _load_state()
    count = _reports_today(state) + 1
    _state_path().write_text(
        json.dumps({"date": date.today().isoformat(), "count": count}),
        encoding="utf-8",
    )


def _daily_cap_reached() -> bool:
    cap = int(os.environ.get("AI_REPORTS_PER_DAY", DEFAULT_REPORTS_PER_DAY))
    return _reports_today(_load_state()) >= cap


# ---------------------------------------------------------------------------
# Claude call + report writing
# ---------------------------------------------------------------------------


def generate_report(summary_text: str) -> str:
    """Call Claude and return the markdown report body."""
    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("AI_MODEL", DEFAULT_MODEL)
    # Non-streaming is fine: haiku responses at this size return in seconds.
    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": summary_text}],
    )
    parts = [
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text"
    ]
    return "\n".join(parts).strip()


def write_report(markdown: str, now: datetime | None = None) -> Path:
    """Write the timestamped report file and refresh ``latest.md``."""
    now = now or datetime.now()
    out_dir = reports_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Network health report\n\n"
        f"_Generated {now.isoformat(timespec='seconds')} by ai-insights "
        f"({os.environ.get('AI_MODEL', DEFAULT_MODEL)})_\n\n"
    )
    body = header + markdown.strip() + "\n"
    stem = f"report-{now.strftime('%Y%m%d-%H%M%S')}"
    path = out_dir / f"{stem}.md"
    counter = 1
    while path.exists():  # same-second re-runs must not overwrite
        path = out_dir / f"{stem}-{counter}.md"
        counter += 1
    path.write_text(body, encoding="utf-8")
    (out_dir / "latest.md").write_text(body, encoding="utf-8")
    return path


def run_once() -> Path | None:
    """Collect, generate, and write a single report.

    Returns the report path, or None when skipped (no API key, daily cap).
    Never raises for the "not configured" case so schedulers don't
    crash-loop.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.info(
            "ANTHROPIC_API_KEY is not set -- skipping report generation. "
            "Set the key in .env to enable AI insights."
        )
        return None
    if _daily_cap_reached():
        logger.warning(
            "AI_REPORTS_PER_DAY cap reached -- skipping report generation."
        )
        return None

    import collector

    hours = int(os.environ.get("REPORT_WINDOW_HOURS", 24))
    data = collector.collect(hours=hours)
    summary = render_summary(data)
    markdown = generate_report(summary)
    path = write_report(markdown)
    _record_report()
    logger.info("Wrote report %s", path)
    return path
