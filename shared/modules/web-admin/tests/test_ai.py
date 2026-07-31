"""AI pages and assistant chat: anthropic and the tool backends are mocked."""

import copy
import json
import types

import pytest
from conftest import login

from app.routes import ai as ai_module
from app.services import ai_tools


# ---------------------------------------------------------------------------
# Fake Anthropic client
# ---------------------------------------------------------------------------


class FakeBlock:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def text_response(text):
    return types.SimpleNamespace(
        stop_reason='end_turn',
        content=[FakeBlock(type='text', text=text)],
    )


def tool_response(name, tool_input, tool_id='toolu_01', text=None):
    content = []
    if text:
        content.append(FakeBlock(type='text', text=text))
    content.append(
        FakeBlock(type='tool_use', id=tool_id, name=name, input=tool_input)
    )
    return types.SimpleNamespace(stop_reason='tool_use', content=content)


def fake_anthropic(monkeypatch, responses):
    """Patch anthropic.Anthropic with a client replaying `responses`."""
    calls = []
    queue = list(responses)

    class FakeMessages:
        def create(self, **kwargs):
            # snapshot: the route mutates the messages list in place after
            # each call, so keep a copy of what the API actually received
            snapshot = dict(kwargs)
            snapshot['messages'] = copy.deepcopy(kwargs.get('messages'))
            calls.append(snapshot)
            return queue.pop(0)

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    import anthropic

    monkeypatch.setattr(anthropic, 'Anthropic', FakeClient)
    return calls


@pytest.fixture()
def ai_enabled(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-test')


@pytest.fixture()
def ai_disabled(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)


# ---------------------------------------------------------------------------
# Reports page
# ---------------------------------------------------------------------------


def test_reports_empty_state(client, monkeypatch, tmp_path, ai_disabled):
    monkeypatch.setenv('REPORTS_DIR', str(tmp_path / 'missing'))
    login(client)
    resp = client.get('/ai/reports')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'No reports yet' in body
    assert 'ANTHROPIC_API_KEY' in body  # not-configured explanation


def test_reports_render_markdown_escaped(client, monkeypatch, tmp_path, ai_enabled):
    monkeypatch.setenv('REPORTS_DIR', str(tmp_path))
    (tmp_path / 'report-20260728-040000.md').write_text(
        '# Health\n\n- **loss** on `google_dns`\n\n<script>alert(1)</script>\n',
        encoding='utf-8',
    )
    login(client)
    resp = client.get('/ai/reports')
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'report-20260728-040000.md' in body
    assert '<strong>loss</strong>' in body
    assert '<code>google_dns</code>' in body
    # raw HTML in the report must be escaped, never rendered
    assert '<script>alert(1)</script>' not in body
    assert '&lt;script&gt;' in body


def test_reports_rejects_traversal(client, monkeypatch, tmp_path, ai_enabled):
    monkeypatch.setenv('REPORTS_DIR', str(tmp_path))
    login(client)
    resp = client.get('/ai/reports?file=../../etc/passwd')
    assert resp.status_code == 200
    assert 'passwd' not in resp.get_data(as_text=True)


def test_nav_has_ai_link(client, monkeypatch, tmp_path, ai_disabled):
    monkeypatch.setenv('REPORTS_DIR', str(tmp_path))
    login(client)
    body = client.get('/ai/reports').get_data(as_text=True)
    assert '/ai/reports' in body
    assert 'bi-stars' in body  # AI nav entry icon present in base template


def test_markdown_helper_covers_lists_and_headers():
    html = str(ai_module.markdown_to_html(
        '# Title\n\n- one\n- two\n\n```\ncode <tag>\n```\n*em* text'
    ))
    assert '<h2>Title</h2>' in html
    assert '<ul>' in html and '<li>one</li>' in html
    assert '<pre><code>' in html and 'code &lt;tag&gt;' in html
    assert '<em>em</em>' in html


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


def test_chat_not_configured_returns_503(client, ai_disabled):
    login(client)
    resp = client.post('/ai/chat', json={'messages': [
        {'role': 'user', 'content': 'hi'}
    ]})
    assert resp.status_code == 503
    assert 'not configured' in resp.get_json()['error']


def test_chat_requires_messages(client, ai_enabled):
    login(client)
    resp = client.post('/ai/chat', json={})
    assert resp.status_code == 400


def test_chat_plain_reply(client, monkeypatch, ai_enabled):
    calls = fake_anthropic(monkeypatch, [text_response('All healthy.')])
    login(client)
    resp = client.post('/ai/chat', json={'messages': [
        {'role': 'user', 'content': 'how is the network?'}
    ]})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['reply'] == 'All healthy.'
    assert data['messages'][-1]['role'] == 'assistant'
    assert 'pending_action' not in data
    # tool registry and single-tool mode were sent to the API
    assert calls[0]['tools'] is ai_tools.TOOLS
    assert calls[0]['tool_choice']['disable_parallel_tool_use'] is True


def test_chat_readonly_tool_roundtrip(client, monkeypatch, ai_enabled):
    """Non-mutating tools execute inline and their result feeds the model."""
    calls = fake_anthropic(monkeypatch, [
        tool_response('list_targets', {}, tool_id='toolu_ro'),
        text_response('You monitor 2 targets.'),
    ])
    monkeypatch.setattr(
        ai_tools.gateway, 'get_all_targets_from_db',
        lambda: {'targets': [
            {'id': 1, 'name': 'a', 'host': '1.1.1.1', 'is_active': True},
            {'id': 2, 'name': 'b', 'host': '8.8.8.8', 'is_active': True},
        ]},
    )
    login(client)
    resp = client.post('/ai/chat', json={'messages': [
        {'role': 'user', 'content': 'what do I monitor?'}
    ]})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['reply'] == 'You monitor 2 targets.'
    # second API call got the tool_result for toolu_ro
    second = calls[1]['messages']
    result_block = second[-1]['content'][0]
    assert result_block['type'] == 'tool_result'
    assert result_block['tool_use_id'] == 'toolu_ro'
    assert '"total": 2' in result_block['content']


def test_chat_mutating_tool_returns_pending_action(client, monkeypatch, ai_enabled):
    fake_anthropic(monkeypatch, [
        tool_response('add_target', {'name': 'cf_dns', 'host': '1.1.1.1'},
                      tool_id='toolu_mut', text='I will add it.'),
    ])
    executed = []
    monkeypatch.setattr(ai_tools, 'execute_tool',
                        lambda *a, **k: executed.append(a) or {})
    login(client)
    resp = client.post('/ai/chat', json={'messages': [
        {'role': 'user', 'content': 'add 1.1.1.1'}
    ]})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['pending_action'] == {
        'id': 'toolu_mut',
        'name': 'add_target',
        'input': {'name': 'cf_dns', 'host': '1.1.1.1'},
    }
    # the mutating tool must NOT have been executed
    assert executed == []
    # history ends on the assistant tool_use turn, ready for /chat/execute
    assert data['messages'][-1]['content'][-1]['type'] == 'tool_use'


def test_chat_execute_approved_runs_tool_and_resumes(client, monkeypatch, ai_enabled):
    calls = fake_anthropic(monkeypatch, [text_response('Added cf_dns.')])
    ran = {}

    def fake_execute(name, tool_input):
        ran['name'] = name
        ran['input'] = tool_input
        return {'success': True}

    monkeypatch.setattr(ai_tools, 'execute_tool', fake_execute)
    login(client)
    messages = [
        {'role': 'user', 'content': 'add 1.1.1.1'},
        {'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': 'toolu_mut', 'name': 'add_target',
             'input': {'name': 'cf_dns', 'host': '1.1.1.1'}},
        ]},
    ]
    resp = client.post('/ai/chat/execute', json={
        'messages': messages,
        'action': {'id': 'toolu_mut', 'name': 'add_target',
                   'input': {'name': 'cf_dns', 'host': '1.1.1.1'}},
        'approved': True,
    })
    data = resp.get_json()
    assert resp.status_code == 200
    assert ran == {'name': 'add_target',
                   'input': {'name': 'cf_dns', 'host': '1.1.1.1'}}
    assert data['executed'] is True
    assert data['reply'] == 'Added cf_dns.'
    # the model received the tool_result before replying
    sent = calls[0]['messages']
    assert sent[-1]['content'][0]['tool_use_id'] == 'toolu_mut'
    assert json.loads(sent[-1]['content'][0]['content']) == {'success': True}


def test_chat_execute_declined_does_not_run_tool(client, monkeypatch, ai_enabled):
    fake_anthropic(monkeypatch, [text_response('Okay, not adding it.')])
    executed = []
    monkeypatch.setattr(ai_tools, 'execute_tool',
                        lambda *a, **k: executed.append(a) or {})
    login(client)
    messages = [
        {'role': 'user', 'content': 'add 1.1.1.1'},
        {'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': 'toolu_mut', 'name': 'add_target',
             'input': {'name': 'cf_dns', 'host': '1.1.1.1'}},
        ]},
    ]
    resp = client.post('/ai/chat/execute', json={
        'messages': messages,
        'action': {'id': 'toolu_mut', 'name': 'add_target', 'input': {}},
        'approved': False,
    })
    data = resp.get_json()
    assert resp.status_code == 200
    assert executed == []
    assert data['executed'] is False
    assert data['result']['cancelled'] is True


def test_chat_execute_rejects_mismatched_action(client, monkeypatch, ai_enabled):
    login(client)
    resp = client.post('/ai/chat/execute', json={
        'messages': [{'role': 'user', 'content': 'hi'}],
        'action': {'id': 'toolu_forged', 'name': 'remove_target', 'input': {}},
        'approved': True,
    })
    assert resp.status_code == 400


def test_chat_execute_rejects_non_mutating_action(client, ai_enabled):
    login(client)
    resp = client.post('/ai/chat/execute', json={
        'messages': [{'role': 'user', 'content': 'hi'}],
        'action': {'id': 'toolu_x', 'name': 'list_targets', 'input': {}},
        'approved': True,
    })
    assert resp.status_code == 400


def test_chat_api_failure_returns_502(client, monkeypatch, ai_enabled):
    import anthropic

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError('api down')

    monkeypatch.setattr(anthropic, 'Anthropic', Boom)
    login(client)
    resp = client.post('/ai/chat', json={'messages': [
        {'role': 'user', 'content': 'hi'}
    ]})
    assert resp.status_code == 502
    assert 'api down' in resp.get_json()['error']
