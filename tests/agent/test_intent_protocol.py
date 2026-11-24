"""Actor-owned protocol through the real loop; only remote transports are replaced."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

from tests.agent.test_intent_attractor import harness as _harness

harness = _harness
from tests.agent.test_run_agent import _mock_response


def call(name, args, id='control'):
    return SimpleNamespace(id=id, type='function', function=SimpleNamespace(
        name=name, arguments=json.dumps(args)))


def declaration(revision, *, mode='retain', transition='act', proposal=None):
    args = {'revision': revision, 'mode': mode, 'transition': transition}
    if proposal is not None:
        args['proposal'] = proposal
    return call('intent_control', args)


PROPOSAL = {'update_type': 'replacement', 'fields': {
    'outcome': 'Implement and test the fix', 'verification': 'Run tests',
    'constraints': '', 'boundaries': 'Do not publish', 'questions': ''}}


def test_actor_contract_initializes_before_action_and_retains_before_finish(harness):
    from agent.intent_attractor import IntentStore
    h = harness
    h.agent._cached_system_prompt = None
    h.probabilities.append({'complete': .99})
    wires = []
    responses = iter([
        _mock_response(content='Checking now.', tool_calls=[
            call('terminal', {'command': 'printf verified'}, 'work'),
            declaration(0, mode='create', proposal=PROPOSAL)], finish_reason='tool_calls'),
        _mock_response(content='', tool_calls=[declaration(1, transition='finish')], finish_reason='tool_calls'),
        _mock_response(content='Implemented and verified.'),
    ])
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    def execute(*args, **kw):
        assert IntentStore(h.agent._session_db, h.agent.session_id).read()['revision'] == 1
        return '{"output":"verified","exit_code":0}'
    with patch('model_tools.handle_function_call', side_effect=execute) as tool:
        result = h.agent.run_conversation('Implement and test the fix; do not publish.')
    assert result['final_response'] == 'Implemented and verified.'
    assert not result['failed']
    assert tool.call_count == 1
    assert not any(c.kwargs.get('task') == 'intent_extraction' for c in h.aux.call_args_list)
    schemas = [t for t in wires[0]['tools'] if t['function']['name'] == 'intent_control']
    assert len(schemas) == 1
    params = schemas[0]['function']['parameters']
    assert set(params['required']) == {'revision', 'mode', 'transition'}
    assert params['additionalProperties'] is False
    assert all(w['tools'] == wires[0]['tools'] for w in wires)
    assert all(w['messages'][0] == wires[0]['messages'][0] for w in wires)
    assert 'intent_control' in wires[0]['messages'][0]['content']
    assert '[Current intent r0' in str(wires[0]['messages'])
    assert len(h.fidelity_payloads) == 1
    assert 'verified' in h.payloads[-1]['state']
    evidence = json.loads(h.payloads[-1]['state'])['tool_results']
    assert all('intent_control' not in e['call']['text'] for e in evidence)
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    assert store.read()['decision'] == 'DONE'
    assert 'Actor protocol' in store.command('inspect')
    results = {m['tool_call_id']: m for m in result['messages'] if m['role'] == 'tool'}
    assert results['work']['content']
    assert 'accepted' in results['control']['content']


def test_omission_malformed_and_stale_controls_are_bounded_not_success(harness):
    h = harness
    wires = []
    responses = iter([
        _mock_response(content='Starting.', tool_calls=[call('terminal', {'command': 'bad'}, 'a')]),
        _mock_response(content='Repairing.', tool_calls=[call('intent_control', {'revision': 0}, 'b'),
                                                        call('terminal', {'command': 'bad'}, 'c')]),
        _mock_response(content='All done.'),
    ])
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        return next(responses)
    h.agent.client.chat.completions.create.side_effect = create
    with patch('model_tools.handle_function_call') as execute:
        result = h.agent.run_conversation('Implement and test the fix.')
    assert not execute.called
    assert result['failed'] and 'Incomplete' in result['final_response']
    assert len(wires) == 3
    assert 'missing current intent' in str(wires[1]['messages'])
    assert 'invalid declaration envelope' in str(wires[2]['messages'])
    assert not h.fidelity_payloads and not h.payloads


def test_retain_cannot_skip_new_input_and_presentation_preserves_aim(harness):
    from agent.intent_attractor import IntentStore
    h = harness
    h.probabilities.append({'complete': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', transition='finish', proposal=PROPOSAL)]),
        _mock_response(content='Implemented.'),
    ]
    first = h.agent.run_conversation('Implement and test the fix.')
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(1, transition='finish')]),
        _mock_response(tool_calls=[declaration(1, mode='update', transition='finish',
                                              proposal={'update_type': 'presentation', 'fields': {}})]),
        _mock_response(content='Brief summary.'),
    ]
    second = h.agent.run_conversation('Give me a TLDR.', conversation_history=first['messages'])
    state = IntentStore(h.agent._session_db, h.agent.session_id).read()
    assert state['fields']['outcome'] == PROPOSAL['fields']['outcome']
    assert state['update_type'] == 'presentation'
    assert second['final_response'] == 'Brief summary.'
    assert h.agent._intent_protocol_repairs == 1


def test_manual_edit_midflight_rejects_old_plan_then_retains_current(harness):
    from agent.intent_attractor import IntentStore
    h = harness
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    wires = []
    def create(**kw):
        wires.append(copy.deepcopy(kw))
        if len(wires) == 1:
            return _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL)])
        if len(wires) == 2:
            store.command('edit boundaries Read local files only')
            return _mock_response(tool_calls=[call('terminal', {'command': 'bad'}, 'old'), declaration(1)])
        if len(wires) == 3:
            return _mock_response(tool_calls=[declaration(2, transition='finish')])
        return _mock_response(content='Stopped at the edited boundary.')
    h.agent.client.chat.completions.create.side_effect = create
    h.probabilities.append({'blocked': .99})
    with patch('model_tools.handle_function_call') as execute:
        result = h.agent.run_conversation('Implement and test the fix.')
    assert not execute.called
    assert 'Read local files only' in str(wires[2]['messages'])
    assert store.read()['fields']['boundaries'] == 'Read local files only'
    assert not result['failed']


def test_edit_between_actions_fences_remaining_batch(harness):
    from agent.intent_attractor import IntentStore
    h = harness
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    h.probabilities.append({'blocked': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL),
                                  call('terminal', {'command': 'one'}, 'one'),
                                  call('terminal', {'command': 'two'}, 'two')]),
        _mock_response(tool_calls=[declaration(2, transition='blocked')]),
        _mock_response(content='Stopped after authority changed.'),
    ]
    def execute(*args, **kwargs):
        store.command('edit boundaries Stop further tool work')
        return 'first completed'
    with patch('model_tools.handle_function_call', side_effect=execute) as execute_tool:
        result = h.agent.run_conversation('Implement and test the fix.')
    assert execute_tool.call_count == 1
    assert not result['failed']
    assert any('stale intent' in str(m.get('content')) for m in result['messages'])


def test_protocol_exhaustion_does_not_fall_through_budget_summary(harness):
    h = harness
    h.agent.max_iterations = 1
    h.agent.client.chat.completions.create.return_value = _mock_response(content='Finished without control.')
    result = h.agent.run_conversation('Implement and test the fix.')
    assert result['failed']
    assert 'Incomplete' in result['final_response']


def test_streamed_progress_survives_protocol_repair(harness):
    from tests.agent.test_streaming import _make_stream_chunk
    h = harness
    deltas = []
    h.agent.stream_delta_callback = deltas.append
    h.agent.client.chat.completions.create.side_effect = lambda **kw: iter([
        _make_stream_chunk(content='Visible progress before control.'),
        _make_stream_chunk(finish_reason='stop'),
    ])
    result = h.agent.run_conversation('Implement and test the fix.')
    assert any('Visible progress before control.' in str(d) for d in deltas)
    assert result['failed']


def test_followthrough_requires_new_declaration_and_uses_actual_result(harness):
    h = harness
    h.probabilities.extend([{'incomplete': .99, 'actionable': .99}, {'complete': .99}])
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', transition='finish', proposal=PROPOSAL)]),
        _mock_response(content='Implementation ready; I can test if you want.'),
        _mock_response(tool_calls=[declaration(1), call('terminal', {'command': 'printf tested'}, 'test')]),
        _mock_response(tool_calls=[declaration(1, transition='finish')]),
        _mock_response(content='Implemented and tested.'),
    ]
    with patch('model_tools.handle_function_call', return_value='tested') as execute:
        result = h.agent.run_conversation('Implement and test the fix.')
    assert execute.call_count == 1
    assert result['final_response'] == 'Implemented and tested.'
    assert 'tested' in h.payloads[-1]['state']


def test_judge_unavailable_does_not_prevent_actor_working_aim_or_tool(harness, monkeypatch):
    from agent.intent_attractor import IntentStore
    h = harness
    monkeypatch.setattr('agent.intent_attractor_judge.assess', lambda *a, **kw: (_ for _ in ()).throw(TimeoutError()))
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL), call('terminal', {'command': 'check'}, 'check')]),
        _mock_response(tool_calls=[declaration(1, transition='blocked')]),
        _mock_response(content='Checked. Independent judgment unavailable.'),
    ]
    with patch('model_tools.handle_function_call', return_value='checked') as execute:
        result = h.agent.run_conversation('Implement and test the fix.')
    assert execute.call_count == 1
    state = IntentStore(h.agent._session_db, h.agent.session_id).read()
    assert state['fields']['outcome'] == PROPOSAL['fields']['outcome']
    assert state['assessments_used'] >= 1
    assert not result['failed']


def test_disabled_session_keeps_original_schema_and_text_path(harness):
    from hermes_cli.config import atomic_config_write
    from run_agent import AIAgent
    from unittest.mock import MagicMock
    from tests.agent.test_run_agent import _make_tool_defs
    h = harness
    h.config['agent']['intent_attractor']['enabled'] = False
    atomic_config_write(h.home / 'config.yaml', h.config)
    with (patch('model_tools.get_tool_definitions', return_value=_make_tool_defs('terminal')),
          patch('model_tools.check_toolset_requirements', return_value={}), patch('agent.process_bootstrap.OpenAI')):
        actor = AIAgent(api_key='test', base_url='https://openrouter.ai/api/v1', quiet_mode=True, skip_context_files=True, skip_memory=True,
                        session_id='disabled', session_db=h.agent._session_db, persistence=h.agent.persistence)
    actor.client = MagicMock()
    actor.client.chat.completions.create.return_value = _mock_response(content='Ordinary answer.')
    result = actor.run_conversation('Explain this.')
    wire = actor.client.chat.completions.create.call_args.kwargs
    assert not any(t['function']['name'] == 'intent_control' for t in wire['tools'])
    assert 'Required actor intent protocol' not in wire['messages'][0]['content']
    assert result['final_response'] == 'Ordinary answer.'
    assert not h.fidelity_payloads and not h.payloads


def test_material_update_edit_during_judgment_is_stale(harness, monkeypatch):
    from agent.intent_attractor import IntentStore
    from agent import intent_attractor_judge
    h = harness
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    original = intent_attractor_judge.assess
    def assess(*a, **kw):
        record = original(*a, **kw)
        if kw.get('assessment_questions'):
            store.command('edit boundaries Only inspect files')
        return record
    monkeypatch.setattr(intent_attractor_judge, 'assess', assess)
    h.agent.client.chat.completions.create.return_value = _mock_response(tool_calls=[
        declaration(0, mode='create', proposal=PROPOSAL), call('terminal', {'command': 'bad'}, 'bad')])
    with patch('model_tools.handle_function_call') as execute:
        result = h.agent.run_conversation('Implement and test the fix.')
    assert not execute.called and result['failed']
    assert store.read()['fields']['boundaries'] == 'Only inspect files'
    receipt = next(r for r in store.assessments() if r['phase'] == 'reconciliation')
    assert receipt['freshness'] == 'STALE'
    assert receipt['application'] == 'APPLIED' and receipt['decision'] == 'ACCEPTED'


def test_saved_protocol_schema_survives_config_change_and_refresh(harness):
    from hermes_cli.config import atomic_config_write
    from tools.mcp_tool_agent import restore_agent_tool_prefix, refresh_agent_mcp_tools, tool_pin_version
    from run_agent import AIAgent
    from tests.agent.test_run_agent import _make_tool_defs
    h = harness
    saved = {'version': tool_pin_version(), 'tools': copy.deepcopy(h.agent.tools)}
    h.config['agent']['intent_attractor']['enabled'] = False
    atomic_config_write(h.home / 'config.yaml', h.config)
    with (patch('model_tools.get_tool_definitions', return_value=_make_tool_defs('terminal')),
          patch('model_tools.check_toolset_requirements', return_value={}), patch('agent.process_bootstrap.OpenAI')):
        actor = AIAgent(api_key='test', base_url='https://openrouter.ai/api/v1', quiet_mode=True,
                        skip_context_files=True, skip_memory=True, session_id='resumed', session_db=h.agent._session_db, persistence=h.agent.persistence)
        restore_agent_tool_prefix(actor, saved)
        refresh_agent_mcp_tools(actor, preserve_prefix=True)
        refresh_agent_mcp_tools(actor, preserve_prefix=False)
    assert actor._intent_guidance_enabled
    assert next(t for t in actor.tools if t['function']['name'] == 'intent_control') == saved['tools'][-1]


def test_final_edit_during_judgment_requires_repair_not_success(harness, monkeypatch):
    from agent.intent_attractor import IntentStore
    from agent import intent_attractor_judge
    h = harness
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    original = intent_attractor_judge.assess
    def assess(*a, **kw):
        record = original(*a, **kw)
        if not kw.get('assessment_questions'):
            store.command('edit verification Also inspect the artifact')
        return record
    monkeypatch.setattr(intent_attractor_judge, 'assess', assess)
    h.probabilities.append({'complete': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', transition='finish', proposal=PROPOSAL)]),
        _mock_response(content='Done.'),
        _mock_response(content='Still done.'),
        _mock_response(content='Still done.'),
    ]
    result = h.agent.run_conversation('Implement and test the fix.')
    assert result['failed']
    assert result['final_response'] != 'Done.'


def test_edit_during_tool_approval_is_fenced_before_execution(harness, monkeypatch):
    from agent.intent_attractor import IntentStore
    h = harness
    store = IntentStore(h.agent._session_db, h.agent.session_id)
    original = h.agent._tool_guardrails.before_call
    def before(name, args):
        result = original(name, args)
        if name == 'terminal':
            store.command('edit boundaries No further actions')
        return result
    monkeypatch.setattr(h.agent._tool_guardrails, 'before_call', before)
    h.probabilities.append({'blocked': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL), call('terminal', {'command': 'bad'}, 'bad')]),
        _mock_response(tool_calls=[declaration(2, transition='blocked')]),
        _mock_response(content='No action executed.')]
    with patch('model_tools.handle_function_call') as execute:
        result = h.agent.run_conversation('Implement and test the fix.')
    assert not execute.called
    assert not result['failed']


def test_unsupported_owned_runtime_cannot_bypass_protocol(harness, monkeypatch):
    h = harness
    h.agent.api_mode = 'codex_app_server'
    owned = []
    monkeypatch.setattr('agent.conversation_loop._codex_app_server_turn', lambda *a: owned.append(True) or {'final_response': 'bypass'})
    result = h.agent.run_conversation('Implement and test the fix.')
    assert not owned
    assert result['failed'] and 'unsupported' in result['final_response'].lower()


def test_protocol_dispatches_real_local_artifact_and_reads_it_back(harness, monkeypatch, tmp_path):
    h = harness
    artifact = tmp_path / 'actor-protocol-proof.txt'
    monkeypatch.setenv('TERMINAL_ENV', 'local')
    monkeypatch.setenv('TERMINAL_CWD', str(tmp_path))
    proposal = copy.deepcopy(PROPOSAL)
    proposal['fields'].update(outcome='Create and verify a local proof file', verification='Read the file back')
    h.probabilities.append({'complete': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=proposal),
                                  call('terminal', {'command': f"printf protocol-verified > '{artifact}'; cat '{artifact}'"}, 'local-proof')]),
        _mock_response(tool_calls=[declaration(1, transition='finish')]),
        _mock_response(content='Created and read back the local proof file.')]
    result = h.agent.run_conversation('Create and verify a local proof file. Do not publish.')
    assert artifact.read_text() == 'protocol-verified'
    assert not result['failed']
    assert 'protocol-verified' in h.payloads[-1]['state']


def test_store_failure_during_repair_never_retries_actor_or_executes(harness, monkeypatch):
    from agent.intent_attractor import IntentStore
    h = harness
    def create(**kw):
        monkeypatch.setattr(IntentStore, 'read', lambda *a: (_ for _ in ()).throw(OSError('private disk failure')))
        return _mock_response(tool_calls=[call('terminal', {'command': 'bad'}, 'bad')])
    h.agent.client.chat.completions.create.side_effect = create
    with patch('model_tools.handle_function_call') as execute:
        result = h.agent.run_conversation('Implement and test the fix.')
    assert not execute.called
    assert h.agent.client.chat.completions.create.call_count == 1
    assert result['failed'] and 'private disk failure' not in result['final_response']


def test_disabled_saved_contract_stays_disabled_when_config_enabled(harness):
    from tools.mcp_tool_agent import restore_agent_tool_prefix, tool_pin_version
    h = harness
    saved = {'version': tool_pin_version(), 'tools': [t for t in h.agent.tools if t['function']['name'] != 'intent_control']}
    restore_agent_tool_prefix(h.agent, saved)
    assert not h.agent._intent_guidance_enabled
    assert all(t['function']['name'] != 'intent_control' for t in h.agent.tools)


def test_new_steer_requires_update_once_without_auxiliary_call(harness):
    from agent.intent_attractor import IntentStore, real_sources
    h = harness
    h.probabilities.append({'complete': .99})
    h.agent.client.chat.completions.create.side_effect = [
        _mock_response(tool_calls=[declaration(0, mode='create', proposal=PROPOSAL), call('terminal', {'command': 'check'}, 'check')]),
        _mock_response(tool_calls=[declaration(1, transition='finish')]),
        _mock_response(tool_calls=[declaration(1, mode='update', transition='finish',
            proposal={'update_type': 'presentation', 'fields': {}})]),
        _mock_response(content='Local result summarized.')]
    def execute(*a, **kw):
        h.agent._pending_steer = 'Only summarize the local result now.'
        return 'checked'
    with patch('model_tools.handle_function_call', side_effect=execute):
        result = h.agent.run_conversation('Implement and test the fix.')
    state = IntentStore(h.agent._session_db, h.agent.session_id).read()
    assert state['revision'] == 2
    assert 'Only summarize the local result now.' in state['immediate_request']['text']
    assert h.agent._intent_protocol_repairs == 1
    assert not result['failed']
    assert len(real_sources(result['messages'])) == 2
    assert not any(c.kwargs.get('task') == 'intent_extraction' for c in h.aux.call_args_list)
