"""Intent plumbing validation and separately gated bounded live smoke."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from evals.intent_v1.sustained import TASKS as WORK_CASES

ROOT = Path(__file__).resolve().parents[2]
CASES = ('topic_swap', 'stale_edit', 'overhead')
LIVE_CASES = ('sustained_review', 'artifact_fix', 'scope_expansion', *WORK_CASES)
LIVE_BLOCKERS = [
    'Use separate --live mode with explicit --actor-budget-usd (at most $5) and retained --spend-root',
    'Live requires successful scoped auth and fresh catalog preflight',
    'Live supports one frozen overhead or topic_swap case; stale_edit remains offline',
    'Verify disabled A against the pinned upstream baseline before comparative claims',
]


def config(arm, *, continuation_comparison=False):
    limit = 2 if arm == 'D' or (arm == 'C' and continuation_comparison) else 0
    return {'enabled': arm != 'A', 'judge_enabled': arm in ('C', 'D'),
            'semantic_continuation': arm == 'D', 'provider': 'typesafe', 'model': 'offline-judge',
            'max_assessments': 20, 'max_continuations': limit, 'feedback_interval': 1}


def save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def child_env(cell):
    # Allowlist, not suffix filtering: no inherited profiles, credentials, plugins or remote routing.
    return {'PATH': os.pathsep.join([str(Path(sys.executable).parent), '/usr/bin', '/bin']),
            'HOME': str(cell / 'home'), 'HERMES_HOME': str(cell / 'hermes'),
            'HERMES_RUNTIME_DIR': str(cell / 'runtime'), 'TMPDIR': str(cell / 'tmp'),
            'XDG_CACHE_HOME': str(cell / 'cache'), 'XDG_CONFIG_HOME': str(cell / 'config'),
            'TERMINAL_CWD': str(cell / 'workspace'), 'TERMINAL_ENV': 'local',
            'TERMINAL_HOME_MODE': 'profile', 'PYTHONHASHSEED': '0', 'TZ': 'UTC',
            'LANG': 'en_US.UTF-8'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path, help='New retained directory; never overwritten')
    parser.add_argument('--dry-run', action='store_true', help='Real AIAgent + file tools, scripted transports only')
    parser.add_argument('--live', action='store_true', help='Bounded real smoke; explicit cap and ledger required')
    parser.add_argument('--preflight-live', action='store_true', help='Read-only credential/catalog check; zero inference')
    parser.add_argument('--credential-home', type=Path, help='Profile to resolve read-only (default current Hermes home)')
    parser.add_argument('--allow-launch-env', action='store_true',
                        help='Authorize standalone OPENROUTER_API_KEY/TYPESAFE_API_KEY launch fallbacks only')
    parser.add_argument('--actor-model', choices=('anthropic/claude-haiku-5.5', 'openai/gpt-6-luna'), default='anthropic/claude-haiku-5.5')
    parser.add_argument('--actor-budget-usd', help='Stop before next call at provider-reported total, at most $5')
    parser.add_argument('--max-iterations', type=int, default=32, help='Existing agent iteration bound per turn (1..128; default 32)')
    parser.add_argument('--cell-timeout', type=int, default=240, help='Killable cell seconds (1..900; default 240)')
    parser.add_argument('--spend-root', type=Path, help='Existing authorized artifact tree; reuse across runs, not a ledger')
    parser.add_argument('--cases', nargs='+', choices=CASES + LIVE_CASES, default=list(CASES))
    parser.add_argument('--arms', nargs='+', choices=list('ABCD'), default=list('ABC'))
    args = parser.parse_args(argv)
    if args.live or args.preflight_live:
        if not 1 <= args.max_iterations <= 128 or not 1 <= args.cell_timeout <= 900:
            parser.error('iterations must be 1..128 and cell timeout 1..900 seconds')
        if args.dry_run:
            parser.error('live and scripted offline modes are separate')
        if args.live and (not args.actor_budget_usd or not args.spend_root):
            parser.error('live requires --actor-budget-usd and --spend-root before any inference')
        from evals.intent_v1.live_driver import run_live
        return run_live(args, parser)
    if 'D' in args.arms:
        parser.error('arm D is an explicit live comparison, not a scripted baseline')
    if any(c in LIVE_CASES for c in args.cases):
        parser.error('scale tranche cases require --live; no scripted actor substitutes')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    git = lambda *a: subprocess.check_output(['git', *a], cwd=ROOT, text=True)
    diff = git('diff', 'HEAD', '--')
    (out / 'source.diff').write_text(diff)
    plan = {'mode': 'scripted-offline' if args.dry_run else 'preflight-only',
            'source_sha': git('rev-parse', 'HEAD').strip(), 'status': git('status', '--porcelain'),
            'source_files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in Path(__file__).parent.glob('*.py')},
            'cells': [{'case': c, 'arm': a, 'config': config(a)} for c in args.cases for a in args.arms],
            'limits': {'actor_calls_per_cell': 16, 'judge_calls_per_cell': 20, 'wall_seconds_per_cell': 60,
                       'retry_policy': 'none; failed directories retained'},
            'live_supported': False, 'live_blockers': LIVE_BLOCKERS,
            'interpretation': 'Scripted fixtures prove plumbing only, not model quality, token cost or comparative efficacy.'}
    save(out / 'plan.json', plan)
    if not args.dry_run:
        print(str(out / 'plan.json'))
        return 0
    results = []
    for cell_plan in plan['cells']:
        name = f"{cell_plan['case']}-{cell_plan['arm']}"
        cell = out / name
        cell.mkdir()
        for directory in ('home', 'hermes', 'workspace', 'runtime', 'tmp', 'cache', 'config'):
            (cell / directory).mkdir()
        save(cell / 'cell.json', cell_plan)
        with (cell / 'stdout.log').open('w') as stdout, (cell / 'stderr.log').open('w') as stderr:
            try:
                completed = subprocess.run([sys.executable, '-m', 'evals.intent_v1.worker', str(cell)],
                                           cwd=ROOT, env=child_env(cell), stdout=stdout, stderr=stderr,
                                           timeout=60, check=False)
                code = completed.returncode
            except subprocess.TimeoutExpired:
                code = 124
        target = cell / 'result.json'
        if not target.exists():
            save(target, {'passed': False, 'infrastructure_invalid': True, 'exit_code': code})
        result = json.loads(target.read_text())
        results.append({'cell': name, 'passed': code == 0 and result.get('passed', False),
                        'behavioral_contract_passed': result.get('behavioral_contract_passed', False),
                        'exit_code': code})
    summary = {'mode': 'scripted-offline', 'cells': results, 'count': len(results),
               'harness_passed': sum(r['passed'] for r in results),
               'behavioral_contract_passed': sum(r['behavioral_contract_passed'] for r in results),
               'live_supported': False}
    save(out / 'summary.json', summary)
    print(json.dumps(summary))
    return 0 if all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
