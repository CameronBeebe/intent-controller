"""One cached intent row: fidelity particles, working aim, separate decision."""
import math
import re
import time
import unicodedata

from prompt_toolkit.utils import get_cwidth


def clean(text):
    """One inert line; never interpret ANSI, bidi controls or markup as formatting."""
    text = re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]', '', str(text))
    return ' '.join(''.join(' ' if unicodedata.category(c).startswith('C') else c for c in text).split())


def clipped(text, width):
    """Clip by the same cell widths prompt_toolkit uses, retaining a visible omission."""
    if get_cwidth(text) <= width:
        return text
    result = ''
    for char in text:
        if get_cwidth(result + char) > max(0, width - 1):
            break
        result += char
    return result + ('~' if width > 0 else '')


def particles(confidence, width=21, phase=0):
    """Deterministic spread/density, not execution progress; None has no target."""
    center = width // 2
    cells = [' '] * width
    known = isinstance(confidence, (int, float)) and math.isfinite(confidence)
    score = max(0., min(1., confidence)) if known else None
    radius = center if score is None else max(1, round(center * (1 - score)))
    # Fixed seeds collapse onto shared cells, increasing density as spread narrows.
    for seed in (-1., -.7, -.4, -.15, .2, .5, .75, 1.):
        position = center + round(seed * radius)
        cells[position] = ':' if cells[position] != ' ' else '·'
    if score is not None:
        cells[center] = '●' if score >= .8 else ('•' if score >= .5 else '·')
    # Shimmer changes brightness, never the spread or confidence. The target stays put.
    for position in range(width):
        if (position + phase) % 4 == 0:
            cells[position] = {'·': '.', ':': '•'}.get(cells[position], cells[position])
    return ''.join(cells)


def row_fragments(state, width, phase=0):
    """Fit the allocated cells; drop the graphic before sacrificing a narrow aim."""
    width = max(0, width)
    revision = clean(state.get('revision', 0))
    label = f'Intent r{revision}'
    if state.get('user_edited'):
        label += ' user-edited'
    aim = clean(state.get('outcome') or 'Ready for a task')
    # Only execution states belong in the row; fidelity receipts stay in /intent.
    execution = {'WAIT': 'waiting', 'WAIT_FOR_EVENT': 'waiting', 'BLOCKED': 'blocked',
                 'DONE': 'done', 'COMPLETE': 'done', 'PAUSED': 'paused'}.get(state.get('decision'), '')
    if state.get('paused'):
        execution = 'paused'
    score = state.get('visual_confidence')
    unknown = not isinstance(score, (int, float)) or not math.isfinite(score)
    graphic_width = 21 if width >= 90 else (15 if width >= 65 else 0)
    prefix = label + ('?' if unknown else '') + ' '
    suffix = ' | ' + execution if execution else ''
    if width < 65:
        prefix = ('user-edited ' if state.get('user_edited') else 'Intent ') + f'r{revision} '
    # Reserve readable aim space, shortening decision labels first on small screens.
    suffix = clipped(suffix, max(0, min(get_cwidth(suffix), width // 3)))
    room = width - get_cwidth(prefix) - graphic_width - (1 if graphic_width else 0) - get_cwidth(suffix)
    if room < 8:
        graphic_width = 0
        room = width - get_cwidth(prefix) - get_cwidth(suffix)
    if room < 1:
        return [('class:status-bar', clipped(aim, width))]
    result = [('class:status-bar', prefix)]
    if graphic_width:
        signal_style = ('status-bar' if unknown else 'status-bar-good' if score >= .8 else
                        'status-bar-warn' if score >= .5 else 'status-bar-critical')
        result.extend([(f'class:status-bar.accent class:{signal_style}', particles(score, graphic_width, phase)),
                       ('class:status-bar', ' ')])
    result.append(('class:status-bar', clipped(aim, room) + suffix))
    return result


def panel_lines(state, width):
    """Plain-text counterpart for previews and callers inspecting the cached row."""
    return [''.join(text for _, text in row_fragments(state, width))]


def animating(cli):
    """Existing spinner cadence may repaint only a working, visible intent row."""
    if not getattr(cli, '_agent_running', False):
        return False
    connection = getattr(getattr(cli, '_session_db', None), 'connection', None)
    sid = getattr(cli, 'session_id', None)
    state = connection.state(sid) if connection and sid else None
    intent = (state.control or {}).get('intent') if state else None
    return bool(intent and not intent.get('paused'))


def widget(cli):
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.layout import ConditionalContainer, Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    subscribed = None

    def intent():
        nonlocal subscribed
        connection = getattr(getattr(cli, '_session_db', None), 'connection', None)
        sid = getattr(cli, 'session_id', None)
        state = connection.state(sid) if connection and sid else None
        if state is not subscribed:
            if subscribed is not None:
                subscribed.control_listeners.remove(changed)
            if state is not None:
                state.control_listeners.append(changed)
            subscribed = state
        if state is None:
            return None
        return (state.control or {}).get('intent')

    def changed():
        cli._invalidate()

    def fragments(width):
        state = intent()
        phase = int(time.monotonic() * 2) % 4 if animating(cli) else 0
        return row_fragments(state, width, phase) if state else []

    class IntentControl(FormattedTextControl):
        def create_content(self, width, height):
            # Only cached state, clipped to the layout's real allocation; no RPC.
            return FormattedTextControl(lambda: fragments(width)).create_content(width, height)

    return ConditionalContainer(Window(IntentControl(), height=1, wrap_lines=False),
                                filter=Condition(lambda: bool(intent())))
