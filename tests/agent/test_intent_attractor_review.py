"""Independent review regressions; real loop and SQLite, mocked transports only."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from tests.fixtures.storage_seed import seed_session, seed_messages
from tests.fixtures.storage_read import transcript_rows
from tests.agent.test_intent_attractor import harness
from tests.agent.test_run_agent import _mock_response


@pytest.fixture(autouse=True)
def no_background_title(monkeypatch):
    monkeypatch.setattr('agent.title_generator.maybe_auto_title', lambda *a, **kw: False)


def tool_response():
    tool = SimpleNamespace(id='obsolete', type='function', function=SimpleNamespace(
        name='terminal', arguments=json.dumps({'command': 'printf obsolete'})))
    return _mock_response(content='', tool_calls=[tool], finish_reason='tool_calls')


@pytest.mark.parametrize('producer', ['verify', 'pre_verify', 'kanban', 'lsp', 'length', 'dropped', 'empty'])
def test_internal_producers_excluded_after_sqlite_replay(harness, monkeypatch, producer):
    from agent.intent_attractor import real_sources
    h = harness
    h.agent._intent_guidance_enabled = False  # Exercise core nudge producers independently of protocol repair.
    h.probabilities.extend([{'complete': .99}] * 4)
    if producer in ('verify', 'pre_verify', 'kanban', 'lsp'):
        seam = {'verify': '_verify_on_stop_nudge', 'pre_verify': '_pre_verify_nudge',
                'kanban': '_kanban_stop_nudge', 'lsp': '_late_lsp_verdicts'}[producer]
        from unittest.mock import MagicMock
        monkeypatch.setattr('agent.turn_stop_gates.' + seam, MagicMock(side_effect=['Internal instruction', None]))
    first = _mock_response(content='A preliminary answer.')
    if producer == 'length':
        first = _mock_response(content='A preliminary answer.', finish_reason='length')
    if producer == 'dropped':
        first = _mock_response(content='A preliminary answer.', finish_reason='tool_calls')
    responses = [first, _mock_response(content='The complete explanation.')]
    if producer == 'empty':
        responses = [tool_response(), _mock_response(content=''), _mock_response(content='The complete explanation.')]
    snapshots = []
    responses = iter(responses)
    def create(**kwargs):
        snapshots.extend(copy.deepcopy(h.agent._session_messages))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    with patch('model_tools.handle_function_call', return_value=json.dumps({'output': 'checked', 'exit_code': 0})):
        result = h.agent.run_conversation('Explain the result.')
    internal = [m for m in snapshots + result['messages'] if m.get('role') == 'user'
                and m.get('content') != 'Explain the result.'
                and not m.get('display_metadata', {}).get('intent_attractor')]
    if producer in ('dropped', 'empty'):
        # These producers intentionally remove their ephemeral rows before a
        # returned/durable replay; they cannot become next-turn user intent.
        assert all(m.get('content') == 'Explain the result.' for m in result['messages']
                   if m.get('role') == 'user' and not m.get('display_metadata', {}).get('intent_attractor'))
        return
    assert internal, producer
    # Exercise the actual producer, then the durable representation. Ephemeral
    # rows may be filtered by the core flush but must remain non-authority in a
    # returned-history replay as well.
    assert all(m.get('display_metadata', {}).get('internal_nudge') for m in internal)
    db = h.agent._session_db
    seed_session(db, 'replay-nudges', source='cli')
    seed_messages(db, 'replay-nudges', *internal)
    assert real_sources(transcript_rows(db, 'replay-nudges')) == []
    human = {'role': 'user', 'content': internal[0]['content']}
    assert real_sources([human])[0]['text'] == human['content']


def test_fidelity_rubric_treats_manual_edits_as_user_input():
    from agent.intent_attractor import questions
    for key in ('coverage', 'unsupported', 'consistent'):
        assert 'manual edits are real user-authorized inputs' in questions()[key]['instructions']
