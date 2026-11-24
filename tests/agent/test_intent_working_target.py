"""Valid actor interpretations are fallible; uncertainty is not generic paralysis."""
import copy
import pytest
from unittest.mock import patch

from tests.agent.test_intent_attractor import harness as _harness

harness = _harness
from tests.agent.intent_test_helpers import answer, initialize
from tests.agent.test_intent_protocol import call, declaration, PROPOSAL
from tests.agent.test_run_agent import _mock_response
from agent.intent_attractor import IntentStore


@pytest.mark.parametrize('score', [.47, .99, None])
def test_uncertain_first_proposal_applies_actor_fields_without_granting_authority(harness, score):
    h = harness
    proposal = copy.deepcopy(PROPOSAL)
    proposal['fields'].update(outcome='Create Fibonacci ASCII art in Julia', constraints='Upload source')
    proposal['operations'] = [{'op': 'add_subgoal', 'text': 'Publish publicly'}]
    h.fidelity_probabilities.append({'unsupported': score})
    h.probabilities.append({'complete': .99})
    answer(h, 'Local art created.', proposal)
    first = h.agent.run_conversation('Create Fibonacci ASCII art in Julia.')
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    state = store.read()
    assert state['fields']['outcome'] == proposal['fields']['outcome']
    assert state['fields']['constraints'] == 'Upload source'
    assert state['subgoals'][0]['text'] == 'Publish publicly'
    assert state['origins']['constraints'] == state['subgoals'][0]['origin'] == 'inferred'
    assert not state.get('pending_proposal')
    store.command('edit boundaries Never publish')
    answer(h, 'Brief explanation.', {'update_type': 'presentation', 'fields': {}})
    h.agent.run_conversation('TLDR', conversation_history=first['messages'])
    assert store.read()['fields']['outcome'] == proposal['fields']['outcome']
    assert store.read()['fields']['boundaries'] == 'Never publish'
    assert not store.read().get('pending_proposal')  # Explicit manual edit retires pending proposals.


def test_uncertain_batch_is_annotated_not_partially_adopted(harness):
    import json
    h = harness
    proposal = copy.deepcopy(PROPOSAL)
    proposal['fields'].update(outcome='Create a GitHub repository with Julia Fibonacci art',
        verification='Run the Julia program and inspect the art',
        boundaries='Never expose credentials', constraints='Buy advertising for the repository')
    proposal['operations'] = [
        {'op': 'add_subgoal', 'text': 'Check GitHub authentication without printing secrets'},
        {'op': 'add_subgoal', 'text': 'Implement and verify Julia Fibonacci art'},
        {'op': 'add_subgoal', 'text': 'Buy advertising'}]
    h.fidelity_probabilities.append({'unsupported': .78, 'means_field_verification': .96,
        'means_field_boundaries': .99, 'means_field_constraints': .02,
        'means_operation_0': .98, 'means_operation_1': .97, 'means_operation_2': .01})
    h.probabilities.append({'complete': .99})
    answer(h, 'Repository prepared.', proposal)
    h.agent.run_conversation('Create a GitHub repo with Fibonacci art written in Julia.')
    state = IntentStore(h.agent._session_db, h.agent.session_id).read()
    assert state is not None
    assert state['fields']['verification'] == proposal['fields']['verification']
    assert state['fields']['boundaries'] == 'Never expose credentials'
    assert state['fields']['constraints'] == proposal['fields']['constraints']
    assert [g['text'] for g in state['subgoals']] == [op['text'] for op in proposal['operations']]
    assert all(g['origin'] == 'inferred' for g in state['subgoals'])
    assert all(g['revision'] == state['revision'] for g in state['subgoals'])
    assert state['origins']['verification'] == 'inferred'
    assert not state.get('pending_proposal')
    assert state['history'][-1]['subgoals'] == state['subgoals']
    payload = h.fidelity_payloads[-1]
    rubric = payload['questions']['unsupported']['instructions']
    assert 'new ends' in rubric and 'inferred means' in rubric and 'permission' in rubric
    context = json.loads(payload['state'])['fidelity_context']
    assert context['proposed_operations']['operations'][2]['text'] == 'Buy advertising'
    assert context['proposed_extracted_specification']['subgoals'][0]['origin'] == 'inferred'


def test_uncertain_additive_means_refresh_with_prior_aim_and_manual_edit(harness):
    h = harness
    history = [{'role': 'user', 'content': 'Implement and test; never publish.', 'message_uid': 'u1'}]
    initialize(h, history)
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    store.command('edit boundaries Never publish or disclose secrets')
    old = store.read()
    assert old is not None
    h.fidelity_probabilities.append({'unsupported': .47, 'means_operation_0': .98})
    h.probabilities.append({'complete': .99})
    answer(h, 'Checks done.', {'update_type': 'refinement',
        'fields': {'boundaries': 'Publishing now allowed'},
        'operations': [{'op': 'add_subgoal', 'text': 'Inspect the relevant test failure'}]})
    h.agent.run_conversation('Continue with the fix.')
    state = store.read()
    assert state is not None
    assert state['fields'] == old['fields']
    assert state['subgoals'][0]['text'] == 'Inspect the relevant test failure'
    assert state['last_judgment']['phase'] == 'completion'  # not ambiguous-stop suppression
    assert state['update_type'] == 'refinement'


@pytest.mark.parametrize('score', [.47, .99])
def test_negative_advice_does_not_restore_previous_actor_aim(harness, score):
    h = harness
    initialize(h, [{'role': 'user', 'content': 'Implement and test; never publish.', 'message_uid': 'u1'}])
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    bad = copy.deepcopy(PROPOSAL)
    bad['fields']['outcome'] = 'Delete all repositories'
    h.fidelity_probabilities.append({'unsupported': score, 'means_field_verification': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(1, mode='update', proposal=bad), call('terminal', {'command': 'check'}, 'check')]),
        _mock_response(tool_calls=[declaration(2, transition='finish')]),
        _mock_response(content='Local status checked.')]
    with patch('model_tools.handle_function_call', return_value='checked') as execute:
        result = h.agent.run_conversation('Read local status.')
    assert execute.call_count == 1
    assert not result['failed']
    assert store.read()['fields'] == bad['fields']
    assert not store.read().get('pending_proposal')
    receipt = [r for r in store.assessments() if r['phase'] == 'reconciliation'][-1]
    assert receipt['decision'] == ('REJECTED' if score == .99 else 'UNCERTAIN')
    assert receipt['application'] == 'APPLIED'


@pytest.mark.parametrize('text', ['TLDR', 'thanks', 'hello', 'okay'])
def test_no_vocabulary_overrides_actor_semantic_proposal(harness, text):
    h = harness
    proposal = copy.deepcopy(PROPOSAL)
    proposal['fields']['outcome'] = 'Explain the context-specific meaning of this expression'
    h.probabilities.append({'complete': .99})
    answer(h, 'Explanation supplied.', proposal)
    h.agent.run_conversation(text)
    assert IntentStore(h.agent._session_db, h.agent.session_id).read()['fields']['outcome'] == proposal['fields']['outcome']
