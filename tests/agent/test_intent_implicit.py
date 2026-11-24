"""Implicit action retention uses pre-inference authority, never response-time state."""
import copy
import json
from dataclasses import asdict
from unittest.mock import patch

import pytest

from agent.intent_attractor import IntentStore, real_sources
from tests.agent.test_intent_attractor import harness as _harness
from tests.agent.test_intent_protocol import PROPOSAL, call, declaration
from tests.agent.test_run_agent import _mock_response

harness = _harness


def test_many_implicit_batches_dispatch_real_terminal_without_control_receipts(harness, monkeypatch, tmp_path):
    h = harness
    h.agent.max_iterations = 16
    from hermes_cli.config import atomic_config_write
    h.config['agent']['intent_attractor'].update(feedback_interval=1, max_assessments=None)
    atomic_config_write(h.home / 'config.yaml', h.config)
    monkeypatch.setenv('TERMINAL_ENV', 'local')
    monkeypatch.setenv('TERMINAL_CWD', str(tmp_path))
    artifact = tmp_path / 'implicit.txt'
    responses = iter([
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL)]),
        *[_mock_response(tool_calls=[call('terminal', {'command': f"printf '{i} ' >> '{artifact}'"}, f'work-{i}')]) for i in range(6)],
        _mock_response(tool_calls=[declaration(1, transition='finish')]),
        _mock_response(content='Verified six local writes.'),
    ])
    wires, bindings = [], []
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        bindings.append(copy.deepcopy(getattr(h.agent, '_intent_request_binding', None)))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    h.probabilities.extend([{'incomplete': .99, 'actionable': .99}] * 6 + [{'complete': .99}])
    result = h.agent.run_conversation('Implement and test the fix; do not publish.')
    assert not result['failed']
    assert artifact.read_text() == '0 1 2 3 4 5 '
    assert [b.revision for b in bindings] == [0] + [1] * 8
    assert all(b.sources == bindings[0].sources for b in bindings)
    assert json.loads(bindings[0].sources) == real_sources(result['messages'])
    controls = [m for m in result['messages'] if m.get('name') == 'intent_control']
    assert len(controls) == 2
    assert not h.agent._intent_protocol_repairs
    assert all(w['tools'] == wires[0]['tools'] for w in wires)
    assert all(w['messages'][0] == wires[0]['messages'][0] for w in wires)
    state = IntentStore(h.agent._session_db, h.agent.session_id).read()
    assert state['tool_rounds'] == 6
    assert state['assessments_used'] == 8
    (tmp_path / 'trace.json').write_text(json.dumps({
        'bindings': [asdict(b) for b in bindings], 'requests': wires,
        'messages': result['messages'], 'artifact': artifact.read_text(),
    }, indent=2, default=str))


@pytest.mark.parametrize('change', ['manual', 'steer'])
def test_inference_edits_refuse_implicit_actions_then_require_reconciliation(harness, change):
    h = harness
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    requests = []
    def create(**kw):
        requests.append(copy.deepcopy(kw))
        if len(requests) == 1:
            return _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL)])
        if len(requests) == 2:
            if change == 'manual':
                store.command('edit boundaries Only inspect local files')
            else:
                h.agent._pending_steer = 'Only summarize the results now.'
            return _mock_response(tool_calls=[call('terminal', {'command': 'must-not-run'}, 'stale')])
        if len(requests) == 3:
            if change == 'manual':
                return _mock_response(tool_calls=[declaration(2, transition='blocked')])
            return _mock_response(tool_calls=[declaration(1, mode='update', transition='finish',
                proposal={'update_type': 'presentation', 'fields': {}})])
        return _mock_response(content='Stopped at the new boundary.')
    h.agent.client.chat.completions.create.side_effect = create
    h.probabilities.append({'blocked': .99})
    with patch('model_tools.handle_function_call') as execute:
        result = h.agent.run_conversation('Implement and test the fix.')
    assert not execute.called
    assert not result['failed']
    assert h.agent._intent_protocol_repairs == 1
    assert store.read()['revision'] == 2
    assert ('Only inspect local files' if change == 'manual' else 'Only summarize') in str(requests[2]['messages'])


def test_new_source_omission_is_refused_update_actions_adopt_new_revision(harness):
    h = harness
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    h.probabilities.append({'complete': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL), call('terminal', {'command': 'first'}, 'first')]),
        _mock_response(tool_calls=[call('terminal', {'command': 'undeclared'}, 'undeclared')]),
        _mock_response(tool_calls=[call('terminal', {'command': 'updated'}, 'updated'), declaration(1, mode='update',
            proposal={'update_type': 'refinement', 'fields': {'verification': 'Also inspect the output'}})]),
        _mock_response(tool_calls=[call('terminal', {'command': 'inherited'}, 'inherited')]),
        _mock_response(tool_calls=[declaration(2, transition='finish')]),
        _mock_response(content='Verified updated result.')]
    revisions = []
    def execute(name, args, *a, **kw):
        revisions.append((args['command'], store.read()['revision'], h.agent._intent_dispatch_revision))
        if args['command'] == 'first':
            h.agent._pending_steer = 'Also inspect the output.'
        return 'verified'
    with patch('model_tools.handle_function_call', side_effect=execute):
        result = h.agent.run_conversation('Implement and test the fix.')
    assert not result['failed']
    assert revisions == [('first', 1, 1), ('updated', 2, 2), ('inherited', 2, 2)]
    assert h.agent._intent_protocol_repairs == 1


@pytest.mark.parametrize('end_transition', [None, 'finish', 'wait', 'blocked'])
def test_implicit_actions_never_replace_explicit_final_transition(harness, end_transition):
    h = harness
    h.agent.max_iterations = 5
    responses = [
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL)]),
        _mock_response(tool_calls=[call('terminal', {'command': 'first'}, 'first')]),
    ]
    if end_transition:
        responses.append(_mock_response(tool_calls=[declaration(1, transition=end_transition)]))
    responses.extend([_mock_response(content='Unqualified final.')]*3)
    h.agent.client.chat.completions.create.side_effect = responses
    h.probabilities.append({'complete': .99})
    with patch('model_tools.handle_function_call', return_value='verified') as execute:
        result = h.agent.run_conversation('Implement and test the fix.')
    assert execute.call_count == 1
    assert result['failed'] == (end_transition is None)


def test_request_authority_read_failure_stops_before_inference(harness, monkeypatch):
    from agent import intent_protocol
    h = harness
    bind = intent_protocol.bind_request
    def broken_read(agent, messages):
        monkeypatch.setattr(agent._intent_attractor, 'read', lambda: (_ for _ in ()).throw(OSError('disk failed')))
        return bind(agent, messages)
    monkeypatch.setattr(intent_protocol, 'bind_request', broken_read)
    result = h.agent.run_conversation('Implement and test the fix.')
    assert result['failed']
    assert not h.agent.client.chat.completions.create.called


def test_steer_during_final_judgment_cannot_claim_old_finish(harness, monkeypatch):
    from agent import intent_attractor_judge
    h = harness
    assess = intent_attractor_judge.assess
    def steer(*a, **kw):
        result = assess(*a, **kw)
        if not kw.get('assessment_questions'):
            h.agent._pending_steer = 'Also inspect the output before finishing.'
        return result
    monkeypatch.setattr(intent_attractor_judge, 'assess', steer)
    h.probabilities.append({'complete': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', transition='finish', proposal=PROPOSAL)]),
        *[_mock_response(content='Done with old work.')] * 4]
    result = h.agent.run_conversation('Implement and test the fix.')
    assert result['failed']
    assert result['final_response'] != 'Done with old work.'
