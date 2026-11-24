"""Actor-owned working interpretations and advisory fidelity annotations, never permission."""
import copy
import math
import time
import uuid
from dataclasses import asdict

from agent.intent_attractor import THRESHOLDS, bounded, questions
from agent.intent_attractor_updates import IntentUpdate, apply_update


def fidelity_decision(answers):
    if not answers:
        return 'UNAVAILABLE'
    p = {k: answers[k]['noul'] for k in ('coverage', 'consistent', 'unsupported')}
    hi, lo = THRESHOLDS['high'], THRESHOLDS['low']
    if p['coverage'] >= hi and p['consistent'] >= hi and p['unsupported'] <= lo:
        return 'ACCEPTED'
    if p['coverage'] <= lo or p['consistent'] <= lo or p['unsupported'] >= hi:
        return 'REJECTED'
    return 'UNCERTAIN'


def specification(state):
    return {'fields': state['fields'], 'subgoals': state.get('subgoals', [])}


def confidence_projection(state):
    annotation = (state or {}).get('fidelity_annotation') or {}
    probabilities = {k: v.get('noul') for k, v in (annotation.get('answers') or {}).items()
                     if k in ('coverage', 'consistent', 'unsupported')}
    values = [probabilities.get(k) for k in ('coverage', 'consistent', 'unsupported')]
    current = annotation.get('revision') == (state or {}).get('revision')
    valid = all(type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 1 for v in values)
    score = min(values[0], values[1], 1 - values[2]) if current and valid else None
    return {'visual_confidence': score, 'fidelity_probabilities': probabilities,
            'fidelity_revision': annotation.get('revision'), 'fidelity_phase': annotation.get('phase', ''),
            'fidelity_target': annotation.get('target', '')}


def establish_working_target(state, sources, decision, outcome=''):
    """Legacy extraction fallback, not actor-update adoption policy."""
    if state['fields']['outcome'].strip():
        return state
    source = next((s for s in sources if s['text'].strip()), None)
    if source is None:
        state['decision'] = 'IDLE'
        return state
    if 'outcome' in state['pins'] or state['origins']['outcome'] == 'user':
        return state
    state['fields']['outcome'] = outcome if outcome.strip() else bounded(source['text'], 1000)['text']
    state['interpretation'] = {'status': 'provisional', 'decision': decision,
                               'basis': 'extracted_outcome' if outcome.strip() else 'bounded_user_expression',
                               'source': source}
    state['update_type'] = 'refinement'
    state['trajectory'][-1].update(kind='revise', label='Current working aim established')
    state['history'][-1]['fields'] = copy.deepcopy(state['fields'])
    return state


def pending(previous, sources, proposal, decision):
    """Read compatibility for legacy extraction-era pending specifications."""
    kind = 'refinement' if (proposal or {}).get('update_type') in ('refinement', 'replacement', 'reconciliation') else 'ambiguous'
    state = apply_update(previous, sources, IntentUpdate(kind, {}))
    state['decision'] = 'UNAVAILABLE' if decision == 'UNAVAILABLE' else 'FIDELITY_UNCERTAIN'
    state['pending_proposal'] = {'proposal': proposal, 'decision': decision}
    state['trajectory'][-1]['label'] = f'Proposed changes: {decision}' if proposal else 'Extraction unavailable'
    outcome = (proposal or {}).get('fields', {}).get('outcome', '')
    return establish_working_target(state, sources, decision, outcome)


def reconcile(previous, sources, update):
    """Deterministic representation update. No decision model owns these fields."""
    candidate = apply_update(previous, sources, update)
    candidate['interpretation'] = {'status': 'unassessed', 'decision': 'UNASSESSED', 'owner': 'actor'}
    if specification(candidate) != specification(previous):
        candidate['trajectory'][-1]['label'] = 'Actor working interpretation applied'
    return candidate


def assess_applied(agent, previous, applied, update, extraction_request, extraction_response):
    """Annotate an already committed revision; stale feedback never restores an old aim."""
    from agent.intent_attractor_judge import assess
    from agent.intent_attractor import settings
    if specification(applied) == specification(previous):
        return applied, None
    cfg = settings()
    if cfg.get('judge_enabled', True) is False:
        return applied, None
    evidence = list({s['anchor']: s for s in previous['sources'] + applied['sources']}.values())
    evidence = evidence if len(evidence) <= 8 else evidence[:2] + evidence[-6:]
    context = {
        'previous_working_specification': specification(previous),
        'proposed_extracted_specification': specification(applied),
        'original_user_expressions': evidence,
        'authoritative_user_edits': {k: v for k, v in applied['fields'].items() if applied['origins'][k] == 'user'},
        'pins': applied['pins'], 'immediate_turn_request': applied['immediate_request'],
        'proposed_operations': asdict(update), 'application': 'already applied; assessment is advisory',
    }
    rubric = {k: v for k, v in questions().items() if k in ('coverage', 'unsupported', 'consistent')}
    cap = cfg.get('max_assessments')
    if cap is not None and applied['assessments_used'] >= cap:
        record = {'status': 'unavailable', 'answers': {}, 'reason': 'assessment_budget_exhausted'}
    else:
        applied['assessments_used'] += 1
        if not agent._intent_attractor.save(applied, applied['revision']):
            raise ValueError('intent changed before advisory assessment')
        try:
            record = assess({'fidelity_context': context}, cfg, assessment_questions=rubric)
        except Exception:  # health: allow BLE001 -- optional judge cannot undo an applied interpretation
            record = {'status': 'unavailable', 'answers': {}, 'error': 'fidelity_assessment_unavailable'}
    decision = fidelity_decision(record.get('answers'))
    record.update(id=f'{time.time_ns():020d}-{uuid.uuid4().hex}', phase='reconciliation',
                  session_id=agent.session_id, revision=applied['revision'],
                  previous_revision=previous['revision'], applied_revision=applied['revision'],
                  application='APPLIED', advisory=True, thresholds=THRESHOLDS.copy(), decision=decision,
                  round=applied.get('tool_rounds', 0),
                  extraction_request=extraction_request, extraction_response=bounded(extraction_response, 12000))
    applied['interpretation'] = {'status': 'validated' if decision == 'ACCEPTED' else 'unassessed' if decision == 'UNAVAILABLE' else 'provisional',
        'owner': 'actor', 'decision': decision, 'advisory': True, 'assessment_id': record['id']}
    applied['fidelity_annotation'] = {'revision': applied['revision'], 'phase': 'reconciliation',
        'target': 'working', 'advisory': True, 'assessment_id': record['id'], 'answers': record.get('answers')}
    if decision == 'ACCEPTED':
        applied['validated_specification'] = copy.deepcopy({**specification(applied),
            'revision': applied['revision'], 'assessment_id': record['id']})
    if not agent._intent_attractor.save(applied, applied['revision']):
        record['freshness'] = 'STALE'
        record['delivery'] = {'delivered': False, 'reason': 'stale'}
        agent._intent_attractor.record(record)
        raise ValueError('intent changed during advisory assessment; use current revision')
    record['freshness'] = 'current'
    return applied, record
