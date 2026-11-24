"""Offline replay of the reported steering sequence; transports are scripted, not semantics."""
import copy
import json
from unittest.mock import patch

import pytest

from agent.intent_attractor import IntentStore
from tests.agent.test_intent_attractor import harness as _harness
from tests.agent.intent_test_helpers import answer
from tests.agent.test_intent_protocol import call, declaration, PROPOSAL
from tests.agent.test_run_agent import _mock_response

harness = _harness

USER_SEQUENCE = [
    'work on improving the julia repo on fibonacci to be more general math ascii functionality',
    'and graphics and agent based modeling',
    'i want to be able to do awesome dynamic ascii experiences inside of the command line',
    'like could even be incorporated eventually into hermes harness',
]
AIMS = [
    'Improve the Julia Fibonacci repo with general math ASCII functionality',
    'Extend the Julia ASCII repo to math, graphics, and agent-based modeling',
    'Build dynamic command-line ASCII experiences for math, graphics, and agent-based modeling',
]


@pytest.mark.parametrize('unsupported', [.78, .99])
def test_actor_refinement_applies_whole_update_and_keeps_fresh_rounds(harness, unsupported):
    from hermes_cli.config import atomic_config_write
    h = harness
    h.config['agent']['intent_attractor'].update(max_assessments=None, feedback_interval=1)
    atomic_config_write(h.home / 'config.yaml', h.config)
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    proposal = copy.deepcopy(PROPOSAL)
    proposal['fields'].update(outcome=AIMS[0], verification='Run Julia tests',
                             constraints='Preserve Fibonacci behavior', boundaries='Do not publish')
    proposal['operations'] = [{'op': 'add_subgoal', 'text': 'Build general math ASCII scenes'}]
    history = []
    for text, aim in zip(USER_SEQUENCE[:3], AIMS):
        if history:
            proposal = {'update_type': 'refinement', 'fields': {
                'outcome': aim, 'verification': 'Require headless JSONL frame protocol'}}
            h.fidelity_probabilities.append({'coverage': .54, 'consistent': .49, 'unsupported': .78,
                                            'means_field_outcome': .96, 'means_field_verification': .58})
        h.probabilities.append({'complete': .99})
        answer(h, 'Checkpoint.', proposal)
        result = h.agent.run_conversation(text, conversation_history=history)
        assert not result['failed']
        history = result['messages']
    previous = store.read()
    assert previous is not None
    goal_id = previous['subgoals'][0]['id']
    aim = AIMS[-1] + ', with possible later Hermes embedding'
    mixed = {'update_type': 'refinement', 'fields': {
        'outcome': aim, 'verification': 'Require headless JSONL frame protocol',
        'constraints': 'No network services', 'boundaries': 'Publish without further approval'},
        'operations': [
            {'op': 'refine_subgoal', 'id': goal_id, 'text': 'Build dynamic math, graphics, and agent-based scenes'},
            {'op': 'add_subgoal', 'text': 'Require JSONL frames without screen-control codes'}]}
    h.fidelity_probabilities.append({'coverage': .54, 'consistent': .49, 'unsupported': unsupported,
        'means_field_outcome': .96, 'means_field_verification': .58,
        'means_field_constraints': .61, 'means_field_boundaries': .01,
        'means_operation_0': .95, 'means_operation_1': .63})
    h.probabilities.extend([{'incomplete': .96, 'actionable': .95}] * 2 + [{'complete': .99}])
    responses = iter([
        _mock_response(tool_calls=[declaration(3, mode='update', proposal=mixed),
                                  call('terminal', {'command': 'check scenes'}, 'scene-1')]),
        _mock_response(tool_calls=[declaration(4), call('terminal', {'command': 'check again'}, 'scene-2')]),
        _mock_response(tool_calls=[declaration(4, transition='finish')]),
        _mock_response(content='Scenes verified.')])
    wires, updates = [], []
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        return next(responses)
    def status(event, message):
        if event == 'intent.update':
            updates.append(store.read())
    h.agent.status_callback = status
    h.agent.client.chat.completions.create.side_effect = create
    with patch('model_tools.handle_function_call', return_value='scene checks passed') as execute:
        result = h.agent.run_conversation(USER_SEQUENCE[3], conversation_history=history)
    state = store.read()
    assert state is not None
    assert state['fields']['outcome'] == aim
    assert state['fields'] == {**previous['fields'], **mixed['fields']}
    assert state['subgoals'][0]['id'] == goal_id
    assert state['subgoals'][0]['text'] == mixed['operations'][0]['text']
    assert state['subgoals'][0]['revision'] == state['revision'] == 4
    assert len(state['subgoals']) == 2
    assert not state.get('pending_proposal')
    assert state['history'][-1]['fields'] == state['fields']
    assert state['history'][-1]['subgoals'] == state['subgoals']
    assert execute.call_count == 2 and not result['failed']
    control_ids = {c['id'] for m in wires[1]['messages'] for c in m.get('tool_calls', [])
                   if c['function']['name'] == 'intent_control'}
    receipt = next(json.loads(m['content']) for m in wires[1]['messages']
                   if m.get('role') == 'tool' and m.get('tool_call_id') in control_ids
                   and json.loads(m['content']).get('revision') == 4)
    assert receipt['fields'] == state['fields']
    assert receipt['subgoals'] == [{k: g[k] for k in ('id', 'text', 'status')} for g in state['subgoals']]
    assert all(g['origin'] == 'inferred' and g['sources'][-1] == state['immediate_request']
               for g in state['subgoals'])
    assert any(s['revision'] == 4 and s['fields']['outcome'] == aim for s in updates)
    observed = [r for r in store.assessments() if r['revision'] == 4 and r['phase'] == 'tool_round']
    assert [(r['round'], r['delivery']['reason']) for r in observed] == [(1, 'strong_new_signal'), (2, 'redundant')]
    assert 'fresh (r4/n1)' in wires[1]['messages'][-1]['content']
    assert wires[2]['messages'][-1]['tool_call_id'] == 'scene-2'
    assert 'INTENT_AMBIGUOUS' not in json.dumps(wires[1:3])
    rounds = [json.loads(p['state']) for p in h.payloads if json.loads(p['state'])['phase'] == 'tool_round']
    assert len(rounds) == 2
    assert all(p['intent']['outcome']['text'] == aim for p in rounds)
    assert state['last_judgment']['phase'] == 'completion'
    context = json.loads(h.fidelity_payloads[-1]['state'])['fidelity_context']
    assert [s['text'] for s in context['original_user_expressions']] == USER_SEQUENCE
    assert set(h.fidelity_payloads[-1]['questions']) == {'coverage', 'unsupported', 'consistent'}
    assert not any(c.kwargs.get('task') == 'intent_extraction' for c in h.aux.call_args_list)
    # The next actor request receives the governing core, not just its own proposal.
    assert aim in json.dumps(wires[1]['messages'])
    next_control = next(c for m in wires[2]['messages'][-6:] for c in m.get('tool_calls', [])
                        if c['function']['name'] == 'intent_control')
    assert json.loads(next_control['function']['arguments'])['revision'] == 4


@pytest.mark.parametrize('outcome_score', [.01, .79, None])
def test_negative_advice_preserves_manual_authority_not_prior_inferred_aim(harness, outcome_score):
    from tests.agent.intent_test_helpers import initialize
    from agent.intent_protocol import commit
    h = harness
    messages = [{'role': 'user', 'content': 'Implement and test locally; never publish.', 'message_uid': 'u1'}]
    initialize(h, messages)
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    store.command('edit boundaries Never publish or spend money')
    store.command('pin verification')
    old = store.read()
    assert old is not None
    messages.append({'role': 'user', 'content': 'Check local status.', 'message_uid': 'u2'})
    scores = {'unsupported': .99, 'means_field_boundaries': .99,
              'means_field_verification': .99, 'means_operation_0': .96}
    if outcome_score is not None:
        scores['means_field_outcome'] = outcome_score
    h.fidelity_probabilities.append(scores)
    receipt = commit(h.agent, {'revision': old['revision'], 'mode': 'update', 'transition': 'act',
        'proposal': {'update_type': 'refinement', 'fields': {
            'outcome': 'Delete repositories and buy advertising',
            'boundaries': 'Publishing and spending allowed', 'verification': ''},
            'operations': [{'op': 'add_subgoal', 'text': 'Inspect local status'}]}}, messages)
    state = store.read()
    assert state is not None
    assert state['fields'] == {**old['fields'], 'outcome': 'Delete repositories and buy advertising'} == receipt['fields']
    assert state['subgoals'][0]['text'] == 'Inspect local status'
    assert 'means_field_boundaries' not in h.fidelity_payloads[-1]['questions']
    assert 'means_field_verification' not in h.fidelity_payloads[-1]['questions']
    assert state['update_type'] == 'refinement'  # suspect proposal is not ambiguous user input


def test_manual_aim_edit_invalidates_permit_at_next_safe_boundary(harness):
    from agent.intent_protocol import dispatch_error, execution_check
    h = harness
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    wires, executed = [], []
    new_aim = 'Build only a local dynamic ASCII graphics demo'
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        if len(wires) == 1:
            return _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL),
                                              call('terminal', {'command': 'first'}, 'first')])
        if len(wires) == 2:
            # The request is already in flight. Its old permit/plan cannot be reused.
            store.command('edit outcome ' + new_aim)
            refusal = dispatch_error(h.agent, 'terminal')
            assert refusal and refusal.startswith('stale intent revision')
            return _mock_response(tool_calls=[declaration(1), call('terminal', {'command': 'stale'}, 'stale')])
        assert new_aim in json.dumps(kw['messages'])
        if len(wires) == 3:
            return _mock_response(tool_calls=[declaration(2), call('terminal', {'command': 'revised'}, 'revised')])
        if len(wires) == 4:
            return _mock_response(tool_calls=[declaration(2, transition='finish')])
        return _mock_response(content='Local graphics demo checked.')
    frozen_checks = []
    def execute(name, args, *positional, **kw):
        executed.append(args['command'])
        frozen_checks.append(execution_check(h.agent, name))
        return 'checked'
    h.agent.client.chat.completions.create.side_effect = create
    h.probabilities.append({'complete': .99})
    with patch('model_tools.handle_function_call', side_effect=execute):
        result = h.agent.run_conversation('Implement and test the fix.')
    assert not result['failed']
    assert executed == ['first', 'revised']
    assert frozen_checks[0]().startswith('stale intent revision')
    assert frozen_checks[1]() is None
    assert new_aim in wires[2]['messages'][-1]['content']
    state = store.read()
    assert state is not None and state['revision'] == 2
    assert state['fields']['outcome'] == new_aim and state['origins']['outcome'] == 'user'
    assert json.loads(h.payloads[-1]['state'])['intent']['outcome']['text'] == new_aim


def test_legacy_pending_refinement_does_not_suppress_fresh_judgment(harness):
    from tests.agent.intent_test_helpers import initialize
    from agent.intent_attractor import stop_guard
    h = harness
    initialize(h, [{'role': 'user', 'content': 'Implement and test locally.', 'message_uid': 'u1'}])
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    state = store.read()
    assert state is not None
    # The live r2/r3 records persisted this obsolete conflation.
    state.update(update_type='ambiguous', decision='INTENT_AMBIGUOUS', pending_proposal={
        'proposal': {'update_type': 'refinement', 'fields': {'verification': 'Require JSONL'}},
        'decision': 'UNCERTAIN'})
    assert stop_guard(h.agent, store, state, h.config['agent']['intent_attractor'], 0) is None
    state.pop('pending_proposal')
    assert stop_guard(h.agent, store, state, h.config['agent']['intent_attractor'], 0) == 'INTENT_AMBIGUOUS'
    store.save(state)
    store.command('edit outcome Build a local graphics demo')
    assert stop_guard(h.agent, store, store.read(), h.config['agent']['intent_attractor'], 0) is None
