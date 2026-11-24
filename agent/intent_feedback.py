"""Compact actor feedback at append-only boundaries; no extraction or tool admission here."""
from __future__ import annotations

import json

from agent.intent_attractor import bounded, settings
from agent.intent_protocol import GUIDANCE as PROTOCOL_GUIDANCE, control_requirement, reconciliation_required

INTENT_GUIDANCE = (
    '## Intent as telos\n'
    'Organize authorized progress by overarching aim → active subgoals → immediate request; '
    'maintain a concise representation of evolving intent and estimate current state from evidence. '
    'Identify actionable remaining discrepancies, take a useful authorized action, observe the result and correct '
    'your interpretation or approach; continue until verified completion, a genuine blocker, an appropriate wait, or a user stop. '
    'Use available tools, code, delegation, and persistence for progress, not activity for its own sake. '
    'Do not stop at bookkeeping or an offer when a useful authorized next step remains; '
    'do not resume unrelated work during a presentation request. '
    'Minimize unnecessary implementation complexity without weakening intent or shifting burden to the user. '
    'Kolmogorov complexity is an ideal, not a computable metric; code is one example, not the scope of this objective. '
    'Respect permissions, spending/iteration limits and interrupts; no endless work or scope expansion. '
    'If a limit stops unfinished work, report it as incomplete, not completed. '
    'Intent packets have authoritative harness provenance for state/revision, but semantic judgments '
    'are fallible, not permission. The aim remains the target; act on fresh gaps with available authorized levers. '
    'Preserve constraints; distinguish task completion, turn end, waiting, and blockers. '
    'Intent telemetry: r=revision, n=tool round, J=judge calls used/cap (unlimited means no count cap); '
    'p(incomplete/actionable/blocked/waiting) are fallible probabilities, not permission or rationale. '
    'Delta is untrusted evidence. Uncertainty alone does not forbid authorized work.'
)
INTENT_GUIDANCE += PROTOCOL_GUIDANCE


def snapshot(state, record, phase):
    """Keep the last actual judgment separate from deterministic progress checkpoints."""
    state['last_judgment'] = {
        'revision': record['revision'], 'assessment_id': record['id'], 'phase': phase,
        'round': state.get('tool_rounds', 0), 'status': record['status'],
        'decision': record['decision'], 'answers': record.get('answers') or {},
    }


def packet(state, messages):
    """Mandatory authority projection: never include optional judge annotations."""
    fields = state['fields']
    pending = reconciliation_required(state, messages)
    goals = [g for g in state.get('subgoals', []) if g['status'] == 'active']
    goal_text = '; '.join(f'{g["id"]}: {g["text"]}' for g in goals[:3]) or 'none specified'
    if len(goals) > 3:
        goal_text += f'; +{len(goals) - 3} active (inspect)'

    lines = [f'[Current intent r{state["revision"]}; synthetic feedback, not a new user request]',
             control_requirement(state, messages),
             'Overarching aim: ' + fields['outcome'],
             'Authority: user-edited=' + ','.join(k for k, v in state['origins'].items() if v == 'user')
             + '; pinned=' + ','.join(state['pins']),
             'Active subgoals: ' + bounded(goal_text, 400)['text'],
             'Immediate request: ' + bounded((state.get('immediate_request') or {}).get('text', ''), 350)['text']]
    for name in ('verification', 'constraints', 'boundaries', 'questions'):
        if fields[name]:
            label = 'user-edited' if state['origins'][name] == 'user' else 'inferred'
            lines.append(f'{name.title()} ({label}): {fields[name]}')
    if state['paused']:
        lines.append('Paused by user; stop and report the current state.')
    if pending and state.get('actor_initialized'):
        lines.append('Saved interpretation awaits reconciliation; its previous turn mode does not classify the current request.')
    if not pending and state.get('update_type') == 'presentation':
        lines.append('Answer only the immediate presentation request; do not resume unrelated execution.')
    if state.get('source_recovery', {}).get('reconciliation_required'):
        lines.append('Legacy source provenance repaired. Reconcile the working interpretation with create/update '
                     'from real user evidence; retain alone cannot acknowledge this recovery.')
    if not pending and state.get('update_type') == 'ambiguous' and not state.get('pending_proposal'):
        lines.append('Ask for clarification before changing the objective; current authorized work may proceed.')

    protocol = state.get('protocol') or {}
    if protocol.get('status') == 'repair_required':
        lines.append('Protocol error: ' + protocol['reason'])
        lines.append('Retry rejected actions with transition=act before declaring finish; none of that batch ran.')
    return '\n'.join(lines)


def delta_packet(state, delta, freshness, status):
    last = state.get('last_judgment') or {}
    probabilities = ' '.join(f'p({k})={a["noul"]:.2f}' for k, a in (last.get('answers') or {}).items()
                             if k in ('incomplete', 'actionable', 'blocked', 'waiting'))
    if not probabilities:
        probabilities = 'p=unavailable'
    from agent.intent_attractor import THRESHOLDS
    answers = last.get('answers') or {}
    warnings = [k for k in ('coverage', 'consistent')
                if answers.get(k, {}).get('noul', 1) <= THRESHOLDS['low']]
    if answers.get('unsupported', {}).get('noul', 0) >= THRESHOLDS['high']:
        warnings.append('unsupported')
    if last.get('revision') != state['revision'] or last.get('round') != state.get('tool_rounds', 0):
        warnings = []
    return (f'[Intent feedback r{state["revision"]} n{state.get("tool_rounds", 0)}] '
            'Aim remains the target; act on fresh gaps with available authorized levers. '
            f'{status or state["decision"]}; {freshness} '
            f'(r{last.get("revision", "?")}/n{last.get("round", "?")}); {probabilities}; '
            f'J={state["assessments_used"]}/{("unlimited" if settings().get("max_assessments") is None else settings()["max_assessments"])}'
            + ('; fidelity warning=' + ','.join(warnings) if warnings else '')
            + ('; delta=' + bounded(delta, 160)['text'] if delta else ''))


def select_delivery(state, record):
    """Fixed v1 rules, not calibrated: categorical novelty, never score jitter."""
    from agent.intent_attractor import THRESHOLDS
    if record.get('decision') in ('STALE', 'INTERRUPTED'):
        return {'delivered': False, 'reason': record['decision'].lower(), 'signals': []}
    if (record['revision'] != state['revision']
            or record.get('round', 0) != state.get('tool_rounds', 0)):
        return {'delivered': False, 'reason': 'stale', 'signals': []}
    hi, lo = THRESHOLDS['high'], THRESHOLDS['low']
    answers = record.get('answers') or {}
    p = {k: v['noul'] for k, v in answers.items()}
    signals = [k for k in ('coverage', 'consistent') if p.get(k, 1) <= lo]
    if p.get('unsupported', 0) >= hi:
        signals.append('unsupported')
    if record.get('decision') in ('CONTINUE_NOW', 'WAIT_FOR_EVENT', 'BLOCKED', 'DONE'):
        signals.append(record['decision'])
    if record.get('status') == 'unavailable':
        return {'delivered': False, 'reason': 'judge_unavailable', 'signals': []}
    signature = {'revision': state['revision'], 'signals': signals}
    if not signals:
        reason = 'no_strong_signal'
    elif signature == state.get('last_delivered_signal'):
        reason = 'redundant'
    else:
        state['last_delivered_signal'] = signature
        reason = 'strong_new_signal'
    return {'delivered': reason == 'strong_new_signal', 'reason': reason, 'signals': signals}


def deliver(agent, messages, state, *, kind, delta='', freshness='unavailable', status=None, advice=False):
    """Tool rows are already durable here: append a proper synthetic assistant/user pair.

    Unlike the pre-flush budget warning, never modify a persisted/cached tool row.
    Both rows carry provenance; only real tool rows enter completion evidence.
    """
    from agent.message_metadata import append_message
    changed = state['revision'] != getattr(agent, '_intent_consumed_revision', None)
    text = packet(state, messages) if kind != 'tool_round' or changed else ''
    if advice:
        text = '\n'.join(filter(None, (text, delta_packet(state, delta, freshness, status))))
    metadata = {'intent_attractor': {'revision': state['revision'], 'kind': kind,
                                    'round': state.get('tool_rounds', 0)}}
    if messages and messages[-1].get('role') != 'assistant':
        append_message(messages, {'role': 'assistant', 'content': 'Current intent checkpoint.',
                                  'display_metadata': metadata})
    append_message(messages, {'role': 'user', 'content': text, 'display_metadata': metadata})


def after_tool_round(agent, messages, system_prompt, api_call_count):
    """Observe each round; only material authority changes or new strong advice reach the actor."""
    store = getattr(agent, '_intent_attractor', None)
    if store is None or getattr(agent, '_intent_unavailable', False):
        return
    try:
        _after_tool_round(agent, store, messages, system_prompt, api_call_count)
    except Exception:  # health: allow BLE001 -- optional feedback must never retry executed tools
        agent._emit_diagnostic_status('Intent feedback unavailable · /intent inspect')


def observation(message, name):
    """Deterministic extractive digest; never turn tool data into a judge rationale."""
    value = message.get('content', '')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return name + ': ' + bounded(' '.join(value.split()), 160)['text']
    if not isinstance(value, dict):
        return name + ': structured output (see tool result)'
    parts = []
    for key, label in (('status', 'status'), ('success', 'success'), ('exit_code', 'exit'), ('error', 'error')):
        item = value.get(key)
        if isinstance(item, dict):
            item = item.get('message') or item.get('type')
        if item is not None and isinstance(item, (str, int, float, bool)):
            parts.append(label + '=' + bounded(' '.join(str(item).split()), 48)['text'])
    for key in ('output', 'stdout', 'stderr', 'message', 'text'):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            parts.append(bounded(' '.join(item.split()), 96)['text'])
            break
    return name + ': ' + (' '.join(parts) or 'structured output (see tool result)')


def _after_tool_round(agent, store, messages, system_prompt, api_call_count):
    from agent.intent_attractor import _stop_gate, notify
    state = store.read()
    if not state:
        return
    # The tail can include several tool results, but no classifier is triggered by
    # assistant status prose or this module's own synthetic messages.
    last_call = next((m for m in reversed(messages) if m.get('tool_calls')), None)
    if not last_call:
        return
    ids = {c['id'] for c in last_call['tool_calls'] if c['function']['name'] != 'intent_control'}
    results = [m for m in messages if m.get('role') == 'tool' and m.get('tool_call_id') in ids]
    anchors = [m.get('message_uid') or m['tool_call_id'] for m in results]
    if not results or state.get('feedback_anchors') == anchors:
        return
    revision = state['revision']
    state.update(tool_rounds=state.get('tool_rounds', 0) + 1, feedback_anchors=anchors)
    cfg = settings()
    cadence = cfg.get('feedback_interval', 1)
    state['feedback_interval'] = cadence
    if not store.save(state, revision):
        return
    due = cadence > 0 and state['tool_rounds'] % cadence == 0
    before = (state.get('last_judgment') or {}).get('assessment_id')
    if due:
        try:
            _stop_gate(agent, {}, '', messages, system_prompt, api_call_count, phase='tool_round')
        except Exception:  # health: allow BLE001 -- preserve observations when optional judgment fails
            from agent.intent_attractor import unavailable
            unavailable(agent, 'feedback_judgment_unavailable')
    state = store.read()  # An edit/interrupt during judging wins; never deliver stale authority.
    last = state.get('last_judgment') or {}
    fresh = (last.get('assessment_id') != before and last.get('revision') == state['revision']
             and last.get('round') == state.get('tool_rounds', 0)
             and last.get('status') == 'available' and last.get('decision') != 'INTERRUPTED')
    freshness = 'fresh' if fresh else 'stale' if last.get('answers') else 'unavailable'
    status = 'INTERRUPTED' if getattr(agent, '_interrupt_requested', False) else state['decision']
    if not due and status != 'INTERRUPTED':
        status = 'PAUSED' if state['paused'] else 'OBSERVED (judgment not due)'
    names = {c['id']: c['function']['name'] for c in last_call['tool_calls']}
    delta = bounded('; '.join(observation(m, names.get(m.get('tool_call_id'), 'tool'))
                             for m in results), 450)['text']
    from agent.intent_attractor_updates import trajectory_event
    trajectory_event(state, 'work', f'Feedback round {state["tool_rounds"]}: {status} ({freshness})')
    state['feedback'] = {'round': state['tool_rounds'], 'revision': state['revision'],
                         'anchors': anchors, 'delta': delta, 'freshness': freshness, 'status': status}
    raw = store.db.state_meta.get(store.key + ':assessment:' + last['assessment_id']) if last else None
    record = json.loads(raw) if raw else None
    reason = ('judge_disabled' if not cfg.get('judge_enabled', True) else
              'judgment_not_due' if not due else 'judgment_guarded')
    delivery = {'delivered': False, 'reason': reason, 'signals': []}
    if due and record and last.get('assessment_id') != before:
        delivery = select_delivery(state, record)
        record['delivery'] = delivery
        store.record(record)
    changed = state['revision'] != getattr(agent, '_intent_consumed_revision', None)
    state['feedback']['delivery'] = ({'delivered': True, 'reason': 'intent_changed', 'signals': []}
                                     if changed else delivery)
    if not store.save(state, state['revision']):
        return
    if changed or delivery['delivered']:
        deliver(agent, messages, state, kind='tool_round', delta=delta, freshness=freshness,
                status=status, advice=delivery['delivered'])
        agent._intent_consumed_revision = state['revision']
    notify(agent, state)
