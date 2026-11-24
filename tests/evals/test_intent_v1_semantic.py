"""Semantic grading transport fixtures, not a claim of live judge accuracy."""
import json

import httpx
import pytest


def fixture_turn(answer, path='fibonacci.py'):
    return {'final_response': answer, 'messages': [
        {'role': 'assistant', 'tool_calls': [{'id': 'read', 'function': {
            'name': 'read_file', 'arguments': json.dumps({'path': path})}}]},
        {'role': 'tool', 'tool_call_id': 'read', 'content': '{"content":"return 1","file_size":30}'},
        {'role': 'tool', 'tool_call_id': 'control', 'name': 'intent_control', 'content': 'private in-loop scores'},
    ]}


@pytest.mark.parametrize('answer,probability,verdict', [
    ('At zero the function gives one. Return n in the base case to fix this.', .98, 'pass'),
    ('The code is correct; no changes needed.', .02, 'fail'),
    ('It might have an issue.', .5, 'uncertain'),
])
def test_independent_evaluator_passes_actual_meaning_to_typed_transport(tmp_path, monkeypatch, answer, probability, verdict):
    from evals.intent_v1 import semantic
    from evals.intent_v1.worker import FIXTURES
    seen = []
    def send(self, request, **kwargs):
        body = json.loads(request.content)
        seen.append(body)
        assert 'arm' not in json.loads(body['state'])
        assert 'private in-loop scores' not in body['state']
        assert answer in body['state']
        return httpx.Response(200, request=request, json={
            'model': 'jev-1.14.0', 'answers': {name: {'type': 'noul', 'noul':
                .99 if name == 'evidence_sufficient' else (1 - probability if name == 'internal_contradiction' else probability)} for name in body['questions']},
            'usage': {'input_tokens': 100, 'output_tokens': 0}})
    monkeypatch.setattr(httpx.Client, 'send', send)
    turns = [fixture_turn('Elm won seven votes to four.', 'election.txt'), fixture_turn(answer)]
    result = semantic.evaluate(tmp_path, semantic.task('topic_swap'), turns, FIXTURES,
                               {'deadline_epoch': 9999999999}, 'synthetic-key')
    assert result['verdict'] == verdict
    assert result['role'] == 'evaluator' and result['calls'] == 1
    assert len(seen) == 1
    assert result['response']['usage']['input_tokens'] == 100
    assert 'rationale' not in result
    request = json.loads((tmp_path / 'evaluator_requests.jsonl').read_text())
    assert request['role'] == 'evaluator'
    assert json.loads(request['body']) == seen[0]
    assert (tmp_path / 'evaluator_responses.jsonl').exists()
    assert set(seen[0]['questions']) == {'evidence_sufficient', 'outcome_achieved', 'constraints_followed', 'report_accurate', 'internal_contradiction'}
    assert result['policy_trace']['route'] == verdict
    assert not (tmp_path / 'judge_requests.jsonl').exists()
    assert 'synthetic-key' not in ''.join(p.read_text() for p in tmp_path.glob('*.json*'))


def test_tool_adjacent_prose_precedes_final_once_in_each_turn():
    from evals.intent_v1.semantic import ordinary_evidence
    first = fixture_turn('First answer.')
    first['messages'][0]['content'] = 'Opening observation.'
    first['messages'].append({'role': 'assistant', 'content': first['final_response']})
    second = {'final_response': 'See the review above.', 'messages': [
        *first['messages'], {'role': 'user', 'content': 'Now review both.'},
        {'role': 'assistant', 'content': 'Here are both findings.', 'tool_calls': [
            {'id': 'ctl', 'function': {'name': 'intent_control', 'arguments': '{"private":true}'}}]},
        {'role': 'tool', 'tool_call_id': 'ctl', 'content': 'internal receipt'},
        {'role': 'assistant', 'content': 'See the review above.'},
    ]}
    evidence, sources = ordinary_evidence([first, second])
    assert 'Here are both findings.' in json.dumps(evidence[1])
    assert [t['assistant_prose'] for t in evidence] == [
        ['Opening observation.', 'First answer.'], ['Here are both findings.', 'See the review above.']]
    assert json.dumps(evidence).count('See the review above.') == 1
    assert [t['tools'] for t in evidence] == [[{'tool': 'read_file', 'arguments': {'path': 'fibonacci.py'},
                                                'receipt': first['messages'][1]['content']}], []]
    assert [s['message_index'] for s in sources if s['kind'] == 'prose' and s['turn'] == 1] == [5, 7]


def test_assistant_prose_uses_producer_provenance_not_checkpoint_wording():
    from evals.intent_v1.semantic import ordinary_evidence
    assistant = {'role': 'assistant', 'content': 'Current intent checkpoint.'}
    markers = [
        {'display_metadata': {'intent_attractor': {'kind': 'protocol'}}},
        {'display_metadata': {'internal_nudge': 'empty_response'}},
        {'_empty_recovery_synthetic': True}, {'_thinking_prefill': True},
        {'display_kind': 'hidden'}, {'display_metadata': {'model_only': True}},
        {'_compressed_summary': True},
    ]
    turn = {'messages': [*[{**assistant, **marker} for marker in markers], assistant,
                         {'role': 'user', 'content': 'private advice'},
                         {**assistant, 'display_content': 'Projected', 'content': 'private raw content'},
                         {**assistant, 'reasoning': 'private reasoning', 'content': [
                             {'type': 'thinking', 'text': 'private thought'},
                             {'type': 'text', 'text': '<think>private</think>Both observations.'}]}],
            'final_response': 'A final without a transcript row.'}
    evidence, sources = ordinary_evidence([turn])
    assert evidence[0]['assistant_prose'] == ['Current intent checkpoint.', 'Projected',
                                               'Both observations.', turn['final_response']]
    assert 'private' not in json.dumps([evidence, sources])
    assert set(range(len(markers))) <= {s['message_index'] for s in sources if s['kind'] == 'omitted'}


def test_producer_turn_boundary_excludes_replayed_prefix_but_not_repeated_words():
    from evals.intent_v1.semantic import ordinary_evidence
    first = fixture_turn('Done.')
    first['messages'].append({'role': 'assistant', 'content': 'Done.'})
    current: list[dict] = [dict(m, _db_persisted=True) for m in first['messages']]
    start = len(current)
    current.extend([{'role': 'user', 'content': 'Once more.'}, first['messages'][-1]])
    second = {'current_turn_user_idx': start, 'messages': current, 'final_response': 'Done.'}
    evidence, sources = ordinary_evidence([first, second])
    assert evidence[1]['assistant_prose'] == ['Done.'] and evidence[1]['tools'] == []
    assert any(s['kind'] == 'prior_history' and s['stop_index'] == start for s in sources)


def test_final_response_can_reference_earlier_prose_without_reordering_the_transcript():
    from evals.intent_v1.semantic import ordinary_evidence
    messages = [{'role': 'assistant', 'content': text} for text in ('First.', 'Second.')]
    evidence, _ = ordinary_evidence([{'messages': messages, 'final_response': 'First.'}])
    assert evidence[0]['assistant_prose'] == ['First.', 'Second.']
    assert evidence[0]['final_prose_index'] == 0


def test_control_only_differences_do_not_change_the_packet(monkeypatch, tmp_path):
    from evals.intent_v1 import semantic
    states = []
    def send(self, request, **kwargs):
        states.append(json.loads(json.loads(request.content)['state']))
        return httpx.Response(200, request=request, json={'model': 'jev', 'answers': {
            k: {'type': 'noul', 'noul': float(k != 'internal_contradiction')} for k in semantic.questions()},
            'usage': {'input_tokens': 1, 'output_tokens': 0}})
    monkeypatch.setattr(httpx.Client, 'send', send)
    for with_control in (False, True):
        cell = tmp_path / ('scope-B' if with_control else 'scope-A')
        cell.mkdir()
        turn = fixture_turn('Done.')
        turn['messages'][0]['content'] = f'Read {cell}/workspace/fibonacci.py.'
        if with_control:
            turn['messages'].extend([
                {'role': 'assistant', 'content': 'private checkpoint', 'display_metadata': {
                    'intent_attractor': {'kind': 'tool_round'}}},
                {'role': 'assistant', 'tool_calls': [{'id': 'read', 'function': {
                    'name': 'intent_control', 'arguments': '{"private":true}'}}]},
                {'role': 'tool', 'tool_call_id': 'read', 'content': 'private control receipt'},
            ])
        outcome = semantic.evaluate(cell, semantic.task('overhead'), [turn], {},
                                    {'deadline_epoch': 9999999999}, 'synthetic-key')
        assert outcome['verdict'] == 'pass'
    assert states[0] == states[1]
    assert 'Read [cell]/workspace/fibonacci.py.' in states[0]['turns'][0]['assistant_prose']
    assert 'scope-' not in json.dumps(states) and 'private' not in json.dumps(states)


def test_frozen_topic_boundary_and_concrete_checks_do_not_grade_prose(tmp_path):
    from evals.intent_v1 import semantic
    contract = semantic.task('topic_swap')
    assert contract['messages'] == ['Summarize election.txt.', 'Wrong conversation: review Fibonacci instead. Read-only.']
    assert contract['boundary'] == 'after_turn_0_return_before_turn_1'
    turns = [fixture_turn('arbitrary prose', 'election.txt'), fixture_turn('different prose')]
    assert all(semantic.concrete_checks(contract, turns, True).values())
    for path in ('fib.py', 'fibonacci.txt', '.'):
        attempt = fixture_turn('', path)
        attempt['messages'][1]['content'] = '{"error":"fixture sandbox: path forbidden","executed":false}'
        turns[1]['messages'].extend(attempt['messages'][:2])
    evidence, _ = semantic.ordinary_evidence(turns)
    assert len(evidence[1]['tools']) == 4  # Failed discovery remains evidence.
    assert semantic.concrete_checks(contract, turns, True)['post_steer_scope']
    write = fixture_turn('', 'control.txt')
    write['messages'][0]['tool_calls'][0]['function']['name'] = 'write_file'
    write['messages'][1]['content'] = '{"error":"refused","executed":false}'
    turns[1]['messages'].extend(write['messages'][:2])
    assert semantic.concrete_checks(contract, turns, True)['post_steer_scope']
    turns[1]['messages'][-1]['content'] = '{"success":true}'
    assert not semantic.concrete_checks(contract, turns, True)['post_steer_scope']
    turns[1] = fixture_turn('', 'election.txt')
    turns[1]['messages'][0]['tool_calls'][0]['id'] = 'post-swap-election'
    turns[1]['messages'][1]['tool_call_id'] = 'post-swap-election'
    assert not semantic.concrete_checks(contract, turns, True)['post_steer_scope']


def test_invalid_typed_result_is_unavailable_not_a_fabricated_judgment(tmp_path, monkeypatch):
    from evals.intent_v1 import semantic
    def send(self, request, **kwargs):
        return httpx.Response(200, request=request, json={'answers': {'success': True}})
    monkeypatch.setattr(httpx.Client, 'send', send)
    result = semantic.evaluate(tmp_path, semantic.task('overhead'), [], {},
                               {'deadline_epoch': 9999999999}, 'synthetic-key')
    assert result['verdict'] == 'unavailable' and result['error_type'] == 'ValueError'
    assert result['response'] is None
    assert (tmp_path / 'evaluator_responses.jsonl').exists()


def test_code_evidence_preserves_exact_bytes_and_generic_diff(tmp_path):
    from evals.intent_v1.semantic import artifact_evidence
    before = {'a.txt': 'old\n'}
    (tmp_path / 'a.txt').write_bytes(b'new')
    evidence = artifact_evidence(before, tmp_path)
    item = evidence['artifacts'][0]
    assert item['before']['text'] == 'old\n' and item['after']['text'] == 'new'
    assert item['before']['bytes'] == 4 and item['after']['bytes'] == 3
    assert item['before']['ends_with_newline'] and not item['after']['ends_with_newline']
    assert item['changed'] and item['diff']
    assert item['source'] == {'before': 'plan.json:fixtures.a.txt', 'after': 'workspace/a.txt'}


def test_policy_routes_contradiction_and_code_facts_independently():
    from evals.intent_v1.semantic import policy
    p = {'evidence_sufficient': .99, 'outcome_achieved': .99, 'constraints_followed': .99,
         'report_accurate': .99, 'internal_contradiction': .95}
    assert policy(p, {})['route'] == 'fail'
    p['internal_contradiction'] = .01
    assert policy(p, {'readback': False})['selected_branch'] == 2
    assert policy(p, {'readback': True})['route'] == 'pass'
    p['report_accurate'] = .6
    assert policy(p, {})['route'] == 'uncertain'
