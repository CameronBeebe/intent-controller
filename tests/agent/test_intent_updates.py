"""Durable objective updates use typed transport decisions, not message keywords."""
import json

import pytest

from tests.agent.test_intent_attractor import harness

from agent.intent_attractor import IntentStore
from agent.intent_protocol import commit
from tests.agent.intent_test_helpers import initialize, answer


def proposal(h, kind, fields):
    h.proposal = {'update_type': kind, 'fields': fields}


@pytest.mark.parametrize('turn_request', ['oh sorry can you repeat tldr', 'explain that', 'thanks'])
def test_repository_objective_survives_tldr_turn_without_execution_escalation(harness, turn_request):
    h = harness
    fields = {'outcome': 'Help manage repositories', 'constraints': 'Prioritize active and portfolio',
              'verification': '', 'boundaries': 'Do not publish', 'questions': ''}
    proposal(h, 'replacement', fields)
    h.probabilities.append({'complete': .99})
    answer(h, 'Active and portfolio repositories first.', h.proposal)
    first = h.agent.run_conversation('Help managing my repos, primarily active and portfolio.')
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    state = store.read()
    state.update(continuations=2, assessments_used=3)
    store.save(state)
    # Presentation retains fields and spending allowances without a new judge call.
    proposal(h, 'presentation', {})
    h.agent.client.chat.completions.create.reset_mock()
    answer(h, 'TLDR: prioritize active and portfolio repos.', h.proposal)
    result = h.agent.run_conversation(turn_request, conversation_history=first['messages'])
    state = store.read()
    assert state['fields'] == fields
    assert (state['continuations'], state['assessments_used']) == (2, 3)
    assert state['immediate_request']['text'] == turn_request
    assert state['update_type'] == 'presentation'
    assert state['decision'] == 'TURN_ONLY'
    assert result['final_response'] == 'TLDR: prioritize active and portfolio repos.'
    assert h.agent.client.chat.completions.create.call_count == 2
    assert len(h.payloads) == 1
    wire = h.agent.client.chat.completions.create.call_args.kwargs['messages']
    # A fresh input is not classified for the actor. The accepted update, not
    # the owner's internal stop label, authorizes the reply at this boundary.
    checkpoint = h.agent.client.chat.completions.create.call_args_list[0].kwargs['messages'][-1]['content']
    assert 'mode=update' in checkpoint and f'Immediate request: {turn_request}' in checkpoint
    receipt = json.loads(wire[-1]['content'])
    assert receipt['accepted'] and receipt['transition'] == 'finish' and receipt['fields'] == fields
    assert receipt['revision'] == state['revision'] and 'no tools' in receipt['next']
    assert 'TURN_ONLY' not in json.dumps(wire)
    assert wire[0]['content'] == h.agent._cached_system_prompt


def test_refinement_replacement_and_manual_authority_survive_legacy_resume(harness):
    h = harness
    from agent.intent_attractor import empty_state
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    legacy = empty_state()  # Old persisted rows have no update_type/immediate_request.
    legacy.update(revision=7, continuations=2, assessments_used=4)
    legacy['fields'].update(outcome='Manage repos', constraints='Active and portfolio', boundaries='No publishing')
    store.save(legacy)
    store.command('edit boundaries Never publish')
    store.command('pin constraints')
    proposal(h, 'refinement', {'verification': 'Run repository checks'})
    initialize(h, [{'role': 'user', 'content': 'Also run repository checks', 'message_uid': 'refine'}], h.proposal)
    refined = store.read()
    assert refined['fields'] == {**legacy['fields'], 'verification': 'Run repository checks', 'boundaries': 'Never publish'}
    assert (refined['continuations'], refined['assessments_used']) == (2, 5)
    proposal(h, 'replacement', dict.fromkeys(legacy['fields'], '') | {'outcome': 'Write a release summary'})
    initialize(h, [{'role': 'user', 'content': 'Instead, write a release summary', 'message_uid': 'replace'}], h.proposal)
    restored = IntentStore(h.agent._session_db, h.agent.session_id).read()
    assert restored['fields']['outcome'] == 'Write a release summary'
    assert restored['fields']['verification'] == ''
    assert restored['fields']['constraints'] == 'Active and portfolio'
    assert restored['fields']['boundaries'] == 'Never publish'
    assert restored['origins']['boundaries'] == 'user'
    assert restored['pins'] == ['constraints']
    assert (restored['continuations'], restored['assessments_used']) == (0, 1)


def test_ambiguous_turn_preserves_target_and_requests_clarification_not_work(harness):
    h = harness
    fields = {'outcome': 'Manage repos', 'verification': '', 'constraints': 'Active and portfolio',
              'boundaries': '', 'questions': ''}
    proposal(h, 'replacement', fields)
    initialize(h, [{'role': 'user', 'content': 'Manage active and portfolio repos', 'message_uid': 'initial'}], h.proposal)
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    proposal(h, 'ambiguous', {})
    answer(h, 'Do you want to change the objective?', h.proposal)
    result = h.agent.run_conversation('Maybe the other thing instead?')
    assert store.read()['fields'] == fields
    assert store.read()['decision'] == 'INTENT_AMBIGUOUS'
    assert h.payloads == []
    assert result['final_response'] == 'Do you want to change the objective?'
    wire = json.dumps(h.agent.client.chat.completions.create.call_args.kwargs['messages'])
    receipt = json.loads(h.agent.client.chat.completions.create.call_args.kwargs['messages'][-1]['content'])
    assert receipt['accepted'] and receipt['fields'] == fields and receipt['transition'] == 'finish'
    assert receipt['revision'] == store.read()['revision'] and 'no tools' in receipt['next']
    assert 'INTENT_AMBIGUOUS' not in wire
    assert 'Maybe the other thing instead?' in wire


@pytest.mark.parametrize('bad', [
    {'outcome': 'Legacy untyped overwrite'},
    {'update_type': 'replacement', 'fields': {'outcome': 'Missing rest'}},
    {'update_type': 'refinement', 'fields': {'constraints': None}},
    {'update_type': 'certain', 'fields': {}},
])
def test_invalid_proposal_never_mutates_existing_objective_or_budget(harness, bad):
    h = harness
    proposal(h, 'replacement', {'outcome': 'Manage repos', 'verification': '', 'constraints': 'Active first',
                                'boundaries': '', 'questions': ''})
    initialize(h, [{'role': 'user', 'content': 'Manage repos', 'message_uid': 'u1'}], h.proposal)
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    old = store.read()
    old.update(continuations=2, assessments_used=4)
    store.save(old)
    with pytest.raises(ValueError):
        commit(h.agent, {'revision': old['revision'], 'mode': 'update', 'transition': 'finish', 'proposal': bad},
               [{'role': 'user', 'content': 'Explain that', 'message_uid': 'u2'}])
    state = store.read()
    assert state['fields'] == old['fields']
    assert (state['continuations'], state['assessments_used']) == (2, 4)
    assert state['revision'] == old['revision']


def test_fidelity_payload_separates_expressions_spec_and_immediate_request(harness):
    h = harness
    proposal(h, 'replacement', {'outcome': 'Manage repos', 'verification': '', 'constraints': 'Active first',
                                'boundaries': '', 'questions': ''})
    h.probabilities.append({'complete': .99})
    answer(h, 'Here is the repo plan.', h.proposal)
    h.agent.run_conversation('Help manage my active repos')
    envelope = json.loads(h.payloads[0]['state'])
    fidelity = envelope['fidelity_context']
    assert fidelity['original_user_expressions'][0]['text'] == 'Help manage my active repos'
    assert fidelity['proposed_extracted_specification']['outcome']['text'] == 'Manage repos'
    assert fidelity['immediate_turn_request']['text'] == 'Help manage my active repos'
    assert 'candidate' not in fidelity and 'tool_results' not in fidelity
