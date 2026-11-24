"""Offline executable readiness contracts; no inference service is contacted."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def run_driver(out, *args):
    return subprocess.run(
        [sys.executable, '-m', 'evals.intent_v1.run', '--out', str(out), *args],
        cwd=ROOT, env={**os.environ, 'UNRELATED_SECRET': 'must-not-reach-worker'},
        capture_output=True, text=True, timeout=100, check=False,
    )


def test_overhead_arms_are_fresh_and_no_judge_means_zero_transport(tmp_path):
    out = tmp_path / 'overhead'
    result = run_driver(out, '--dry-run', '--cases', 'overhead')
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads((out / f'overhead-{arm}' / 'result.json').read_text()) for arm in 'ABC']
    assert all(r['passed'] for r in records)
    assert [r['judge_calls'] > 0 for r in records] == [False, False, True]
    assert len({r['home'] for r in records}) == 3
    assert len({r['workspace'] for r in records}) == 3
    for arm, record in zip('ABC', records):
        assert record['config']['semantic_continuation'] is False
        assert record['config']['judge_enabled'] is (arm == 'C')
        intent = json.loads((out / f'overhead-{arm}' / 'intent.json').read_text())
        if arm != 'C':
            assert intent['assessments'] == []
        else:
            assert any(r['delivery']['delivered'] for r in intent['assessments'])
            assert any(not r['delivery']['delivered'] for r in intent['assessments'])
        calls = [json.loads(line) for line in (out / f'overhead-{arm}' / 'actor_requests.jsonl').read_text().splitlines()]
        names = [t['function']['name'] for t in calls[0]['request']['tools']]
        assert ('intent_control' in names) is (arm != 'A')
        assert all(c['request']['tools'] == calls[0]['request']['tools'] for c in calls)
        assert all(c['request']['messages'][0] == calls[0]['request']['messages'][0] for c in calls)
        assert record['final_answers'] == ['Fibonacci Notes', '- Fibonacci Notes']
        assert record['fixture_unchanged']
        receipts = json.loads((out / f'overhead-{arm}' / 'tool_receipts.json').read_text())
        assert any('Fibonacci Notes' in m['content'] and 'file_size' in m['content'] for m in receipts)
        assert 'must-not-reach-worker' not in ''.join(p.read_text(errors='replace') for p in (out / f'overhead-{arm}').glob('*.json*'))


def test_topic_swap_and_inflight_edit_record_real_boundaries(tmp_path):
    out = tmp_path / 'steering'
    result = run_driver(out, '--dry-run', '--cases', 'topic_swap', 'stale_edit')
    assert result.returncode == 0, result.stdout + result.stderr
    for arm in 'ABC':
        swap = json.loads((out / f'topic_swap-{arm}' / 'result.json').read_text())
        assert swap['checks']['review_grounded'] and swap['checks']['no_election_after_swap']
        cell = out / f'stale_edit-{arm}'
        edit = json.loads((cell / 'result.json').read_text())
        events = [json.loads(line) for line in (cell / 'events.jsonl').read_text().splitlines()]
        boundary = next(e for e in events if e['event'] == 'inflight_edit')
        assert boundary['before_response_return'] and boundary['thread_joined']
        assert edit['checks']['positive_write_control']
        assert edit['checks']['stale_write_absent'] == (not (cell / 'workspace' / 'stale-patch.txt').exists())
        if arm != 'A':
            assert edit['checks']['stale_write_absent']
            state = json.loads((cell / 'intent.json').read_text())['state']
            assert state['origins']['boundaries'] == 'user'
            assert 'Read-only' in state['fields']['boundaries']
            assert boundary['after_revision'] > boundary['before_revision']
        assert not (tmp_path / 'stale-patch.txt').exists()


def test_live_refused_and_existing_run_never_overwritten(tmp_path):
    live = tmp_path / 'live'
    result = run_driver(live, '--live')
    assert result.returncode != 0 and 'actor-budget-usd' in result.stderr
    assert not live.exists()
    out = tmp_path / 'plan'
    assert run_driver(out).returncode == 0
    original = (out / 'plan.json').read_bytes()
    assert run_driver(out, '--dry-run').returncode != 0
    assert (out / 'plan.json').read_bytes() == original


def test_offline_transport_call_caps_and_network_fence(tmp_path):
    import pytest
    from evals.intent_v1.worker import Script, offline_guard
    from evals.intent_v1.run import config, child_env
    script = Script(tmp_path, config('B'))
    with pytest.raises(SystemExit, match='exhausted'):
        script.create(messages=[], tools=[])
    with pytest.raises(SystemExit, match='arm violation'):
        script.judge_client().stream('POST', 'offline', content=b'{}')
    with pytest.raises(RuntimeError, match='forbids network'):
        offline_guard('socket.connect', ())
    assert not any('KEY' in k or 'TOKEN' in k or 'SECRET' in k for k in child_env(tmp_path))
