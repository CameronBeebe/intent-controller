"""Explicit scripted actor declarations for intent tests (never production extraction)."""
import json
from types import SimpleNamespace

from agent.intent_attractor import IntentStore, begin_turn
from agent.intent_protocol import commit
from tests.agent.test_run_agent import _mock_response

DEFAULT_PROPOSAL = {'update_type': 'replacement', 'fields': {
    'outcome': 'Implement and test the fix', 'verification': 'Run tests',
    'constraints': '', 'boundaries': 'Do not publish', 'questions': ''}}


def initialize(h, messages, proposal=None):
    begin_turn(h.agent, messages)
    state = IntentStore(h.agent._session_db, h.agent.session_id).read()
    args = {'revision': state['revision'], 'mode': 'update' if state.get('actor_initialized') else 'create',
            'transition': 'act', 'proposal': proposal or DEFAULT_PROPOSAL}
    return commit(h.agent, args, messages)


def control(h, transition='finish', proposal=None, id='control'):
    state = IntentStore(h.agent._session_db, h.agent.session_id).read()
    args = {'revision': state['revision'] if state else 0, 'mode': 'retain', 'transition': transition}
    if proposal is not None:
        args.update(mode='update' if state and state.get('actor_initialized') else 'create', proposal=proposal)
    return SimpleNamespace(id=id, type='function', function=SimpleNamespace(
        name='intent_control', arguments=json.dumps(args)))


def answer(h, text, proposal):
    """A concrete control round then a concrete text round; preserves request capture."""
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[control(h, proposal=proposal)], finish_reason='tool_calls'),
        _mock_response(content=text)]
