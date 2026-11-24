"""New real requests reconcile prior interpretation without inheriting its turn mode."""
import copy
from unittest.mock import patch

import pytest

from agent.intent_attractor import IntentStore
from tests.agent.test_intent_attractor import harness as _harness
from tests.agent.test_intent_protocol import PROPOSAL, call, declaration
from tests.agent.test_intent_selective_feedback import configure
from tests.agent.test_run_agent import _mock_response

harness = _harness


@pytest.mark.parametrize('user_input,fields,manual', [
    ('Apply the smallest correction to retry.json now. Preserve every key, all other values, '
     'and the client API; do not modify any other file or contact the endpoint. '
     'Read the saved file back to verify the artifact.',
     {'outcome': 'Correct retry.json and read it back', 'boundaries': 'Edit only retry.json; no network'}, False),
    ('Review only the Fibonacci code; do not edit any files.',
     {'outcome': 'Review Fibonacci only', 'boundaries': 'Read-only; no file edits'}, False),
    ('Apply the correction now.',
     {'outcome': 'Correct retry.json', 'boundaries': 'Edits allowed'}, True),
])
def test_new_user_input_checkpoint_does_not_classify_from_previous_turn(harness, user_input, fields, manual):
    h = harness
    configure(h, judge_enabled=False)
    first_proposal = copy.deepcopy(PROPOSAL)
    first_proposal['fields'].update(outcome='Review retry.json', boundaries='Read-only; no file edits')
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', transition='finish', proposal=first_proposal)]),
        _mock_response(content='Reviewed without edits.'),
        _mock_response(tool_calls=[declaration(1, mode='update', transition='finish',
            proposal={'update_type': 'presentation', 'fields': {}})]),
        _mock_response(content='- Reviewed.'),
    ]
    first = h.agent.run_conversation('Review retry.json without editing any files.')
    detour = h.agent.run_conversation('Summarize in one bullet.', conversation_history=first['messages'])
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    if manual:
        store.command('edit boundaries Read-only; no file edits')
        store.command('pin boundaries')
    before = store.read()
    assert before is not None
    from agent.intent_feedback import packet as intent_packet
    assert 'Answer only the immediate presentation request' in intent_packet(before, detour['messages'])
    revision = before['revision']
    wires = []
    responses = iter([
        _mock_response(tool_calls=[declaration(revision, mode='update',
            proposal={'update_type': 'refinement', 'fields': fields}),
            call('terminal', {'command': 'inspect authorized scope'}, 'inspect')]),
        _mock_response(tool_calls=[declaration(revision + 1, transition='finish')]),
        _mock_response(content='Scope reconciled.'),
    ])
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        if len(wires) == 1:
            published = store.read()
            assert published is not None and published['sources'] == before['sources']
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    with patch('model_tools.handle_function_call', return_value='inspected') as execute:
        result = h.agent.run_conversation(user_input, conversation_history=detour['messages'])
    assert not result['failed'] and execute.call_count == 1
    packet = wires[0]['messages'][-1]['content']
    assert 'mode=update' in packet and f'revision={revision}' in packet
    assert 'Immediate request: ' + user_input in packet
    assert 'Presentation-only:' not in packet
    assert 'Answer only the immediate presentation request' not in packet
    assert 'Reconcile the current user request' in packet
    # Publication cannot itself consume the user_input or erase authoritative state.
    state = store.read()
    assert state is not None
    assert state['sources'][-1]['text'] == user_input
    assert state['fields']['outcome'] == fields['outcome']
    if manual:
        assert 'user-edited=boundaries; pinned=boundaries' in packet
        assert state['fields']['boundaries'] == before['fields']['boundaries']
        assert state['pins'] == before['pins'] and state['origins']['boundaries'] == 'user'
    else:
        assert state['fields']['boundaries'] == fields['boundaries']
