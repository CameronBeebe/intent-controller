"""Shared offline harness and independent Jev transport contracts.

Extractor-era loop scenarios live in test_intent_protocol and test_intent_updates
with explicit actor declarations; no production extractor compatibility mode."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.agent.test_run_agent import _mock_response, _make_tool_defs


def make_actor(db, session_id):
    """Construct the actor with the writer handle alone, as the gateway does."""
    from run_agent import AIAgent
    with (patch('model_tools.get_tool_definitions', return_value=_make_tool_defs('terminal')),
          patch('model_tools.check_toolset_requirements', return_value={}),
          patch('agent.process_bootstrap.OpenAI')):
        agent = AIAgent(api_key='test-key', base_url='https://openrouter.ai/api/v1',
                        quiet_mode=True, skip_context_files=True, skip_memory=True,
                        session_id=session_id, persistence=db, max_iterations=8)
    agent.client = MagicMock()
    agent._cached_system_prompt = 'Stable instructions. Do only authorized work.'
    agent._use_prompt_caching = False
    agent.compression_enabled = False
    agent.save_trajectories = False
    agent._stall_guards = False
    return agent


@pytest.fixture
def harness(tmp_path, monkeypatch):
    from hermes_cli.config import load_config, atomic_config_write
    config = load_config()
    config['agent']['intent_attractor'] = {
        'enabled': True, 'provider': 'typesafe', 'model': 'jev-test',
        'max_continuations': 2, 'max_assessments': 12, 'feedback_interval': 0,
    }
    atomic_config_write(tmp_path / 'config.yaml', config)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('TYPESAFE_API_KEY', 'synthetic-test-key')
    monkeypatch.setattr('hermes_cli.plugins.discover_plugins', lambda: None)
    from persistence_assembly import open_persistence, profile_scope
    db = open_persistence(profile_scope(home=tmp_path))
    agent = make_actor(db, 'intent-test')
    aux = MagicMock(return_value=_mock_response(content=json.dumps({
        'update_type': 'replacement', 'fields': {
            'outcome': 'Implement and test the fix', 'verification': 'Run tests',
            'constraints': '', 'boundaries': 'Do not publish', 'questions': '',
        },
    })))
    monkeypatch.setattr('agent.auxiliary_client.call_llm', aux)
    payloads = []
    probabilities = []
    fidelity_payloads = []
    fidelity_probabilities = []
    def stream(method, url, **kwargs):
        payload = json.loads(kwargs['content'])
        if 'complete' not in payload['questions']:
            fidelity_payloads.append(payload)
            values = fidelity_probabilities.pop(0) if fidelity_probabilities else {}
        else:
            payloads.append(payload)
            values = probabilities.pop(0)
        data = {'model': 'jev-served', 'answers': {
            name: {'type': 'noul', 'noul': values.get(name, .95 if name in
                     ('coverage', 'consistent', 'evidence') else .05)}
            for name in payload['questions']},
            'usage': {'input_tokens': 100, 'output_tokens': 10}}
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_bytes.return_value = [json.dumps(data).encode()]
        return response
    client = MagicMock()
    client.__enter__.return_value = client
    client.stream.side_effect = stream
    monkeypatch.setattr('agent.read_assessment_typesafe.http_client', lambda **kw: client)
    yield SimpleNamespace(agent=agent, aux=aux, payloads=payloads, probabilities=probabilities,
                          fidelity_payloads=fidelity_payloads, fidelity_probabilities=fidelity_probabilities,
                          config=config, home=tmp_path)
    db.close()


def test_judge_enforces_caller_deadline(harness, monkeypatch):
    import threading
    from agent.intent_attractor_judge import bounded_evaluate
    release = threading.Event()
    entered = threading.Event()
    def hang(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
    monkeypatch.setattr('agent.read_assessment_typesafe.evaluate', hang)
    try:
        with pytest.raises(TimeoutError):
            bounded_evaluate({}, 'jev-test', 'synthetic', wait_seconds=.01)
        assert entered.is_set()
    finally:
        release.set()


def test_large_evidence_is_bounded_before_transmission_and_recording(harness):
    from agent.intent_attractor_judge import assess
    h = harness
    h.probabilities.append({'complete': .99})
    record = assess({'candidate': '文' * 50000, 'sources': [{'text': 'x' * 90000}]},
                    h.config['agent']['intent_attractor'])
    assert len(h.payloads) == 1
    assert len(json.dumps(record['payload'], ensure_ascii=False).encode()) <= 32768
    assert record['payload'] == h.payloads[0]
    assert 'omitted' in record['payload']['state']


def test_scoped_missing_key_does_not_borrow_ambient_key(harness):
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    from agent.intent_attractor_judge import assess
    h = harness
    token = set_secret_scope({}, profile_home=str(h.home / 'other-profile'))
    try:
        record = assess({}, h.config['agent']['intent_attractor'])
    finally:
        reset_secret_scope(token)
    assert record['reason'] == 'missing_key'
    assert h.payloads == []


def test_provider_extras_cannot_overwrite_assessment_provenance(harness, monkeypatch):
    from agent import intent_attractor_judge
    from agent.intent_attractor import questions
    h = harness
    result = {'model': 'served', 'answers': {k: {'type': 'noul', 'noul': .5} for k in questions()},
              'usage': {'input_tokens': 1, 'output_tokens': 1}, 'payload': 'forged', 'provider': 'forged'}
    monkeypatch.setattr(intent_attractor_judge, 'bounded_evaluate', lambda *a, **kw: result)
    record = intent_attractor_judge.assess({'evidence': 'actual'}, h.config['agent']['intent_attractor'])
    assert record['rubric_version'] == 'intent-v3-means'
    assert record['provider'] == 'typesafe'
    assert json.loads(record['payload']['state']) == {'evidence': 'actual'}
