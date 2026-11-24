# Intent controller evaluation

Run from the repository root with the project's test Python. No provider keys are needed for the scripted run or the tests:

```sh
run_root=$(mktemp -d)
python -m evals.intent_v1.run --dry-run --out "$run_root/readiness"
python -m evals.intent_v1.report "$run_root/readiness"
bash scripts/run_tests.sh tests/evals/test_intent_v1_*.py
```

Open `report.html` in the run directory. Without `--dry-run` or a live flag the runner writes only a plan. It refuses to overwrite a run directory.

| Arm | Intent controller | In-loop Jev | Semantic continuation |
| --- | --- | --- | --- |
| A | Disabled | Off | Off |
| B | Actor-maintained | Off | Off |
| C | Actor-maintained | Advisory | Off |

Each cell uses a fresh process, home, persistence store and workspace. Scripted actors exercise real file tools and state transitions for `topic_swap` (replacement request), `overhead` (presentation change) and `stale_edit` (steering while a write response is in flight). A has no revision fence: its scripted stale write can fail the behavioral contract while the harness passes. These are plumbing checks, not measurements of model quality, costs or the benefit of the controller.

## Live mode is separate

`--preflight-live` contacts providers for read-only credential and catalog checks; it is not an offline command. `--live` performs inference. Use only with separate authorization for the actor, judge, task, arms, data flow and spending threshold. See `python -m evals.intent_v1.run --help` for arguments. Select one of `topic_swap`, `overhead`, `artifact_fix`, `sustained_review` or `scope_expansion`; `stale_edit` is scripted only.

The driver requires an explicit `--actor-budget-usd` (positive, at most 5) and a new `--out` inside an existing `--spend-root`. Reuse the same authorized root across runs. The stop checks provider-reported actor cost, deduplicated by response ID; missing cost is unknown and an in-flight call can exceed the threshold. This is not a total billing cap. The configured iteration and wall limits bound each cell. Infrastructure failures halt the run; behavioral failures are retained without blocking the next approved arm. Resource stops are reported as incomplete.

Each live arm exposes the same fixture files and ordinary tools. An independent post-run evaluator receives the frozen task, selected transcript evidence and artifact changes, including in A and B. Only C uses in-loop judgments. See [Grading and evidence](GRADING.md).

Keep run directories and generated HTML private and outside the checkout. They can contain full prompts, responses, fixture contents, absolute paths, logs and an isolated state store. Do not publish them as part of the eval source. Live credentials are resolved in the selected profile; standalone launch-environment fallback is opt-in. Credentials go to the worker over stdin, not in the plan, worker command line or report.
