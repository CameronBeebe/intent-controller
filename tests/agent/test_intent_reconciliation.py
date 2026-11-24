"""Hierarchical intent through the real turn loop; only external transports are mocked."""
import json

from agent.intent_attractor import IntentStore
from tests.agent.test_intent_attractor import harness
from tests.agent.intent_test_helpers import answer


def test_multi_turn_hierarchy_preserves_aim_requirements_and_subgoal_identity(harness):
    h = harness
    from hermes_cli.config import atomic_config_write
    h.config['agent']['intent_attractor']['max_assessments'] = 20
    atomic_config_write(h.home / 'config.yaml', h.config)
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    fields = {'outcome': 'Maintain a useful portfolio', 'constraints': 'Keep active projects',
              'verification': 'Run checks', 'boundaries': 'No publishing', 'questions': ''}
    history = []

    def turn(text, kind, changes=None, operations=None):
        nonlocal history
        proposal = {'update_type': kind, 'fields': changes or {}}
        if operations is not None:
            proposal['operations'] = operations
        h.probabilities.append({'complete': .99})
        answer(h, 'Current request answered.', proposal)
        history = h.agent.run_conversation(text, conversation_history=history)['messages']
        state = store.read()
        assert state is not None
        return state

    turn('Help maintain my portfolio; keep active projects, run checks, no publishing.', 'replacement', fields)
    state = turn('First inventory the active repositories.', 'reconciliation', operations=[
        {'op': 'add_subgoal', 'text': 'Inventory active repositories'}])
    assert state['fields'] == fields
    goal = state['subgoals'][0]
    assert goal['status'] == 'active'
    assert goal['sources'][0]['text'] == 'First inventory the active repositories.'
    identity = goal['id']
    state = turn('Defer the inventory for now.', 'reconciliation', operations=[{'op': 'suspend', 'id': identity}])
    assert state['subgoals'][0]['status'] == 'suspended'
    state = turn('Resume the inventory, focusing on maintained projects.', 'reconciliation', operations=[
        {'op': 'resume', 'id': identity}, {'op': 'refine_subgoal', 'id': identity, 'text': 'Inventory maintained projects'}])
    assert state['subgoals'][0]['status'] == 'active'
    assert state['subgoals'][0]['text'] == 'Inventory maintained projects'
    state = turn('Give me the short explanation before continuing.', 'presentation')
    assert state['subgoals'][0]['id'] == identity
    assert state['fields'] == fields
    state = turn('Inventory is done; defer it while we focus on portfolio examples.', 'reconciliation', operations=[
        {'op': 'progress', 'id': identity, 'status': 'completed'}])
    assert state['subgoals'][0]['status'] == 'completed'
    assert state['fields']['outcome'] == fields['outcome']
    state = turn('Also check the README links.', 'refinement', {'verification': 'Run checks and validate README links'})
    assert state['fields']['constraints'] == fields['constraints']
    assert state['subgoals'][0]['id'] == identity
    state = turn('Instead, prepare a private release summary.', 'replacement', {**fields, 'outcome': 'Prepare private release summary'})
    assert state['fields']['outcome'] == 'Prepare private release summary'
    assert state['subgoals'][0]['id'] == identity
    assert state['subgoals'][0]['status'] == 'completed'
    assert any(e['kind'] == 'revise' for e in state['trajectory'])
    assert any(e['kind'] == 'done' for e in state['trajectory'])
    assert any(e['kind'] == 'assess' for e in state['trajectory'])
    completion_context = json.loads(h.payloads[-1]['state'])
    assert completion_context['subgoals'][0]['id'] == identity
