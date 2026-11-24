"""Cached fidelity particles in one row; animation is display-only."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from hermes_cli.cli_intent_toolbar import widget
from hermes_cli.gateway_connection import GatewayConnection


def surface(snapshot, width=100):
    connection = GatewayConnection()
    connection.call = MagicMock(side_effect=AssertionError('No RPC in paint'))
    cli = SimpleNamespace(session_id='basin', _session_db=SimpleNamespace(connection=connection),
                          _invalidate=MagicMock(), _get_tui_terminal_width=lambda: width,
                          _trim_status_bar_text=lambda text, width: text[:width])
    connection.state(cli.session_id).adopt_snapshot({'control': {'intent': snapshot}})
    panel = widget(cli)
    app = Application(input=DummyInput(), output=DummyOutput())
    return cli, connection, panel, app


def paint(panel, app, width=100):
    with set_app(app):
        app.render_counter += 1
        if not panel.filter():
            return ''
        content = panel.content.content.create_content(width, 8)
        return '\n'.join(''.join(part[1] for part in content.get_line(i))
                         for i in range(content.line_count))


def test_compact_particles_converge_in_actual_render():
    from prompt_toolkit.utils import get_cwidth

    snapshots = [None, .25, .6, .9]
    graphics = []
    for confidence in snapshots:
        state = {'revision': 2, 'outcome': 'Track Brood War', 'decision': 'PENDING',
                 'visual_confidence': confidence}
        _, connection, panel, app = surface(state)
        text = paint(panel, app)
        assert len(text.splitlines()) == 1
        assert 'Track Brood War' in text and 'r2' in text
        assert '%' not in text and 'cloud' not in text and 'schematic' not in text
        fragments = panel.content.content.create_content(100, 1).get_line(0)
        graphic = next(part[1] for part in fragments if 'class:status-bar.accent' in part[0])
        assert 15 <= get_cwidth(graphic) <= 25
        graphics.append(graphic)
        assert panel.content.preferred_height(100, 30).preferred == 1
        connection.call.assert_not_called()
    spans = [len(g.strip()) for g in graphics]
    assert spans[0] > spans[1] > spans[2] > spans[3]
    assert '●' not in graphics[0] and '●' in graphics[-1]
    assert len(set(graphics)) == 4


@pytest.mark.parametrize('confidence', [None, .25, .6, .9])
def test_working_shimmer_is_phase_only_and_idle_is_stable(monkeypatch, confidence):
    state = {'revision': 2, 'outcome': 'Track Brood War', 'decision': 'PENDING',
             'visual_confidence': confidence}
    cli, connection, panel, app = surface(state)
    cli._agent_running = True
    monkeypatch.setattr('time.monotonic', lambda: 0)
    first = paint(panel, app)
    monkeypatch.setattr('time.monotonic', lambda: .5)
    second = paint(panel, app)
    assert first != second
    assert first.replace('·', '.').replace('•', ':') == second.replace('·', '.').replace('•', ':')
    cli._agent_running = False
    idle = paint(panel, app)
    monkeypatch.setattr('time.monotonic', lambda: 12)
    assert paint(panel, app) == idle
    assert connection.state('basin').control['intent'] == state
    connection.call.assert_not_called()

    cli._agent_running = True
    state['paused'] = True
    paused = paint(panel, app)
    monkeypatch.setattr('time.monotonic', lambda: 12.5)
    assert paint(panel, app) == paused


def test_unknown_is_not_zero_and_completion_does_not_change_geometry():
    from hermes_cli.cli_intent_toolbar import particles, row_fragments

    assert particles(None) != particles(0)
    for confidence in (None, 0, .25, .6, .9, 1):
        graphics = []
        for decision in ('PENDING', 'COMPLETE'):
            fragments = row_fragments({'outcome': 'Aim', 'visual_confidence': confidence,
                                       'decision': decision}, 100)
            graphics.append(next(text for style, text in fragments if 'class:status-bar.accent' in style))
        assert graphics[0] == graphics[1]


def test_existing_spinner_cadence_refreshes_only_working_visible_intent(monkeypatch):
    from cli import HermesCLI

    cli, connection, panel, app = surface({'revision': 1, 'outcome': 'Aim'})
    paint(panel, app)
    cli._app = app
    cli._command_running = False
    for running, enabled in ((True, True), (False, True), (True, False)):
        cli._agent_running = running
        connection.state('basin').control['intent'] = {'outcome': 'Aim'} if enabled else None
        cli._should_exit = False
        cli._invalidate.reset_mock()
        monkeypatch.setattr('hermes_cli.cli_tui_mixin.time.sleep', lambda _: setattr(cli, '_should_exit', True))
        HermesCLI._tui_spinner_loop(cli)
        assert cli._invalidate.called == (running and enabled)
    connection.call.assert_not_called()


@pytest.mark.parametrize('light', ['0', '1'])
def test_intent_signal_color_uses_theme_palette_in_actual_render(monkeypatch, light):
    from prompt_toolkit.styles import Style
    from hermes_cli.skin_engine import get_active_skin, set_active_skin, get_prompt_toolkit_style_overrides

    import cli
    monkeypatch.setenv('HERMES_LIGHT', light)
    monkeypatch.setattr(cli, '_LIGHT_MODE_CACHE', None)
    previous = get_active_skin().name
    try:
        set_active_skin('default')
        skin = get_active_skin()
        style = Style.from_dict(get_prompt_toolkit_style_overrides())
        for score, css, color in ((None, 'status-bar', 'status_bar_text'),
                                  (.1, 'status-bar-critical', 'status_bar_critical'),
                                  (.6, 'status-bar-warn', 'status_bar_warn'),
                                  (.95, 'status-bar-good', 'status_bar_good')):
            _, _, panel, app = surface({'outcome': 'Create Fibonacci ASCII art in Julia',
                'visual_confidence': score, 'working_status': 'provisional',
                'pending_decision': 'UNCERTAIN', 'fidelity_target': 'proposal'})
            text = paint(panel, app)
            assert not any(word in text for word in ('PROVISIONAL', 'proposal', 'UNCERTAIN'))
            fragments = panel.content.content.create_content(100, 1).get_line(0)
            particle_style = next(part[0] for part in fragments if 'status-bar.accent' in part[0])
            assert f'class:{css}' in particle_style.split()
            attrs = style.get_attrs_for_style_str(particle_style)
            assert attrs.color is not None and attrs.bgcolor is not None
            assert attrs.color.lower() == skin.get_color(color).lstrip('#').lower()
            assert attrs.bgcolor.lower() == skin.get_color('status_bar_bg').lstrip('#').lower()
    finally:
        set_active_skin(previous)


def test_compact_aim_idle_and_working_leaves_details_to_inspection():
    snapshot = {'revision': 2, 'outcome': 'Track Brood War', 'decision': 'FIDELITY_UNCERTAIN',
                'current_request': 'Give a TLDR', 'subgoals': [
                    {'id': 'sg1', 'text': 'Find tournament schedule', 'status': 'active', 'sources': [], 'revision': 2}],
                'trajectory': [{'revision': 2, 'kind': 'work', 'label': 'Read schedule'},
                               {'revision': 2, 'kind': 'uncertain', 'label': 'Coverage unclear'}]}
    cli, connection, panel, app = surface(snapshot)
    for running in (False, True):
        cli._agent_running = running
        text = paint(panel, app)
        assert len(text.splitlines()) == 1
        assert 'Intent r2' in text and 'Track Brood War' in text
        assert not any(word in text for word in ('PROVISIONAL', 'proposal', 'ACCEPTED', 'UNCERTAIN', 'PENDING'))
        assert "'id':" not in text and "'text':" not in text
        assert 'judged-done' not in text
    assert panel.content.preferred_height(100, 30).preferred == 1
    connection.call.assert_not_called()


def test_narrow_cell_clipping_sanitizes_every_field_without_mutating_cache():
    import copy
    import unicodedata
    from prompt_toolkit.utils import get_cwidth

    hostile = '界界\x1b[31m\u202e\x00\n[bold]e\u0301' * 30
    snapshot = {'revision': 3, 'outcome': hostile, 'decision': 'BLOCKED',
                'current_request': hostile, 'subgoals': [hostile],
                'trajectory': [{'revision': 3, 'kind': 'blocked', 'label': hostile}]}
    saved = copy.deepcopy(snapshot)
    cli, connection, panel, app = surface(snapshot)
    for width in (100, 65, 38, 20, 3, 1, 0):
        cli._get_tui_terminal_width = lambda: width
        text = paint(panel, app, width)
        assert all(get_cwidth(line) <= width for line in text.splitlines())
        assert all(not unicodedata.category(c).startswith('C') for c in text.replace('\n', ''))
        assert len(text.splitlines()) <= 1
        if width == 38:
            assert 'Intent r3' in text and 'blocked' in text
    assert connection.state('basin').control['intent'] == saved
    cli._get_tui_terminal_width = lambda: 100
    actual = paint(panel, app, 32)
    assert all(get_cwidth(row) <= 32 for row in actual.splitlines())


def test_narrow_keeps_aim_and_manual_authority_visible():
    state = {'revision': 4, 'outcome': 'A manually corrected target', 'user_edited': True,
             'paused': True, 'decision': 'COMPLETE',
             'trajectory': [{'revision': 3, 'kind': 'work', 'label': 'Earlier work'},
                            {'revision': 4, 'kind': 'revise', 'label': 'New target'},
                            {'revision': 4, 'kind': 'work', 'label': 'Verified artifact'},
                            {'revision': 4, 'kind': 'done', 'label': 'Complete'}]}
    _, _, panel, app = surface(state, 38)
    text = paint(panel, app, 38)
    assert 'A manu' in text
    assert 'user-edited' in text and 'paused' in text


def test_event_refresh_fences_old_judgment_from_changed_target():
    initial = {'revision': 1, 'outcome': 'Original target', 'decision': 'COMPLETE',
               'trajectory': [{'revision': 1, 'kind': 'done', 'label': 'Verified original'}]}
    cli, connection, panel, app = surface(initial)
    assert 'done' in paint(panel, app)
    edited = {**initial, 'revision': 2, 'outcome': 'Revised target', 'decision': 'PENDING',
              'user_edited': True, 'trajectory': initial['trajectory'] + [
                  {'revision': 2, 'kind': 'revise', 'label': 'User changed outcome'}]}
    connection.state(cli.session_id).apply_event('session.control.update', {'control': {'intent': edited}})
    assert cli._invalidate.called
    text = paint(panel, app)
    assert 'user-edited' in text and 'Revised target' in text
    assert 'done' not in text and 'PENDING' not in text
    for decision, label in (('WAIT', 'waiting'), ('BLOCKED', 'blocked'), ('FIDELITY_UNCERTAIN', '')):
        edited = {**edited, 'decision': decision}
        connection.state(cli.session_id).apply_event('session.control.update', {'control': {'intent': edited}})
        text = paint(panel, app)
        assert label in text and decision not in text
        if not label:
            assert 'blocked' not in text and 'waiting' not in text
    cli.session_id = 'new-session'
    assert paint(panel, app) == ''
    assert not connection.state('basin').control_listeners
    cli.session_id = 'basin'
    assert paint(panel, app)
    connection.state('basin').apply_event('session.control.update', {'control': {'intent': None}})
    assert paint(panel, app) == ''
    connection.call.assert_not_called()


def test_presentation_revision_is_not_a_target_change_or_measured_progress():
    state = {'revision': 2, 'outcome': 'Same target', 'decision': 'TURN_ONLY',
             'trajectory': [{'revision': 1, 'kind': 'done', 'label': 'Earlier assessment'},
                            {'revision': 2, 'kind': 'work', 'label': 'Current request'}]}
    _, _, panel, app = surface(state)
    text = paint(panel, app)
    assert 'target-changed' not in text
    assert 'COMPLETE' not in text
    assert 'r2' in text and 'TURN_ONLY' not in text and 'Same target' in text
    assert '%' not in text
