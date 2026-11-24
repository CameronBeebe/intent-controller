"""Same adopted revision drives owner events, the CLI paint, and actor receipts."""
import asyncio
import copy
from typing import Any

import pytest

from tests.agent.test_intent_attractor import harness as _harness
from tests.gateway.test_session_viewer_automation import owner as _owner

harness = _harness
owner = _owner


@pytest.mark.asyncio
async def test_actor_refinement_publishes_owner_snapshot_and_actual_paint(owner, harness, request):
    from agent.intent_attractor import IntentStore, begin_turn
    from agent.intent_protocol import commit
    from gateway.run_turn_progress import GatewayTurnProgressMixin
    from tests.hermes_cli.test_cli_intent_basin import surface, paint
    from tests.agent.test_intent_protocol import PROPOSAL
    h = harness
    from persistence_assembly import open_persistence
    actor_store = open_persistence(owner.authority.persistence.scope)
    request.addfinalizer(actor_store.close)
    from tests.agent.test_intent_attractor import make_actor
    actor = make_actor(actor_store, owner.target)
    scheduled, events = [], []
    turn: Any = GatewayTurnProgressMixin()
    turn._approval_owner = (owner.authority, owner.ref.session_id, 1)
    turn._schedule = lambda coro, message: scheduled.append(asyncio.ensure_future(coro))
    actor.status_callback = turn._status_callback_sync
    owner.authority.sessions[owner.ref.session_id].event_stream.observers.add(
        lambda frame: frame['params']['type'] == 'session.control.update'
        and events.append(frame['params']['payload']))
    messages = [{'role': 'user', 'content': 'Implement and test locally.', 'message_uid': 'u1'}]
    proposal = copy.deepcopy(PROPOSAL)
    proposal['operations'] = [{'op': 'add_subgoal', 'text': 'Inspect local scenes'}]
    begin_turn(actor, messages)
    commit(actor, {'revision': 0, 'mode': 'create', 'transition': 'act', 'proposal': proposal}, messages)
    await asyncio.gather(*scheduled)
    h.fidelity_probabilities.append({'coverage': .13, 'consistent': .14, 'unsupported': .81})
    aim = 'Look at the Fibonacci repo and summarize its recent work'
    messages.append({'role': 'user', 'content': aim, 'message_uid': 'u2'})
    receipt = commit(actor, {'revision': 1, 'mode': 'update', 'transition': 'act',
        'proposal': {'update_type': 'refinement', 'fields': {
            'outcome': aim, 'verification': 'Require JSONL frame protocol'}}}, messages)
    await asyncio.gather(*scheduled)
    snapshot = events[-1]['control']['intent']
    reply = await owner.viewer.dispatch({'id': 9, 'method': 'session.control.read',
                                        'params': {'session_id': owner.ref.session_id}})
    assert reply['result']['control']['intent'] == snapshot
    state = IntentStore(owner.db, owner.target).read()
    assert state is not None
    assert snapshot['revision'] == receipt['revision'] == state['revision'] == 2
    assert snapshot['outcome'] == receipt['fields']['outcome'] == aim
    cli, connection, panel, app = surface(snapshot, width=180)
    rendered = paint(panel, app, width=180)
    assert aim in rendered and 'r2' in rendered
    assert not any(word in rendered.lower() for word in ('accepted', 'provisional', 'jsonl'))
    # Manual edits use the same canonical owner and event projection, not a UI-only cache.
    await owner.call('intent', 'command', 'edit outcome Local graphics only')
    snapshot = events[-1]['control']['intent']
    connection.state(cli.session_id).adopt_snapshot({'control': {'intent': snapshot}})
    assert snapshot['revision'] == 3
    assert 'Local graphics only' in paint(panel, app, width=180)
    assert actor._intent_attractor.read()['fields']['outcome'] == 'Local graphics only'
