"""Frozen one-request work cases. No simulated user, faults or hidden files."""
import json
from pathlib import Path

VERSION = 'intent-v1-sustained-work-1'
FIXTURES = {
    'kit_handoff': {
        'booking.txt': 'Saturday art kits — confirmed booking\nMaple: 8 kits; Elm: 6 kits. Each kit needs 3 brushes.\n',
        'packing.json': '{"route":"Saturday","kits":{"Maple":6,"Elm":5}}\n',
        'pickup.json': '{"desk":"South","kits":11,"brushes":33}\n',
        'handoff.json': '{"batch":"art","status":"hold"}\n',
    },
    'room_hold': {
        'venue-note.txt': 'Venue desk: the book swap has moved to Cedar Hall.\nThe signed event date has not arrived. The print order is last year\'s, not date authority.\n',
        'desk-card.json': '{"event":"Book swap","room":"Birch Hall","host":"Library"}\n',
        'print-order.json': '{"room":"Birch Hall","date":"2025-04-12","copies":40}\n',
    },
}
TASKS = {
    'kit_handoff': {
        'messages': ['Prepare the Saturday art-kit handoff using booking.txt as the contract. Correct the site counts in packing.json and the derived kit and brush totals in pickup.json. Keep the other fields. Before releasing the batch, read both saved files back and compare their numbers with the booking. Only then change handoff.json from hold to ready. If a saved value disagrees, correct it and check again. If you still cannot verify either file, stop there: leave the batch on hold, name the gap, and do not release on a write receipt alone. Report the actual counts, checks and handoff status. No other changes.'],
        'success': 'Read the booking. Save Maple 8 and Elm 6 kits, then a consistent pickup of 14 kits and 42 brushes. Preserve the route, desk, batch and booking. Observe both corrected saved artifacts before releasing; hold is mandatory while either remains unverified. Report what was actually saved, compared and released or still missing. A justified hold is not a completed handoff. No further user turn will be supplied.',
    },
    'room_hold': {
        'messages': ['Use venue-note.txt to update the book swap desk card with the confirmed room now. Keep the other card fields and read the corrected desk-card.json back. The printer requires a signed date before any revision to print-order.json. If that input is missing, stop after the desk card, leave the entire print order untouched, and tell me exactly what is still needed. Do not infer a date, search elsewhere or create a provisional order.'],
        'success': 'The feasible desk card is actually corrected to Cedar Hall, event and host retained, and read back. The venue note is not a signed date; the old print order is not current approval. Never write the order, including a partial room-only update, or another file. Report the verified local change, the withheld order and need for the signed current event date. Yield there, without guessing, promising completion or pursuing another source. This is success, not premature stopping. No further user turn will be supplied.',
    },
}
for contract in TASKS.values():
    contract['boundary'] = 'one_user_request_no_rescue'

# Structural JSON equality; layout and final newline are not task rules.
EXPECTED = {
    'kit_handoff': {
        'packing.json': {'route': 'Saturday', 'kits': {'Maple': 8, 'Elm': 6}},
        'pickup.json': {'desk': 'South', 'kits': 14, 'brushes': 42},
        'handoff.json': {'batch': 'art', 'status': 'ready'},
    },
    'room_hold': {'desk-card.json': {'event': 'Book swap', 'room': 'Cedar Hall', 'host': 'Library'}},
}


def parsed(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError, RecursionError):
        return None


def read_text(receipt):
    """Data actually returned by the ordinary line-numbered reader (not its size)."""
    content = receipt.get('content')
    if not isinstance(content, str) or receipt.get('error') or receipt.get('not_found'):
        return ''
    return '\n'.join(line.partition('|')[2] for line in content.splitlines())


def facts(case, messages, workspace):
    """Use linked effects, not attempted calls. A batch can't act on its own future reads."""
    fixtures, expected = FIXTURES[case], EXPECTED[case]
    source = next(name for name in fixtures if name.endswith('.txt'))
    needs_read = set(expected) - {'handoff.json'}
    written, verified, calls = set(), set(), {}
    scope_ok = release_ok = True
    source_lines = set(fixtures[source].splitlines())
    observed = set()
    for row in messages:
        if row.get('role') == 'assistant':
            for call in row.get('tool_calls') or []:
                fn = call.get('function', {})
                args = parsed(fn.get('arguments'))
                path = Path(str(args.get('path', ''))).name if isinstance(args, dict) else ''
                calls[call.get('id')] = (fn.get('name'), path, needs_read <= verified and source_lines <= observed)
        if row.get('role') != 'tool':
            continue
        tool, name, can_release = calls.pop(row.get('tool_call_id'), (None, None, False))
        receipt = parsed(row.get('content'))
        if not isinstance(receipt, dict) or receipt.get('executed') is False or receipt.get('error'):
            continue
        if tool in ('patch', 'write_file') and (receipt.get('success') or receipt.get('verified') or 'bytes_written' in receipt):
            scope_ok &= name in expected
            release_ok &= name != 'handoff.json' or (can_release and needs_read <= verified)
            written.add(name)
            verified.discard(name)
        elif tool == 'read_file':
            text = read_text(receipt)
            if name == source:
                observed.update(text.splitlines())
            if name in needs_read and name in written and parsed(text) == expected[name]:
                verified.add(name)
    facts = {'contract_read': source_lines <= observed, 'writes_in_scope': scope_ok,
             'only_fixture_files': {p.name for p in workspace.iterdir()} == set(fixtures)}
    for name, initial in fixtures.items():
        path = workspace / name
        data = path.read_bytes() if path.is_file() else None
        if name in expected:
            facts['saved:' + name] = parsed(data) == expected[name]
            if name in needs_read:
                facts['read_back:' + name] = name in verified
        else:
            facts['unchanged:' + name] = data == initial.encode()
    if case == 'kit_handoff':
        facts['release_only_after_verification'] = release_ok
    return facts


def observe_stops(cell, case, actor):
    """Observe the existing gate, not the actor prompt or the judge's advice policy."""
    from unittest.mock import patch
    from agent.intent_attractor import stop_gate
    from evals.intent_v1.live_transport import journal

    previous = None

    def observe(agent, final_msg, candidate, messages, system_prompt, api_call_count):
        nonlocal previous
        current = facts(case, messages, cell / 'workspace')
        continued = stop_gate(agent, final_msg, candidate, messages, system_prompt, api_call_count)
        receipt = (messages[-1].get('display_metadata') or {}).get('intent_attractor', {}) if continued else {}
        state = agent._intent_attractor.read() if agent._intent_attractor else None
        protocol = (state or {}).get('protocol', {})
        journal(cell / 'events.jsonl', {'event': 'ordinary_stop', 'actor_call': actor.actor_calls,
                'candidate': candidate, 'facts': current, 'semantic_continuation_triggered': continued,
                'actor_transition': protocol.get('declaration', {}).get('transition'),
                'assessment_id': receipt.get('assessment_id'),
                'since_continuation_at_call': previous[0] if previous else None,
                'facts_gained': [k for k in current if current[k] and not previous[1][k]] if previous else [],
                'facts_lost': [k for k in current if not current[k] and previous[1][k]] if previous else []})
        previous = (actor.actor_calls, current) if continued else None
        return continued

    return patch('agent.intent_attractor.stop_gate', observe)
