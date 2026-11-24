"""Runtime nudges stay non-authoritative in returned history and SQLite replay."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.fixtures.storage_seed import seed_session, seed_messages
from persistence.conversation.messages import ConversationRequest
from tests.fixtures.storage_read import transcript_rows, conversation
from tests.agent.test_intent_attractor import harness
from tests.agent.test_run_agent import _mock_response


@pytest.fixture(autouse=True)
def no_background_title(monkeypatch):
    monkeypatch.setattr("agent.title_generator.maybe_auto_title", lambda *a, **kw: False)


def assert_nudge_is_not_user_intent(h, messages, nudge_text, request):
    from agent.intent_attractor import IntentStore, begin_turn, real_sources

    nudges = [m for m in messages if m.get("role") == "user" and m.get("content") == nudge_text]
    assert nudges, "The real producer must have emitted its nudge"
    db = h.agent._session_db
    seed_session(db, "nudge-replay", source="cli")
    seed_messages(db, "nudge-replay", *messages)
    replay = transcript_rows(db, "nudge-replay")
    for history in (messages, replay):
        assert [s["text"] for s in real_sources(history)] == [request]
        sources = real_sources(history + [{"role": "user", "content": "Thanks"}])
        assert [s["text"] for s in sources] == [request, "Thanks"]
    assert all(m.get("display_metadata", {}).get("internal_nudge") for m in nudges)
    # Provenance, not matching text, distinguishes runtime instructions from users.
    assert real_sources([{"role": "user", "content": nudge_text}])[0]["text"] == nudge_text


def test_max_iterations_summary_nudge_is_not_user_intent(harness):
    from agent.context_compressor import MAX_ITERATIONS_SUMMARY_REQUEST
    from run_agent import IterationBudget

    h = harness
    h.agent._intent_guidance_enabled = False  # Core recovery producer contract, not actor protocol.
    h.agent.max_iterations = 1
    h.agent.iteration_budget = IterationBudget(1)
    tool = SimpleNamespace(
        id="check", type="function", function=SimpleNamespace(
            name="terminal", arguments=json.dumps({"command": "printf checked"}),
        ),
    )
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(content="", tool_calls=[tool], finish_reason="tool_calls"),
        _mock_response(content="The iteration budget is exhausted; here is the result."),
    ]
    request = "Check the result without publishing."
    with patch("model_tools.handle_function_call", return_value=json.dumps({"output": "checked", "exit_code": 0})):
        result = h.agent.run_conversation(request)
    assert result["final_response"] == "The iteration budget is exhausted; here is the result."
    assert h.agent.client.chat.completions.create.call_count == 2
    assert_nudge_is_not_user_intent(h, result["messages"], MAX_ITERATIONS_SUMMARY_REQUEST, request)


def test_codex_incomplete_nudge_is_not_user_intent(harness, monkeypatch):
    from agent.conversation_loop import _CODEX_INCOMPLETE_NUDGE
    from tests.agent.test_run_agent_codex_responses import (
        _codex_incomplete_message_response, _codex_message_response,
    )
    from unittest.mock import MagicMock

    h = harness
    h.agent._intent_guidance_enabled = False  # Core recovery producer contract, not actor protocol.
    h.agent.api_mode = "codex_responses"
    h.agent.model = "gpt-5-codex"
    h.probabilities.append({"complete": .99})
    api = MagicMock(side_effect=[
        _codex_incomplete_message_response("Partial explanation."),
        _codex_incomplete_message_response("Partial explanation."),
        _codex_message_response("The complete explanation."),
    ])
    monkeypatch.setattr(h.agent, "_interruptible_api_call", api)
    request = "Explain the result without executing commands."
    result = h.agent.run_conversation(request)
    assert result["final_response"] == "The complete explanation."
    assert api.call_count == 3
    assert_nudge_is_not_user_intent(h, result["messages"], _CODEX_INCOMPLETE_NUDGE, request)


def test_empty_response_producer_does_not_reconcile_synthetic_input(harness):
    from agent.conversation_loop import _EMPTY_TOOL_RESPONSE_NUDGE
    from agent.intent_attractor import IntentStore, real_sources
    from gateway.run import _build_gateway_agent_history
    from tests.agent.test_intent_protocol import PROPOSAL, declaration
    from tests.agent.test_intent_selective_feedback import configure

    h = harness
    configure(h, judge_enabled=False)
    wires, live = [], []
    responses = iter([
        _mock_response(tool_calls=[declaration(0, mode='create', transition='finish', proposal=PROPOSAL)]),
        _mock_response(content=''),  # Real empty-response producer, after accepted finish.
        _mock_response(tool_calls=[declaration(1, transition='finish')]),
        _mock_response(content='Reviewed.'),
    ])
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        live.append(copy.deepcopy(h.agent._session_messages))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    request = 'Review the result without editing files.'
    result = h.agent.run_conversation(request)
    state = IntentStore(h.agent._session_db, h.agent.session_id).read()
    assert state is not None
    assert not result['failed']
    assert len(wires) == 4 and state['revision'] == 1
    assert [s['text'] for s in state['sources']] == [request]
    # Latest upstream adds recovery only to the outgoing copy, never to canonical evidence.
    assert not any(m.get('_empty_recovery_synthetic') for m in live[2])
    assert _EMPTY_TOOL_RESPONSE_NUDGE not in str(live[2])
    assert _EMPTY_TOOL_RESPONSE_NUDGE in str(wires[2]['messages'])
    assert 'mode=update' not in wires[2]['messages'][-1]['content']
    assert 'display_metadata' not in json.dumps(wires[2]['messages'])
    assert all('internal_nudge' not in json.dumps(w['messages']) for w in wires)
    replay, _ = _build_gateway_agent_history(
        conversation(h.agent._session_db, h.agent.session_id, ConversationRequest(repair_alternation=True)))
    for history in (live[2], result['messages'], replay):
        assert [s['text'] for s in real_sources(history)] == [request]
    # Normal persistence still omits ephemeral scaffolding, rather than making it durable.
    assert not any(m.get('content') == _EMPTY_TOOL_RESPONSE_NUDGE for m in replay)
    # A real user can submit the exact literal: it must require an authentic update.
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(1, mode='update', transition='finish',
            proposal={'update_type': 'presentation', 'fields': {}})]),
        _mock_response(content='The result is reviewed.'),
    ]
    followup = h.agent.run_conversation(_EMPTY_TOOL_RESPONSE_NUDGE, conversation_history=replay)
    assert not followup['failed']
    assert [s['text'] for s in real_sources(followup['messages'])] == [request, _EMPTY_TOOL_RESPONSE_NUDGE]


@pytest.mark.parametrize('durable', [False, True])
def test_empty_recovery_repairs_only_proven_sources_on_normal_turn(harness, durable):
    from agent.conversation_loop import _EMPTY_TOOL_RESPONSE_NUDGE
    from agent.intent_attractor import IntentStore, begin_turn, real_sources
    from agent.intent_protocol import commit

    from gateway.run import _build_gateway_agent_history
    from tests.agent.intent_test_helpers import initialize
    from tests.agent.test_intent_selective_feedback import configure

    h = harness
    configure(h, judge_enabled=False)
    db = h.agent._session_db
    seed_session(db, h.agent.session_id, source='cli')
    messages = [{'role': 'user', 'content': 'Inspect only; do not edit files.'}]
    initialize(h, messages)
    store = IntentStore(db, h.agent.session_id)
    store.command('edit boundaries Read-only; do not edit files')
    store.command('pin boundaries')
    # Saved legacy rows still need recovery even though upstream no longer emits them.
    recovery = {'role': 'user', 'content': _EMPTY_TOOL_RESPONSE_NUDGE,
                '_empty_recovery_synthetic': True,
                'display_metadata': {'internal_nudge': 'empty_response'}}
    # Contamination from the old selector: the exact runtime-authored row became evidence.
    polluted = {'role': 'user', 'content': recovery['content'], 'message_uid': 'proven-recovery'}
    recovery['message_uid'] = polluted['message_uid']
    state = store.read()
    assert state is not None
    real = state['sources'][0]
    unknown = {'anchor': 'missing-row', 'text': _EMPTY_TOOL_RESPONSE_NUDGE}
    state.update(source_provenance_version=1, sources=[real, unknown, *real_sources([polluted])])
    state['immediate_request'] = state['sources'][-1]
    state['last_judgment'] = {'decision': 'DONE'}
    store.save(state)
    if durable:
        # Storage/replay adapters that retain a nudge must preserve its producer metadata.
        seed_messages(db, h.agent.session_id, messages[0], {'role': 'assistant', 'content': 'Inspected.'}, recovery)
        history, _ = _build_gateway_agent_history(conversation(db, h.agent.session_id))
        assert history[-1]['display_metadata']['internal_nudge'] == 'empty_response'
    else:
        # Old returned history has the producer's ephemeral flag, but no durable row/metadata.
        recovery.pop('display_metadata', None)
        history = [messages[0], {'role': 'assistant', 'content': '(empty)'}, recovery]
    begin_turn(h.agent, history)
    repaired = store.read()
    assert repaired is not None
    assert repaired['source_recovery']['removed_anchors'] == ['proven-recovery']
    assert {s['anchor'] for s in repaired['sources']} == {real['anchor'], 'missing-row'}
    assert repaired['revision'] == state['revision'] + 1
    assert repaired['fields'] == state['fields'] and repaired['pins'] == state['pins']
    assert repaired['origins'] == state['origins'] and 'last_judgment' not in repaired
    assert repaired['source_recovery']['reconciliation_required']
    assert 'mode=update' in history[-1]['content']
    with pytest.raises(ValueError, match='require create/update'):
        commit(h.agent, {'revision': repaired['revision'], 'mode': 'retain', 'transition': 'finish'}, history)
