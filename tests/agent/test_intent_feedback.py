"""Compact progress feedback from actual outcomes; actor control is not an outcome."""
import copy
import json
import pytest
from unittest.mock import patch

from tests.fixtures.storage_read import transcript_rows
from tests.agent.test_intent_attractor import harness as _harness

harness = _harness
from tests.agent.test_intent_protocol import call, declaration, PROPOSAL
from tests.agent.test_run_agent import _mock_response


def test_actor_receives_strong_feedback_but_budget_exhaustion_is_silent(harness):
    from hermes_cli.config import atomic_config_write
    from agent.intent_attractor import IntentStore, real_sources
    h = harness
    h.config['agent']['intent_attractor'].update(max_assessments=2, feedback_interval=1)
    atomic_config_write(h.home / 'config.yaml', h.config)
    h.probabilities.append({'incomplete': .97, 'actionable': .96})
    responses = iter([
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL),
                                  call('terminal', {'command': 'check'}, 'one')]),
        _mock_response(tool_calls=[declaration(1), call('terminal', {'command': 'check again'}, 'two')]),
        _mock_response(tool_calls=[declaration(1, transition='finish')]),
        _mock_response(content='Checked; further verification remains.')])
    wires = []
    def create(**kw):
        wires.append(copy.deepcopy(kw['messages']))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    with patch('model_tools.handle_function_call', return_value='verified') as execute:
        result = h.agent.run_conversation('Implement and test the fix; do not publish.')
    assert execute.call_count == 2
    for index in (1,):
        packet = wires[index][-1]['content']
        assert '[Intent feedback' in packet and 'verified' in packet
        assert 'p(incomplete)=' in packet and len(packet) < 480
        assert 'Overarching aim' not in packet
        assert wires[index][:len(wires[index-1])] == wires[index-1]
    assert 'fresh' in wires[1][-1]['content']
    assert wires[2][-1]['role'] == 'tool'  # no synthetic checkpoint or repeated budget warning
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    assert store.read()['tool_rounds'] == 2
    assert store.read()['assessments_used'] == 2
    assert len(real_sources(result['messages'])) == 1
    assert len(real_sources(transcript_rows(h.agent._session_db, h.agent.session_id))) == 1
    assert not any(c.kwargs.get('task') == 'intent_extraction' for c in h.aux.call_args_list)


@pytest.mark.parametrize('explicit_null', [True, False])
def test_unlimited_refreshes_past_four_rounds_and_final(harness, explicit_null):
    from hermes_cli.config import atomic_config_replace, DEFAULT_CONFIG
    from agent.intent_attractor import IntentStore
    h = harness
    cfg = h.config['agent']['intent_attractor']
    cfg.update(max_assessments=None, feedback_interval=1)
    if not explicit_null:
        cfg.pop('max_assessments')
    atomic_config_replace(h.home / 'config.yaml', h.config)
    h.probabilities.extend([{'incomplete': .97, 'actionable': .96}] * 5 + [{'complete': .99}])
    responses = iter([
        *[_mock_response(tool_calls=[
            declaration(0, mode='create', proposal=PROPOSAL) if n == 0 else declaration(1),
            call('terminal', {'command': 'check'}, f'check-{n}')]) for n in range(5)],
        _mock_response(tool_calls=[declaration(1, transition='finish')]),
        _mock_response(content='Verified.')])
    wires = []
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    with patch('model_tools.handle_function_call', return_value='verified') as execute:
        result = h.agent.run_conversation('Implement and test the fix; do not publish.')
    assert not result['failed'] and execute.call_count == 5
    state = IntentStore(h.agent._session_db, h.agent.session_id).read()
    assert state is not None
    assert state['assessments_used'] == 7  # reconciliation, five rounds, final
    assert state['last_judgment']['phase'] == 'completion' and state['decision'] == 'DONE'
    assert [json.loads(p['state'])['phase'] for p in h.payloads] == ['tool_round'] * 5 + ['completion']
    for wire in wires[1:2]:
        packet = wire['messages'][-1]['content']
        assert 'fresh' in packet and 'stale' not in packet
        assert '/unlimited' in packet and '/4' not in packet and len(packet) < 480
    assert all(w['messages'][-1]['role'] == 'tool' for w in wires[2:6])
    assert DEFAULT_CONFIG['agent']['intent_attractor']['max_assessments'] is None


def test_first_packet_has_real_request_before_declaration(harness):
    h = harness
    request = 'Create a GitHub repo with Fibonacci art written in Julia.'
    from agent.intent_feedback import INTENT_GUIDANCE
    h.agent._cached_system_prompt += '\n' + INTENT_GUIDANCE
    wires = []
    responses = iter([_mock_response(tool_calls=[declaration(0, mode='create',
        transition='finish', proposal=PROPOSAL)]), _mock_response(content='Prepared.')])
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    h.probabilities.append({'complete': .99})
    h.agent.run_conversation(request)
    assert 'Immediate request: ' + request in wires[0]['messages'][-1]['content']
    assert INTENT_GUIDANCE in wires[0]['messages'][0]['content']
    assert 'r=revision' in INTENT_GUIDANCE and 'n=tool round' in INTENT_GUIDANCE
    assert 'authoritative harness provenance' in INTENT_GUIDANCE
    assert 'fallible' in INTENT_GUIDANCE and 'not permission' in INTENT_GUIDANCE


def test_structured_delta_exposes_status_error_and_useful_excerpt(harness):
    from hermes_cli.config import atomic_config_write
    h = harness
    h.config['agent']['intent_attractor'].update(max_assessments=None, feedback_interval=1)
    atomic_config_write(h.home / 'config.yaml', h.config)
    h.probabilities.extend([{'incomplete': .97, 'actionable': .96}, {'complete': .99}])
    wires = []
    responses = iter([
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL),
                                  call('terminal', {'command': 'julia art.jl'}, 'one')]),
        _mock_response(tool_calls=[declaration(1, transition='finish')]),
        _mock_response(content='Julia is missing.')])
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    tool_result = json.dumps({'metadata': {'cwd': '/irrelevant/' * 80},
        'status': 'failed', 'exit_code': 127, 'error': 'executable missing',
        'output': 'julia: command not found'})
    with patch('model_tools.handle_function_call', return_value=tool_result):
        h.agent.run_conversation('Create and verify Julia art.')
    packet = wires[1]['messages'][-1]['content']
    assert 'terminal: status=failed exit=127 error=executable missing' in packet
    assert 'julia: command not found' in packet
    assert 'metadata' not in packet and '{' not in packet
    assert 'Aim remains the target; act on fresh gaps with available authorized levers.' in packet
    assert len(packet) <= 480
    print('EXACT_ACTOR_PACKET=' + packet)


def test_wait_and_interrupt_preserve_existing_scheduling(harness, monkeypatch):
    from hermes_cli.config import atomic_config_write
    from agent.intent_attractor import IntentStore
    h = harness
    h.config['agent']['intent_attractor']['feedback_interval'] = 1
    atomic_config_write(h.home / 'config.yaml', h.config)
    monkeypatch.setattr('hermes_cli.goals.count_active_delegations', lambda sid: 1)
    monkeypatch.setattr('hermes_cli.goals.gather_background_processes', lambda **kw: [])
    h.probabilities.extend([{'waiting': .99}, {'waiting': .99}])
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL), call('terminal', {'command': 'check'}, 'one')]),
        _mock_response(tool_calls=[declaration(1, transition='wait')]),
        _mock_response(content='Waiting for the existing worker.')]
    with patch('model_tools.handle_function_call', return_value='pending'):
        result = h.agent.run_conversation('Check the worker, then yield.')
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    assert not result['failed']
    assert store.read()['decision'] == 'WAIT_FOR_EVENT'
    assert store.read()['continuations'] == 0
    assert json.loads(h.payloads[0]['state'])['active_delegations'] == 1


def test_interrupt_during_round_judgment_never_calls_actor_again(harness, monkeypatch):
    from hermes_cli.config import atomic_config_write
    from agent import intent_attractor_judge
    h = harness
    h.config['agent']['intent_attractor']['feedback_interval'] = 1
    atomic_config_write(h.home / 'config.yaml', h.config)
    original = intent_attractor_judge.assess
    def assess(*a, **kw):
        record = original(*a, **kw)
        if not kw.get('assessment_questions'):
            h.agent._interrupt_requested = True
        return record
    monkeypatch.setattr(intent_attractor_judge, 'assess', assess)
    h.probabilities.append({'incomplete': .99, 'actionable': .99})
    h.agent.client.chat.completions.create.return_value = _mock_response(tool_calls=[
        declaration(0, mode='create', proposal=PROPOSAL), call('terminal', {'command': 'check'}, 'one')])
    with patch('model_tools.handle_function_call', return_value='checked') as execute:
        result = h.agent.run_conversation('Run the checks.')
    assert execute.call_count == 1
    assert h.agent.client.chat.completions.create.call_count == 1
    assert result['interrupted']
