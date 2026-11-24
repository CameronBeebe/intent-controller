"""One fresh process per cell. Only actor/judge transports are scripted."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS
from typing import Any
from unittest.mock import patch

from evals.intent_v1.run import save

FIXTURES = {
    'notes.txt': 'Fibonacci Notes\nA tiny local review fixture.\n',
    'fibonacci.py': 'def fib(n):\n    if n < 2:\n        return 1  # defect: fib(0) should be 0\n    return fib(n - 1) + fib(n - 2)\n',
    'election.txt': 'Frozen election result\nCandidate Elm: 7 votes\nCandidate Oak: 4 votes\n',
}
FIXTURE_VERSION = 'intent-v1-fixture-manifest-1'
FIXTURE_MANIFEST = 'Available workspace files (relative paths):\n' + '\n'.join(sorted(FIXTURES))


def append(path, data):
    with path.open('a') as stream:
        stream.write(json.dumps(data, ensure_ascii=False) + '\n')


def call(name, args, id):
    return NS(id=id, type='function', function=NS(name=name, arguments=json.dumps(args)))


def response(content='', calls=None):
    return NS(model='offline-actor', usage=None,
              choices=[NS(message=NS(content=content, tool_calls=calls),
                          finish_reason='tool_calls' if calls else 'stop')])


class Script:
    def __init__(self, cell, cfg):
        self.cell, self.cfg = cell, cfg
        self.steps: list[Any] = []
        self.actor_calls = self.judge_calls = 0
        self.agent: Any = None

    def create(self, **request):
        self.actor_calls += 1
        prepared = copy.deepcopy(request)
        timeout = prepared.pop('timeout', None)
        digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        append(self.cell / 'actor_requests.jsonl', {'index': self.actor_calls, 'request': prepared,
               'schema_sha256': digest(prepared.get('tools')),
               'system_sha256': digest([m for m in prepared.get('messages', []) if m['role'] == 'system']),
               'transport_timeout': timeout.as_dict() if hasattr(timeout, 'as_dict') else timeout})
        if self.agent is not None:
            from agent.intent_attractor import IntentStore
            append(self.cell / 'intent_snapshots.jsonl', {'actor_call': self.actor_calls,
                   'state': IntentStore(self.agent._session_db, self.agent.session_id).read()})
        if self.actor_calls > 16 or not self.steps:
            # BaseException bypasses the agent's retry ladder; preserve this failed cell.
            raise SystemExit('offline actor call budget/script exhausted')
        step = self.steps.pop(0)
        answer = step() if callable(step) else step
        append(self.cell / 'actor_responses.jsonl', {'index': self.actor_calls, 'response': {
            'content': answer.choices[0].message.content,
            'tool_calls': [{'id': c.id, 'function': vars(c.function)}
                           for c in (answer.choices[0].message.tool_calls or [])]}})
        return answer

    def control(self, mode='retain', transition='act', outcome=None, update_type='replacement'):
        from agent.intent_attractor import IntentStore
        state = IntentStore(self.agent._session_db, self.agent.session_id).read()
        args = {'revision': state['revision'] if state else 0, 'mode': mode, 'transition': transition}
        if mode != 'retain':
            args['proposal'] = {'update_type': update_type, 'fields': ({'outcome': outcome,
                'verification': 'Inspect fixture lines', 'constraints': 'No network or dependencies',
                'boundaries': 'Only this fixture workspace', 'questions': ''} if outcome else {})}
        return call('intent_control', args, f'control-{self.actor_calls}')

    def read_step(self, filename, outcome, mode='create'):
        def step():
            calls = [call('read_file', {'path': str(self.cell / 'workspace' / filename)}, f'read-{self.actor_calls}')]
            if self.cfg['enabled']:
                calls.insert(0, self.control(mode, outcome=outcome))
            return response(calls=calls)
        return step

    def finish(self):
        return response(calls=[self.control(transition='finish')])

    def write(self, filename, content):
        return response(calls=[call('write_file', {'path': str(self.cell / 'workspace' / filename),
                        'content': content}, f'write-{self.actor_calls}')])

    def stale_write(self):
        from evals.intent_v1.cases import inject_edit
        answer = self.write('stale-patch.txt', 'Replace return 1 with return n')
        inject_edit(self)
        return answer

    def judge_client(self, **kwargs):
        script = self

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def stream(self, method, url, **kwargs):
                script.judge_calls += 1
                body = kwargs['content'].decode()
                append(script.cell / 'judge_requests.jsonl', {'index': script.judge_calls,
                       'method': method, 'url': url, 'body': body})  # Never persist auth headers.
                if script.judge_calls > 20 or not script.cfg['judge_enabled']:
                    raise SystemExit('offline judge budget/arm violation')
                payload = json.loads(body)
                data = {'model': 'offline-judge', 'usage': {'input_tokens': 0, 'output_tokens': 0},
                        'answers': {k: {'type': 'noul', 'noul': .01 if
                            script.judge_calls == 1 and k == 'coverage' else .5}
                            for k in payload['questions']}}
                append(script.cell / 'judge_responses.jsonl', {'index': script.judge_calls,
                       'synthetic': True, 'response': data})
                self.data = json.dumps(data).encode()
                return self

            def raise_for_status(self):
                return None

            def iter_bytes(self, **kwargs):
                yield self.data

        return Client()


def offline_guard(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):
        raise RuntimeError('offline readiness forbids network')


def run(cell):
    from evals.intent_v1.run import child_env
    expected = child_env(cell)
    if any(os.environ.get(k) != expected[k] for k in ('HOME', 'HERMES_HOME', 'TERMINAL_CWD')):
        raise RuntimeError('Worker requires the isolated driver environment')
    sys.addaudithook(offline_guard)
    plan = json.loads((cell / 'cell.json').read_text())
    cfg = plan['config']
    workspace = cell / 'workspace'
    os.chdir(workspace)
    for name, content in FIXTURES.items():
        (workspace / name).write_text(content)
    hashes = {name: hashlib.sha256((workspace / name).read_bytes()).hexdigest() for name in FIXTURES}
    save(cell / 'fixtures.before.json', hashes)
    # JSON is valid YAML. Construct the feature configuration before importing AIAgent.
    save(cell / 'hermes' / 'config.yaml', {'agent': {'intent_attractor': cfg},
         'terminal': {'backend': 'local'}, 'compression': {'enabled': False}})
    os.environ['TYPESAFE_API_KEY'] = 'offline-synthetic-not-a-credential'
    from run_agent import AIAgent
    from persistence_assembly import open_persistence, profile_scope
    from agent.intent_attractor import IntentStore
    import model_tools
    script = Script(cell, cfg)
    # Use real registered file schemas and real dispatch/handlers, not test tool definitions.
    definitions = model_tools.get_tool_definitions(enabled_toolsets=['file'])
    definitions = [t for t in definitions if t['function']['name'] in ('read_file', 'write_file')]
    db = open_persistence(profile_scope(home=cell / 'hermes'))
    client = NS(chat=NS(completions=NS(create=script.create)))
    with (patch('agent.process_bootstrap.OpenAI', return_value=client),
          patch('model_tools.get_tool_definitions', return_value=definitions),
          patch('hermes_cli.plugins.discover_plugins', return_value=None),
          patch('agent.read_assessment_typesafe.http_client', script.judge_client),
          patch('agent.auxiliary_client.call_llm', side_effect=RuntimeError('offline auxiliary calls forbidden')),
          patch('agent.title_generator.maybe_auto_title', return_value=None)):
        agent: Any = AIAgent(model='offline-actor', provider='openai', api_key='offline-synthetic',
                        base_url='http://offline.invalid/v1', quiet_mode=True,
                        session_id=cell.name, persistence=db, enabled_toolsets=['file'],
                        max_iterations=12, max_tokens=512, skip_context_files=True,
                        skip_memory=True, skip_background_review=True, cwd=str(workspace))
        script.agent = agent
        agent.client = client
        agent.compression_enabled = False
        store = IntentStore(db, agent.session_id)
        results, history = [], []
        from evals.intent_v1.cases import turn_specs, grade
        for index, (prompt, answer, steps) in enumerate(turn_specs(script, plan['case'])):
            if index == 1 and plan['case'] == 'overhead' and cfg['enabled']:
                steps = [lambda: response(calls=[script.control('update', 'finish', update_type='presentation')])]
            elif cfg['enabled']:
                steps.append(script.finish)
            script.steps = [*steps, response(answer)]
            append(cell / 'events.jsonl', {'event': 'user_turn', 'index': index, 'text': prompt})
            started = time.monotonic()
            result = agent.run_conversation(prompt, conversation_history=history)
            history = result['messages']
            save(cell / f'turn-{index}.json', result)
            append(cell / 'events.jsonl', {'event': 'turn_end', 'index': index,
                   'wall_seconds': time.monotonic() - started, 'state': store.read()})
            results.append(result)
        state = store.read()
        save(cell / 'intent.json', {'state': state, 'history': store.command('history'),
                                  'assessments': store.assessments()})
        save(cell / 'tool_receipts.json', [m for m in history if m.get('role') == 'tool'])
        save(cell / 'transcript.json', history)
        unchanged = all(hashlib.sha256((workspace / n).read_bytes()).hexdigest() == h for n, h in hashes.items())
        save(cell / 'fixtures.after.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                            for p in workspace.iterdir() if p.is_file()})
        answers = [r['final_response'] for r in results]
        checks, passed = grade(plan['case'], script, results, unchanged, state)
        record = {'passed': passed, 'grader_version': 'intent-v1-offline-invariants-1', 'checks': checks, 'behavioral_contract_passed': all(checks.values()),
                  'mode': 'scripted-offline', 'config': cfg, 'home': str(cell / 'hermes'),
                  'workspace': str(workspace), 'fixture_unchanged': unchanged,
                  'final_answers': answers, 'actor_calls': script.actor_calls, 'judge_calls': script.judge_calls,
                  'usage': None, 'cost': None, 'usage_note': 'No live inference; scripted usage is not measurement.'}
        save(cell / 'result.json', record)
        agent.close()
        db.close()
        return 0 if record['passed'] else 1


if __name__ == '__main__':
    cell = Path(sys.argv[1]).resolve()
    try:
        raise SystemExit(run(cell))
    except BaseException as exc:
        if not (cell / 'result.json').exists():
            save(cell / 'result.json', {'passed': False, 'infrastructure_invalid': True,
                                      'error_type': type(exc).__name__})
        raise
