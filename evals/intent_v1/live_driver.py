"""Bounded live driver. One simple case only; inspect before expanding."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from evals.intent_v1.live import actual_spend, money
from evals.intent_v1.semantic import VERSION, INVARIANT_VERSION, task, questions
from evals.intent_v1.worker import FIXTURE_VERSION
from evals.intent_v1 import sustained
from evals.intent_v1.tranche import TASKS, VERSION as TRANCHE_VERSION, fixtures_for, manifest_for
from evals.intent_v1.live_auth import preflight, JUDGE_MODEL
from evals.intent_v1.run import ROOT, config, child_env, save


def run_live(args, parser):
    cap = None
    if args.live:
        try:
            cap = money(args.actor_budget_usd)
            if not 0 < cap <= 5:
                raise ValueError('must be positive and at most $5')
        except ValueError:
            parser.error('--actor-budget-usd must be positive and at most $5')
        if len(args.cases) != 1 or args.cases[0] not in ('overhead', 'topic_swap', *TASKS):
            parser.error('live supports one frozen case per bounded invocation')
        if len(set(args.arms)) != len(args.arms):
            parser.error('duplicate arms are not allowed')
    from hermes_constants import get_hermes_home
    home = args.credential_home or get_hermes_home()
    out = args.out.resolve()
    if args.live and (not args.spend_root.is_dir() or not out.is_relative_to(args.spend_root.resolve())):
        parser.error('--out must be inside the existing authorized --spend-root')
    out.mkdir(parents=True, exist_ok=False)
    report, credentials = preflight(home, args.actor_model, allow_launch_env=args.allow_launch_env)
    save(out / 'preflight.json', report)
    if not args.live or not report['ready']:
        print(json.dumps(report))
        return 0 if report['ready'] else 2

    git = lambda *a: subprocess.check_output(['git', *a], cwd=ROOT, text=True)
    (out / 'source.diff').write_text(git('diff', 'HEAD', '--'))
    # Each independent arm gets its own allowance; no shared wall starvation.
    experiment_seconds = args.cell_timeout * len(args.arms) + 30
    deadline = time.time() + experiment_seconds
    case = args.cases[0]
    fixtures = fixtures_for(case)
    fixture_version = TRANCHE_VERSION if case in TASKS else FIXTURE_VERSION
    if case in sustained.TASKS:
        fixture_version = sustained.VERSION
    invariant_version = sustained.VERSION if case in sustained.TASKS else INVARIANT_VERSION
    manifest = manifest_for(case)
    common = {'actor_model': args.actor_model, 'actor_canonical_slug': report['catalog']['canonical_slug'],
              'actor_metadata': report['catalog'],
              'fixture_version': fixture_version, 'fixture_manifest': manifest,
              'invariant_version': invariant_version,
              'spend_root': str(args.spend_root.resolve()), 'actor_budget_usd': str(cap),
              'max_output_tokens': 1024, 'max_iterations': args.max_iterations,
              'deadline_epoch': deadline}
    cells = []
    for arm in args.arms:
        cfg = config(arm, continuation_comparison=case in sustained.TASKS or 'D' in args.arms)
        cfg.update(model=JUDGE_MODEL, max_assessments=None)
        cells.append({'case': args.cases[0], 'task': task(args.cases[0]), 'arm': arm, 'config': cfg, **common})
    plan = {'mode': 'live', 'grader_version': VERSION, 'evaluator_questions': questions(),
            'fixture_version': fixture_version, 'fixture_manifest': manifest,
            'invariant_version': invariant_version,
            'fixtures': fixtures, 'source_sha': git('rev-parse', 'HEAD').strip(),
            'status': git('status', '--porcelain'), 'cells': cells,
            'source_files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in Path(__file__).parent.glob('*.py')},
            'limits': {**common, 'judge_calls_per_cell': None, 'wall_seconds_per_cell': args.cell_timeout,
                       'experiment_wall_seconds': experiment_seconds,
                       'retry_policy': 'none; actual costs only; in-flight call can overshoot $5'},
            'live_supported': True,
            'interpretation': 'Tiny live smoke only; no superiority claim or upstream-equivalence proof.'}
    save(out / 'plan.json', plan)
    results = []
    for cell_plan in cells:
        name = cell_plan['case'] + '-' + cell_plan['arm']
        cell = out / name
        cell.mkdir()
        for directory in ('home', 'hermes', 'workspace', 'runtime', 'tmp', 'cache', 'config'):
            (cell / directory).mkdir()
        remaining = deadline - time.time()
        cell_plan = {**cell_plan, 'deadline_epoch': min(deadline, time.time() + args.cell_timeout)}
        save(cell / 'cell.json', cell_plan)
        code = 124
        if remaining > 0:
            with (cell / 'stdout.log').open('w') as stdout, (cell / 'stderr.log').open('w') as stderr:
                try:
                    child = subprocess.run([sys.executable, '-m', 'evals.intent_v1.live_worker', str(cell)],
                        cwd=ROOT, env=child_env(cell), input=json.dumps(credentials), text=True,
                        stdout=stdout, stderr=stderr, timeout=min(args.cell_timeout, remaining), check=False)
                    code = child.returncode
                except subprocess.TimeoutExpired:
                    pass
        target = cell / 'result.json'
        if not target.exists():
            censored = code == 124
            save(target, {'mode': 'live', 'passed': False, 'infrastructure_invalid': not censored,
                          'harness_valid': censored, 'outcome': 'censored' if censored else 'infrastructure_invalid',
                          'incomplete': censored, 'stop_reason': 'worker wall deadline' if censored else None,
                          'exit_code': code, 'partial_evidence': ['actor_requests.jsonl', 'actor_responses.jsonl',
                              'partial_request.json', 'transcript.json', 'tool_receipts.jsonl', 'workspace']})
        record = json.loads(target.read_text())
        results.append({'cell': name, 'passed': code == 0 and record.get('passed', False),
                        'outcome': record.get('outcome'), 'infrastructure_invalid': record.get('infrastructure_invalid', False),
                        'harness_valid': record.get('harness_valid', False),
                        'evaluator_verdict': record.get('evaluator', {}).get('verdict', 'unavailable'),
                        'behavioral_contract_passed': record.get('behavioral_contract_passed', False), 'exit_code': code})
        # A genuine task failure is data, not a prerequisite for other approved arms.
        # Auth/accounting/transport failures still halt; never reroll a failed cell.
        if record.get('infrastructure_invalid') or (code not in (0, 1) and record.get('outcome') != 'censored'):
            break
    summary = {'mode': 'live', 'cells': results, 'count': len(results), 'planned_count': len(cells),
               'harness_passed': sum(r['harness_valid'] for r in results),
               'behavioral_contract_passed': sum(r['behavioral_contract_passed'] for r in results),
               'live_supported': True, 'actor_spend': actual_spend(args.spend_root),
               'actor_budget_usd': str(cap), 'judge_count_cap': None}
    save(out / 'summary.json', summary)
    print(json.dumps(summary))
    return 0 if len(results) == len(cells) and all(r['passed'] for r in results) else 1
