"""Explicit actor changes and finishes; unchanged work inherits request authority.

Control is validated before mixed-batch dispatch. Text streams normally; it is
not proof that the harness accepted finish. No generative sidecar or retain rows.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import time
import uuid

from agent.intent_attractor import FIELDS, IntentStore, empty_state, notify, real_sources
from agent.intent_attractor_updates import EXTRACTION_PROMPT, IntentUpdate, UPDATE_TYPES

NAME = 'intent_control'
REPAIR_LIMIT = 2
GUIDANCE = (
    '\n## Required actor intent protocol v2\n'
    'Before first tools OR final text, call intent_control(mode=create, proposal=...). '
    'For each new user input, use mode=update with a proposal, even for presentation-only requests. '
    'Use the checkpoint revision and at most one control per response. '
    'With tools: transition=act; control may accompany them or precede them alone. '
    'Unchanged tool batches inherit the revision frozen before inference: no repeated retain. '
    'Before final text: call transition=finish (or wait/blocked), WITHOUT other tools; '
    'then send the answer after its receipt. Use mode=retain without a proposal if already reconciled; '
    'otherwise combine create/update and finish in one control. '
    'Protocol errors reject the entire batch; retry needed actions with transition=act. '
    'You own the working interpretation. Valid updates apply directly; manual fields/pins, '
    'provenance and revision fences govern. accepted is structural application, not permission. '
    'Jev advice cannot veto interpretation; finish is independently assessed against actual outcomes.\n'
    + EXTRACTION_PROMPT
)


def tool_schema():
    return {'type': 'function', 'function': {
        'name': NAME, 'description': 'Declare initial intent, reconcile changed user input, or explicitly finish/wait/blocked. Unchanged tool batches inherit pre-inference intent; no repeated retain needed. Not authorization.',
        'parameters': {'type': 'object', 'additionalProperties': False,
            'required': ['revision', 'mode', 'transition'], 'properties': {
                'revision': {'type': 'integer', 'minimum': 0},
                'mode': {'type': 'string', 'enum': ['create', 'update', 'retain']},
                'transition': {'type': 'string', 'enum': ['act', 'finish', 'wait', 'blocked']},
                'proposal': {'type': 'object', 'additionalProperties': False,
                    'required': ['update_type', 'fields'], 'properties': {
                        'update_type': {'type': 'string', 'enum': list(UPDATE_TYPES)},
                        'fields': {'type': 'object', 'additionalProperties': False,
                            'properties': {k: {'type': 'string', 'maxLength': 1000} for k in FIELDS}},
                        'operations': {'type': 'array', 'maxItems': 12, 'items': {
                            'type': 'object', 'additionalProperties': False, 'required': ['op'],
                            'properties': {'op': {'type': 'string', 'enum': ['retain', 'add_subgoal', 'refine_subgoal', 'progress', 'suspend', 'resume']},
                                           'id': {'type': 'string'}, 'text': {'type': 'string', 'maxLength': 1000},
                                           'status': {'type': 'string', 'enum': ['completed']}}}}}}}}}}


def surface_tools(agent, tools):
    if not getattr(agent, '_intent_guidance_enabled', False):
        return tools
    carried = next((t for t in getattr(agent, 'tools', []) if t['function']['name'] == NAME), None)
    return [t for t in tools if t['function']['name'] != NAME] + [carried or tool_schema()]


def restore_contract(agent, saved):
    pinned = saved.get('tools', []) if isinstance(saved, dict) else saved
    if not isinstance(pinned, list) or not pinned:
        return
    contract = next((t for t in pinned if isinstance(t, dict) and t.get('function', {}).get('name') == NAME), None)
    agent._intent_guidance_enabled = contract is not None
    agent.tools = [t for t in agent.tools if t['function']['name'] != NAME]
    if contract:
        agent.tools.append(copy.deepcopy(contract))
    agent.valid_tool_names = {t['function']['name'] for t in agent.tools}


def final_fence(agent, message, messages):
    if not getattr(agent, '_intent_guidance_enabled', False):
        return None
    try:
        if (agent._intent_attractor.read()['revision'] == agent._intent_consumed_revision
                and not getattr(agent, '_pending_steer', None)):
            agent._intent_final_declared = True
            return None
        agent._intent_final_declared = False
        return reject(agent, message, messages, 'intent changed during final assessment; reconcile current revision')
    except Exception:  # health: allow BLE001 -- no successful finish when authority cannot be read
        agent._intent_final_declared = False
        agent._intent_protocol_failed = True
        return 'break'


def configure(agent, section):
    """Pin once at construction; never toggle an existing session's contract."""
    agent._intent_guidance_enabled = (section.get('intent_attractor') or {}).get('enabled') is True
    if agent._intent_guidance_enabled:
        agent.tools = [*agent.tools, tool_schema()]
        agent.valid_tool_names.add(NAME)


def owned_runtime(agent, loop, run):
    if getattr(agent, '_intent_guidance_enabled', False):
        return {'final_response': 'Incomplete: actor intent protocol is unsupported by the owned codex_app_server runtime. Use a tool-calling transport or a new disabled session.',
                'messages': loop.messages, 'failed': True, 'error': 'intent_protocol_unsupported_runtime',
                'interrupted': False}
    return run(agent, loop)


def fail_unfinished(agent, loop):
    if (getattr(agent, '_intent_guidance_enabled', False)
            and not getattr(agent, '_intent_final_declared', False)
            and not agent._interrupt_requested):
        loop.failed = True
        loop.final_response = 'Incomplete: required intent protocol could not be satisfied; no rejected actions were executed.'
        loop._turn_exit_reason = 'intent_protocol_failure'


def run_candidate(agent, loop, run_phase):
    from types import SimpleNamespace
    from agent.turn_tool_round import run_tool_round
    from agent.turn_final_response import finish_text_response
    action = boundary(agent, loop.assistant_message, loop.messages)
    if action:
        return SimpleNamespace(action=action)
    return run_phase(run_tool_round if loop.assistant_message.tool_calls else finish_text_response, agent, loop)


def finish_candidate(agent, message, final_msg, candidate, messages, system_prompt, api_call_count):
    from agent.intent_attractor import stop_gate
    if stop_gate(agent, final_msg, candidate, messages, system_prompt, api_call_count):
        agent._intent_final_declared = False
        return 'continue'
    return final_fence(agent, message, messages)


@dataclass(frozen=True)
class RequestBinding:
    """Authority observed before inference; never minted from returned actions."""
    revision: int
    sources: str
    visible_revision: int


def bind_request(agent, messages):
    if not getattr(agent, '_intent_guidance_enabled', False):
        return True
    agent._intent_request_binding = None
    try:
        state = agent._intent_attractor.read()
        agent._intent_request_binding = RequestBinding(
            state['revision'], json.dumps(real_sources(messages), sort_keys=True),
            agent._intent_consumed_revision)
        return not agent._intent_unavailable
    except Exception:  # health: allow BLE001 -- authority failure must not enter provider retries
        agent._intent_protocol_failed = True
        return False


def request_state(agent, messages):
    state = agent._intent_attractor.read()
    binding = agent._intent_request_binding
    if (binding is None or binding.revision != state['revision']
            or binding.visible_revision != binding.revision):
        raise ValueError('stale request intent revision; reconcile current revision')
    if (binding.sources != json.dumps(real_sources(messages), sort_keys=True)
            or getattr(agent, '_pending_steer', None)):
        raise ValueError('user input changed during inference; reconcile before acting')
    return state


def begin(agent, messages):
    agent._intent_attractor = None
    agent._intent_unavailable = False
    agent._intent_permit = None
    agent._intent_protocol_repairs = 0
    agent._intent_protocol_failed = False
    agent._intent_control_results = {}
    agent._intent_dispatch_revision = None
    agent._intent_request_binding = None
    agent._intent_final_declared = False
    if not getattr(agent, '_intent_guidance_enabled', False):
        return
    # An actor contract may be pinned before this scope is bound. Its control
    # is applied before tool dispatch, so consult the independent hosted policy
    # here too, before reading host state or invoking the profile's judge.
    from tools.hosted_tool_policy import hosted_tool_refusal
    if hosted_tool_refusal(NAME) is not None:
        agent._intent_unavailable = True
        return
    try:
        from agent.intent_attractor import settings
        for key in ('judge_enabled', 'semantic_continuation'):
            if type(settings().get(key, True)) is not bool:
                raise ValueError('invalid intent switch')
        for key, default in (('max_continuations', 2), ('max_assessments', None), ('feedback_interval', 1)):
            value = settings().get(key, default)
            if key == 'max_assessments' and value is None:
                continue
            if type(value) is not int or value < 0 or (key != 'max_assessments' and value > 20):
                raise ValueError('invalid intent limit')
        store = IntentStore.for_agent(agent)
        agent._intent_attractor = store
        state = store.read() or empty_state()
        revision = state['revision']
        from agent.intent_source_recovery import recover_sources
        state = recover_sources(store, state, messages)
        state['protocol'] = {'version': 2, 'owner': 'actor', 'status': 'declaration_required'}
        if not store.save(state, revision):
            raise ValueError('state changed during initialization')
        publish(agent, messages, state)
    except Exception:  # health: allow BLE001 -- authority loss must fail closed without retrying tools
        agent._intent_unavailable = True


def publish(agent, messages, state):
    from agent.intent_feedback import deliver
    sources = real_sources(messages)
    # Publication is not reconciliation: do not consume these sources yet.
    visible = {**state, 'immediate_request': sources[-1] if sources else state.get('immediate_request')}
    deliver(agent, messages, visible, kind='protocol')
    agent._intent_consumed_revision = state['revision']
    agent._intent_published_sources = real_sources(messages)
    notify(agent, state)


def reconciliation_required(state, messages):
    return (real_sources(messages) != state['sources']
            or state.get('source_recovery', {}).get('reconciliation_required', False))


def control_requirement(state, messages):
    """Tell the actor the next legal control, using authority rather than Jev status."""
    revision = state['revision']
    if not state.get('actor_initialized'):
        mode = 'create'
    elif reconciliation_required(state, messages):
        mode = 'update'
    else:
        return (f'Before final text: intent_control(revision={revision}, mode=retain, transition=finish '
                'or wait/blocked), without other tools. Unchanged tools need no control.')
    return (f'Before tools or final text: intent_control(revision={revision}, mode={mode}, proposal=...). '
            'Use transition=act with tools, or finish without tools then answer. '
            + ('Reconcile the current user request with the saved interpretation; choose the proposal '
               'update_type from real user evidence. Preserve manual fields/pins and applicable user constraints.' if mode == 'update'
               else 'Initial task: proposal update_type=replacement with all five fields.'))


def commit(agent, args, messages):
    store = agent._intent_attractor
    state = store.read()
    previous = copy.deepcopy(state)
    if not isinstance(args, dict) or not {'revision', 'mode', 'transition'} <= set(args) <= {'revision', 'mode', 'transition', 'proposal'}:
        raise ValueError('invalid declaration envelope')
    revision = args['revision']
    if type(revision) is not int or revision != state['revision']:
        raise ValueError('stale or invalid revision')
    mode, transition = args['mode'], args['transition']
    if mode not in ('retain', 'create', 'update') or transition not in ('act', 'finish', 'wait', 'blocked'):
        raise ValueError('invalid mode or transition')
    sources = real_sources(messages)
    record = None
    update = None
    request = None
    from agent.intent_attractor_judge import redact_evidence
    if not sources:
        sources = state['sources']
    if mode == 'retain':
        if ('proposal' in args or not state.get('actor_initialized') or sources != state['sources']
                or state.get('source_recovery', {}).get('reconciliation_required')):
            raise ValueError('first interpretation and new user input require create/update proposal')
    else:
        if (mode == 'create') == bool(state.get('actor_initialized')):
            raise ValueError('use create initially and update thereafter')
        update = IntentUpdate.parse(args.get('proposal'))
        if update.update_type in ('presentation', 'ambiguous') and (update.fields or update.operations):
            raise ValueError('presentation/ambiguous cannot change fields or operations')
        from agent.intent_reconciliation import reconcile
        request = redact_evidence({'owner': 'actor', 'declaration': copy.deepcopy(args), 'original_user_expressions': sources})
        state = reconcile(state, sources, update)
        state['actor_initialized'] = True
        if state.get('source_recovery'):
            state['source_recovery']['reconciliation_required'] = False
        state['history'][-1]['source'] = 'actor'
    if getattr(agent, '_interrupt_requested', False):
        raise ValueError('interrupted')
    state['protocol'] = {'version': 2, 'owner': 'actor', 'status': 'accepted',
                         'declaration': copy.deepcopy(args), 'revision': state['revision']}
    if not store.save(state, revision):
        raise ValueError('intent changed during reconciliation; use current revision')
    notify(agent, state)  # Publish the committed interpretation before optional judging.
    if update is not None:
        from agent.intent_reconciliation import assess_applied
        state, record = assess_applied(agent, previous, state, update, request, json.dumps(redact_evidence(args)))
        if record:
            from agent.intent_feedback import select_delivery
            record['delivery'] = select_delivery(state, record)
            if not store.save(state, state['revision']):
                raise ValueError('intent changed before advisory delivery')
            record['actor_declaration'] = redact_evidence(args)
            store.record(record)
    if getattr(agent, '_interrupt_requested', False):
        raise ValueError('interrupted after applying interpretation')
    agent._intent_consumed_revision = state['revision']
    agent._intent_permit = (state['revision'], transition)
    notify(agent, state)
    result = {'accepted': True, 'revision': state['revision'], 'transition': transition,
            'next': (f'Unchanged tools need no control. Before final text call intent_control(revision={state["revision"]}, '
                     'mode=retain, transition=finish or wait/blocked), without other tools.'
                     if transition == 'act' else 'Send the final text now; no tools.'),
            'fields': state['fields'], 'pins': state['pins'],
            'origins': state['origins'],
            'subgoals': [{k: g[k] for k in ('id', 'text', 'status')} for g in state.get('subgoals', [])],
            'assessment': {'role': 'advisory', 'decision': record['decision'],
                           'answers': record.get('answers') or {}} if record and record['delivery']['delivered'] else None}
    if mode == 'retain':
        return {k: result[k] for k in ('accepted', 'revision', 'transition', 'next')}
    return result


def dispatch_error(agent, name):
    """Recheck at each actual dispatch, after plugin hooks and before approvals."""
    if not getattr(agent, '_intent_guidance_enabled', False) or name == NAME:
        return None
    try:
        state = agent._intent_attractor.read()
        if state['revision'] != getattr(agent, '_intent_dispatch_revision', None):
            return 'stale intent revision; action not executed; declare the current revision'
        if getattr(agent, '_pending_steer', None):
            return 'user input changed; action not executed; reconcile before acting'
        if state['paused']:
            return 'intent paused; action not executed'
    except Exception:  # health: allow BLE001 -- unreadable authority cannot authorize dispatch
        return 'intent authority unavailable; action not executed'
    return None


def execution_check(agent, name):
    """Freeze the dispatch's owner/revision: a later turn cannot revive old work."""
    if not getattr(agent, '_intent_guidance_enabled', False) or name == NAME:
        return None
    store = agent._intent_attractor
    revision = agent._intent_dispatch_revision

    def check():
        state = store.read()
        if state['revision'] != revision:
            return 'stale intent revision; action not executed; declare the current revision'
        if getattr(agent, '_pending_steer', None):
            return 'user input changed; action not executed; reconcile before acting'
        if state['paused']:
            return 'intent paused; action not executed'
        return None

    return check


def receipt(agent, args, ctx):
    """Only the loop can commit; a nested invocation cannot mint a permit."""
    return json.dumps(agent._intent_control_results.pop(ctx.tool_call_id, {
        'error': 'intent_control must be a top-level actor call validated by the turn loop'}))


def reject(agent, message, messages, reason):
    from agent.message_metadata import append_message
    from agent.turn_tool_round import stage_tool_call_message
    calls = message.tool_calls or []
    if calls:
        row, _ = stage_tool_call_message(agent, assistant_message=message, finish_reason='tool_calls', messages=messages)
        append_message(messages, row)
        for tc in calls:
            append_message(messages, {'role': 'tool', 'name': tc.function.name,
                'tool_call_id': tc.id, 'content': json.dumps({'error': 'intent_protocol', 'reason': reason,
                                                          'executed': False})})
    else:
        append_message(messages, {'role': 'assistant', 'content': message.content or 'No intent declaration.'})
    agent._intent_permit = None
    agent._intent_protocol_repairs += 1
    agent._intent_final_declared = False
    state = agent._intent_attractor.read()
    state['protocol'] = {'version': 2, 'owner': 'actor', 'status': 'repair_required',
                         'reason': reason, 'repairs': agent._intent_protocol_repairs}
    store = agent._intent_attractor
    store.save(state, state['revision'])
    from agent.intent_attractor_judge import redact_evidence
    store.record(redact_evidence({'id': f'{time.time_ns():020d}-{uuid.uuid4().hex}', 'phase': 'protocol',
                  'revision': state['revision'], 'decision': 'REPAIR', 'reason': reason,
                  'calls': [dict(name=c.function.name, arguments=c.function.arguments) for c in calls]}))
    publish(agent, messages, state)
    if agent._intent_protocol_repairs > REPAIR_LIMIT or agent.iteration_budget.remaining <= 0:
        agent._intent_protocol_failed = True
        return 'break'
    return 'continue'


def boundary(agent, message, messages):
    """Return continue/break to stop a candidate, None to use normal loop semantics."""
    if not getattr(agent, '_intent_guidance_enabled', False):
        return None
    if getattr(agent, '_interrupt_requested', False):
        return 'break'
    if getattr(agent, '_intent_unavailable', False):
        agent._intent_protocol_failed = True
        return 'break'
    try:
        calls = message.tool_calls or []
        from agent.message_sanitization import normalize_provider_tool_call_ids, uniquify_tool_call_ids, coalesce_tool_call_id
        normalize_provider_tool_call_ids(calls)
        uniquify_tool_call_ids(calls, [c.get('id') for m in messages for c in m.get('tool_calls', [])])
        controls = [c for c in calls if c.function.name == NAME]
        actions = [c for c in calls if c.function.name != NAME]
        if len(controls) > 1:
            raise ValueError('exactly one intent_control per response')
        state = request_state(agent, messages)
        if controls:
            args = json.loads(controls[0].function.arguments)
            if isinstance(args, dict) and args.get('revision') != agent._intent_request_binding.revision:
                raise ValueError('stale or invalid request revision')
            if actions and isinstance(args, dict) and 'transition' in args and args['transition'] != 'act':
                raise ValueError('action batch requires transition=act')
            result = commit(agent, args, messages)
            agent._intent_control_results[coalesce_tool_call_id(controls[0])] = result
        else:
            permit = agent._intent_permit
            reconciled = (state.get('actor_initialized') and real_sources(messages) == state['sources']
                          and not state.get('source_recovery', {}).get('reconciliation_required'))
            if actions:
                # End transitions cannot silently turn into more work. Otherwise
                # unchanged actions inherit this request's frozen authority.
                allowed = not permit or permit == (state['revision'], 'act')
            else:
                allowed = permit and permit[0] == state['revision'] and permit[1] in ('finish', 'wait', 'blocked')
            if not reconciled or not allowed:
                raise ValueError('missing current intent declaration before action/finish')
            agent._intent_consumed_revision = agent._intent_request_binding.revision
        if actions or not calls:
            agent._intent_permit = None
        if actions:
            agent._intent_dispatch_revision = agent._intent_consumed_revision
        return None
    except (ValueError, TypeError, KeyError) as exc:
        try:
            return reject(agent, message, messages, str(exc))
        except Exception:  # health: allow BLE001 -- a broken store cannot save its own rejection
            agent._intent_protocol_failed = True
            return 'break'
    except Exception:  # health: allow BLE001 -- storage/judge failure cannot bypass participation or repeat an action
        agent._intent_protocol_failed = True
        return 'break'
