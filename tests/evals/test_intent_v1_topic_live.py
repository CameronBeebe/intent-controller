"""Real loop and files; synthetic actor/Jev transports only."""
import json

import pytest


def _http_fixture(cell):
    """Child entry point: preserve the real worker, file dispatch and HTTP adapters."""
    from unittest.mock import patch
    import httpx
    from evals.intent_v1 import live_worker
    from evals.intent_v1.live_auth import ACTOR_BASE, JUDGE_BASE
    from evals.intent_v1.worker import offline_guard
    import sys

    blocked = []
    def no_network(event, args):
        try:
            offline_guard(event, args)
        except RuntimeError:
            blocked.append(event)
            raise

    from evals.intent_v1.live_transport import worker_network_guard
    sys.addaudithook(no_network)
    sys.addaudithook(worker_network_guard)
    enabled = json.loads((cell / 'cell.json').read_text())['config']['enabled']
    seen = []
    def send(client, request, **kwargs):
        seen.append((request.method, str(request.url)))
        body = json.loads(request.content)
        if str(request.url) == ACTOR_BASE + '/chat/completions':
            index = sum(url == str(request.url) for _, url in seen)
            assert request.headers['Authorization'] == 'Bearer synthetic-actor'
            turn, phase = divmod(index - 1, 3 if enabled else 2)
            args = {'mode': ('create', 'update')[turn], 'revision': turn, 'transition': 'act',
                    'proposal': {'update_type': 'replacement', 'fields': {
                        'outcome': ('Summarize election', 'Review Fibonacci read-only')[turn],
                        'verification': 'Inspect fixture lines', 'constraints': 'No network',
                        'boundaries': 'Only this workspace', 'questions': ''}}}
            calls = ([('intent_control', args), ('read_file', {'path': ('election.txt', 'fibonacci.py')[turn]})]
                     if phase == 0 else [('intent_control', {'mode': 'retain', 'revision': turn + 1,
                                                             'transition': 'finish'})] if phase == 1 else [])
            if not enabled:
                calls = [(name, value) for name, value in calls if name != 'intent_control']
            msg = {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': f'{name}-{index}', 'type': 'function', 'function': {'name': name,
                 'arguments': json.dumps(args)}} for name, args in calls]} if calls else {
                'role': 'assistant', 'content': ('Elm won with seven votes against four.',
                    'For zero, this returns one. The base case should return the input n.')[turn]}
            if turn == 1 and phase == 0:
                msg['content'] = 'I will check the Fibonacci base case.'
            data = {'id': f'synthetic-{index}', 'model': body['model'], 'created': 1, 'object': 'chat.completion',
                    'choices': [{'index': 0, 'message': msg, 'finish_reason': 'tool_calls' if calls else 'stop'}],
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110, 'cost': .001}}
        else:
            assert str(request.url) == JUDGE_BASE + '/systemone'
            assert request.headers['Authorization'] == 'Bearer synthetic-judge'
            if 'outcome_achieved' in body['questions']:
                state = json.loads(body['state'])
                assert [t['arguments']['path'] for t in state['turns'][1]['tools']] == ['fibonacci.py']
                assert state['turns'][1]['assistant_prose'] == [
                    'I will check the Fibonacci base case.',
                    'For zero, this returns one. The base case should return the input n.']
            data = {'model': 'jev-1.14.0', 'answers': {name: {'type': 'noul',
                    'noul': .01 if name == 'internal_contradiction' else .99} for name in body['questions']},
                    'usage': {'input_tokens': 100, 'output_tokens': 0}}
        return httpx.Response(200, request=request, json=data)

    with patch.object(httpx.Client, 'send', send):
        code = live_worker.run(cell, {'actor': 'synthetic-actor', 'judge': 'synthetic-judge'})
    # Every attempted exchange must have passed through a declared, recorded role.
    assert not blocked, blocked
    record = json.loads((cell / 'result.json').read_text())
    assert len(seen) == record['actor_calls'] + record['judge_calls'] + record['evaluator_calls']
    assert all(method == 'POST' and url in (ACTOR_BASE + '/chat/completions',
               JUDGE_BASE + '/systemone') for method, url in seen), seen
    return code


@pytest.mark.parametrize('arm', ['A', 'B', 'C'])
def test_topic_swap_live_worker_real_http_boundaries(tmp_path, arm):
    from evals.intent_v1.run import ROOT, child_env, config, save
    from evals.intent_v1.semantic import task
    from evals.intent_v1.tranche import manifest_for
    import subprocess
    import sys
    for folder in ('home', 'hermes', 'runtime', 'workspace', 'tmp', 'cache', 'config'):
        (tmp_path / folder).mkdir()
    cfg = {**config(arm), 'model': 'jev-1.14.0', 'max_assessments': None}
    plan = {'case': 'topic_swap', 'task': task('topic_swap'), 'arm': arm, 'config': cfg,
            'actor_model': 'anthropic/claude-haiku-5.5',
            'spend_root': str(tmp_path), 'actor_budget_usd': '5', 'max_iterations': 32,
            'max_output_tokens': 1024, 'deadline_epoch': 9999999999}
    save(tmp_path / 'cell.json', plan)
    run = subprocess.run([sys.executable, '-m', __name__, str(tmp_path)],
        cwd=ROOT, env=child_env(tmp_path), capture_output=True, text=True, timeout=45)
    assert (tmp_path / 'result.json').exists(), run.stderr
    record = json.loads((tmp_path / 'result.json').read_text())
    assert run.returncode == 0, (record, run.stderr)
    calls = [json.loads(line)['wire_request'] for line in (tmp_path / 'actor_requests.jsonl').read_text().splitlines()]
    assert manifest_for('topic_swap') in json.dumps(calls[0]['messages']).replace('\\n', '\n')
    count = 4 if arm == 'A' else 6
    assert len(calls) == record['actor_calls'] == count
    tools = {'read_file', 'write_file', 'patch'}
    if arm != 'A':
        tools.add('intent_control')
    assert all({t['function']['name'] for t in c['tools']} == tools for c in calls)
    assert record['actor_spend'] == {'provider_reported_usd': f'0.00{count}', 'known_calls': count, 'unknown_calls': 0}
    assert (record['judge_calls'] > 0) is (arm == 'C') and record['evaluator_calls'] == 1
    assert json.loads((tmp_path / 'evaluator_requests.jsonl').read_text())['role'] == 'evaluator'
    assert (tmp_path / 'judge_requests.jsonl').exists() is (arm == 'C')
    assert record['fixture_unchanged'] and record['behavioral_contract_passed'] and all(record['checks'].values())
    events = [json.loads(line) for line in (tmp_path / 'events.jsonl').read_text().splitlines()]
    boundary = next(i for i, e in enumerate(events) if e['event'] == 'steering_boundary')
    assert events[boundary]['boundary'] == 'after_turn_0_return_before_turn_1'
    assert events[boundary - 1]['event'] == 'turn_end' and events[boundary + 1]['event'] == 'user_turn'


def test_live_driver_freezes_task_and_continues_after_behavioral_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from evals.intent_v1 import live_driver
    from evals.intent_v1.run import main, save
    from evals.intent_v1.semantic import task
    monkeypatch.setattr(live_driver, 'preflight', lambda *a, **k: (
        {'ready': True, 'catalog': {'canonical_slug': 'fixture'}, 'rates': {}},
        {'actor': 'fixture', 'judge': 'fixture'}))
    attempted = []
    def run(command, **kwargs):
        from pathlib import Path
        cell = Path(command[-1])
        plan = json.loads((cell.parent / 'plan.json').read_text())
        assert all(c['task'] == task('topic_swap') for c in plan['cells'])
        assert all(c['config']['semantic_continuation'] is False for c in plan['cells'])
        assert plan['fixtures']['fibonacci.py']
        attempted.append(cell.name)
        passed = cell.name.endswith('C')
        save(cell / 'result.json', {'passed': passed, 'harness_valid': True,
            'infrastructure_invalid': False, 'behavioral_contract_passed': passed,
            'evaluator': {'verdict': 'pass' if passed else 'fail'}})
        return SimpleNamespace(returncode=0 if passed else 1)
    monkeypatch.setattr(live_driver.subprocess, 'run', run)
    monkeypatch.setattr(live_driver.subprocess, 'check_output', lambda *a, **k: 'fixture-sha')
    out = tmp_path / 'run'
    assert main(['--live', '--cases', 'topic_swap', '--out', str(out),
                 '--spend-root', str(tmp_path), '--actor-budget-usd', '5']) == 1
    assert attempted == ['topic_swap-A', 'topic_swap-B', 'topic_swap-C']
    assert json.loads((out / 'summary.json').read_text())['harness_passed'] == 3


if __name__ == '__main__':
    from pathlib import Path
    import sys
    raise SystemExit(_http_fixture(Path(sys.argv[1])))
