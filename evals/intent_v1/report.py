"""Build a private, dependency-free HTML inspector; never executes artifact content.

Usage: python -m evals.intent_v1.report RUN_DIRECTORY [--out RUN_DIRECTORY/report.html]
Open the HTML directly, or serve only the run directory on 127.0.0.1.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any


METRICS = ('input_tokens', 'output_tokens', 'latency_ms', 'cost_usd')


def obj(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def reject_constant(value):
    raise ValueError('nonfinite JSON constant: ' + value)


def read_artifact(path, warnings, default=None):
    if path.is_symlink():
        warnings.append(f'{path.name}: symlink excluded')
        return default
    if not path.is_file():
        return default
    try:
        if path.suffix == '.jsonl':
            result = []
            for i, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    result.append(json.loads(line, parse_constant=reject_constant))
                except (ValueError, RecursionError):
                    warnings.append(f'{path.name}:{i}: malformed record retained as warning')
            return result
        return json.loads(path.read_text(encoding='utf-8'), parse_constant=reject_constant)
    except (OSError, ValueError, RecursionError) as exc:
        warnings.append(f'{path.name}: unreadable ({type(exc).__name__})')
        return default


def counts(values):
    count = Counter('passed' if v is True else 'failed' if v is False else 'unknown' for v in values)
    return {key: count[key] for key in ('passed', 'failed', 'unknown')}


def aggregate(cells, include_arms=True):
    metrics = {}
    for actor in ('actor', 'jev', 'evaluator'):
        metrics[actor] = {}
        for metric in METRICS:
            values = [c['metrics'][actor][metric] for c in cells]
            known = [v for v in values if v is not None]
            metrics[actor][metric] = {'sum': sum(known) if known else None,
                                      'known': len(known), 'missing': len(values) - len(known)}
    calls = {}
    for actor, key in (('actor', 'actor_calls'), ('jev', 'judge_calls'), ('evaluator', 'evaluator_calls')):
        known = [v for c in cells if (v := number(c['result'].get(key))) is not None]
        calls[actor] = {'sum': sum(known) if known else None, 'known': len(known),
                        'missing': len(cells) - len(known)}
    result = {'count': len(cells), 'harness': counts(c['harness'] for c in cells),
              'contracts': counts(c['contracts'] for c in cells), 'calls': calls,
              'modes': dict(Counter(c['mode'] for c in cells)), 'metrics': metrics,
              'evaluator_verdicts': dict(Counter(obj(c['result'].get('evaluator')).get('verdict', 'not_evaluated') for c in cells))}
    if include_arms:
        result['arms'] = {arm: aggregate([c for c in cells if c['arm'] == arm], False)
                          for arm in sorted(set(c['arm'] for c in cells))}
    return result


def load_run(root):
    root = Path(root).resolve()
    warnings = []
    plan = obj(read_artifact(root / 'plan.json', warnings, {}))
    summary = obj(read_artifact(root / 'summary.json', warnings, {}))
    planned = {}
    for item in rows(plan.get('cells')):
        name = item.get('cell') or f"{item.get('case', 'unknown')}-{item.get('arm', '?')}"
        if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
            warnings.append('Unsafe cell name in plan ignored')
            continue
        if name in planned:
            warnings.append(f'Duplicate planned cell: {name}')
        planned[name] = item
    summaries = {r['cell']: r for r in rows(summary.get('cells')) if isinstance(r.get('cell'), str)}
    last = next(reversed(summaries.values()), {})
    halted_after = (last.get('cell') if summary.get('mode') == 'live' and
                    (last.get('passed') is False or last.get('exit_code') not in (None, 0)) else None)
    names = set(planned)
    names.update(n for n in summaries if Path(n).name == n and n not in ('.', '..'))
    names.update(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                 and any((p / n).exists() for n in ('cell.json', 'result.json', 'actor_requests.jsonl')))
    cells = []
    for name in sorted(names):
        directory = root / name
        issues = []
        if directory.is_symlink():
            warnings.append(f'Symlink cell excluded: {name}')
            continue
        def read(filename, default=None):
            path = directory / filename
            if path.is_symlink():
                issues.append(f'{filename}: symlink excluded')
                return default
            return read_artifact(path, issues, default)
        config = obj(read('cell.json', planned.get(name, {})))
        result = obj(read('result.json', {}))
        attempted = name in summaries or any((directory / f).is_file() for f in
                    ('cell.json', 'result.json', 'actor_requests.jsonl', 'events.jsonl'))
        stopped = (not attempted and halted_after in planned and name in planned and
                   list(planned).index(name) > list(planned).index(halted_after))
        execution_status = 'attempted' if attempted else 'stopped' if stopped else 'unattempted'
        stop_reason = f'Halted after failed cell {halted_after}; not attempted' if stopped else None
        if not result:
            issues.append('Missing or unreadable result.json; result is unknown, not a success')
        mode = result.get('mode', plan.get('mode', summary.get('mode', 'unknown')))
        harness = result.get('harness_valid', result.get('passed'))
        s = summaries.get(name, {})
        if result.get('infrastructure_invalid') is True or ('harness_valid' not in result and
                (s.get('passed') is False or s.get('exit_code') not in (None, 0))):
            harness = False
        contracts = result.get('behavioral_contract_passed')
        if result.get('outcome') == 'censored':
            contracts = None
            stop_reason = 'Censored / incomplete: ' + str(result.get('stop_reason') or 'resource limit')
        metrics: dict[str, dict[str, Any]] = {actor: {key: None for key in METRICS} for actor in ('actor', 'jev', 'evaluator')}
        # Only explicit actor-scoped measurements: never treat default turn zeros or
        # synthetic Jev usage as observations, nor duplicate cumulative usage.
        if isinstance(mode, str) and mode.startswith('live'):
            for actor, alias in (('actor', 'actor'), ('jev', 'judge'), ('evaluator', 'evaluator')):
                source = obj(obj(result.get('metrics')).get(actor) or obj(result.get('metrics')).get(alias))
                usage = obj(obj(result.get('usage')).get(actor) or obj(result.get('usage')).get(alias))
                for key in METRICS:
                    metrics[actor][key] = number(source.get(key, usage.get(key)))
        issues.append('Accounting: unknown measurements are not zero; retries/auxiliary calls are not assumed covered')
        artifacts = {}
        for filename in ('events.jsonl', 'actor_requests.jsonl', 'actor_responses.jsonl',
                         'judge_requests.jsonl', 'judge_responses.jsonl', 'intent_snapshots.jsonl',
                         'transcript.json', 'tool_receipts.json', 'tool_receipts.jsonl', 'partial_request.json', 'intent.json',
                         'fixtures.before.json', 'fixtures.after.json', 'code_evidence.json', 'evaluator.json',
                         'evaluator_evidence.json', 'evaluator_provenance.json',
                         'evaluator_requests.jsonl', 'evaluator_responses.jsonl'):
            artifacts[filename] = read(filename)
        if isinstance(mode, str) and mode.startswith('live'):
            for actor, prefix in (('actor', 'actor'), ('jev', 'judge'), ('evaluator', 'evaluator')):
                captured = rows(artifacts.get(prefix + '_responses.jsonl'))
                declared = result.get(prefix + '_calls')
                indexes = [r.get('index') for r in captured]
                complete = (type(declared) is int and declared > 0 and len(captured) == declared
                            and all(type(i) is int for i in indexes)
                            and sorted(i for i in indexes if isinstance(i, int)) == list(range(1, declared + 1)))
                if not complete:
                    issues.append(f'{actor}: incomplete response accounting; transport sums unknown')
                    continue
                measurements = {key: [] for key in METRICS}
                for record in captured:
                    usage = obj(record.get('usage') or obj(record.get('response')).get('usage'))
                    measurements['input_tokens'].append(number(usage.get('input_tokens', usage.get('prompt_tokens'))))
                    measurements['output_tokens'].append(number(usage.get('output_tokens', usage.get('completion_tokens'))))
                    wall = number(record.get('wall_seconds'))
                    measurements['latency_ms'].append(wall * 1000 if wall is not None else None)
                    cost = usage.get('cost')
                    if isinstance(cost, str):
                        try:
                            cost = float(cost)
                        except ValueError:
                            cost = None
                    measurements['cost_usd'].append(number(cost))
                    if record.get('cost_source'):
                        issues.append(f"{actor} cost provenance: {record['cost_source']}")
                for key, values in measurements.items():
                    if metrics[actor][key] is None and all(v is not None for v in values):
                        metrics[actor][key] = sum(values)
                issues.append(f'{actor}: latency is summed captured transport wall time, not isolated model computation')
        for filename in ('actor_requests.jsonl', 'actor_responses.jsonl', 'transcript.json', 'intent.json'):
            if artifacts[filename] is None:
                issues.append(f'Missing {filename}')
        links = []
        if directory.is_dir():
            for path in sorted([*directory.glob('*.json'), *directory.glob('*.jsonl'),
                                *directory.glob('*.log'), *directory.glob('workspace/*')]):
                if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
                    links.append(str(path.relative_to(root)))
        case, _, arm = name.rpartition('-')
        cells.append({'name': name, 'case': config.get('case', case or name), 'arm': config.get('arm', arm),
                      'mode': mode, 'config': config, 'result': result, 'harness': harness,
                      'contracts': contracts, 'metrics': metrics, 'warnings': issues,
                      'execution_status': execution_status, 'stop_reason': stop_reason,
                      'artifacts': artifacts, 'links': links})
    execution = {'planned': len(planned),
                 'attempted': sum(c['execution_status'] == 'attempted' for c in cells),
                 'stopped': sum(c['execution_status'] == 'stopped' for c in cells),
                 'unattempted': sum(c['execution_status'] != 'attempted' for c in cells)}
    if summary.get('count') is not None and summary['count'] != execution['attempted']:
        warnings.append(f"Summary count {summary['count']} disagrees with attempted cells {execution['attempted']}")
    if summary.get('planned_count') is not None and summary['planned_count'] != execution['planned']:
        warnings.append(f"Summary planned_count {summary['planned_count']} disagrees with plan cells {execution['planned']}")
    return {'root': str(root), 'name': root.name, 'plan': plan, 'summary': summary,
            'cells': cells, 'aggregate': aggregate(cells), 'execution': execution, 'warnings': warnings}


def render_html(data):
    """Serialize untrusted data safely in a non-executable JSON script element."""
    import base64
    import hashlib
    script = Path(__file__).with_suffix('.js').read_text(encoding='utf-8')
    template = Path(__file__).with_suffix('.html').read_text(encoding='utf-8')
    encoded = json.dumps(data, ensure_ascii=True, allow_nan=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    # Insert data last: artifact text must never be interpreted as a template token.
    return template.replace('__SCRIPT_HASH__', digest).replace('__REPORT_SCRIPT__', script).replace('__REPORT_DATA__', encoded)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    root = args.run.resolve()
    if not root.is_dir():
        parser.error('run must be an existing artifact directory')
    output = args.out.resolve() if args.out else root / 'report.html'
    # Relative evidence links intentionally stay inside the run's serving root.
    if output.parent != root or output.suffix.lower() != '.html':
        parser.error('--out must be an HTML file directly inside the run directory')
    data = load_run(root)
    output.write_text(render_html(data), encoding='utf-8')
    print(json.dumps({'html': str(output), 'aggregate': data['aggregate'], 'warnings': data['warnings']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
