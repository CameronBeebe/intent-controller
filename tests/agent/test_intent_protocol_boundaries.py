"""Independent-review regressions at real approval and user-input boundaries."""
import copy
import json
import queue
import threading
from unittest.mock import patch

import pytest

from agent.intent_attractor import IntentStore
from tests.agent.test_intent_attractor import harness as _harness
from tests.agent.test_intent_protocol import PROPOSAL, call, declaration
from tests.agent.test_run_agent import _mock_response

harness = _harness


@pytest.mark.parametrize('background', [False, True])
def test_terminal_actual_approval_must_recheck_revision(harness, monkeypatch, tmp_path, background):
    h = harness
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    artifact = tmp_path / 'must-not-exist.txt'
    monkeypatch.setenv('TERMINAL_ENV', 'local')
    monkeypatch.setenv('TERMINAL_CWD', str(tmp_path))
    approvals = []

    def approve(*args, **kwargs):
        approvals.append(args)
        store.command('edit boundaries No further commands')
        return {'approved': True, 'user_approved': True}

    monkeypatch.setattr('tools.terminal_tool._check_all_guards', approve)
    h.probabilities.append({'blocked': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL),
            call('terminal', {'command': f"printf stale-executed > '{artifact}'", 'background': background}, 'actual-approval')]),
        _mock_response(tool_calls=[declaration(2, transition='blocked')]),
        _mock_response(content='Stopped after authority changed.'),
    ]
    result = h.agent.run_conversation('Write the local proof file.')
    assert len(approvals) == 1
    assert store.read()['revision'] == 2
    assert not artifact.exists(), 'STALE ACTION EXECUTED after real terminal approval changed intent'
    assert any('stale intent' in str(m.get('content')) for m in result['messages'])
    assert not result['failed']


@pytest.mark.parametrize('unavailable', [False, True])
def test_terminal_rechecks_after_cwd_preparation_without_retry(harness, monkeypatch, tmp_path, unavailable):
    from tools import terminal_tool as tt
    h = harness
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    artifact = tmp_path / 'must-not-run.txt'
    monkeypatch.setenv('TERMINAL_ENV', 'local')
    monkeypatch.setenv('TERMINAL_CWD', str(tmp_path))
    original = tt._resolve_command_cwd
    preparations = []

    def prepare(**kw):
        cwd = original(**kw)
        # Pre-execution skill guards also resolve paths, without an execution
        # environment. Exercise the final cwd preparation, not those scans.
        if 'env' not in kw:
            return cwd
        preparations.append(cwd)
        if unavailable:
            monkeypatch.setattr(store.db.state_meta, 'get', lambda *a: (_ for _ in ()).throw(OSError('private failure')))
        else:
            store.command('edit boundaries No more commands')
        return cwd

    monkeypatch.setattr(tt, '_resolve_command_cwd', prepare)
    h.probabilities.append({'blocked': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL),
                                  call('terminal', {'command': f"printf stale > '{artifact}'"}, 'prepared')]),
        _mock_response(tool_calls=[declaration(2, transition='blocked')]),
        _mock_response(content='Stopped.')]
    result = h.agent.run_conversation('Write the proof file.')
    assert len(preparations) == 1
    assert not artifact.exists()
    tool_result = next(m['content'] for m in result['messages'] if m.get('tool_call_id') == 'prepared')
    assert ('authority unavailable' if unavailable else 'stale intent') in tool_result
    assert 'private failure' not in tool_result


def test_execution_callback_is_owner_scoped_frozen_and_reset(harness):
    from agent.intent_attractor import empty_state
    from agent.intent_protocol import execution_check
    from tools.approval_context import bound_execution_check, execution_check_error
    from tools.thread_context import propagate_context_to_thread
    h = harness
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    store.save(empty_state())
    h.agent._intent_attractor = store
    h.agent._intent_dispatch_revision = 0
    check = execution_check(h.agent, 'terminal')
    with bound_execution_check(check):
        assert execution_check_error() is None
        store.command('edit boundaries No commands')
        h.agent._intent_dispatch_revision = 1  # Later turn cannot revive the old callback.
        results = []
        worker = threading.Thread(target=propagate_context_to_thread(lambda: results.append(execution_check_error())))
        worker.start()
        worker.join(timeout=5)
        assert len(results) == 1 and 'stale intent' in results[0]
        with bound_execution_check(None):
            assert execution_check_error() is None
        assert 'stale intent' in execution_check_error()
    assert execution_check_error() is None
    h.agent._intent_guidance_enabled = False
    assert execution_check(h.agent, 'terminal') is None


@pytest.mark.parametrize('desktop,change', [(False, 'manual'), (True, 'manual'), (False, 'steer')])
def test_blocking_approval_edit_prevents_real_commands(harness, monkeypatch, tmp_path, desktop, change):
    """Wait on the real gateway queue, edit authority, then answer once."""
    from gateway.session_context import clear_session_vars
    from tools import approval
    from tools.terminal_scope import reset_terminal_scope, set_terminal_scope
    from tools.terminal_tool_lifecycle import cleanup_vm
    from tools.thread_context import propagate_context_to_thread
    from tui_gateway import server

    h = harness
    key = h.agent.session_id
    store = IntentStore(h.agent._session_db, key)
    artifacts = [tmp_path / 'first.txt', tmp_path / 'second.txt'] if desktop else [tmp_path / 'first.txt']
    monkeypatch.setenv('HERMES_EXEC_ASK', '1')
    monkeypatch.setattr('tools.approval_context._get_approval_mode', lambda: 'manual')
    monkeypatch.setattr('agent.title_generator.maybe_auto_title', lambda *a, **kw: None)
    published = queue.Queue()
    approval.register_gateway_notify(key, published.put)
    monkeypatch.setattr(server, '_sessions', {key: {
        'session_key': key, 'source': 'desktop' if desktop else 'cli',
        'agent': h.agent, 'cwd': str(tmp_path)}})
    tokens = server._set_session_context(key)
    terminal_token = set_terminal_scope({'TERMINAL_ENV': 'local', 'TERMINAL_CWD': str(tmp_path)})
    h.probabilities.append({'blocked': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL)]),
        _mock_response(tool_calls=[*[
            call('terminal', {'command': f"rm -rf '{tmp_path / 'absent'}'; printf stale-executed > '{p}'"}, p.stem) for p in artifacts]]),
        _mock_response(tool_calls=[declaration(2, transition='blocked') if change == 'manual' else
            declaration(1, mode='update', transition='blocked', proposal={'update_type': 'presentation', 'fields': {}})]),
        _mock_response(content='No stale command executed.')]
    results, errors = [], []

    def run():
        try:
            results.append(h.agent.run_conversation('Write the local files.', task_id=key))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=propagate_context_to_thread(run), daemon=True)
    worker.start()
    try:
        notices = [published.get(timeout=15) for _ in artifacts]
        assert not any(p.exists() for p in artifacts)
        if change == 'manual':
            store.command('edit boundaries No further commands')
        else:
            h.agent._pending_steer = 'Only summarize; do not run the command.'
        for notice in reversed(notices):
            assert approval.resolve_gateway_approval(key, 'once', request_id=notice['request_id']) == 1
        worker.join(timeout=20)
        assert not worker.is_alive() and not errors
        assert not any(p.exists() for p in artifacts), 'approved stale shell executed'
        assert published.empty(), 'refusal must not repeat approval/command'
        assert approval.list_gateway_approvals(key) == []
        assert not results[0]['failed']
        assert any(('stale intent' if change == 'manual' else 'user input changed') in str(m.get('content')) for m in results[0]['messages'])
    finally:
        h.agent.interrupt('test cleanup')
        approval.unregister_gateway_notify(key)
        worker.join(timeout=5)
        cleanup_vm(key)
        reset_terminal_scope(terminal_token)
        clear_session_vars(tokens)


def test_control_provider_schema_conversions():
    from agent.intent_protocol import tool_schema
    from agent.anthropic_message_convert import convert_tools_to_anthropic
    from agent.codex_responses_adapter import _responses_tools
    from agent.bedrock_adapter import convert_tools_to_converse
    schema = tool_schema()
    original = copy.deepcopy(schema)
    anthropic = convert_tools_to_anthropic([schema])[0]
    responses = _responses_tools([schema])[0]
    bedrock = convert_tools_to_converse([schema])[0]['toolSpec']
    for params in [anthropic['input_schema'], responses['parameters'], bedrock['inputSchema']['json']]:
        assert set(params['required']) == {'revision', 'mode', 'transition'}
        assert params['additionalProperties'] is False
    assert responses['strict'] is False
    assert schema == original
