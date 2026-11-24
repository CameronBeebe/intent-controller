"""Repair legacy intent evidence by stable source identity, never matching text."""
import copy

from agent.intent_attractor import real_sources


def recover_sources(store, state, messages=()):
    """Versioned normal-turn repair. Historical receipts/transcripts stay immutable.

    Only producer metadata/flags on a row with the exact saved UID proves contamination.
    Missing rows/metadata cannot be repaired heuristically. Preserve manual fields
    and the current interpretation; require the actor to reconcile it from evidence.
    """
    live_synthetic = {m.get('message_uid') for m in messages
                      if m.get('_empty_recovery_synthetic')
                      or (m.get('display_metadata') or {}).get('internal_nudge')}
    if (state.get('source_provenance_version') == 2
            and not any(s['anchor'] in live_synthetic for s in state['sources'])):
        return state
    from persistence.conversation.messages import TranscriptRead, TranscriptView
    rows = [row.as_wire_row() for row in store.db.messages.read_transcript(
        store.session_id, TranscriptRead(view=TranscriptView.DISPLAY))]
    synthetic = live_synthetic | {m.get('message_uid') for m in rows
                 if (m.get('display_metadata') or {}).get('intent_attractor')
                 or (m.get('display_metadata') or {}).get('internal_nudge')}
    removed = [s['anchor'] for s in state['sources'] if s['anchor'] in synthetic]
    state['source_provenance_version'] = 2
    if not removed:
        return state
    consumed = {s['anchor'] for s in state['sources']}
    last = max((i for i, m in enumerate(rows) if m.get('message_uid') in consumed), default=-1)
    known = {m.get('message_uid') for m in rows}
    # Missing provenance is not proof of synthetic authorship: retain unresolved
    # historical anchors instead of silently dropping them during reconstruction.
    unresolved = [s for s in state['sources'] if s['anchor'] not in known | synthetic]
    sources = unresolved + real_sources([m for m in rows[:last + 1] if m.get('message_uid') not in synthetic])
    state['sources'] = sources if len(sources) <= 8 else sources[:2] + sources[-6:]
    state['immediate_request'] = state['sources'][-1] if state['sources'] else None
    state['revision'] += 1
    state['source_recovery'] = {'removed_anchors': removed, 'revision': state['revision'],
        'basis': 'producer provenance and exact message_uid', 'reconciliation_required': True}
    for key in ('pending_proposal', 'fidelity_annotation', 'last_judgment', 'validated_specification'):
        state.pop(key, None)
    state['interpretation'] = {'status': 'unassessed', 'decision': 'UNASSESSED', 'owner': 'actor'}
    for goal in state.get('subgoals', []):
        goal['sources'] = [s for s in goal.get('sources', []) if s['anchor'] not in synthetic]
    state['history'].append({'revision': state['revision'], 'source': 'provenance_recovery',
                             'fields': copy.deepcopy(state['fields']),
                             'subgoals': copy.deepcopy(state.get('subgoals', [])),
                             'removed_anchors': removed})
    return state
