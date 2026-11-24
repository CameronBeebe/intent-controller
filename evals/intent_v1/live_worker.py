"""Live simple-case worker: real actor loop, no scripted model replies."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from contextlib import nullcontext
from types import SimpleNamespace as NS
from typing import Any
from unittest.mock import patch

from evals.intent_v1.live import LiveStop, ResourceStop, actual_spend
from evals.intent_v1.semantic import VERSION, INVARIANT_VERSION, task, concrete_checks, evaluate, artifact_evidence
from evals.intent_v1.live_transport import Actor, Judge, fixture_dispatch, journal
from evals.intent_v1.run import child_env, save
from evals.intent_v1.worker import FIXTURE_VERSION
from evals.intent_v1 import sustained
from evals.intent_v1.tranche import TASKS, fixtures_for, manifest_for, checks as tranche_checks


def run(cell, credentials):
    cell = Path(cell).resolve()
    expected = child_env(cell)
    if any(os.environ.get(k) != expected[k] for k in ('HOME', 'HERMES_HOME', 'TERMINAL_CWD')):
        raise LiveStop('isolated worker environment required')
    plan = json.loads((cell / 'cell.json').read_text())
    contract = plan.get('task', task(plan['case']))
    if contract != task(plan['case']):
        raise LiveStop('frozen task mismatch')
    cfg = plan['config']
    fixtures = fixtures_for(plan['case'])
    workspace = cell / 'workspace'
    os.chdir(workspace)
    for name, content in fixtures.items():
        (workspace / name).write_text(content)
    before = {n: hashlib.sha256((workspace / n).read_bytes()).hexdigest() for n in fixtures}
    save(cell / 'fixtures.before.json', before)
    save(cell / 'hermes' / 'config.yaml', {
        'agent': {'intent_attractor': cfg, 'read_assessment': {'enabled': False}},
        'terminal': {'backend': 'local'}, 'compression': {'enabled': False}})
    # Both context-window and usage-pricing readers use this per-profile catalog.
    # Reuse the driver's preflight row instead of issuing unrecorded worker GETs.
    catalog_cache = cell / 'hermes' / 'cache'
    catalog_cache.mkdir(exist_ok=True)
    metadata = {'id': plan['actor_model'], **plan.get('actor_metadata', {})}
    save(catalog_cache / 'openrouter_model_metadata.json', {plan['actor_model']: metadata})
    # The reasoning-capabilities warmup uses urllib, not the actor's HTTP client.
    from hermes_cli.models_reasoning_caps import _seed_reasoning_caps
    from evals.intent_v1.live_auth import ACTOR_BASE
    _seed_reasoning_caps(ACTOR_BASE + '/models', [metadata])
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    token = set_secret_scope({'TYPESAFE_API_KEY': credentials['judge']})
    from run_agent import AIAgent
    from persistence_assembly import open_persistence, profile_scope
    from agent.intent_attractor import IntentStore
    import model_tools
    actor = Actor(cell, plan, credentials['actor'])
    judge = Judge(cell, plan, credentials['judge'])
    definitions = [t for t in model_tools.get_tool_definitions(enabled_toolsets=['file'])
                   if t['function']['name'] in ('read_file', 'write_file', 'patch')]
    client = NS(chat=NS(completions=NS(create=actor.create)))
    db = open_persistence(profile_scope(home=cell / 'hermes'))
    results, history = [], []
    agent: Any = None
    error = None
    censored = False
    stop_reason = None
    started = time.monotonic()
    try:
        with (patch('agent.process_bootstrap.OpenAI', return_value=client),
              patch('model_tools.get_tool_definitions', return_value=definitions),
              patch('model_tools.handle_function_call', fixture_dispatch(workspace, model_tools.handle_function_call,
                    set(fixtures) if plan['case'] in TASKS else None,
                    receipt_path=cell / 'tool_receipts.jsonl')),
              patch('hermes_cli.plugins.discover_plugins', return_value=None),
              patch('agent.read_assessment_typesafe.http_client', judge.client),
              patch('agent.auxiliary_client.call_llm', side_effect=LiveStop('auxiliary inference forbidden')),
              patch('agent.title_generator.maybe_auto_title', return_value=None),
              sustained.observe_stops(cell, plan['case'], actor) if plan['case'] in sustained.TASKS else nullcontext()):
            agent = AIAgent(model=plan['actor_model'], provider='openrouter', api_key='transport-owned-not-a-key',
                            base_url='https://openrouter.ai/api/v1', quiet_mode=True,
                            session_id=cell.name, persistence=db, enabled_toolsets=['file'],
                            ephemeral_system_prompt=manifest_for(plan['case']),
                            max_iterations=plan['max_iterations'],
                            max_tokens=plan['max_output_tokens'], skip_context_files=True,
                            skip_memory=True, skip_background_review=True, cwd=str(workspace))
            actor.agent = agent
            agent.client = client
            agent._disable_streaming = True
            agent.platform = 'subagent'  # inline HTTP path; LiveStop reaches the worker, not a daemon thread
            agent.compression_enabled = False
            def exhausted(messages, *_):
                # Existing iteration bound, no second transport call-count ceiling
                # and no extra paid summary after the bound is exhausted.
                save(cell / 'partial_request.json', {'messages': messages})
                raise ResourceStop('agent iteration limit')
            agent._handle_max_iterations = exhausted
            for index, prompt in enumerate(contract['messages']):
                if index:
                    journal(cell / 'events.jsonl', {'event': 'steering_boundary',
                            'boundary': contract['boundary'], 'history_length': len(history),
                            'actor_calls_completed': actor.actor_calls, 'next_user_message': prompt})
                journal(cell / 'events.jsonl', {'event': 'user_turn', 'index': index, 'text': prompt})
                turn_started = time.monotonic()
                result = agent.run_conversation(prompt, conversation_history=history)
                history = result['messages']
                results.append(result)
                save(cell / f'turn-{index}.json', result)
                journal(cell / 'events.jsonl', {'event': 'turn_end', 'index': index,
                        'wall_seconds': time.monotonic() - turn_started,
                        'state': IntentStore(db, agent.session_id).read()})
    except BaseException as exc:
        error = type(exc).__name__
        censored = isinstance(exc, ResourceStop)
        stop_reason = str(exc) if isinstance(exc, LiveStop) else None
        journal(cell / 'events.jsonl', {'event': 'worker_failure', 'error_type': error,
                'reason': str(exc) if isinstance(exc, LiveStop) else None})
    finally:
        store = IntentStore(db, cell.name)
        save(cell / 'intent.json', {'state': store.read(), 'history': store.command('history'),
                                   'assessments': store.assessments()})
        if error and (cell / 'partial_request.json').exists():
            history = json.loads((cell / 'partial_request.json').read_text()).get('messages', history)
        save(cell / 'transcript.json', history)
        save(cell / 'tool_receipts.json', [m for m in history if m.get('role') == 'tool'])
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in workspace.iterdir() if p.is_file()}
        save(cell / 'fixtures.after.json', after)
        answers = [r.get('final_response', '') for r in results]
        checks = (tranche_checks(plan['case'], contract, results, workspace) if plan['case'] in TASKS else
                  concrete_checks(contract, results, before == after))
        checks['feature_judge_isolation'] = cfg['judge_enabled'] or judge.judge_calls == 0
        code_evidence = artifact_evidence(fixtures, workspace)
        save(cell / 'code_evidence.json', code_evidence)
        evaluation = (evaluate(cell, contract, results, fixtures, plan, credentials['judge'], code_evidence, checks) if not error else
                      {'role': 'evaluator', 'version': VERSION, 'verdict': 'unavailable', 'calls': 0})
        passed = not error and all(checks.values()) and evaluation['verdict'] == 'pass'
        record = {'mode': 'live', 'grader_version': VERSION, 'config': cfg, 'passed': passed, 'checks': checks,
                  'fixture_version': plan.get('fixture_version', FIXTURE_VERSION),
                  'invariant_version': plan.get('invariant_version', INVARIANT_VERSION),
                  'harness_valid': censored or (not error and evaluation['verdict'] != 'unavailable'),
                  'outcome': 'censored' if censored else ('complete' if not error else 'infrastructure_invalid'),
                  'incomplete': censored, 'stop_reason': stop_reason,
                  'behavioral_contract_passed': passed,
                  'infrastructure_invalid': not censored and (bool(error) or evaluation['verdict'] == 'unavailable'),
                  'evaluator': evaluation, 'evaluator_calls': evaluation['calls'],
                  'error_type': error, 'home': str(cell / 'hermes'), 'workspace': str(workspace),
                  'fixture_unchanged': before == after, 'final_answers': answers,
                  'actor_calls': actor.actor_calls, 'judge_calls': judge.judge_calls,
                  'wall_seconds': time.monotonic() - started, 'usage': actor.usage,
                  'cost': {'actor_provider_reported_usd': (sum(float(u['cost']) for u in actor.usage)
                            if actor.usage and all(u.get('cost') is not None for u in actor.usage) else None),
                           'judge': 'Provider cost only; absent cost is unknown, not estimated'},
                  'actor_spend': actual_spend(plan['spend_root']),
                  'usage_note': 'Provider-reported actual costs only; missing costs are unknown. In-flight calls can overshoot the stop threshold.'}
        save(cell / 'result.json', record)
        if agent:
            agent.close()
        db.close()
        reset_secret_scope(token)
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    # Anonymous stdin pipe only; never argv, environment, credential files or auth-store copies.
    from evals.intent_v1.live_transport import worker_network_guard
    sys.addaudithook(worker_network_guard)
    try:
        raise SystemExit(run(Path(sys.argv[1]), json.load(sys.stdin)))
    except BaseException as exc:
        target = Path(sys.argv[1]) / 'result.json'
        if not target.exists():
            save(target, {'mode': 'live', 'passed': False, 'infrastructure_invalid': True,
                          'error_type': type(exc).__name__})
        if isinstance(exc, SystemExit):
            raise
        raise SystemExit(1) from None
