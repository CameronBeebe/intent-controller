"""Classic CLI routing and real authorized gateway intent state."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tests.gateway.test_session_viewer_automation import owner


@pytest.mark.asyncio
async def test_intent_command_routes_to_exact_owner_without_claiming_goal_loop(owner):
    from agent.intent_attractor import IntentStore, FIELDS
    store = IntentStore(owner.db, owner.target)
    store.save({'revision': 1, 'fields': dict.fromkeys(FIELDS, ''), 'sources': [],
                'origins': dict.fromkeys(FIELDS, 'inferred'), 'pins': [], 'history': [],
                'decision': 'PENDING', 'continuations': 0, 'assessments_used': 0, 'paused': False}, 0)
    response = await owner.call('intent', 'command', 'edit outcome Ship only locally')
    assert 'Ship only locally' in response['value']
    assert 'Extracted intent r2' in response['value']
    assert 'outcome [user-edited]' in response['value']
    assert 'User messages (evidence):' in response['value']
    assert store.read()['revision'] == 2
    assert not getattr(owner.authority, 'viewer_automation', {}).get(owner.ref.session_id)
    assert (await owner.call('intent', '_save'))['message'] == 'invalid_params'
    assert (await owner.call('intent', 'command', 'edit', 'unexpected'))['message'] == 'invalid_params'


@pytest.mark.asyncio
async def test_invalid_intent_arguments_return_usage_through_owner_and_cli(owner):
    import asyncio
    from hermes_cli.intent_command import cli_command
    from hermes_cli.gateway_client import GatewayRPCError

    loop = asyncio.get_running_loop()

    def call(method, **params):
        reply = asyncio.run_coroutine_threadsafe(owner.viewer.dispatch(
            {'id': 2, 'method': method, 'params': params}), loop).result(timeout=10)
        if 'error' in reply:
            error = reply['error']
            raise GatewayRPCError(error['message'], error['code'], error.get('data'))
        return reply['result']

    cli = SimpleNamespace(session_id=owner.ref.session_id,
                          _session_db=SimpleNamespace(connection=SimpleNamespace(call=call)))
    from agent.intent_attractor import IntentStore, FIELDS
    store = IntentStore(owner.db, owner.target)
    for populated in (True, False):
        if populated:
            store.save({'revision': 1, 'fields': dict.fromkeys(FIELDS, ''), 'sources': [],
                        'origins': dict.fromkeys(FIELDS, 'inferred'), 'pins': [], 'history': [],
                        'decision': 'PENDING', 'paused': False}, 0)
        else:
            owner.db.state_meta.set(store.key, 'null')
        for command in ('edit', 'edit outcome', 'pin nonsense', 'pin outcome extra', 'inspect extra'):
            text = await asyncio.to_thread(cli_command, cli, command)
            assert '/intent edit FIELD TEXT' in text
            assert 'outcome' in text
            assert 'gateway command failed' not in text
    cli.session_id = 'not-owned'
    refused = await asyncio.to_thread(cli_command, cli, 'edit')
    assert 'owner rejected' in refused
    assert 'gateway command failed' not in refused
    from dataclasses import replace
    cli.session_id = owner.ref.session_id
    owner.viewer.actor = replace(owner.viewer.actor, capabilities=frozenset({'session:read'}))
    before = store.read()
    refused = await asyncio.to_thread(cli_command, cli, 'edit outcome Unauthorized')
    assert 'owner rejected' in refused and 'permission_denied' in refused
    assert store.read() == before


def test_classic_registry_handler_uses_current_session_and_fails_closed(monkeypatch):
    from hermes_cli.commands import resolve_command
    assert resolve_command('intent').cli_only is True
    from cli import HermesCLI
    cli = HermesCLI.__new__(HermesCLI)
    conn = MagicMock()
    conn.call.return_value = {'value': 'Intent r2 · PENDING', 'state': None}
    cli.session_id = 'exact-owner'
    cli._session_db = SimpleNamespace(connection=conn)
    cli.agent = None
    printed = []
    monkeypatch.setattr('hermes_cli.cli_commands_mixin._cp', printed.append)
    cli.process_command('/intent inspect')
    assert conn.call.call_args.kwargs['session_id'] == 'exact-owner'
    assert conn.call.call_args.kwargs['kind'] == 'intent'
    assert printed == ['Intent r2 · PENDING']
    conn.call.side_effect = RuntimeError('offline')
    cli.process_command('/intent edit outcome never local fallback')
    assert 'unavailable' in printed[-1].lower()


@pytest.mark.asyncio
async def test_hierarchy_snapshot_and_manual_revert_preserve_subgoal_provenance(owner, monkeypatch):
    from agent.intent_attractor import IntentStore, empty_state
    from agent.intent_attractor_updates import IntentUpdate, apply_update
    monkeypatch.setattr('agent.intent_attractor.settings', lambda: {'enabled': True})
    store = IntentStore(owner.db, owner.target)
    state = empty_state()
    state['fields']['outcome'] = 'Maintain portfolio'
    source = {'anchor': 'u1', 'text': 'Inventory projects', 'truncated': False}
    state = apply_update(state, [source], IntentUpdate('reconciliation', {}, [
        {'op': 'add_subgoal', 'text': 'Inventory projects'}]))
    store.save(state)
    await owner.call('intent', 'command', 'edit constraints No publishing')
    store.command('revert 1')
    reply = await owner.viewer.dispatch({'id': 8, 'method': 'session.control.read',
                                        'params': {'session_id': owner.ref.session_id}})
    assert 'error' not in reply, reply
    intent = reply['result']['control']['intent']
    assert intent['subgoals'] == state['subgoals']
    assert intent['trajectory'][-1] == {'kind': 'revise', 'revision': 3, 'label': 'User revert'}
    assert intent['outcome'] == 'Maintain portfolio'
    assert intent['subgoals'][0]['sources'][0]['anchor'] == 'u1'
    assert 'Inventory projects' in store.command('inspect')


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted', ['', 'Maintain portfolio'])
async def test_pending_extraction_survives_owner_rpc_and_actual_paint(owner, monkeypatch, accepted):
    from agent.intent_attractor import IntentStore, empty_state
    from agent.intent_reconciliation import pending
    from agent.intent_attractor_updates import IntentUpdate, apply_update
    from tests.hermes_cli.test_cli_intent_basin import surface, paint
    monkeypatch.setattr('agent.intent_attractor.settings', lambda: {'enabled': True})
    store = IntentStore(owner.db, owner.target)
    state = empty_state()
    state['fields']['outcome'] = accepted
    source = {'anchor': 'u1', 'text': 'Get up to speed on newest AI mathematics', 'truncated': False}
    state = pending(state, [source], {'update_type': 'replacement',
                    'fields': {**state['fields'], 'outcome': source['text']}}, 'UNCERTAIN')
    state['fidelity_annotation'] = {'revision': state['revision'], 'phase': 'reconciliation',
        'assessment_id': 'fixture', 'answers': {'coverage': {'noul': .93},
        'consistent': {'noul': .94}, 'unsupported': {'noul': .47}}}
    proposal = state['pending_proposal']
    # Existing saved states use work for a new request, not measured execution.
    state = apply_update(state, [source, {**source, 'anchor': 'u2', 'text': 'Did you extract my intent?'}],
                         IntentUpdate('presentation', {}))
    state.update(pending_proposal=proposal, decision='DONE')
    store.save(state)
    reply = await owner.viewer.dispatch({'id': 9, 'method': 'session.control.read',
                                        'params': {'session_id': owner.ref.session_id}})
    assert 'error' not in reply, reply
    intent = reply['result']['control']['intent']
    assert intent['outcome'] == (accepted or source['text'])
    assert intent['working_status'] == ('unassessed' if accepted else 'provisional')
    assert intent['visual_confidence'] is None  # Annotation is for previous revision.
    assert intent['extraction_status'] == 'extracted'
    assert intent['pending_outcome'] == source['text']
    assert intent['pending_decision'] == 'UNCERTAIN'
    assert intent['current_request'] == 'Did you extract my intent?'
    _, _, panel, app = surface(intent, 120)
    text = paint(panel, app, 120)
    assert (accepted or source['text']) in text
    assert 'PROVISIONAL' not in text and 'proposal' not in text
    assert 'UNCERTAIN' not in text and 'no accepted target' not in text
    assert 'done' in text  # Completion status is distinct from pending interpretation.
    assert 'awaiting extraction' not in text
    assert 'target-changed' not in text
    assert len(text.splitlines()) == 1 and '%' not in text
    if accepted:
        assert accepted in text
    assert store.read() == state  # Projection and rendering never adopt or rewrite it.


@pytest.mark.asyncio
async def test_extracted_empty_interpretation_is_not_reported_as_awaiting_extraction(owner, monkeypatch):
    from agent.intent_attractor import IntentStore
    from agent.intent_attractor_updates import IntentUpdate, apply_update
    monkeypatch.setattr('agent.intent_attractor.settings', lambda: {'enabled': True})
    store = IntentStore(owner.db, owner.target)
    state = apply_update(None, [{'anchor': 'u1', 'text': 'Hello', 'truncated': False}],
                         IntentUpdate('presentation', {}))
    store.save(state)
    reply = await owner.viewer.dispatch({'id': 10, 'method': 'session.control.read',
                                        'params': {'session_id': owner.ref.session_id}})
    assert reply['result']['control']['intent']['extraction_status'] == 'extracted'
    from tests.hermes_cli.test_cli_intent_basin import surface, paint
    _, _, panel, app = surface(reply['result']['control']['intent'])
    text = paint(panel, app)
    assert 'Ready for a task' in text
    assert '?' in text and 'cloud' not in text and 'no accepted target' not in text


@pytest.mark.asyncio
async def test_owner_command_and_snapshot_use_the_new_physical_target_after_rotation(owner, monkeypatch):
    from agent.intent_attractor import IntentStore, empty_state
    from tests.gateway.test_local_resume_compression_child import _compress
    monkeypatch.setattr('agent.intent_attractor.settings', lambda: {'enabled': True})
    previous = IntentStore(owner.db, owner.target)
    previous.save(empty_state(), 0)
    _compress(owner.db, owner.target, 'continued-segment', 'cli')
    assert owner.db.bindings.receipt(owner.ref.session_id).current_target == 'continued-segment'
    current = IntentStore(owner.db, 'continued-segment')
    current.save(empty_state(), 0)
    await owner.call('intent', 'command', 'edit outcome Stay local')
    reply = await owner.viewer.dispatch({'id': 21, 'method': 'session.control.read',
                                         'params': {'session_id': owner.ref.session_id}})
    assert reply['result']['control']['intent']['outcome'] == current.read()['fields']['outcome'] == 'Stay local'
    assert previous.read()['revision'] == 0


@pytest.mark.asyncio
async def test_enabled_session_without_record_starts_with_uncertainty_cloud(owner, monkeypatch):
    from tests.hermes_cli.test_cli_intent_basin import surface, paint
    monkeypatch.setattr('agent.intent_attractor.settings', lambda: {'enabled': True})
    reply = await owner.viewer.dispatch({'id': 11, 'method': 'session.control.read',
                                        'params': {'session_id': owner.ref.session_id}})
    intent = reply['result']['control']['intent']
    assert intent['working_status'] == 'unformed'
    _, _, panel, app = surface(intent)
    text = paint(panel, app)
    assert '?' in text and 'Ready for a task' in text
    assert len(text.splitlines()) == 1 and 'awaiting extraction' not in text


def test_confidence_cloud_is_heuristic_and_revision_scoped():
    from agent.intent_reconciliation import confidence_projection
    receipt = {'revision': 3, 'phase': 'reconciliation', 'target': 'proposal',
               'answers': {'coverage': {'noul': .93}, 'consistent': {'noul': .94},
                           'unsupported': {'noul': .47}}}
    projected = confidence_projection({'revision': 3, 'fidelity_annotation': receipt})
    assert projected['visual_confidence'] == pytest.approx(.53)
    assert projected['fidelity_probabilities']['unsupported'] == .47
    assert projected['fidelity_revision'] == 3
    assert confidence_projection({'revision': 4, 'fidelity_annotation': receipt})['visual_confidence'] is None
    assert confidence_projection({'revision': 3, 'fidelity_annotation': {**receipt, 'answers': {}}})['visual_confidence'] is None
    from hermes_cli.cli_intent_toolbar import panel_lines
    state = {'revision': 3, 'outcome': 'Identify prompt files', 'working_status': 'provisional',
             'decision': 'DONE', 'visual_confidence': .53, 'fidelity_revision': 3,
             'fidelity_phase': 'reconciliation'}
    text = '\n'.join(panel_lines(state, 120))
    assert '•' in text and 'Identify prompt files' in text
    assert 'done' in text and '%' not in text
    text = '\n'.join(panel_lines({**state, 'visual_confidence': .95}, 120))
    assert '●' in text
    text = '\n'.join(panel_lines({**state, 'visual_confidence': None}, 120))
    assert '●' not in text and '?' in text
