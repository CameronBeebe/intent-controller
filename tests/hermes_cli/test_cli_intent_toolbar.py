"""Owner controls -> connection cache -> actual persistent footer widget, without render RPCs."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tests.gateway.test_session_viewer_automation import owner


@pytest.fixture
def render_app():
    from prompt_toolkit.application import Application
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput
    app = Application(input=DummyInput(), output=DummyOutput())
    with set_app(app):
        yield app


@pytest.mark.asyncio
@pytest.mark.parametrize('modern', [True, False])
async def test_intent_owner_events_refresh_persistent_footer(owner, monkeypatch, render_app, modern):
    from agent.intent_attractor import FIELDS, IntentStore, notify
    from cli import HermesCLI
    from hermes_cli.gateway_connection import GatewayConnection
    from gateway.run_turn_progress import GatewayTurnProgressMixin

    monkeypatch.setattr('agent.intent_attractor.settings', lambda: {'enabled': True})
    store = IntentStore(owner.db, owner.target)
    store.save({'revision': 1, 'fields': {**dict.fromkeys(FIELDS, ''), 'outcome': 'Follow Brood War'},
                'sources': [], 'origins': dict.fromkeys(FIELDS, 'inferred'), 'pins': [], 'history': [],
                'decision': 'FIDELITY_UNCERTAIN', 'paused': False}, 0)
    connection = GatewayConnection()
    connection.call = MagicMock(side_effect=AssertionError("render must not call RPC"))
    cli = HermesCLI.__new__(HermesCLI)
    cli._modern_layout = modern
    cli.session_id = owner.ref.session_id
    cli._session_db = SimpleNamespace(connection=connection)
    cli._invalidate = MagicMock()
    cli._get_tui_terminal_width = lambda: 110
    widgets = dict.fromkeys(('sudo_widget', 'secret_widget', 'approval_widget', 'clarify_widget',
                            'spacer', 'status_bar', 'input_rule_top', 'image_bar', 'input_area',
                            'input_rule_bot', 'voice_status_bar', 'completions_menu'))
    widgets['status_bar'] = object()
    widgets['input_area'] = object()
    children = cli._build_tui_layout_children(**widgets)
    intent_row = children[children.index(widgets['status_bar']) + 1]
    assert intent_row.content.preferred_height(110, 30).preferred == 1
    assert children.index(widgets['input_area']) < children.index(widgets['status_bar'])
    assert children.count(intent_row) == 1

    # Resume hydration uses the owner's exact physical target, not a local metadata lookup.
    from gateway.session_ancillary import control_snapshot
    reply = await owner.viewer.dispatch({'id': 3, 'method': 'session.resume',
                                         'params': {'session_id': cli.session_id}})
    assert 'result' in reply, reply
    connection.state(cli.session_id).adopt_snapshot(reply['result'])

    def rendered():
        from prompt_toolkit.application.current import get_app
        get_app().render_counter += 1
        lines = []
        for child in children:
            if hasattr(child, 'filter') and child.filter():
                content = child.content.content.create_content(cli._get_tui_terminal_width(), 8)
                lines.extend(''.join(text for _, text in content.get_line(i))
                             for i in range(content.line_count))
        return '\n'.join(lines)

    for working in (False, True):
        cli._agent_running = working
        line = rendered()
        assert 'Intent r1' in line and 'Follow Brood War' in line
        assert 'FIDELITY_UNCERTAIN' not in line

    # Feed real published frames through the connection pump, including an idle edit.
    frames = asyncio.Queue()
    live = owner.authority.sessions[owner.ref.session_id]
    publish = live.event_stream.publish

    def capture(session_id, payload, *, event_type, **kwargs):
        result = publish(session_id, payload, event_type=event_type, **kwargs)
        frames.put_nowait({'params': {'session_id': session_id, 'type': event_type, 'payload': payload}})
        return result

    monkeypatch.setattr(live.event_stream, 'publish', capture)
    pump = asyncio.create_task(connection._pump(SimpleNamespace(events=frames)))
    try:
        response = await owner.call('intent', 'command', 'edit outcome User-edited target')
        assert 'User-edited target' in response.get('value', ''), response
        from tests.waiting import async_wait_until
        await async_wait_until(lambda: connection.state(cli.session_id).control.get('intent', {}).get('revision') == 2)
        assert 'User-edited target' in rendered(), (rendered(), connection.state(cli.session_id).control)
        assert 'r2' in rendered() and 'user-edited' in rendered() and 'PENDING' not in rendered()
        assert cli._invalidate.called

        # The agent's notify travels through the real gateway status callback, before tools/turn end.
        pending = []
        turn = SimpleNamespace(_approval_owner=(owner.authority, cli.session_id, 1),
                               _ctx=SimpleNamespace(mute_notification_reply=True),
                               _schedule=lambda coro, label: pending.append(asyncio.create_task(coro)))
        state = store.read()
        state['decision'] = 'COMPLETE'
        state['fidelity_annotation'] = {
            'revision': 2, 'phase': 'completion', 'target': 'working',
            'answers': {key: {'noul': value} for key, value in
                        [('coverage', .95), ('consistent', .94), ('unsupported', .1)]}}
        store.save(state, 2)
        agent = SimpleNamespace(_emit_diagnostic_status=lambda text: None,
                                _call_callback=lambda name, *args, **kw:
                                GatewayTurnProgressMixin._status_callback_sync(turn, *args))
        notify(agent, state)
        await asyncio.gather(*pending)
        await async_wait_until(lambda: 'done' in rendered())
        assert '●' in rendered()  # Owner-produced confidence reaches the real row.

        # Display-only sanitation and cell-aware clipping; durable source is untouched.
        hostile = 'Brood\nWar\x1b[31m\u202e ' + '界' * 100
        await owner.call('intent', 'command', 'edit outcome ' + hostile)
        await async_wait_until(lambda: 'r3' in rendered())
        cli._get_tui_terminal_width = lambda: 65
        line = rendered()
        assert '\x1b' not in line and '\u202e' not in line
        assert '?' in line and '●' not in line  # Edit invalidates the old score.
        assert len(line.splitlines()) == 1
        assert all(cli._status_bar_display_width(row) <= 65 for row in line.splitlines())
        assert store.read()['fields']['outcome'] == hostile
        cli.session_id = 'other-owner'
        assert rendered() == ''
        cli.session_id = owner.ref.session_id
        monkeypatch.setattr('agent.intent_attractor.settings', lambda: {'enabled': False})
        connection.state(cli.session_id).adopt_snapshot({'control': control_snapshot(owner.authority, owner.ref)})
        assert rendered() == ''
        connection.call.assert_not_called()
    finally:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
