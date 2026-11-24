"""Tranche artifact checks: actual bytes and linked receipts, not answer wording."""
import json


def test_artifact_fix_preserves_schema_and_unrelated_files(tmp_path):
    from evals.intent_v1 import tranche
    contract = tranche.TASKS['artifact_fix']
    fixtures = tranche.FIXTURES['artifact_fix']
    tmp_path = tmp_path / 'workspace'
    tmp_path.mkdir()
    for name, text in fixtures.items():
        (tmp_path / name).write_text(text)
    def turn(tools):
        return {'final_response': 'Unscored prose', 'messages': tools}
    def read(name, ident):
        return [{'role': 'assistant', 'tool_calls': [{'id': ident, 'function': {
            'name': 'read_file', 'arguments': json.dumps({'path': name})}}]},
            {'role': 'tool', 'tool_call_id': ident, 'content': json.dumps({'file_size': 20})}]
    turns = [turn(read('retry.json', 'a') + read('client.py', 'b')),
             turn(read('retry.json', 'c'))]
    assert not tranche.checks('artifact_fix', contract, turns, tmp_path)['artifact_correct']
    fixed = json.loads(fixtures['retry.json'])
    fixed['timeout_ms'] = 2500
    (tmp_path / 'retry.json').write_text(json.dumps(fixed))
    assert all(tranche.checks('artifact_fix', contract, turns, tmp_path).values())
    (tmp_path / 'client.py').write_text('changed')
    assert not tranche.checks('artifact_fix', contract, turns, tmp_path)['unrelated_unchanged']


def test_tranche_file_tools_use_real_handler_and_path_fence(tmp_path):
    from evals.intent_v1.live_transport import fixture_dispatch
    import model_tools
    model_tools.get_tool_definitions(enabled_toolsets=['file'])
    dispatch = fixture_dispatch(tmp_path, model_tools.handle_function_call, {'retry.json'})
    result = json.loads(dispatch('write_file', {'path': 'retry.json', 'content': '{"timeout_ms": 2500}'}))
    assert result['verified'], result
    assert json.loads((tmp_path / 'retry.json').read_text()) == {'timeout_ms': 2500}
    assert json.loads(dispatch('read_file', {'path': '../outside'}))['executed'] is False
    assert json.loads(dispatch('terminal', {'command': 'true'}))['executed'] is False
    dispatch('read_file', {'path': 'retry.json'})
    result = json.loads(dispatch('patch', {'mode': 'replace', 'path': 'retry.json',
                                        'old_string': '2500', 'new_string': '25'}))
    assert result['success'], result
    assert json.loads((tmp_path / 'retry.json').read_text()) == {'timeout_ms': 25}
    assert json.loads(dispatch('patch', {'mode': 'patch', 'patch': 'unsafe multi-file'}))['executed'] is False
    assert json.loads(dispatch('patch', {'path': '../outside', 'old_string': 'x', 'new_string': 'y'}))['executed'] is False
