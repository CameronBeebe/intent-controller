# Intent-attractor offline verification receipt

## November 24 fork integration

Parents: fork main `879bee5fdf8b10b170a9b37b661d1afda80db59c` and controller
`6f815b22235dbfb0c4ca1dbf05566e6c62b4f70d`. The existing state-meta keys and JSON
remain readable. The actor borrows its writer handle; the gateway uses its owner
handle and the authorized physical target. A conditional insert or update checks
exact stored bytes, so a competing writer cannot slip between the revision check
and the write. The two-handle regression makes both writers observe the same old
value before either commits, for initial creation and for a subsequent revision.

Offline on macOS, using the prepared upstream interpreter, an isolated home and
`scripts/run_tests.sh --file-retries 0`:

| Check | Result |
| --- | --- |
| Controller, including real loop, owner RPC, replay, approval queue, CLI paint and assembled prompt (25 files) | 175 passed |
| Upstream overlap, including storage contracts, hosted policy, elicitation, delegated cwd, turn loop and generated contracts (22 files) | 629 passed, 1 Linux-only skip |
| Resume and deletion races, storage handles, pre-model veto and scripted prelude (9 files) | 26 passed |
| Stock prompt snapshot (1 file) | 1 passed, 1 failed before rendering: `_sysconfigdata__linux_darwin` (known host issue below) |

The six inherited failures below now assert the current contract: compact subgoals
on the wire with provenance retained in storage; fresh, non-redundant judge advice
only; and explicit current-request/finish receipts without ambient stop labels.
Two additional upstream resume failures exposed an unconditional control-state read;
the disabled path no longer needs it, and the transcript's deletion check runs first.
The hosted tool policy is consulted before the enabled protocol can touch the
profile's intent state, not just later when a tool receipt would be dispatched.

The tests use scripted model and judge transports. A local command actually wrote
and read back a file; the approval tests checked file absence after stale approvals.
The rotation test checks the physical target of reads and edits, not migration of
intent history across a change of session ID. No live benchmark or full-suite claim.

## Earlier fork integration and standing-loop guidance

Candidate base: fork `0bf34a165ea852ae6c422dd097d4511ac913a52e`; feature:
`ee5bd97ef1f72cbb5d22be0c82f628c7b0839f1e`. Merge commit
`2702e7c8b9b6f14b11ffd173c887281173de1e42` retains both as ancestors.
The fork still places runtime code in `agent/`, `gateway/`, etc.; `hermes_agent/`
contains only the rename-compatibility layer with an empty move table. No module
relocation or duplicate compatibility modules were introduced.

Three textual conflicts were resolved preserving upstream's outgoing-only empty
response hint, replay metadata/typed-ask support plus feature provenance, and both
RPC imports. Gateway contracts were regenerated. The recovery tests now verify
that new retry hints never enter canonical intent evidence and separately exercise
saved legacy provenance recovery, including manual edits/pins.

The new standing-prompt test failed first on missing evidence/state-loop guidance,
then passed for enabled and disabled real system-prompt assembly. It checks stable
tier placement, unchanged guidance under evolving state, protocol/authority rules,
and zero judge calls. Actor/Jev transports are scripted, not live inference.

With a freshly PM-built Python 3.14.9 test environment and the canonical runner
(`HERMES_TEST_FILE_RETRIES=0`):

- Focused 10-file run: **73 passed, zero failed** (prompt, actor-direct updates,
  protocol, approval boundaries/guards, content provenance, implicit retention,
  gateway commands and generated contracts).
- Broader 26-file run: **169 passed, 7 failed**. These overlap the focused run;
  do not add the counts. Six failures in `test_intent_refinement.py` and
  `test_intent_updates.py` also reproduce on unchanged feature `ee5bd97e`
  (**11 passed, 6 failed** in those two files): they expect full provenance in
  compact subgoal receipts or ambient `TURN_ONLY`/`INTENT_AMBIGUOUS` wire labels.
  They remain unresolved, rather than weakening assertions to manufacture green.
- The seventh failure is the stock prompt snapshot renderer on macOS:
  `_pin_environment()` changes `sys.platform` to `linux` before importing
  `hermes_time`/`zoneinfo`, causing `ModuleNotFoundError:
  _sysconfigdata__linux_darwin`. The renderer is unchanged from fork main;
  stock snapshot verification is therefore blocked on this host.

This is a tested candidate, **not an all-green integration or a runtime rollout**.
No production owner/alias was changed, no paid model call was made, no push/PR
was published, and evaluation/dev-launcher tooling was not ported. Historical
receipts below describe their original revisions, not this candidate.

## Actor-owned protocol v1

### Independent-review enforcement fixes

The independent scratch reproducer on `4ab74183` failed both enforcement cases:
the real terminal wrote `stale-executed` after approval changed intent, and a new
content-block user turn finished under `retain` without reconciliation. Its schema
conversion control passed. Both regressions are now ported to repository tests.

- RED: actual terminal approval seam plus real blocking gateway approval and desktop
  preparation: **3 failed**, each because the stale command created the file.
- RED: block-text/mixed-image/audio-only new turns × act/finish plus source evidence
  contracts: **8 failed**. First fix caught the input but exposed lossy SQLite replay;
  preserving bounded source metadata fixed the six replay failures too.
- GREEN: **81 passed, zero failed across 14 focused files**, canonical runner, no
  retries. Includes foreground/background stale refusals, cwd-preparation edit and
  unavailable-store refusal, callback isolation/reset, real blocking approval queues,
  desktop preparation, source identity/replay, restored privacy/provider/pause/goal/
  round-judgment-race cases, terminal approval compatibility and persistence.
- Independent scratch rerun plus boundary tests: **11 passed, zero failed**.
- `python scripts/check --base HEAD` against pre-fix `4ab74183`: **14 checks passed**,
  zero code-health blocking/advisory findings (rerun before commit).

Non-execution evidence is an absent disposable artifact **after** real approval was
answered and the worker joined, plus a stale/authority-unavailable tool result and
no repeated approval/command. The existing positive real write/read-back smoke
also passes. Actor/Jev inference is scripted; no authenticated live calls, production
state changes, gateway restarts or integration changes are claimed. Desktop queue
tests exercise backend preparation, not the Electron approval UI. Tests added here
are in `test_intent_protocol_boundaries.py`, `test_intent_content_sources.py` and
`test_intent_protocol_guards.py`; the earlier broad counts below are historical.

Canonical runner evidence for this implementation:

- 13 files, **397 passed**, zero failures: protocol, feedback, updates, hierarchy,
  working target, judge transport, provenance, gateway controls, CLI toolbar/basin,
  core `test_run_agent.py` and declared-read-intent compatibility.
- After excluding control receipts from completion evidence: focused protocol +
  feedback rerun, **22 passed**. The protocol suite has 19 tests, including a real
  local-terminal write/read-back artifact smoke; actor/Jev inference is scripted.
- `python scripts/check --base HEAD` (base before the implementation commit):
  **14 checks passed**; code-health reports zero blocking/advisory findings.
- Exact actor prepared request schemas/messages are captured inside protocol tests.
  No authenticated/paid live actor or Jev call was made, and no production owner
  was restarted. Real-user smoke procedure and schema examples are in
  [the current protocol guide](intent-attractor.md).

Extractor-era loop tests were ported/consolidated into explicit actor-declaration
scenarios, rather than retaining an auxiliary compatibility mode. Independent
judge transport and core nudge provenance contracts remain. Passing scripted
semantic proposals does not establish live classification accuracy.


> Historical extractor-era design/verification notes. The enabled runtime now uses
> mandatory actor-owned `intent_control`; see [current protocol and testing](intent-attractor.md).
> Old auxiliary-extraction sequences and test counts below are not current runtime claims.

## Provisional working-target correction

First-question presentation, uncertain extraction and extractor outage failed RED
with blank outcome; greetings failed with a task-like TURN_ONLY state. The real
loop now keeps a bounded source-anchored provisional aim, excludes suspect new
requirements, permits ordinary-stop assessment, and preserves manual controls and
later TLDR continuity. Added invalid-Jev-response coverage also passes. Owner
RPC/paint RED caught missing working status/initial cloud and later caught hidden
completion status behind a pending proposal; those are corrected. No title changes.

Focused combined run: **94 passed, 0 failed across 11 files** (before the final
invalid-Jev/visible-completion additions). Canonical isolated runner, mocked
external transports, no live calls. Final targeted rerun after those additions:
**23 passed, 0 failed across 4 files** (working lifecycle, owner RPC/paint, basin,
toolbar). Ruff and `git diff --check` passed. Confidence is the documented deterministic
fidelity minimum, not completion probability; missing/stale remains unknown.

The runner used an isolated, PM-prepared test interpreter selected via `HERMES_PYTHON`.
All runs used `scripts/run_tests.sh`, disposable HOME/HERMES_HOME, clean credential
runner, no file retries, no installs, no live model calls. The isolated runner's
per-file subprocesses mocked main/auxiliary/TypeSafe transports. This is not live
provider certification, calibration validation, or a whole-repository test run.

## Parent verification after review fixes

Final parent rerun including max-iteration/Codex provenance regressions and related Codex suites: **634 passed, 0 failed, 1 existing Linux-only skip across 21 files**. The earlier 17-file run passed 560 tests; the final run supersedes that count. The 13 review cases plus two additional producer cases cover extraction outage/concurrent-edit authority, next-turn synthetic-nudge exclusion (including budget-summary and Codex-incomplete messages), store failure handling, stable steer identity and sibling producer provenance. Main/auxiliary/Jev and verification tool responses remain mocked. This verifies loop/control integration, not live model judgment quality or a real terminal verification command.

Development launcher verification belongs to the separate dev-workflow/integration
layer, not this feature branch. The historical checks below cover feature behavior;
they do not establish authenticated live inference on this checkout.

## RED → GREEN observations

* Initial real turn returned “I can run its tests if you want” instead of executing
  the verification (1 failed). After the ordinary-stop integration, the same
  conversation executed a terminal verification and ended on the evidence-backed
  answer (1 passed).
* Manual intent workspace: missing `IntentStore.command` failed; authoritative
  edits/pins/history/revert survived extraction and reconstruction (2 passed).
* Classic CLI + actual gateway owner: unknown command/automation kind failed;
  after registry, owner route and canonical contract integration, both passed.
  The first owner test also caught the missing Pydantic literal before generation.
* Stop guards: 5 failures (pause, goal conflict, interrupt, assessment budget,
  extraction exception); 14 passed after fixes.
* Action fencing/privacy: 2 failures (old action executed; manual secret exported);
  18 passed after revision admission and bounded redaction changes.
* Config/status: missing default-off registry key failed; status/activation passed.
* Next-turn authoritative fields/large process summaries: 2 failures; then 22 passed.
* Exception/policy replay: 3 failures (auxiliary prior-value secret, missing replay,
  storage failure hitting main retry ladder); 28 passed across the two new files.
* Mid-turn steering, tool-call evidence and caller deadline: 3 failures, fixed.
* Interrupt during judge, internal-nudge provenance and invalid limits: 3 failures,
  fixed without weakening the continuation assertion.
* Disk failure/visible unavailability: 2 failures, fixed.
* Oversized evidence failed before transmission; bounded-envelope test passed.
  Scoped-secret test was corrected to model a routed profile, matching the existing
  documented single-profile environment fallback rather than inventing semantics.
* Restricted evidence exported before the guard (1 failed), then passed.
* Provider extra fields overwrote local provenance (1 failed), then allowlisted
  model/answers/usage preserved it.

## Final focused regression result

**547 passed, 0 failed, 1 skipped across 16 files**, canonical runner, 117.2 s.
The skip is the existing Linux-only goal test on macOS. New feature tests:
38 agent cases plus 2 CLI/gateway cases. `git diff --check` passed. Ruff on the
new implementation and tests passed (repository preview-selection warnings only).

Files exercised:

* `tests/agent/test_intent_attractor.py`
* `tests/gateway/test_intent_commands.py`
* `tests/agent/test_read_assessment_typesafe.py`
* `tests/agent/test_read_assessment.py`
* `tests/hermes_cli/test_intent_continuation_audit.py`
* `tests/hermes_cli/test_goals.py`
* `tests/hermes_cli/test_goal_dispatch.py`
* `tests/hermes_cli/test_goal_gates.py`
* `tests/hermes_cli/test_goal_judge_delegations.py`
* `tests/agent/test_verification_stop.py`
* `tests/agent/test_verification_stop_caching.py`
* `tests/agent/test_kanban_stop.py`
* `tests/gateway/test_session_viewer_automation.py`
* `tests/gateway/test_canonical_wire_contract.py`
* `tests/tui_gateway/contracts/test_generated.py`
* `tests/agent/test_run_agent.py`

The parent-owned launcher and production setup are outside this implementation
receipt. See `docs/intent-attractor.md` for the actual API, config and unsupported
paths, especially deterministic evidence selection and WAIT/event scheduling.
