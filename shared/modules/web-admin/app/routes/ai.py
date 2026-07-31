"""AI routes: generated health reports + the in-UI assistant chat.

- GET  /ai/reports       list + render markdown reports from REPORTS_DIR
- GET  /ai/chat          assistant chat page
- POST /ai/chat          run one assistant turn (Anthropic tool-use loop)
- POST /ai/chat/execute  execute (or decline) a confirmed mutating action

Design notes:
- Responses are non-streaming: Haiku answers small tool-augmented prompts
  in a couple of seconds, so SSE plumbing isn't worth the complexity.
- The client owns the conversation history and sends the full ``messages``
  array each turn; the server is stateless.
- Mutating tools (add/remove/toggle target) are never executed inside the
  chat loop. The loop stops and returns a ``pending_action``; the UI shows
  a confirm card and, on approval, POSTs /ai/chat/execute which runs the
  tool and resumes the conversation.
- If ANTHROPIC_API_KEY is unset, pages render a "not configured" state and
  the JSON endpoints return 503 -- never a 500.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request
from markupsafe import Markup, escape

from app.services import ai_tools

ai_bp = Blueprint('ai', __name__)

DEFAULT_MODEL = 'claude-haiku-4-5-20251001'
MAX_TOOL_ROUNDS = 6          # max model<->tool round-trips per POST
MAX_HISTORY_MESSAGES = 80    # bound prompt size / cost per request
MAX_OUTPUT_TOKENS = 1024

CHAT_SYSTEM_PROMPT = (
    "You are the built-in assistant of a SmokePing network-monitoring "
    "system running on a Raspberry Pi. You can inspect latency/loss/"
    "microcut statistics and manage monitoring targets through your tools. "
    "Target changes (add/remove/toggle) require the user to confirm a "
    "pending action card in the UI before they run. Configuration is "
    "regenerated automatically after target changes. Be concise and "
    "concrete; cite target names and numbers from tool results."
)

_REPORT_NAME_RE = re.compile(r'^(report-\d{8}-\d{6}(-\d+)?|latest)\.md$')


def _ai_configured() -> bool:
    return bool(os.environ.get('ANTHROPIC_API_KEY'))


def _model() -> str:
    return os.environ.get('AI_MODEL', DEFAULT_MODEL)


def _reports_dir() -> Path:
    return Path(os.environ.get('REPORTS_DIR', '/reports'))


# ---------------------------------------------------------------------------
# Minimal markdown -> safe HTML (headers / bold / italics / lists / code).
# Deliberately tiny instead of pulling in a markdown dependency; ALL input
# is HTML-escaped before any tags are added.
# ---------------------------------------------------------------------------

_INLINE_CODE_RE = re.compile(r'`([^`]+)`')
_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
_ITALIC_RE = re.compile(r'(?<!\*)\*([^*\n]+)\*(?!\*)')


def _inline_md(text: str) -> str:
    text = _INLINE_CODE_RE.sub(r'<code>\1</code>', text)
    text = _BOLD_RE.sub(r'<strong>\1</strong>', text)
    text = _ITALIC_RE.sub(r'<em>\1</em>', text)
    return text


def markdown_to_html(source: str) -> Markup:
    """Convert a small markdown subset to HTML with all input escaped."""
    out = []
    in_code = False
    in_list = False
    for raw_line in source.splitlines():
        line = str(escape(raw_line))
        stripped = line.strip()
        if stripped.startswith('```'):
            if in_list:
                out.append('</ul>')
                in_list = False
            out.append('<pre><code>' if not in_code else '</code></pre>')
            in_code = not in_code
            continue
        if in_code:
            out.append(line)
            continue
        heading = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        bullet = re.match(r'^[-*]\s+(.*)$', stripped)
        if in_list and not bullet:
            out.append('</ul>')
            in_list = False
        if not stripped:
            continue
        if heading:
            level = min(len(heading.group(1)) + 1, 6)  # h1 -> h2 etc.
            out.append(f'<h{level}>{_inline_md(heading.group(2))}</h{level}>')
        elif bullet:
            if not in_list:
                out.append('<ul>')
                in_list = True
            out.append(f'<li>{_inline_md(bullet.group(1))}</li>')
        else:
            out.append(f'<p>{_inline_md(stripped)}</p>')
    if in_code:
        out.append('</code></pre>')
    if in_list:
        out.append('</ul>')
    return Markup('\n'.join(out))


# ---------------------------------------------------------------------------
# Reports page
# ---------------------------------------------------------------------------


def _list_reports() -> list:
    directory = _reports_dir()
    if not directory.is_dir():
        return []
    reports = []
    for path in directory.glob('report-*.md'):
        if not _REPORT_NAME_RE.match(path.name):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        reports.append({'name': path.name, 'mtime': stat.st_mtime})
    reports.sort(key=lambda r: r['name'], reverse=True)
    return reports


@ai_bp.route('/reports')
def reports():
    """List generated reports and render the selected one."""
    report_list = _list_reports()
    selected = request.args.get('file') or (
        report_list[0]['name'] if report_list else None
    )
    report_html = None
    if selected:
        # strict allow-list: prevents traversal and hides non-report files
        if not _REPORT_NAME_RE.match(selected):
            selected = None
        else:
            path = _reports_dir() / selected
            try:
                report_html = markdown_to_html(path.read_text(encoding='utf-8'))
            except OSError:
                report_html = None
                selected = None
    return render_template(
        'ai/index.html',
        active_tab='reports',
        ai_configured=_ai_configured(),
        reports=report_list,
        selected_report=selected,
        report_html=report_html,
    )


@ai_bp.route('/chat')
def chat_page():
    return render_template(
        'ai/index.html',
        active_tab='chat',
        ai_configured=_ai_configured(),
        reports=[],
        selected_report=None,
        report_html=None,
    )


# ---------------------------------------------------------------------------
# Assistant chat (Anthropic tool-use loop)
# ---------------------------------------------------------------------------


def _serialize_content(blocks) -> list:
    """Convert SDK content blocks to plain JSON-safe dicts."""
    out = []
    for block in blocks:
        if isinstance(block, dict):
            if block.get('type') in ('text', 'tool_use'):
                out.append(block)
            continue
        btype = getattr(block, 'type', None)
        if btype == 'text':
            out.append({'type': 'text', 'text': block.text})
        elif btype == 'tool_use':
            out.append(
                {
                    'type': 'tool_use',
                    'id': block.id,
                    'name': block.name,
                    'input': block.input,
                }
            )
        # other block types (e.g. thinking) are dropped from the history
    return out


def _text_of(content: list) -> str:
    return '\n'.join(
        b.get('text', '') for b in content if b.get('type') == 'text'
    ).strip()


def _validate_messages(payload):
    messages = payload.get('messages') if isinstance(payload, dict) else None
    if not isinstance(messages, list) or not messages:
        return None
    if len(messages) > MAX_HISTORY_MESSAGES:
        messages = messages[-MAX_HISTORY_MESSAGES:]
        # never start the trimmed window on an orphaned tool_result turn
        while messages and messages[0].get('role') != 'user':
            messages.pop(0)
    for m in messages:
        if not isinstance(m, dict) or m.get('role') not in ('user', 'assistant'):
            return None
    return messages


def _tool_result_message(tool_use_id: str, result: dict) -> dict:
    return {
        'role': 'user',
        'content': [
            {
                'type': 'tool_result',
                'tool_use_id': tool_use_id,
                'content': json.dumps(result, default=str),
            }
        ],
    }


def _run_chat_loop(messages: list) -> dict:
    """Drive the model<->tools loop until final text or a pending action.

    Mutating tools stop the loop and are surfaced as ``pending_action`` for
    the user to confirm; read-only tools execute inline. At most one tool
    per response (disable_parallel_tool_use) keeps the confirm flow simple.
    """
    import anthropic

    client = anthropic.Anthropic()
    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=_model(),
            max_tokens=MAX_OUTPUT_TOKENS,
            system=CHAT_SYSTEM_PROMPT,
            tools=ai_tools.TOOLS,
            tool_choice={'type': 'auto', 'disable_parallel_tool_use': True},
            messages=messages,
        )
        content = _serialize_content(response.content)
        messages.append({'role': 'assistant', 'content': content})

        if response.stop_reason != 'tool_use':
            return {'reply': _text_of(content), 'messages': messages}

        tool_use = next((b for b in content if b.get('type') == 'tool_use'), None)
        if tool_use is None:  # defensive: stop_reason said tool_use
            return {'reply': _text_of(content), 'messages': messages}

        if tool_use['name'] in ai_tools.MUTATING_TOOLS:
            return {
                'reply': _text_of(content),
                'messages': messages,
                'pending_action': {
                    'id': tool_use['id'],
                    'name': tool_use['name'],
                    'input': tool_use['input'],
                },
            }

        result = ai_tools.execute_tool(tool_use['name'], tool_use['input'])
        messages.append(_tool_result_message(tool_use['id'], result))

    return {
        'reply': (
            'I hit the per-message tool limit before finishing. '
            'Please ask a more specific question.'
        ),
        'messages': messages,
    }


def _chat_error(exc: Exception):
    current_app.logger.error(f'AI chat failed: {exc}')
    return jsonify({'error': f'Assistant request failed: {exc}'}), 502


@ai_bp.route('/chat', methods=['POST'])
def chat():
    if not _ai_configured():
        return jsonify({
            'error': 'AI assistant is not configured (ANTHROPIC_API_KEY is not set).'
        }), 503
    messages = _validate_messages(request.get_json(silent=True))
    if messages is None:
        return jsonify({
            'error': 'Request must include a non-empty "messages" array.'
        }), 400
    try:
        return jsonify(_run_chat_loop(messages))
    except Exception as exc:
        return _chat_error(exc)


@ai_bp.route('/chat/execute', methods=['POST'])
def chat_execute():
    """Execute (or decline) a confirmed mutating action, then resume."""
    if not _ai_configured():
        return jsonify({
            'error': 'AI assistant is not configured (ANTHROPIC_API_KEY is not set).'
        }), 503
    payload = request.get_json(silent=True) or {}
    messages = _validate_messages(payload)
    action = payload.get('action')
    approved = bool(payload.get('approved'))
    if messages is None or not isinstance(action, dict):
        return jsonify({'error': 'Request must include "messages" and "action".'}), 400
    name = action.get('name')
    tool_use_id = action.get('id')
    if name not in ai_tools.MUTATING_TOOLS or not tool_use_id:
        return jsonify({'error': f'Invalid action {name!r}.'}), 400

    # The action must correspond to the trailing assistant tool_use turn.
    last = messages[-1]
    tool_use_ids = []
    if last.get('role') == 'assistant' and isinstance(last.get('content'), list):
        tool_use_ids = [
            b.get('id')
            for b in last['content']
            if isinstance(b, dict) and b.get('type') == 'tool_use'
        ]
    if tool_use_id not in tool_use_ids:
        return jsonify({
            'error': 'Action does not match the pending tool call in the conversation.'
        }), 400

    if approved:
        result = ai_tools.execute_tool(name, action.get('input') or {})
        executed = True
    else:
        result = {
            'cancelled': True,
            'message': (
                'The user declined this action. Do not retry it unless asked again.'
            ),
        }
        executed = False
    messages.append(_tool_result_message(tool_use_id, result))

    try:
        out = _run_chat_loop(messages)
    except Exception as exc:
        return _chat_error(exc)
    out['executed'] = executed
    out['result'] = result
    return jsonify(out)
