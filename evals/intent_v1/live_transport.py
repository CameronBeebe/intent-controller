"""Real HTTP transports with durable capture and local-only fixture dispatch."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from evals.intent_v1.live import LiveStop, ResourceStop, money, check_spend
from evals.intent_v1.live_auth import ACTOR_BASE, JUDGE_BASE


# Only the synchronous, journaled transport may open a network connection in a
# live worker. Thread-local by design: background catalog refreshes must not
# inherit permission, even if they explicitly copy the caller's ContextVars.
_wire = threading.local()


@contextmanager
def provider_exchange():
    previous = getattr(_wire, 'allowed', False)
    _wire.allowed = True
    try:
        yield
    finally:
        _wire.allowed = previous


def worker_network_guard(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto', 'socket.sendmsg'):
        if not getattr(_wire, 'allowed', False):
            raise LiveStop('network outside captured provider exchange')


def journal(path, value, secrets=()):
    text = json.dumps(value, ensure_ascii=False, default=str)
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[REDACTED]')
    try:
        with Path(path).open('a') as stream:
            stream.write(text + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise LiveStop('evidence journal storage failure') from exc


def deadline(plan):
    remaining = plan['deadline_epoch'] - time.time()
    if remaining <= 0:
        raise ResourceStop('experiment wall deadline')
    return min(30, remaining)


class Actor:
    def __init__(self, cell, plan, key, *, http=None):
        import httpx
        self.cell, self.plan, self.key = Path(cell), plan, key
        self.http = http or httpx.Client(follow_redirects=False, trust_env=False)

        self.actor_calls = 0
        self.agent: Any = None
        self.after_response = None
        self.usage = []

    def create(self, **request):
        from openai.types.chat import ChatCompletion
        started = time.monotonic()
        # Checkpoint BEFORE resource guards: the next request contains tool results
        # produced since the previous response, even when no further call is sent.
        from evals.intent_v1.run import save
        partial = {k: v for k, v in request.items() if k != 'timeout'}
        try:
            save(self.cell / 'partial_request.json', partial)
            save(self.cell / 'transcript.json', partial.get('messages', []))
        except Exception as exc:
            raise LiveStop('partial evidence storage failure') from exc
        timeout = deadline(self.plan)

        # Preserve the prepared request; apply only explicit frozen transport controls.
        original = dict(request)
        original.pop('timeout', None)
        payload = dict(original)
        extra = payload.pop('extra_body', {}) or {}
        if set(extra) - {'provider', 'reasoning', 'usage', 'transforms', 'session_id'}:
            raise LiveStop('unsupported extra request fields')
        payload.update(extra)
        if set(payload) - {'model', 'messages', 'tools', 'tool_choice', 'max_tokens', 'max_completion_tokens',
                           'stream', 'parallel_tool_calls', 'reasoning', 'reasoning_effort',
                           'temperature', 'top_p', 'provider', 'usage', 'transforms', 'session_id', 'verbosity'}:
            raise LiveStop('unsupported actor request fields')
        payload.pop('max_completion_tokens', None)
        payload.pop('temperature', None)
        payload.pop('top_p', None)
        payload.pop('reasoning_effort', None)
        payload.update(model=self.plan['actor_model'], max_tokens=self.plan['max_output_tokens'],
                       stream=False, reasoning={'effort': 'none'}, usage={'include': True},
                       provider={'allow_fallbacks': False, 'require_parameters': True})
        payload.pop('transforms', None)
        def text_only(content):
            return isinstance(content, (str, type(None))) or (isinstance(content, list) and all(
                isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str)
                and not (set(part) - {'type', 'text', 'cache_control'}) for part in content))
        if not isinstance(payload.get('messages'), list) or any(
                not text_only(m.get('content')) for m in payload['messages']):
            raise LiveStop('only text actor input supported')
        check_spend(self.plan['spend_root'], self.plan['actor_budget_usd'])
        self.actor_calls += 1
        index = self.actor_calls
        digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        capture = {'index': index, 'request': original, 'wire_request': payload,
                   'schema_sha256': digest(payload.get('tools')),
                   'system_sha256': digest([m for m in payload['messages'] if m['role'] == 'system']),
                   'transport_timeout': timeout}
        journal(self.cell / 'actor_requests.jsonl', capture, (self.key,))
        if self.agent is not None:
            from agent.intent_attractor import IntentStore
            journal(self.cell / 'intent_snapshots.jsonl', {'actor_call': index,
                    'state': IntentStore(self.agent._session_db, self.agent.session_id).read()})

        try:
            with provider_exchange():
                response = self.http.post(ACTOR_BASE + '/chat/completions', json=payload,
                                          headers={'Authorization': 'Bearer ' + self.key}, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            journal(self.cell / 'actor_responses.jsonl', {'index': index, 'response': data,
                    'wall_seconds': time.monotonic() - started}, (self.key,))
            answer = ChatCompletion.model_validate(data)
            usage = data.get('usage') or {}
            if usage.get('cost') is not None:
                money(usage['cost'])
            self.usage.append(usage)
            if self.after_response:
                self.after_response(answer)
            return answer
        except BaseException as exc:
            journal(self.cell / 'actor_failures.jsonl', {'index': index, 'error_type': type(exc).__name__,
                    'wall_seconds': time.monotonic() - started})
            # No hidden HTTP/agent retries, no fallback to another model or account.
            raise LiveStop('actor transport/validation failure; cost may be unknown') from None


class Judge:
    def __init__(self, cell, plan, key, *, role='in_loop'):
        self.cell, self.plan, self.key = Path(cell), plan, key
        self.role = role
        self.prefix = 'evaluator' if role == 'evaluator' else 'judge'
        self.judge_calls = 0
        self.lock = threading.Lock()

    def client(self, **options):
        import httpx
        judge = self
        class Client:
            def __enter__(self):
                self.http = httpx.Client(**options)
                return self

            def __exit__(self, *args):
                self.http.close()

            def stream(self, method, url, **kwargs):
                deadline(judge.plan)
                if method != 'POST' or url != JUDGE_BASE + '/systemone':
                    raise LiveStop('judge route violation')
                with judge.lock:
                    judge.judge_calls += 1
                    self.index = judge.judge_calls
                journal(judge.cell / f'{judge.prefix}_requests.jsonl', {'index': self.index, 'role': judge.role,
                        'method': method, 'url': url, 'body': kwargs['content'].decode()}, (judge.key,))
                self.started = time.monotonic()
                self.context = self.http.stream(method, url, **kwargs)
                return Stream(self)

        class Stream:
            def __init__(self, owner):
                self.owner = owner
                self.chunks = bytearray()

            def __enter__(self):
                try:
                    with provider_exchange():
                        self.response = self.owner.context.__enter__()
                except BaseException as exc:
                    journal(judge.cell / f'{judge.prefix}_responses.jsonl', {'index': self.owner.index, 'role': judge.role,
                            'response': None, 'usage': None,
                            'error_type': type(exc).__name__,
                            'wall_seconds': time.monotonic() - self.owner.started})
                    raise
                return self

            def raise_for_status(self):
                self.response.raise_for_status()

            def iter_bytes(self, **kwargs):
                for chunk in self.response.iter_bytes(**kwargs):
                    self.chunks.extend(chunk)
                    yield chunk

            def __exit__(self, kind, value, traceback):
                try:
                    data = json.loads(self.chunks) if self.chunks else None
                except ValueError:
                    data = None
                usage = data.get('usage') if isinstance(data, dict) else None
                journal(judge.cell / f'{judge.prefix}_responses.jsonl', {'index': self.owner.index, 'role': judge.role,
                        'response': data, 'usage': usage,
                        'error_type': kind.__name__ if kind else None,
                        'wall_seconds': time.monotonic() - self.owner.started}, (judge.key,))
                return self.owner.context.__exit__(kind, value, traceback)
        return Client()


def fixture_dispatch(workspace, original, allowed=None, receipt_path=None):
    """Allow exact regular fixture paths only, even if actor fabricates a tool call."""
    workspace = Path(workspace).resolve()
    if allowed is None:
        allowed = {'notes.txt', 'fibonacci.py', 'election.txt', 'control.txt', 'stale-patch.txt'}
    def dispatch(name, args, *pos, **kwargs):
        if name not in ('read_file', 'write_file', 'patch'):
            return json.dumps({'error': 'fixture sandbox: tool forbidden', 'executed': False})
        if name == 'patch' and (args.get('mode', 'replace') != 'replace' or args.get('patch')):
            return json.dumps({'error': 'fixture fence: single-file replacement only', 'executed': False})
        path = Path(str(args.get('path', '')))
        path = path if path.is_absolute() else workspace / path
        if path.is_symlink() or path.resolve().parent != workspace or path.name not in allowed:
            return json.dumps({'error': 'fixture sandbox: path forbidden', 'executed': False})
        if name == 'write_file' and len(str(args.get('content', '')).encode()) > 32768:
            return json.dumps({'error': 'fixture sandbox: write too large', 'executed': False})
        if name == 'patch' and len(str(args.get('new_string', '')).encode()) > 32768:
            return json.dumps({'error': 'fixture fence: replacement too large', 'executed': False})
        result = original(name, {**args, 'path': str(path)}, *pos, **kwargs)
        if receipt_path is not None:
            journal(receipt_path, {'name': name, 'arguments': args, 'result': result})
        return result
    return dispatch
