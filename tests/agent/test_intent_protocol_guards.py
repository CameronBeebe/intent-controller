"""Actor-era equivalents of stop/privacy/round-race regression contracts."""
import copy
import json
from unittest.mock import patch

import pytest

from agent.intent_attractor import IntentStore
from tests.agent.test_intent_attractor import harness as _harness
from tests.agent.test_intent_protocol import PROPOSAL, call, declaration
from tests.agent.test_run_agent import _mock_response

harness = _harness


def test_hosted_turn_cannot_use_profile_intent_store_or_judge(harness):
    from agent.runtime_scope import bind_state
    from tests.tools.test_sandbox_scope_conformance import A
    from agent.intent_protocol import begin

    agent = harness.agent  # Enabled local contract is not a hosted capability.
    messages = [{'role': 'user', 'content': 'Summarize my task.'}]
    with bind_state(A):
        begin(agent, messages)
    assert agent._intent_unavailable and agent._intent_attractor is None
    assert IntentStore(agent.persistence, agent.session_id).read() is None
    assert len(messages) == 1 and not harness.fidelity_payloads and not harness.payloads


def test_restricted_tool_evidence_has_no_extra_export(harness):
    from hermes_cli.config import atomic_config_write
    from tools.connectors.data_use import record_data_use
    h = harness
    h.config['agent']['intent_attractor']['feedback_interval'] = 1
    atomic_config_write(h.home / 'config.yaml', h.config)
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL),
                                  call('terminal', {'command': 'read restricted'}, 'restricted')]),
        _mock_response(tool_calls=[declaration(1, transition='blocked')]),
        _mock_response(content='Restricted evidence remains local.')]

    def execute(*a, **kw):
        record_data_use(['no_external_export'])
        return 'PRIVATE_RESTRICTED_EVIDENCE'

    with patch('model_tools.handle_function_call', side_effect=execute):
        result = h.agent.run_conversation('Analyze the local result.')
    assert not result['failed']
    assert len(h.fidelity_payloads) == 1  # Initial user-only reconciliation.
    assert not h.payloads
    assert 'PRIVATE_RESTRICTED_EVIDENCE' not in json.dumps(h.fidelity_payloads)
    assert IntentStore(h.agent._session_db, h.agent.session_id).read()['decision'] == 'RESTRICTED_EVIDENCE'


def test_provider_mismatch_never_falls_back_or_exports(harness):
    from hermes_cli.config import atomic_config_write
    h = harness
    h.config['agent']['intent_attractor']['provider'] = 'automatic'
    atomic_config_write(h.home / 'config.yaml', h.config)
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', transition='finish', proposal=PROPOSAL)]),
        _mock_response(content='Independent judgment unavailable.')]
    result = h.agent.run_conversation('Explain the result.')
    assert not result['failed']
    assert not h.payloads and not h.fidelity_payloads
    h.aux.assert_not_called()
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    assert store.read()['decision'] == 'UNAVAILABLE'
    assert all(r['status'] == 'unavailable' for r in store.assessments())


@pytest.mark.parametrize('guard,expected', [('pause', 'PAUSED'), ('goal', 'GOAL_CONFLICT')])
def test_stop_pause_and_explicit_goal_do_not_continue_or_claim_done(harness, guard, expected):
    h = harness
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    responses = iter([
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL)]),
        # A declaration guessing the new revision cannot upgrade an old request.
        *([_mock_response(tool_calls=[declaration(2, transition='blocked')])] if guard == 'pause' else []),
        _mock_response(tool_calls=[declaration(2 if guard == 'pause' else 1, transition='blocked')]),
        _mock_response(content='Stopped at the controlling boundary.')])
    calls = []

    def create(**kw):
        calls.append(kw)
        if len(calls) == 2:
            if guard == 'pause':
                store.command('pause')
            else:
                store.db.state_meta.set('goal:' + store.session_id, json.dumps({'status': 'active'}))
        return next(responses)

    h.agent.client.chat.completions.create.side_effect = create
    result = h.agent.run_conversation('Implement and verify.')
    assert not result['failed']
    assert len(calls) == (4 if guard == 'pause' else 3)
    assert not h.payloads
    assert store.read()['decision'] == expected
    assert store.read()['continuations'] == 0


def test_edit_during_round_judgment_delivers_current_authority(harness, monkeypatch):
    from hermes_cli.config import atomic_config_write
    from agent import intent_attractor_judge
    h = harness
    h.config['agent']['intent_attractor']['feedback_interval'] = 1
    atomic_config_write(h.home / 'config.yaml', h.config)
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    original = intent_attractor_judge.assess

    def assess(envelope, *a, **kw):
        record = original(envelope, *a, **kw)
        if envelope.get('phase') == 'tool_round':
            store.command('edit boundaries Only read local files')
        return record

    monkeypatch.setattr(intent_attractor_judge, 'assess', assess)
    h.probabilities.extend([{'incomplete': .99, 'actionable': .99}, {'blocked': .99}])
    responses = iter([
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL),
                                  call('terminal', {'command': 'check'}, 'one')]),
        _mock_response(tool_calls=[declaration(1), call('terminal', {'command': 'obsolete'}, 'obsolete')]),
        _mock_response(tool_calls=[declaration(2, transition='blocked')]),
        _mock_response(content='Stopped at the edited boundary.')])
    wires = []

    def create(**kw):
        wires.append(copy.deepcopy(kw['messages']))
        return next(responses)

    h.agent.client.chat.completions.create.side_effect = create
    with patch('model_tools.handle_function_call', return_value='observed result') as execute:
        result = h.agent.run_conversation('Check the result.')
    assert execute.call_count == 1
    assert not result['failed']
    packet = wires[1][-1]['content']
    assert 'Only read local files' in packet and '[Current intent r2' in packet
    assert all(token not in packet for token in ('unavailable', 'fresh', 'CONTINUE_NOW', 'p(incomplete)'))
    records = store.assessments()
    assert next(r for r in records if r.get('phase') == 'tool_round')['decision'] == 'STALE'
    assert store.read()['continuations'] == 0
