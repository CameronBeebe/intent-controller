"""Offline request-boundary regressions: frequent observation, selective delivery."""
import copy
import json
from unittest.mock import patch

from agent.intent_attractor import IntentStore
from tests.agent.test_intent_attractor import harness as _harness
from tests.agent.test_intent_protocol import PROPOSAL, call, declaration
from tests.agent.test_run_agent import _mock_response

harness = _harness


def configure(h, **settings):
    from hermes_cli.config import atomic_config_write
    h.config['agent']['intent_attractor'].update(settings)
    atomic_config_write(h.home / 'config.yaml', h.config)


def scripted_rounds(h, count):
    responses = iter([
        *[_mock_response(tool_calls=([
            declaration(0, mode='create', proposal=PROPOSAL)] if n == 0 else []) + [
            call('terminal', {'command': 'check'}, f'check-{n}')]) for n in range(count)],
        _mock_response(tool_calls=[declaration(1, transition='finish')]),
        _mock_response(content='Verified.')])
    wires = []
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    with patch('model_tools.handle_function_call', return_value='checked'):
        result = h.agent.run_conversation('Implement and test the fix; do not publish.')
    return wires, result, IntentStore(h.agent._session_db, h.agent.session_id)


def test_twelve_uncertain_fidelity_rounds_are_silent_but_observed(harness):
    h = harness
    configure(h, max_assessments=None, feedback_interval=1)
    h.agent.max_iterations = 20
    h.fidelity_probabilities.append({'coverage': .5, 'consistent': .5, 'unsupported': .5})
    h.probabilities.extend([{'coverage': .5, 'consistent': .5, 'unsupported': .5,
                            'evidence': .5}] * 12 + [
        {'coverage': .5, 'consistent': .5, 'unsupported': .5, 'complete': .99}])
    wires, result, store = scripted_rounds(h, 12)
    assert not result['failed']
    for wire in wires[1:13]:
        assert wire['messages'][-1]['role'] == 'tool', 'silent rounds add no checkpoint pair'
    receipts = [json.loads(m['content']) for m in wires[1]['messages']
                if m.get('role') == 'tool' and m.get('tool_call_id') == 'control']
    assert receipts and receipts[-1]['assessment'] is None
    assert len(h.payloads) == 13 and len(h.fidelity_payloads) == 1
    state = store.read()
    assert state is not None and state['decision'] == 'DONE', 'fidelity must not short-circuit completion'
    rounds = [r for r in store.assessments() if r['phase'] == 'tool_round']
    assert len(rounds) == 12
    assert all(r['delivery']['reason'] == 'no_strong_signal' for r in rounds)
    assert all(r['payload'] and r['answers'] for r in rounds)


def test_suppressed_judgments_never_leak_through_repairs_receipts_or_next_turn(harness):
    """C05: silent tool advice leaked via a finish repair and next-turn checkpoint."""
    h = harness
    configure(h, feedback_interval=1, semantic_continuation=False)
    h.fidelity_probabilities.append({'coverage': .54321, 'consistent': .54321, 'unsupported': .54321})
    h.probabilities.extend([{'evidence': .5, 'incomplete': .72345}, {'complete': .99}])
    wires = []
    responses = iter([
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL),
                                  call('terminal', {'command': 'check'}, 'check')]),
        _mock_response(content='Verified.'),  # Missing finish: the repair must not expose Jev.
        _mock_response(tool_calls=[declaration(1, transition='finish')]),
        _mock_response(content='Verified.'),
        _mock_response(tool_calls=[declaration(1, transition='finish')]),  # New input needs update.
        _mock_response(tool_calls=[declaration(1, mode='update', transition='finish',
            proposal={'update_type': 'presentation', 'fields': {}})]),
        _mock_response(content='- Verified.'),
    ])
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    with patch('model_tools.handle_function_call', return_value='checked') as execute:
        first = h.agent.run_conversation('Implement and test the fix; do not publish.')
        second = h.agent.run_conversation('Now in one bullet.', conversation_history=first['messages'])
    assert not first['failed'] and not second['failed'] and execute.call_count == 1
    assert len(wires) == 7
    initial = wires[0]['messages'][-1]['content']
    repair = wires[2]['messages'][-1]['content']
    new_input = wires[4]['messages'][-1]['content']
    assert 'mode=create' in initial and 'revision=0' in initial
    assert 'mode=retain' in repair and 'transition=finish' in repair
    assert 'mode=update' in new_input and 'revision=1' in new_input
    assert 'Reconcile the current user request' in new_input and 'Presentation-only:' not in new_input
    assert 'Actor protocol: {' not in repair
    controls = [json.loads(m['content']) for w in wires for m in w['messages']
                if m.get('role') == 'tool' and m.get('tool_call_id', '').startswith('control')]
    applied = next(r for r in controls if r.get('transition') == 'act')
    assert 'revision=1' in applied['next'] and 'transition=finish' in applied['next']
    assert 'no tools' in next(r for r in controls if r.get('transition') == 'finish')['next']
    for wire in wires:
        outbound = json.dumps(wire['messages'])
        for forbidden in ('UNCERTAIN', 'DONE', 'last judgment', '.72345', '0.72', '.54321'):
            assert forbidden not in outbound
        for message in wire['messages']:
            if message.get('role') == 'tool' and message.get('tool_call_id', '').startswith('control'):
                receipt = json.loads(message['content'])
                assert 'decision' not in receipt
                assert not receipt.get('assessment')
    assert 'Now in one bullet.' in wires[4]['messages'][-1]['content']
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    rounds = [r for r in store.assessments() if r['phase'] == 'tool_round']
    assert rounds[0]['answers']['incomplete']['noul'] == .72345
    assert rounds[0]['delivery']['delivered'] is False
    state = store.read()
    assert state is not None and state['last_judgment']['decision'] == 'DONE'


def test_no_jev_arm_has_zero_transport_and_no_failure_spam(harness):
    h = harness
    configure(h, judge_enabled=False, max_assessments=0, feedback_interval=1)
    with patch('agent.intent_attractor_judge.assess', side_effect=AssertionError('judge disabled')) as assess:
        wires, result, store = scripted_rounds(h, 3)
    assert not result['failed']
    assert assess.call_count == 0 and h.payloads == [] and h.fidelity_payloads == []
    state = store.read()
    assert state is not None
    assert state['assessments_used'] == 0 and state['continuations'] == 0
    assert state['decision'] == 'JUDGE_DISABLED'
    assert all(w['messages'][-1]['role'] == 'tool' for w in wires[1:4])
    assert all('JUDGE_DISABLED' not in json.dumps(w['messages']) for w in wires)
    assert not any(r.get('status') == 'unavailable' for r in store.assessments())


def test_continuation_off_observes_without_forcing_extra_actor_call(harness):
    h = harness
    configure(h, semantic_continuation=False, feedback_interval=1)
    h.probabilities.extend([{'incomplete': .99, 'actionable': .99}] * 2)
    wires, result, store = scripted_rounds(h, 1)
    assert not result['failed'] and len(wires) == 3
    assert len(h.payloads) == 2
    state = store.read()
    assert state is not None and state['decision'] == 'CONTINUE_NOW'
    assert state['continuations'] == 0
    completion = [r for r in store.assessments() if r['phase'] == 'completion'][-1]
    assert completion['delivery']['reason'] == 'continuation_disabled'


def test_strong_new_warning_emits_once_and_status_change_emits_again(harness):
    h = harness
    configure(h, feedback_interval=1)
    h.probabilities.extend([{'coverage': .05, 'evidence': .5}] * 2 + [
        {'coverage': .05, 'waiting': .99}, {'waiting': .99}])
    wires, result, store = scripted_rounds(h, 3)
    assert not result['failed']
    assert 'coverage' in wires[1]['messages'][-1]['content']
    assert wires[2]['messages'][-1]['role'] == 'tool'
    assert 'WAIT_FOR_EVENT' in wires[3]['messages'][-1]['content']
    rounds = [r for r in store.assessments() if r['phase'] == 'tool_round']
    assert [r['delivery']['reason'] for r in rounds] == [
        'strong_new_signal', 'redundant', 'strong_new_signal']


def test_edit_during_judgment_always_delivered_without_stale_signal(harness, monkeypatch):
    from agent import intent_attractor_judge
    h = harness
    configure(h, feedback_interval=1)
    original = intent_attractor_judge.assess
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    def assess(*args, **kwargs):
        record = original(*args, **kwargs)
        if not kwargs.get('assessment_questions'):
            store.command('edit outcome Only inspect; no changes')
        return record
    monkeypatch.setattr(intent_attractor_judge, 'assess', assess)
    h.probabilities.append({'incomplete': .99, 'actionable': .99})
    wires = []
    responses = iter([
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL),
                                  call('terminal', {'command': 'check'}, 'one')]),
        _mock_response(tool_calls=[declaration(2, transition='finish')]),
        _mock_response(content='Inspected.')])
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        if len(wires) == 2:
            configure(h, judge_enabled=False)
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    with patch('model_tools.handle_function_call', return_value='checked'):
        result = h.agent.run_conversation('Implement and test the fix; do not publish.')
    assert not result['failed']
    packet = wires[1]['messages'][-1]['content']
    assert 'Only inspect; no changes' in packet and 'Current intent r2' in packet
    assert 'fresh' not in packet
    record = next(r for r in store.assessments() if r['phase'] == 'tool_round')
    assert record['decision'] == 'STALE' and record['delivery']['reason'] == 'stale'


def test_unavailable_is_not_no_alert_and_remains_silent(harness, monkeypatch):
    from agent import intent_attractor_judge
    h = harness
    configure(h, feedback_interval=1)
    original = intent_attractor_judge.assess
    def assess(*args, **kwargs):
        if not kwargs.get('assessment_questions'):
            return {'status': 'unavailable', 'reason': 'assessment_error', 'answers': {}}
        return original(*args, **kwargs)
    monkeypatch.setattr(intent_attractor_judge, 'assess', assess)
    wires, result, store = scripted_rounds(h, 2)
    assert not result['failed']
    assert all(w['messages'][-1]['role'] == 'tool' for w in wires[1:3])
    assert all('UNAVAILABLE' not in json.dumps(w['messages']) for w in wires)
    rounds = [r for r in store.assessments() if r['phase'] == 'tool_round']
    assert all(r['delivery']['reason'] == 'judge_unavailable' for r in rounds)
    state = store.read()
    assert state is not None and state['decision'] == 'UNAVAILABLE'


def test_finish_with_actions_repair_retries_before_finishing(harness):
    h = harness
    configure(h, judge_enabled=False)
    responses = iter([
        _mock_response(tool_calls=[declaration(0, mode='create', transition='finish', proposal=PROPOSAL),
                                  call('terminal', {'command': 'check'}, 'rejected')]),
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL),
                                  call('terminal', {'command': 'check'}, 'retried')]),
        _mock_response(tool_calls=[declaration(1, transition='finish')]),
        _mock_response(content='Verified.')])
    wires = []
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    with patch('model_tools.handle_function_call', return_value='checked') as execute:
        result = h.agent.run_conversation('Implement and test the fix; do not publish.')
    assert not result['failed'] and execute.call_count == 1
    repair = wires[1]['messages'][-1]['content']
    assert 'Retry rejected actions with transition=act before declaring finish' in repair


def test_completion_propositions_do_not_depend_on_fidelity():
    from agent.intent_attractor import decide, questions
    answers = {k: {'noul': .5} for k in questions()}
    answers.update({k: {'noul': .05} for k in ('complete', 'blocked', 'waiting')})
    answers.update({k: {'noul': .99} for k in ('evidence', 'incomplete', 'actionable')})
    assert decide(answers) == 'CONTINUE_NOW'
    answers['waiting']['noul'] = .99
    assert decide(answers) == 'WAIT_FOR_EVENT'
    answers['waiting']['noul'] = .05
    answers['blocked']['noul'] = .99
    assert decide(answers) == 'BLOCKED'


def test_same_revision_old_judge_round_is_not_fresh():
    from agent.intent_feedback import select_delivery
    state = {'revision': 3, 'tool_rounds': 7}
    record = {'revision': 3, 'round': 6, 'status': 'available',
              'decision': 'CONTINUE_NOW', 'answers': {'coverage': {'noul': .01}}}
    assert select_delivery(state, record)['reason'] == 'stale'
