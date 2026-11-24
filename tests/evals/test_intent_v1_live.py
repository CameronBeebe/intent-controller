"""Live safety plumbing tests: transports stay synthetic and offline."""
import json


import pytest


def test_actual_costs_only_dedup_unknown_and_stop(tmp_path):
    from evals.intent_v1.live import actual_spend, check_spend, LiveStop, ResourceStop
    cell = tmp_path / 'cell'
    cell.mkdir()
    (cell / 'cell.json').write_text(json.dumps({'actor_budget_usd': '5'}))
    path = cell / 'actor_responses.jsonl'
    path.write_text(json.dumps({'index': 1, 'response': {'id': 'provider-1', 'usage': {'cost': '0.01'}}}) + '\n')
    assert actual_spend(tmp_path) == {'provider_reported_usd': '0.01', 'known_calls': 1, 'unknown_calls': 0}
    check_spend(tmp_path, '5')
    copy = tmp_path / 'copied-cell'
    copy.mkdir()
    (copy / 'cell.json').write_text((cell / 'cell.json').read_text())
    (copy / 'actor_responses.jsonl').write_text(path.read_text())
    assert actual_spend(tmp_path)['known_calls'] == 1
    (copy / 'actor_responses.jsonl').unlink()
    path.write_text(path.read_text() + json.dumps({'index': 2, 'response': {'id': 'provider-2', 'usage': {}}}) + '\n')
    assert actual_spend(tmp_path)['unknown_calls'] == 1
    assert actual_spend(tmp_path)['provider_reported_usd'] == '0.01'
    path.write_text(json.dumps({'index': 1, 'response': {'id': 'provider-1', 'usage': {'cost': '5'}}}) + '\n')
    with pytest.raises(ResourceStop, match='actual'):
        check_spend(tmp_path, '5')
    path.write_text(json.dumps({'index': 1, 'response': {'id': 'provider-1', 'usage': {'cost': '-1'}}}) + '\n')
    with pytest.raises(LiveStop):
        check_spend(tmp_path, '5')
    with pytest.raises((ValueError, LiveStop)):
        actual_spend(tmp_path)


def test_preflight_checks_auth_without_inference_and_never_returns_secrets(tmp_path, monkeypatch):
    from evals.intent_v1 import live_auth
    seen = []
    def get(url, **kwargs):
        seen.append(url)
        class Response:
            status_code = 200
            def json(self):
                if url.endswith('/key'):
                    return {'data': {'limit_remaining': 2, 'label': 'private'}}
                if 'typesafe' in url:
                    return {'models': [{'name': 'jev-latest'}]}
                return {'data': [{'id': live_auth.DEFAULT_MODEL, 'canonical_slug': 'pinned-test',
                    'supported_parameters': ['tools'], 'pricing': {'prompt': '.000001', 'completion': '.000002'}}]}
        return Response()
    monkeypatch.setattr(live_auth, 'resolve_credentials', lambda home, **kwargs: (
        {'actor': 'secret-actor', 'judge': 'secret-judge'}, {'home': str(home), 'actor_source': 'pool'}))
    report, credentials = live_auth.preflight(tmp_path, get=get)
    assert report['ready'] and len(seen) == 3
    assert all('/chat/' not in url and '/systemone' not in url for url in seen)
    assert credentials['actor'] == 'secret-actor'
    assert 'secret-actor' not in json.dumps(report)
    assert '"label"' not in json.dumps(report)


def test_live_transport_captures_actual_cost_without_reservations(tmp_path):
    from evals.intent_v1.live_transport import Actor, fixture_dispatch

    key = 'secret-key-test'
    class HTTP:
        def post(self, url, **kwargs):
            assert kwargs['json']['max_tokens'] == 512
            class Response:
                status_code = 200
                def raise_for_status(self):
                    pass
                def json(self):
                    return {'id': 'test', 'created': 1, 'object': 'chat.completion', 'model': 'test',
                        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'title'}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12, 'cost': .00001}}
            return Response()
    plan = {'actor_model': 'anthropic/claude-haiku-5.5', 'rates': {'input': '.000001', 'output': '.0000025', 'request': '0'},
            'spend_root': str(tmp_path), 'actor_budget_usd': '1', 'max_output_tokens': 512,
            'max_input_bound': 131072, 'max_iterations': 2, 'deadline_epoch': 9999999999}
    actor = Actor(tmp_path, plan, key, http=HTTP())
    assert actor.create(messages=[{'role': 'user', 'content': 'title'}], tools=[]).choices[0].message.content == 'title'
    assert actor.usage[0]['cost'] == .00001
    assert key not in ''.join(p.read_text() for p in tmp_path.glob('*.jsonl'))
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'notes.txt').write_text('title')
    called = []
    dispatch = fixture_dispatch(workspace, lambda *a, **k: called.append(a) or 'ok')
    assert 'error' in dispatch('read_file', {'path': str(tmp_path / 'ledger')})
    assert dispatch('read_file', {'path': str(workspace / 'notes.txt')}) == 'ok'
    assert 'error' in dispatch('terminal', {'command': 'curl example.com'})
    assert len(called) == 1


def test_failed_actor_http_attempt_has_unknown_cost_and_no_retry(tmp_path):
    from evals.intent_v1.live_transport import Actor
    from evals.intent_v1.live import LiveStop
    class HTTP:
        calls = 0
        def post(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError('do not persist secret-key')
    plan = {'actor_model': 'anthropic/claude-haiku-5.5', 'rates': {'input': '.000001', 'output': '.0000025', 'request': '0'},
            'spend_root': str(tmp_path), 'actor_budget_usd': '.01', 'max_output_tokens': 512,
            'max_input_bound': 131072, 'max_iterations': 4, 'deadline_epoch': 9999999999}
    http = HTTP()
    actor = Actor(tmp_path, plan, 'secret-key', http=http)
    with pytest.raises(LiveStop, match='cost may be unknown'):
        actor.create(messages=[{'role': 'user', 'content': 'test'}], tools=[])
    assert http.calls == 1
    assert json.loads((tmp_path / 'actor_failures.jsonl').read_text())['index'] == 1
    assert 'secret-key' not in (tmp_path / 'actor_failures.jsonl').read_text()


def test_live_cli_requires_explicit_cap_before_any_network(tmp_path):
    from tests.evals.test_intent_v1_readiness import run_driver
    result = run_driver(tmp_path / 'no-cap', '--live')
    assert result.returncode != 0 and 'actor-budget-usd' in result.stderr
    assert not (tmp_path / 'no-cap').exists()


@pytest.mark.parametrize('limited', [False, True])
def test_live_worker_uses_real_loop_not_scripted_answer(tmp_path, monkeypatch, limited):
    from evals.intent_v1 import live_worker
    from evals.intent_v1.run import child_env, config, save
    import httpx
    for folder in ('home', 'hermes', 'runtime', 'workspace', 'tmp', 'cache', 'config'):
        (tmp_path / folder).mkdir()
    for key, value in child_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    cfg = config('A')
    cfg.update(model='jev-1.14.0', max_assessments=None)
    plan = {'case': 'overhead', 'arm': 'A', 'config': cfg, 'actor_model': 'anthropic/claude-haiku-5.5',
            'rates': {'input': '.000001', 'output': '.0000025', 'request': '0'},
            'spend_root': str(tmp_path), 'actor_budget_usd': '1', 'max_output_tokens': 512,
            'max_input_bound': 131072, 'max_iterations': 4, 'deadline_epoch': 9999999999}
    if limited:
        plan['max_iterations'] = 1
    save(tmp_path / 'cell.json', plan)
    def post(self, url, **kwargs):
        data = {'id': 'test', 'created': 1, 'object': 'chat.completion', 'model': 'test',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'Not the fixture title'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
        if limited:
            data['choices'][0]['message'] = {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'read-1', 'type': 'function', 'function': {'name': 'read_file',
                    'arguments': json.dumps({'path': 'notes.txt'})}}]}
            data['choices'][0]['finish_reason'] = 'tool_calls'
        return httpx.Response(200, json=data, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx.Client, 'post', post)
    def send(self, request, **kwargs):
        body = json.loads(request.content)
        return httpx.Response(200, request=request, json={'model': 'jev-1.14.0',
            'answers': {name: {'type': 'noul', 'noul': .99 if name == 'evidence_sufficient' else .01}
                        for name in body['questions']},
            'usage': {'input_tokens': 100, 'output_tokens': 0}})
    monkeypatch.setattr(httpx.Client, 'send', send)
    assert live_worker.run(tmp_path, {'actor': 'synthetic-key', 'judge': 'synthetic-key'}) == 1
    result = json.loads((tmp_path / 'result.json').read_text())
    assert result['mode'] == 'live' and not result['behavioral_contract_passed']
    if limited:
        assert result['outcome'] == 'censored' and result['infrastructure_invalid'] is False
        assert result['final_answers'] == [] and result['evaluator_calls'] == 0
        receipts = json.loads((tmp_path / 'tool_receipts.json').read_text())
        assert any(m.get('tool_call_id') == 'read-1' for m in receipts)
        assert result['actor_calls'] == 1
        assert (tmp_path / 'partial_request.json').exists()
        return
    assert result['final_answers'] == ['Not the fixture title', 'Not the fixture title'], (tmp_path / 'events.jsonl').read_text()
    assert result['actor_calls'] == 2 and result['judge_calls'] == 0
    assert result['evaluator_calls'] == 1
    assert result['evaluator']['verdict'] == 'fail'
    assert (tmp_path / 'actor_requests.jsonl').exists()


def test_driver_continues_after_censored_cell_with_frozen_limits(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from evals.intent_v1 import live_driver
    from evals.intent_v1.run import main
    monkeypatch.setattr(live_driver, 'preflight', lambda *a, **k: (
        {'ready': True, 'catalog': {'canonical_slug': 'test'},
         'rates': {'input': '.000001', 'output': '.000002', 'request': '0'}}, {}))
    calls = []
    def run(command, **kwargs):
        cell = __import__('pathlib').Path(command[-1])
        calls.append(cell)
        plan = json.loads((cell / 'cell.json').read_text())
        assert plan['max_iterations'] == 32
        assert kwargs['timeout'] == 240
        assert plan['deadline_epoch'] > 0
        if len(calls) == 1:
            raise __import__('subprocess').TimeoutExpired(command, kwargs['timeout'])
        (cell / 'result.json').write_text(json.dumps({'passed': False, 'harness_valid': True,
            'infrastructure_invalid': False, 'outcome': 'complete', 'behavioral_contract_passed': False}))
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(live_driver.subprocess, 'run', run)
    # Avoid intercepting check_output's own use of subprocess.run.
    monkeypatch.setattr(live_driver.subprocess, 'check_output', lambda *a, **k: 'test')
    assert main(['--out', str(tmp_path / 'run'), '--live', '--cases', 'overhead',
                 '--actor-budget-usd', '5', '--spend-root', str(tmp_path)]) == 1
    summary = json.loads((tmp_path / 'run' / 'summary.json').read_text())
    assert len(calls) == summary['count'] == 3
    assert summary['cells'][0]['outcome'] == 'censored'
    assert summary['cells'][0]['infrastructure_invalid'] is False


def test_worker_network_guard_fences_even_same_provider_catalog_get():
    from evals.intent_v1.live import LiveStop
    from evals.intent_v1.live_transport import provider_exchange, worker_network_guard
    import threading

    def probe():
        worker_network_guard('socket.getaddrinfo', ('openrouter.ai', 443))

    with pytest.raises(LiveStop):
        probe()
    with provider_exchange():
        probe()
        # Background warmups are not part of a captured synchronous request.
        blocked = []
        def warmup():
            try:
                probe()
            except LiveStop:
                blocked.append(True)
        thread = threading.Thread(target=warmup)
        thread.start()
        thread.join()
        assert blocked == [True]
    with pytest.raises(LiveStop):
        probe()


def test_judge_has_no_count_cap_and_persists_connect_failure(tmp_path, monkeypatch):
    from evals.intent_v1.live_transport import Judge
    import httpx
    def fail(*args, **kwargs):
        raise httpx.ConnectError('secret-judge')
    monkeypatch.setattr(httpx.Client, 'send', fail)
    judge = Judge(tmp_path, {'deadline_epoch': 9999999999}, 'secret-judge')
    judge.judge_calls = 20
    with pytest.raises(httpx.ConnectError):
        with judge.client(timeout=1, follow_redirects=False, trust_env=False) as client:
            with client.stream('POST', 'https://api.typesafe.ai/v1/systemone', content=b'{}'):
                pass
    assert judge.judge_calls == 21
    failure = (tmp_path / 'judge_responses.jsonl').read_text()
    assert 'ConnectError' in failure and 'secret-judge' not in failure
