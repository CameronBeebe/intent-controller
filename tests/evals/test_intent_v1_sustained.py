"""Frozen sustained-work contracts; no inference services."""
import json

import httpx
import pytest

from evals.intent_v1.run import child_env, config, save
from evals.intent_v1 import live_worker, sustained


def test_continuation_is_the_only_cd_switch():
    a, b, c, d = (config(arm, continuation_comparison=True) for arm in 'ABCD')
    assert config('C')['max_continuations'] == 0  # historical tasks without D
    assert [cfg['enabled'] for cfg in (a, b, c, d)] == [False, True, True, True]
    assert [cfg['judge_enabled'] for cfg in (a, b, c, d)] == [False, False, True, True]
    assert not any(cfg['semantic_continuation'] for cfg in (a, b, c))
    assert d == {**c, 'semantic_continuation': True}
    assert d['max_continuations'] == 2


@pytest.mark.parametrize('case,arm', [('kit_handoff', 'C'), ('kit_handoff', 'D'), ('room_hold', 'D')])
def test_scripted_stop_in_live_worker(tmp_path, monkeypatch, case, arm):
    """Identical kit script until the real gate returns or schedules another call."""
    from evals.intent_v1.worker import Script, call, response
    from evals.intent_v1.semantic import task
    from evals.intent_v1.live_auth import JUDGE_MODEL, DEFAULT_MODEL
    for name in ('home', 'hermes', 'runtime', 'workspace', 'tmp', 'cache', 'config'):
        (tmp_path / name).mkdir()
    for k, v in child_env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    cfg = {**config(arm, continuation_comparison=True), 'model': JUDGE_MODEL, 'max_assessments': None}
    save(tmp_path / 'cell.json', {'case': case, 'arm': arm, 'actor_model': DEFAULT_MODEL, 'config': cfg,
         'actor_budget_usd': '5', 'spend_root': str(tmp_path), 'max_iterations': 32,
         'max_output_tokens': 1024, 'deadline_epoch': 9999999999})
    script = Script(tmp_path, cfg)
    source, *artifacts = sustained.FIXTURES[case]
    expected = sustained.EXPECTED[case]

    def read(name):
        return lambda: response(calls=[call('read_file', {'path': name}, f'r-{script.actor_calls}')])

    def correct(name):
        return [read(name), lambda: script.write(name, json.dumps(expected[name])), read(name)]

    kit = case == 'kit_handoff'
    script.steps = [script.read_step(source, 'Prepare the handoff' if kit else 'Correct the desk card'),
                    *correct(artifacts[0])]
    if kit:
        script.steps += [script.finish, response('Packing is corrected; pickup and handoff remain.'),
                         *correct(artifacts[1]), *correct(artifacts[2]), script.finish, response('Handoff ready.')]
    else:
        script.steps += [lambda: response(calls=[script.control(transition='blocked')]),
                         response('Desk card verified; print order unchanged pending the signed date.')]

    def actor_create(actor, **request):
        script.agent = actor.agent
        answer = script.create(**request)
        actor.actor_calls = script.actor_calls
        return answer

    monkeypatch.setattr(live_worker.Actor, 'create', actor_create)

    def send(_client, request, **kwargs):
        body = json.loads(request.content)
        state = json.loads(body['state'])
        if 'task' in state:  # Post-run evaluator plumbing only; never a semantic-quality test.
            assert state['task'] == task(case)
            high = {'evidence_sufficient', 'report_accurate', 'constraints_followed', 'outcome_achieved'}
        elif state.get('phase') == 'completion':
            high = {'evidence', 'coverage', 'consistent'}
            if not kit:
                high.add('blocked')
            elif json.loads((tmp_path / 'workspace/handoff.json').read_text())['status'] != 'ready':
                high.update(('incomplete', 'actionable'))
            else:
                high.add('complete')
        else:
            high = {'coverage', 'consistent'}
        return httpx.Response(200, request=request, json={'model': JUDGE_MODEL,
            'answers': {k: {'type': 'noul', 'noul': .99 if k in high else .01} for k in body['questions']},
            'usage': {'input_tokens': 1, 'output_tokens': 0}})

    monkeypatch.setattr(httpx.Client, 'send', send)
    code = live_worker.run(tmp_path, {'actor': 'offline-fixture-key', 'judge': 'offline-fixture-key'})
    record = json.loads((tmp_path / 'result.json').read_text())
    events = [json.loads(line) for line in (tmp_path / 'events.jsonl').read_text().splitlines()]
    stops = [e for e in events if e['event'] == 'ordinary_stop']
    assert [e['semantic_continuation_triggered'] for e in stops] == ([True, False] if kit and arm == 'D' else [False]), (events, record)
    assert code == (1 if arm == 'C' else 0), record
    assert sum(e['event'] == 'user_turn' for e in events) == 1
    assessments = json.loads((tmp_path / 'intent.json').read_text())['assessments']
    completion = [r for r in assessments if r.get('phase') == 'completion']
    assert completion[0]['decision'] == ('CONTINUE_NOW' if kit else 'BLOCKED')
    if kit and arm == 'D':
        assert completion[0]['id'] == stops[0]['assessment_id']
        assert stops[1]['since_continuation_at_call'] == stops[0]['actor_call']
        assert set(stops[1]['facts_gained']) == {'saved:pickup.json', 'read_back:pickup.json', 'saved:handoff.json'}
    assert record['judge_calls'] > 0 and record['evaluator_calls'] == 1
