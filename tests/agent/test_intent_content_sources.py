"""Real user block transports must reconcile; internal/tool evidence is not authority."""
import copy
import json
from unittest.mock import patch

import pytest

from agent.intent_attractor import IntentStore, real_sources
from tests.fixtures.storage_read import transcript_rows
from tests.agent.test_intent_attractor import harness as _harness
from tests.agent.test_intent_protocol import PROPOSAL, call, declaration
from tests.agent.test_run_agent import _mock_response

harness = _harness


@pytest.mark.parametrize('content', [
    [{'type': 'text', 'text': 'New request: inspect the diagram instead; no tools.'}],
    [{'type': 'text', 'text': 'Inspect this instead.'},
     {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,PRIVATE_IMAGE'}}],
    [{'type': 'input_audio', 'input_audio': {'data': 'PRIVATE_AUDIO', 'format': 'wav'}}],
])
@pytest.mark.parametrize('transition', ['act', 'finish'])
def test_new_content_block_turn_requires_update(harness, content, transition):
    h = harness
    h.probabilities.append({'complete': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', transition='finish', proposal=PROPOSAL)]),
        _mock_response(content='First request answered.'),
    ]
    first = h.agent.run_conversation('Implement and test the fix.')
    actions = [call('terminal', {'command': 'must not run'}, 'rejected')] if transition == 'act' else []
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(1, transition=transition), *actions]),
        _mock_response(tool_calls=[declaration(1, mode='update', transition='finish',
            proposal={'update_type': 'presentation', 'fields': {}})]),
        _mock_response(content='New request reconciled.'),
    ]
    with patch('model_tools.handle_function_call') as execute:
        result = h.agent.run_conversation(copy.deepcopy(content), conversation_history=first['messages'])
    assert not execute.called
    assert h.agent._intent_protocol_repairs == 1
    assert not result['failed']
    state = IntentStore(h.agent._session_db, h.agent.session_id).read()
    assert state['revision'] == 2
    assert len(state['sources']) == 2
    assert state['sources'][-1]['anchor'] == real_sources(result['messages'])[-1]['anchor']
    replay = transcript_rows(h.agent._session_db, h.agent.session_id)
    assert real_sources(replay) == real_sources(result['messages'])
    assert 'PRIVATE_' not in json.dumps(state['sources'])


def test_source_blocks_preserve_text_identity_and_explicit_omissions():
    content = [
        {'type': 'text', 'text': 'Keep this requirement.'},
        {'type': 'image_url', 'image_url': {'url': 'https://private.invalid/secret'}},
        {'type': 'input_text', 'text': 'And this boundary.'},
        {'type': 'document', 'source': {'data': 'PRIVATE_DOCUMENT'}},
    ]
    msg = {'role': 'user', 'content': content}
    original = copy.deepcopy(content)
    sources = real_sources([msg])
    assert len(sources) == 1
    source = sources[0]
    assert source['text'] == 'Keep this requirement.\nAnd this boundary.'
    assert source['content_blocks'] == [
        {'index': 0, 'type': 'text', 'content_omitted': False},
        {'index': 1, 'type': 'image_url', 'content_omitted': True},
        {'index': 2, 'type': 'input_text', 'content_omitted': False},
        {'index': 3, 'type': 'document', 'content_omitted': True},
    ]
    assert source['omitted_blocks'] == 0
    assert source['omitted_chars'] == 0
    assert msg['content'] == original
    assert real_sources(json.loads(json.dumps([msg]))) == sources
    untrusted = [dict(msg, role='tool'), dict(msg, role='assistant'),
                 dict(msg, display_metadata={'internal_nudge': {'kind': 'test'}}),
                 dict(msg, display_metadata={'intent_attractor': {'revision': 1}})]
    assert real_sources(untrusted) == []
    assert real_sources([dict(msg, display_kind='steer')]) == sources


def test_source_block_bounds_do_not_drop_message_or_late_text():
    msg = {'role': 'user', 'content': [
        *[{'type': 'image', 'source': {'data': 'PRIVATE'}} for _ in range(40)],
        {'type': 'text', 'text': 'late boundary ' + 'x' * 3000}]}
    source = real_sources([msg])[0]
    assert source['text'].startswith('late boundary')
    assert source['omitted_chars'] > 0
    assert len(source['content_blocks']) <= 16
    assert source['omitted_blocks'] == len(msg['content']) - len(source['content_blocks'])
    assert 'PRIVATE' not in json.dumps(source)
