"""Actor-owned interpretation; mocked inference, real loop/store/replay boundaries."""
import copy
import json
from unittest.mock import patch

import pytest

from agent.intent_attractor import IntentStore, real_sources
from tests.fixtures.storage_seed import seed_session
from persistence.conversation.messages import ConversationRequest
from tests.fixtures.storage_read import conversation
from tests.agent.test_intent_attractor import harness as _harness
from tests.agent.test_intent_protocol import PROPOSAL, call, declaration
from tests.agent.test_run_agent import _mock_response

harness = _harness
REQUEST = 'Can you look in the repos I have which ones we have been working on most recently'
STEERING = 'oh i think there was one on fibonacci stuff lets take a look at that plz'
AIM = 'Look at the Fibonacci repo and summarize the recent work there'


@pytest.mark.parametrize('scores', [
    {'coverage': .79, 'consistent': .79, 'unsupported': .21, 'means_field_outcome': .79},
    {'coverage': .13, 'consistent': .14, 'unsupported': .81},
    None,
])
def test_actor_update_is_applied_before_advisory_judge_and_visible_next_request(harness, scores):
    from agent import intent_attractor_judge
    from tests.agent.intent_test_helpers import answer
    h = harness
    proposal = copy.deepcopy(PROPOSAL)
    proposal['fields']['outcome'] = 'Identify recently active local repositories'
    h.probabilities.append({'complete': .99})
    answer(h, 'Repositories reviewed.', proposal)
    first = h.agent.run_conversation(REQUEST)
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    original_assess = intent_attractor_judge.assess
    observed = []
    def assess(*args, **kwargs):
        if kwargs.get('assessment_questions'):
            observed.append(store.read()['fields']['outcome'])
            if scores is None:
                raise OSError('offline judge')
        return original_assess(*args, **kwargs)
    h.fidelity_probabilities.append(scores or {})
    h.probabilities.append({'complete': .99})
    update = {'update_type': 'refinement', 'fields': {'outcome': AIM},
              'operations': [{'op': 'add_subgoal', 'text': 'Inspect Fibonacci commits'}]}
    responses = iter([
        _mock_response(tool_calls=[declaration(1, mode='update', proposal=update),
                                  call('terminal', {'command': 'inspect fibonacci'}, 'inspect')]),
        _mock_response(tool_calls=[call('terminal', {'command': 'verify commits'}, 'verify')]),
        _mock_response(tool_calls=[declaration(2, transition='finish')]),
        _mock_response(content='Recent Fibonacci work summarized.'),
    ])
    wires = []
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    with patch.object(intent_attractor_judge, 'assess', side_effect=assess), patch(
            'model_tools.handle_function_call', return_value='local commits inspected') as execute:
        result = h.agent.run_conversation(STEERING, conversation_history=first['messages'])
    state = store.read()
    assert observed == [AIM], 'Jev must observe an already-applied interpretation'
    assert state['fields']['outcome'] == AIM and not state.get('pending_proposal')
    assert state['subgoals'][-1]['text'] == 'Inspect Fibonacci commits'
    assert not result['failed'] and execute.call_count == 2
    receipts = [json.loads(m['content']) for m in wires[1]['messages'] if m.get('role') == 'tool'
                and m.get('tool_call_id', '').startswith('control')]
    assert any(r.get('fields', {}).get('outcome') == AIM for r in receipts)
    record = [r for r in store.assessments() if r['phase'] == 'reconciliation'][-1]
    assert record['applied_revision'] == 2 and record['application'] == 'APPLIED'
    assert record['decision'] == ('UNAVAILABLE' if scores is None else 'UNCERTAIN' if scores['coverage'] == .79 else 'REJECTED')
    assert record['revision'] == 2
    assert record['advisory'] is True
    if record['decision'] == 'REJECTED':
        assert receipts[-1]['assessment']['role'] == 'advisory'
    else:
        assert receipts[-1]['assessment'] is None  # weak/unavailable advice stays in receipts
    assert AIM in json.dumps(wires[1]['messages'])


def test_feedback_provenance_survives_gateway_sqlite_rehydration(harness):
    from gateway.run import _build_gateway_agent_history
    from agent.intent_attractor import begin_turn
    h = harness
    db = h.agent._session_db
    seed_session(db, h.agent.session_id, source='cli')
    messages = [{'role': 'user', 'content': REQUEST}]
    begin_turn(h.agent, messages)  # actual protocol producer
    synthetic = messages[-1]['content']
    messages += [{'role': 'assistant', 'content': 'Checking.'},
                 {'role': 'user', 'content': STEERING, 'display_kind': 'steer'},
                 {'role': 'assistant', 'content': 'Inspecting Fibonacci.'},
                 {'role': 'user', 'content': synthetic},  # identical real paste is still a source
                 {'role': 'assistant', 'content': 'Received.'},
                 {'role': 'user', 'content': [{'type': 'text', 'text': 'Use this diagram'},
                      {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,PRIVATE'}}]}]
    expected = real_sources(messages)
    h.agent._flush_messages_to_session_db(messages)
    replay, _ = _build_gateway_agent_history(conversation(db, h.agent.session_id, ConversationRequest(repair_alternation=True)))
    assert real_sources(replay) == expected
    store = IntentStore(db, h.agent.session_id)
    # Simulate the old gateway projection: same durable UID, provenance lost.
    polluted = copy.deepcopy(messages[2])
    polluted.pop('display_metadata')
    state = store.read()
    state.pop('source_provenance_version', None)  # legacy state predates recovery
    state['sources'] = real_sources([messages[0], polluted])
    state['immediate_request'] = state['sources'][-1]
    store.save(state)
    begin_turn(h.agent, replay)
    assert store.read()['sources'] == [expected[0]]
    assert store.read().get('source_recovery', {}).get('removed_anchors') == [polluted['message_uid']]
    assert 'provenance_recovery' in store.command('history')
    assert 'Immediate request: Use this diagram' in replay[-1]['content']
    assert real_sources(replay) == expected
    from agent.intent_protocol import commit
    receipt = commit(h.agent, {'revision': store.read()['revision'], 'mode': 'create',
        'transition': 'act', 'proposal': copy.deepcopy(PROPOSAL)}, replay)
    assert receipt['accepted']
    context = json.loads(h.fidelity_payloads[-1]['state'])['fidelity_context']
    assert context['original_user_expressions'] == expected
    assert context['immediate_turn_request'] == expected[-1]
    assert 'PRIVATE' not in json.dumps(context)
    from agent.intent_source_recovery import recover_sources
    unknown = {'anchor': 'unavailable-historical-row', 'text': 'Keep my earlier boundary'}
    legacy = copy.deepcopy(store.read())
    legacy.pop('source_provenance_version', None)
    legacy['sources'] = [unknown, *real_sources([messages[0], polluted])]
    recovered = recover_sources(store, legacy)
    assert recovered['sources'] == [unknown, expected[0]]
