# Intent-aware continuation: source audit and branch plan

## Decision

The gap is real and reproducible: Hermes tells the model to finish the task, but does not ordinarily classify conversational intent into an active goal or semantically veto premature completion. Existing persistent goals require explicit activation. Existing automatic nudges are narrower than the user's complaint.

Build a separate intent-continuation feature from the fork's current main. Reuse existing goal state/continuation concepts and the merged decision-model transport work, but do not treat a read-relevance classifier or a larger regex as a completion supervisor. The desired UX is automatic assessment for ordinary authorized tasks, not another per-task `/goal` ceremony. A one-time provider/privacy configuration is distinct from requiring repeated task activation.

This branch is an audit and executable characterization, not an enabled supervisor. No runtime implementation or production configuration was changed.

## Source snapshots and evidence strength

- Fork base: `334ab2ba074c75b72c101c0efefd9c2c273ebc0c`.
- Installed source: `bf0203a794a07aac3ff764972ab9087baa4e4f4e`.
- `hermes_cli/goals.py` is byte-identical across those snapshots; parent independently compared SHA-256 values. Other prompt/loop files differ, so the fork is not assumed to match every production detail.
- Source citations below are relative to the fork base unless explicitly stated.
- Parent read the key goal, command, stop-gate, detector and adapter paths and ran the real canonical per-file test runner in isolated state. Child investigations supplied broader callsite/surface audits; their untested race/liveness findings remain qualified below.
- Read-only inspection of this conversation's saved state found no goal row. Its deduplicated stored prompt contains “Finishing the job,” “Tool-use enforcement,” “Keep working until,” and “Execution discipline”; it contains no `/goal` instruction. These are stored prompt facts, not a claim of complete wire capture.

## 1. Explicit activation is proven, not inferred from docs

`GoalManager.evaluate_after_turn` exits immediately with `verdict=inactive`, `should_continue=False` when state is absent or not active (`hermes_cli/goals.py:1484-1494`). It does not call a classifier to decide whether to create a goal. `GoalManager.set` explicitly creates active state (`:1172-1181`).

The shared slash-command parser creates it through `_set` (`hermes_cli/goal_command.py:137-172`). CLI, messaging, local gateway and retained TUI adapters route to that parser; authorized session RPC can also invoke manager methods. Raw Python integrations may explicitly call the manager, but must drive continuation themselves. There is no native model-callable session-goal tool in the inspected tracked code.

Exception: `kanban_create(goal_mode=true)` enables a goal loop for the new card's worker (`tools/kanban_tools_schemas.py:484-504`). This is explicit per-card activation and does not set a standing goal in the caller's conversation. `goal_mode` defaults false.

Executable characterization now demonstrates:

- With no goal, “I wrote the implementation. Next I could run the tests if you want” produces inactive and no judge call.
- After explicitly setting a goal, a judge CONTINUE result produces a continuation and persists goal state across manager reconstruction.

These tests exercise actual manager and persistence code with model transport replaced, not a simulated manager.

## 2. What intent representation already exists

Persistent state includes the objective string, active/paused/done/cleared status, turn counters, last verdict/reason, subgoals, contract, wait barriers, quality gates and error streaks (`hermes_cli/goals.py:420-481`). It is stored under `goal:<session_id>` in profile SQLite state metadata (`:501-502,636-677`). Compression migrates existing state; it does not infer new goals.

The optional contract has five fields (`:287-305`):

- outcome
- verification
- constraints
- boundaries
- stop_when

A normal `/goal` stores the supplied text and parses recognized inline fields. `/goal draft` asks an auxiliary model to infer those fields. Drafting receives only `Objective:\n` plus the first 4,000 characters of the supplied objective (`:1044-1073`), not conversation history, actual tools, files, or project evidence. It immediately activates the goal and kicks off work; “draft” does not mean a pending approval draft.

This is a useful container, but not a continually maintained model of conversational intent. Clarifications, changing scope, implicit acceptance criteria and prior commitments are not automatically reconciled into it.

### First-turn contract gap

The command stores the full contract but returns `goal_kick_prompt(state.goal, ...)`, which contains the headline rather than the contract (`goal_command.py:150-172`). Subsequent continuations include the contract (`goals.py:1570-1582`). Parent's characterization confirms that inline verification/boundaries are stored but absent from the initial kickoff prompt. The command's human-facing output displays them; that is not the same as supplying them to the acting model.

## 3. What the existing judge sees and controls

`judge_goal` accepts goal, last_response, optional contract/subgoals, background-process summaries and active-delegation count (`hermes_cli/goals.py:894-959`). It sends two text messages via auxiliary task `goal_judge`; no tool schema is supplied.

Actual bounds:

- Goal: first 2,000 characters.
- Final response: first 4,000 characters, not the last 4 KB suggested by documentation.
- Contract plus folded subgoals: first 2,500 characters; standalone subgoals: first 2,000.
- Limited live-process identity/command/trigger/output summaries, delegation count and current time.

Missing: full tool trajectory, actual available actions, acting model's system instructions, todo state, independent artifact inspection, and provenance-grounded proof behind completion claims. A contract strengthens the grading instructions but does not authenticate the final answer's claims.

Verdicts are DONE, CONTINUE, BLOCKED and WAIT. This is generated JSON classification, not a Jev probability policy. CONTINUE appends a normal continuation message through the hosting surface; it does not rewrite the cached system prompt. A plain continuation says to take the next concrete step, explicitly state completion, or explain the blocker (`goals.py:58-94`).

Quality gates are separate, explicitly configured shell commands. They execute before the judge; a failure supplies command, exit code and bounded output as the next prompt. Passing gates allow normal judging; passing output is not itself added to the judge's evidence envelope (`:1306-1359,1503-1514`).

### Budgets and failures

- Default goal budget is 20 judged turns, including real-user turns, not just automatic continuations.
- This is not a token, wall-clock or money budget; each turn may contain many inner iterations.
- Normal configured judge timeout is 60 seconds; the module fallback is 30. Output allowance is 4,096 tokens unless overridden, not the roughly 200-output-token claim in the guide.
- Transport/parse failures initially continue; five consecutive transport failures or three parse failures pause. Some invalid-but-parseable objects become CONTINUE without incrementing parse failures.
- WAIT, DONE and BLOCKED are handled before ordinary budget exhaustion. Parent reproduced WAIT leaving active parked state at 1/1 budget. This is not a hard pre-admission spending ceiling.

## 4. Instructions versus enforcement

### Prompt instructions exist and are relevant

`agent/prompt_builder.py:344-393,433-498` defines:

- Tool-use enforcement: execute promised actions now; do not end with a promise.
- Task completion guidance: deliver a working artifact backed by real output, not a stub or plan.
- Execution discipline: keep using tools until complete and verified; act on obvious interpretations; read back external writes.

Injection is controlled in `agent/system_prompt.py:562-586`. Completion guidance defaults on for tool-enabled sessions. The other guidance blocks have model/config gates. Their names sound like enforcement, but they are instructions to the main model, not independent acceptance checks.

The stored prompt for this session already contains those relevant blocks. The problem is not simply that the model was never told to follow through.

### Runtime promise guards are narrow

`agent/turn_final_response.py:216-298` evaluates trailing-action, degenerate-fragment and initial-ack predicates. They can append a corrective message and request another iteration.

The trailing predicate only examines short visible replies (maximum 400 characters) and specific tail phrases (`agent/agent_runtime_helpers.py:3377-3394`). Parent-executed results:

| Candidate text | Trailing guard |
|---|---|
| Let me now run the tests. | catches |
| I will run the tests. | misses |
| I can implement that next if you want. | misses |
| Finished the investigation. Next steps: implement the fix. | misses |
| Long substantive text ending with matching phrase | misses |

These statements describe that predicate, not a guarantee every other guard misses every case. The separate initial-ack detector defaults to Codex Responses mode only (`:3439-3452`) and rejects candidates when any historical tool-result row exists (`:3168-3182`). It does not generally cover a substantial multi-step conversation that later stops early. A GPT model name alone does not establish Codex Responses transport.

Thus the problematic behavior can be grammatical, substantive and superficially helpful—and pass the detectors.

### Todos are context, not a stop veto

Todo state is stored and restored across turns/compression (`tools/todo_tool.py:102-131`; `agent/conversation_compression.py:3010-3058`). The model is told to complete items only after verification. No pending-todo semantic stop gate appears in the inspected ordinary stop/finalization path. An incomplete todo list does not itself force another model call.

## 5. Actual ordinary stop gates

The executed ordering is (`agent/turn_stop_gates.py:161-223`):

1. Late LSP diagnostics.
2. Built-in verify-on-stop.
3. Registered pre_verify hook.
4. Kanban worker terminal-status nudge.
5. Otherwise accept the candidate final.

None is a general user-intent completion judge.

Verify-on-stop defaults OFF (`hermes_cli/config_defaults.py:205-210`; `agent/verification_stop.py:62-88`). It requires tracked file mutations, recognized project context and missing fresh verification evidence; prose-only edits are filtered. The tracked trigger is successful write_file/patch, not every shell/Python mutation. Its default nudge cap is two.

The pre_verify hook requires registered callbacks and tracked edits; it is not a default callback and not appropriate as the only entry point for research/non-editing tasks. Its default cap is three, separate from the built-in verification cap (`agent/verify_hooks.py:16,29-34`).

Kanban enforcement is worker-specific and checks lifecycle-call presence, not general conversational intent. LSP only concerns diagnostics.

Budget exhaustion can return a previously withheld verification candidate with `completed=False` (`agent/turn_finalizer.py:124-195`). Normal completion-envelope calculation is transport/loop outcome, not proof that the user's acceptance criteria are met (`:654-659`). The UI and supervisor must distinguish “turn finished” from “task achieved.”

## 6. Host boundaries and legitimate yielding

Persistent-goal continuation runs in surface drivers, not inside raw `AIAgent.run_conversation`. CLI REPL, gateway, local-owner sessions and retained TUI paths have distinct scheduling/queue handling. A true local one-shot/library call does not automatically become a persistent loop simply because goal state exists. Kanban one-shot has an explicit goal-mode exception.

Active state is not a durable scheduling guarantee. Host hooks skip certain failed/empty turns; queue/admission errors may leave active state without a scheduled next turn. Classic CLI has an idle parked-goal poll. The audit did not establish equivalent independent timed-wait wakeup for all gateway/TUI paths; transport-level verification remains necessary.

Crucially, async delegation asks the parent to END ITS TURN so results can be delivered (`tools/delegate_tool_dispatch.py:335-345`). A supervisor that treats every yield as premature completion can obstruct correct behavior. It needs separate DONE, CONTINUE_NOW, WAIT_FOR_EVENT and BLOCKED decisions; the task may remain incomplete while ending the current turn is correct.

Interruptions, safety refusals, provider failures, hard toolguard halts and iteration exhaustion also must not be treated as ordinary candidate finals to restart blindly. Some paths bypass ordinary stop gates entirely.

## 7. Reproduced gaps and findings requiring further testing

Parent's new characterization tests reproduce:

- No automatic goal activation/judging for an ordinary incomplete response.
- Explicit activation enables judging, continuation and persistence.
- Draft context is objective-only.
- Judge API lacks conversation/tool/system-prompt inputs.
- Initial kickoff omits inline contract.
- `/goal unwait` clears the barrier but returns no kickoff prompt.
- WAIT can park active state at exhausted goal budget.
- Narrow trailing-action misses listed above.

Additional source-audit concerns, not asserted as end-to-end reproduced defects:

- Gate-failure continuation prefix does not match the gateway's plain-continuation classifier, relevant to pause/clear and stale queue handling.
- Reloaded cleared goal rows can be reactivated by resume, unlike an immediately cleared in-memory manager.
- Gateway/TUI timed-wait liveness depends on re-entry paths not fully exercised here.
- Gateway ACP forwarding appears capable of `/goal` despite stale documentation claiming no implementation.

Keep these small correctness fixes separable from the semantic-supervisor experiment; they should not become a prerequisite to building the first useful slice.

## 8. Relationship to the fork's previous PR stack

Fresh GitHub reads confirm native stack #3 is closed and these PRs are all merged into fork main:

- #1, merge `d19caf77fa0948fca499f6dafdb06868be83e79f`: required declared read objective when enabled, optional display.
- #2, merge `a521ca8b69f13328bda15b6057b20b4147f1e9b4`: observational read assessment with TypeSafe decision adapter.
- #4, merge `334ab2ba074c75b72c101c0efefd9c2c273ebc0c`: standalone semantic context previews/inspection.

URLs: https://github.com/CameronBeebe/hermes-agent/pull/1 , /pull/2 , /pull/4 ; stack https://github.com/CameronBeebe/hermes-agent/stack/3 . No open fork PRs were returned during this audit.

This work is related conceptually but is a separate functional feature. The earlier stack made individual reads and selected context inspectable; it did not modify goal activation or stop supervision. Since merged, it is already available on the new branch's base. No need to append to or resurrect the old stack.

Reuse candidates:

- Explicit TypeSafe routing, secret scoping, bounded transport, strict typed-answer validation and provenance patterns from #2.
- Read declared intent as optional evidence, not permission or proof of task completion.
- Evidence-display and omitted-context conventions from #4.

Do not directly reuse #2's rubric or context builder: it considers recent text user messages and one read excerpt, deliberately excludes system/assistant/tool context, hardcodes read-helpfulness questions, and never feeds results back (`agent/read_assessment.py:48-68,71-103`; `agent/read_assessment_typesafe.py:13-80`). Generalize transport separately from task-specific assessment.

## 9. Recommended first implementation slice

### Behavior

For ordinary actionable user requests, maintain a concise task intent automatically from real user context. Before accepting an ordinary final response, assess whether a concrete authorized next action remains. If so, continue without asking for another “go ahead.” Display intent, evidence references, probability judgments, decision and stopping/waiting reason.

Trust Jev's returned probabilities provisionally as the user requested. Do not make calibration research a prerequisite. Treat missing/invalid responses as unavailable, not as a fabricated low probability or completed intent.

### Context contract

Provide:

- Real-user objective and relevant clarifications, with provenance; synthetic nudges are not new user intent.
- Requested outcome, scope/constraints, acceptance evidence and current budget.
- Relevant applicable instruction excerpts and the actual available action/tool surface.
- Candidate final answer and pertinent actual tool outcomes/artifact references since task start.
- Outstanding work/delegations/processes, delivery semantics, and concrete access/authorization blockers.
- Explicit omission/truncation metadata and a recovery route for necessary evidence.

Intent can change with user steering; an agent-proposed optional extra must not become a new user requirement merely because it appeared in assistant prose.

### Probabilistic policy

Use atomic propositions such as “an authorized useful action can be taken now,” “stopping would materially leave the requested outcome incomplete,” “user input is genuinely necessary,” and “progress depends on already-running work.” Apply explicit thresholds in code with a bounded uncertainty policy. Do not conflate task completion, evidence sufficiency and execution feasibility into one percentage.

A small initial seam is immediately after existing ordinary stop gates and before final transformation/persistence (`agent/turn_final_response.py:375-420`). Reuse append-only feedback and existing continuation machinery; preserve task identity and do not have both an inner supervisor and an outer goal loop independently re-prompt the same stop.

Do not use pre_verify as the sole hook: its edit-only condition excludes much of the target behavior. A general stop-assessment hook or core seam is justified. A plugin can own the decision adapter/display, but the host must expose the complete candidate-stop context and a continuation directive at the right lifecycle boundary.

### Intent-attractor: CLI-first user-facing intent workspace

The feature name is **intent-attractor**. The MVP stays in the classic CLI; Desktop is a later interface over the same backend. Developer inspection of Jev/judging/scoring is a first-class requirement: retain and expose exact assessment questions, bounded context actually sent (with source anchors and omissions), raw returned probabilities, explicit thresholds, resulting control decision, intent revision, served model, usage/latency and unavailable/error status. Distinguish a fixed rubric explanation from a model-generated rationale; do not fabricate prose rationales for typed answers. Keep sensitive evidence local with deliberate provider export configuration.

### Governing requirement: persistent overarching aim

Treat intent as a maintained interpretation of the user's overarching aim, not a summary of the latest utterance. The hierarchy is durable aim → active subgoals → immediate conversational request. New messages are evidence about how that hierarchy should evolve: progress, clarification, presentation, correction, refinement, suspension, cancellation or replacement. Do not erase broader aims/constraints merely because attention shifts locally; equally, do not preserve them against an actual user change of direction. Preserve unresolved requirements with provenance and distinguish completed/suspended/replaced objectives. Ambiguous updates preserve the last supported aim while exposing the ambiguity, rather than silently inventing a new attractor.

The four update types in the current implementation are an initial guardrail, not proof this general capability is solved. Evaluate varied multi-turn sequences, including indirect refinements, side questions, temporary detours, multiple subgoals and genuine cancellation/replacement. Assess proposed state transitions against the relevant original user expressions and prior accepted specification before making them authoritative; end-of-turn fidelity scoring alone is too late to validate the update itself. Keep immediate-response compliance separate from durable-task fulfillment. This is the organizing principle for both continuation policy and eventual attractor visualization.

### Visual metaphor: trajectories toward an intent attractor

Treat the formal intent representation as an attractor defining a basin of acceptable outcomes, not a single exact answer. Show observed execution as a trajectory, assessment checkpoints as observations, and continuation nudges as course corrections. User expressions remain source evidence distinct from the extracted specification. Editing/refining the specification changes the attractor; annotate the revision transition rather than retroactively moving old observations into the new frame.

For the CLI MVP, use a persistent concise target plus a compact event trajectory (work, assess, correct, wait, verified stop); keep detailed source/assessment inspection available. Do not invent geometric coordinates, convergence distance, velocity, or percent completion from Jev probabilities. Until explicit meaningful axes are defined, present this as a schematic event trajectory, not a measured phase-space plot. Low-confidence judgments must appear uncertain rather than visually imply drift or convergence. Distinguish successful arrival, waiting, blocked, and budget-limited termination. A later richer visualization may use named observable dimensions, with explicit semantics and versioned target changes.

The intent/spec is the primary user control surface, not hidden supervisor metadata. Use one versioned, session-scoped canonical intent shared by CLI and Desktop; do not create competing UI-local goals.

- Automatically extract from relevant real user messages, retain source-message anchors, and distinguish user-authored requirements from model-inferred interpretations and optional assistant suggestions.
- Expose outcome, acceptance evidence, scope/constraints, open ambiguities, current execution state and stopping/waiting rationale. Show changes as a versioned diff; allow inspect, edit, pin and revert. User-edited/pinned fields must not be silently overwritten by subsequent extraction.
- Assess intent fidelity separately from execution completion with Jev: material user requirements covered, unsupported requirements added, and consistency with latest user steering. Provide the relevant original messages, not only the extracted summary. Calibration validation is not a prerequisite for this experiment.
- Make editing a real control operation: increment intent revision, invalidate old judgments, and make the next safe action boundary consume the new revision. Fence in-flight stale judgments and queued continuations; do not claim an edit undoes an already-running external action. Preserve explicit pause/stop controls.
- Keep initial extraction and updates automatic. Human review is available, not a mandatory per-task approval ceremony. Ask only when a consequential unresolved ambiguity prevents safe progress. Fidelity uncertainty is shown as uncertainty, not silently converted into authorization.
- CLI minimum: compact current-intent/status indicator plus inspect/edit/history commands (names to be designed, not claimed shipped); display decision changes without printing a full assessment every tool call.
- Desktop preferred inspection surface: persistent intent pane with editable fields, source anchors, revision diff/history and separate fidelity/completion judgments. It uses shared backend state/events rather than duplicating orchestration. Desktop plugin SDK offers panes/status contributions and gateway RPC, but the new canonical intent control API and stop-policy integration still need implementation.
- First vertical slice includes visible and editable intent plus one supervised follow-through example; a hidden continuation loop alone does not satisfy the requirement.

### Scope and acceptance

First prove one useful vertical slice: an agent performs partial work, offers an already-authorized next step, receives a Jev-based corrective signal, performs that step and stops with evidence—without a second user prompt.

Include cases for legitimate completed questions, completed artifacts, long partial finals, substantial prior tool history, terminal-only work, genuine human blockers, async waiting, new user steering, judge unavailability, finite budget exhaustion and stop interrupts. Preserve exact assessment inputs and returned probabilities locally for inspection. Test with synthetic model responses first, then a bounded live run under explicitly configured data flow. No large corpus or new orchestration framework is required.

## Verification and branch status

Branch: `research/intent-continuation-audit`, created from refreshed fork main in a separate worktree. Existing lab/assessment/context-map worktrees were not switched or rebased. Production source/config remained unchanged.

Canonical runner result after adding characterization tests: **174 passed, 0 failed, 1 Linux-only skip across 8 files**. This includes 12 new characterization cases. These tests intentionally document current behavior; they do not claim an implementation has fixed it. The model boundary is mocked; no Jev/provider inference occurred in these tests. No full repository suite, transport end-to-end test or calibration result is claimed.

Reproduce with an existing prepared test interpreter (no dependency installation):

    HERMES_AUDIT_PYTHON=/path/to/prepared/python HERMES_AUDIT_STATE_ROOT=/path/to/disposable/scratch bash scripts/verify-intent-continuation-audit.sh

The wrapper isolates HOME/HERMES_HOME/runtime/temp state and uses the canonical runner's clean environment. Independent review confirmed the central source claims and found an interpreter-validation gap that could allow the canonical runner to provision dependencies. The wrapper now rejects non-absolute/non-executable interpreters and failed pytest imports before invoking the runner; both refusal branches were exercised and the complete focused suite rerun successfully. A missing pytest report from the editor's separate language-server interpreter does not describe the verified test interpreter. No production installation or runtime activation is included.
