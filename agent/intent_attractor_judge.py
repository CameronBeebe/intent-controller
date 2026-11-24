"""Typed intent assessments; explicit TypeSafe route, scoped secrets, no LLM fallback."""
import json
import time
import threading
import contextvars
from typing import Any

_SLOTS = threading.BoundedSemaphore(2)


def redact_evidence(value: Any) -> Any:
    from agent.intent_attractor import bounded
    if isinstance(value, str):
        return bounded(value, len(value))['text']
    if isinstance(value, dict):
        return {key: redact_evidence(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_evidence(item) for item in value]
    return value


def bounded_evaluate(state, model, key, *, wait_seconds=3.0, assessment_questions=None):
    from agent.read_assessment_typesafe import evaluate
    from agent.intent_attractor import questions
    from tools.daemon_pool import DaemonThreadPoolExecutor
    if not _SLOTS.acquire(blocking=False):
        raise TimeoutError('judge_busy')
    def run():
        try:
            return evaluate(state, model, key, assessment_questions=assessment_questions or questions(), labeler=lambda data: data)
        finally:
            _SLOTS.release()
    pool = DaemonThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(contextvars.copy_context().run, run).result(timeout=wait_seconds)
    finally:
        pool.shutdown(wait=False)


def assess(state, settings, assessment_questions=None):
    from agent.intent_attractor import questions
    from agent.secret_scope import get_secret
    from agent.intent_attractor import bounded
    state = redact_evidence(state)
    # Whole values are redacted before clipping. If the structured envelope is
    # oversized, retain an explicitly truncated evidence copy rather than send
    # an unbounded request or silently pretend that missing evidence is complete.
    if len(json.dumps(state, ensure_ascii=False).encode()) > 22000:
        state = {'bounded_evidence': bounded(state, 6000),
                 'omitted_structure': True, 'recovery': 'Inspect original source anchors in the local session.'}
    payload = {'model': settings.get('model'),
               'state': json.dumps(state, ensure_ascii=False, allow_nan=False), 'questions': assessment_questions or questions()}
    record = {'provider': settings.get('provider'), 'requested_model': settings.get('model'),
              'rubric_version': 'intent-v3-means', 'payload': payload, 'status': 'unavailable',
              'reason': 'invalid_configuration', 'model': None, 'usage': None}
    started = time.monotonic()
    try:
        if settings.get('provider') == 'typesafe' and settings.get('model'):
            key = get_secret('TYPESAFE_API_KEY')
            if not key:
                record['reason'] = 'missing_key'
            else:
                result = bounded_evaluate(state, settings['model'], key, assessment_questions=assessment_questions)
                record.update({k: result[k] for k in ('model', 'answers', 'usage')})
                record.update(status='available', reason='fixed_rubric_not_generated_rationale')
    except Exception:  # health: allow BLE001 -- never log transport exceptions containing credentials/evidence
        record['reason'] = 'assessment_error'
    record['latency_ms'] = round((time.monotonic() - started) * 1000)
    return record
