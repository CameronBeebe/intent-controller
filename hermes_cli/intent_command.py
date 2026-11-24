"""Shared, revision-fenced intent workspace commands. No execution or task kickoff."""
import copy
import difflib
import json

from agent.intent_attractor import FIELDS

HELP = ('/intent [inspect|history|assessments|replay|edit FIELD TEXT|pin FIELD|unpin FIELD|'
        'revert REVISION|pause|resume]')
USAGE = (HELP + '\nUsage: /intent edit FIELD TEXT (1–1000 characters).\nFields: ' + ', '.join(FIELDS))


class IntentCommandError(ValueError):
    """Actionable command refusal, not an RPC or storage failure."""


def validate(arg):
    parts = arg.strip().split(maxsplit=2)
    verb = parts[0] if parts else 'inspect'
    if verb in ('edit', 'pin', 'unpin'):
        count = 3 if verb == 'edit' else 2
        if len(parts) != count or parts[1] not in FIELDS or (verb == 'edit' and len(parts[2]) > 1000):
            raise IntentCommandError(USAGE)
    elif verb == 'revert':
        if len(parts) != 2 or not parts[1].isdigit():
            raise IntentCommandError(USAGE)
    elif verb not in ('inspect', 'history', 'assessments', 'replay', 'pause', 'resume') or len(parts) > 1:
        raise IntentCommandError(USAGE)
    return parts, verb


def render(state):
    if not state:
        return 'Intent-attractor: no intent yet (enable agent.intent_attractor for actor-owned interpretation).'
    lines = [f'Extracted intent r{state["revision"]} · {state["decision"]}' + (' · paused' if state['paused'] else '')]
    lines.append('Actor protocol: ' + json.dumps(state.get('protocol') or {}, ensure_ascii=True))
    for key in FIELDS:
        label = 'user-edited' if state['origins'][key] == 'user' else 'inferred'
        pin = ', pinned' if key in state['pins'] else ''
        lines.append(f'{key} [{label}{pin}]: {state["fields"][key] or "—"}')
    lines.append(f'Feedback cadence: every {state.get("feedback_interval", 1)} completed tool round(s); '
                 f'round {state.get("tool_rounds", 0)}; judgments used {state.get("assessments_used", 0)}')
    lines.append('Feedback trace: ' + json.dumps([e for e in state.get('trajectory', [])
                                                 if e.get('label', '').startswith('Feedback round')], ensure_ascii=True))
    lines.append('Latest feedback: ' + json.dumps(state.get('feedback') or {}, ensure_ascii=True))
    lines.append('Last judgment: ' + json.dumps(state.get('last_judgment') or {}, ensure_ascii=True))
    lines.append('User messages (evidence): ' + json.dumps(state['sources'], ensure_ascii=True))
    if state.get('error'):
        lines.append('Diagnostic: ' + state['error'])
    lines.append('Immediate request: ' + (state.get('immediate_request') or {}).get('text', '—'))
    lines.append('Working interpretation (not permission): ' + json.dumps(state.get('interpretation') or {}, ensure_ascii=True))
    lines.append('Fidelity confidence receipt: ' + json.dumps(state.get('fidelity_annotation') or {}, ensure_ascii=True))
    if state.get('validated_specification'):
        lines.append('Historical model-validated specification (not user approval): ' +
                     json.dumps(state['validated_specification'], ensure_ascii=True))
    for goal in state.get('subgoals', []):
        lines.append(f'Subgoal {goal["id"]} [{goal["status"]}]: {goal["text"]}')
    if state.get('pending_proposal'):
        lines.append('Pending interpretation: ' + json.dumps(state['pending_proposal'], ensure_ascii=True))
    return '\n'.join(lines)


def dispatch(store, arg):
    parts, verb = validate(arg)
    state = store.read()
    if verb == 'inspect':
        return render(state)
    if verb == 'assessments':
        return json.dumps(store.assessments(), indent=2, ensure_ascii=True)
    if verb == 'replay':
        from agent.intent_attractor import decide
        records = store.assessments()
        if not records:
            return 'No saved assessment.'
        record = records[-1]
        from agent.intent_reconciliation import fidelity_decision
        replay = fidelity_decision if record.get('phase') == 'reconciliation' else decide
        return json.dumps({'recorded_decision': record['decision'], 'revision': record['revision'],
                           'replayed_decision': ('REPAIR' if record.get('phase') == 'protocol' else
                               replay(record['answers']) if record.get('answers') else 'UNAVAILABLE')})
    if not state:
        return render(state)
    if verb == 'history':
        lines, old = [], {}
        for entry in state['history']:
            lines.append(f'Revision {entry["revision"]} ({entry["source"]})')
            snapshot = {'fields': entry['fields'], 'subgoals': entry.get('subgoals', [])}
            lines.extend(difflib.unified_diff(json.dumps(old, indent=2).splitlines(),
                         json.dumps(snapshot, indent=2).splitlines(), lineterm=''))
            old = snapshot
        return '\n'.join(lines)
    revision = state['revision']
    if verb in ('edit', 'pin', 'unpin'):
        field = parts[1]
        if verb == 'edit':
            state['fields'][field] = parts[2]
            state['origins'][field] = 'user'
            if field == 'outcome':
                # An explicit current aim supersedes an older ambiguous/presentation turn.
                state['update_type'] = 'refinement'
        elif verb == 'pin':
            state['pins'] = sorted(set(state['pins']) | {field})
        else:
            state['pins'] = [p for p in state['pins'] if p != field]
            state['origins'][field] = 'inferred'
    elif verb == 'revert' and len(parts) == 2:
        entry = next((h for h in state['history'] if str(h['revision']) == parts[1]), None)
        if entry is None:
            raise IntentCommandError('Unknown intent revision')
        state['fields'] = copy.deepcopy(entry['fields'])
        state['subgoals'] = copy.deepcopy(entry.get('subgoals', []))
        state['origins'] = {k: 'user' for k in FIELDS}
    elif verb in ('pause', 'resume') and len(parts) == 1:
        state['paused'] = verb == 'pause'
    else:
        raise IntentCommandError(HELP)
    state['revision'] += 1
    state['decision'] = 'PENDING'
    state.pop('pending_proposal', None)
    from agent.intent_attractor_updates import trajectory_event
    trajectory_event(state, 'wait' if verb == 'pause' else 'revise', 'User ' + verb)
    state['history'].append({'revision': state['revision'], 'fields': copy.deepcopy(state['fields']),
                             'subgoals': copy.deepcopy(state.get('subgoals', [])),
                             'source': 'user:' + verb})
    if not store.save(state, revision):
        raise IntentCommandError('Intent changed concurrently; inspect and retry')
    return render(state)


def cli_command(cli, arg):
    """Classic CLI addresses its exact gateway-owned session; no local fallback."""
    connection = getattr(getattr(cli, '_session_db', None), 'connection', None)
    session_id = getattr(cli, 'session_id', None)
    if not connection or not session_id:
        return 'Intent-attractor unavailable: an active gateway-owned CLI session is required.'
    from hermes_cli.gateway_client import GatewayRPCError
    try:
        response = connection.call('session.automation', session_id=session_id,
                                   kind='intent', call='command', args=[arg], kwargs={})
        if response.get('error'):
            error = response['error']
            if error.get('type') == 'IntentCommandError':
                return error['message']
            return 'Intent-attractor unavailable: owner rejected the command.'
        return response['value']
    except GatewayRPCError as exc:
        from hermes_cli.gateway_client import _bounded_reason
        reason = _bounded_reason(str(exc)) or 'rpc_error'
        return f'Intent-attractor unavailable: owner rejected the command ({reason}).'
    except Exception:  # health: allow BLE001 -- refuse disconnected/older owners, never mutate a local substitute
        return 'Intent-attractor unavailable: gateway command failed (no local fallback).'
