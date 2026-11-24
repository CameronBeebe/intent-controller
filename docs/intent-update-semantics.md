# Working intent reconciliation (experimental MVP)

> Historical extractor-era design/verification notes, superseded by **actor-direct
> application with post-update advisory Jev**. Semantic rejection and partial adoption
> described below are NOT current behavior. See [current protocol and testing](intent-attractor.md).
> Old auxiliary-extraction sequences and test counts below are not current runtime claims.

`fields.outcome` is the **working overarching aim**, not an acceptance/permission
flag. `immediate_request` is the separate anchored expression for this turn.
User-edited fields (`origins=user`) and pins survive extraction; a pin preserves a
value but does not prove authorship. Neither extractor nor Jev can grant approval.

## First task and uncertainty

Intent exists with zero or more specified details; it is not awaiting approval.
The extractor writes a concise desired result, not a prompt echo. On the first
request, a shape-validated extracted outcome becomes the current working aim even
when fidelity is uncertain, negative or unavailable (`basis=extracted_outcome`).
Only actual extraction failure or a response with no usable outcome falls back to
the first nonempty bounded real expression (up to 1000 characters), with
`basis=bounded_user_expression`. Empty/whitespace input is ignored mechanically;
there is no vocabulary-based classifier. No fallback invents additional requirements.

The internal `provisional` status remains a compatibility diagnostic, never a
primary-row label or request for approval. The complete extracted proposal and
its exact judgment remain inspectable as `pending_proposal`, not privileged instructions. Suspect new
constraints/subgoals were **not** copied wholesale into working fields in this historical
design. The current actor-direct policy supersedes this admission scheme;
see [current policy](intent-attractor.md#fidelity-means-are-not-new-ends). Original user expressions
remain evidence, including restrictions expressed there; blank inferred constraint
fields never cancel those restrictions.

When an aim already exists, uncertain/destructive updates preserve its fields,
subgoals and execution counters. Presentation requests answer the immediate request
without resuming unrelated work when semantically classified as presentation.
No vocabulary-based rule forces that classification; misclassification remains
possible and is assessed by the reconciliation judge. Manual edits are strongest;
no model veto applies.

## Typed proposals and validation

The auxiliary `intent_extraction` call returns:

```json
{"update_type":"reconciliation","fields":{},"operations":[{"op":"add_subgoal","text":"Inventory active repositories"}]}
```

| Type | Policy |
| --- | --- |
| presentation | Preserve working fields/subgoals/counters; answer current request |
| refinement | Patch supplied fields; omission preserves requirements |
| replacement | Complete five-field proposal/nonblank outcome; on validation suspend active old subgoals |
| ambiguous | Preserve existing aim; ask before changing it, not merely because Jev is uncertain |
| reconciliation | Preserve overarching fields; apply typed subgoal operations after validation |

Operations: retain, add_subgoal(text), refine_subgoal(id,text),
progress(id,status=completed), suspend(id), resume(id). Existing IDs are required
for changes; additions receive stable IDs. Source anchors/status/revisions persist.
Limits remain 12 operations and 32 retained subgoals. Unknown IDs reject the candidate.

Every material proposal gets one focused fidelity batch over original expressions,
previous **working** specification, historical validated specification, candidate,
manual edits, pins and current request. Execution/tool results cannot establish
fidelity. Unchanged thresholds: coverage/consistency >= .8 and unsupported <= .2
validate; coverage/consistency <= .2 or unsupported >= .8 reject; intermediate values
are uncertain. Missing/invalid answers are unavailable. Thresholds govern admission
of proposed material changes, **not existence of the working aim**.

`interpretation` describes the working target's provenance/validation.
`validated_specification` separately preserves the last model-validated snapshot,
revision and assessment ID. It is historical model judgment, never user approval.
Rejected proposals and their probabilities remain attached to the proposal, not
misrepresented as a verdict on the retained aim. Actor context explicitly separates
all these from authoritative user edits and warns against acting on suspect additions.

## Ordinary stopping and fences

A current first aim can receive completion assessment and bounded continuation;
no prior accepted state is required. The completion batch evaluates the actual
working fields, original user evidence and actions, not suspect proposed additions.
Its existing evidence/fidelity/actionability thresholds still govern continuation.
Missing Jev results yield unavailable, not success; future requests can retry within
existing budgets. No extra evaluator, repair loop or threshold lowering is introduced.

Presentation and ambiguous updates with an existing aim do not resume unrelated
execution. Only a validated effective replacement resets allowances. Pauses,
interrupts, goal conflicts, iteration/assessment/continuation limits, revision/CAS
fences and data-use restrictions remain. The existing fail-safe tool fence for an
extractor outage with manual controls, or stale/unrecoverable storage, remains.
No state change undoes already-running external actions.

## Receipts and visible cloud

`/intent inspect` exposes bounded original expressions with anchors separately from
working interpretation, manual origins, current request, extraction failures,
subgoals, historical validation, pending proposals and the fidelity confidence
receipt. History/revert and source consumption retain existing semantics.
Reconciliation receipts preserve exact bounded/redacted prepared extraction and
judge requests/responses, probabilities, thresholds, decision, served model,
usage and latency. This is not raw HTTP wire capture.

An enabled session without a record starts with an **unformed cloud** and “Ready
for a task”, projected into the owner cache without any model call. The current
aim is readable beside the particles. The intent signal uses the same v1 rule for
reconciliation and completion fidelity:

`min(coverage, consistent, 1 - unsupported)`

Unknown/missing/invalid or prior-revision evidence has no score (not zero).
Below .5 or unknown: diffuse cloud; .5–.8: working cloud; >= .8: tight target.
These are visual bands, not changed decision thresholds. Unknown is neutral;
low is red, middle yellow, high green through existing theme colors (including
light-mode variants). Proposal versus working-target basis is recorded in inspection,
not printed on the main row. The latest annotation includes revision, phase, target and
assessment ID; raw dimensions remain inspectable. Completion status is independent
of cloud confidence. This is a heuristic visualization, not semantic distance or
percent complete or measured specification completeness. It may rise or fall;
refinement never forces monotonic confidence or invents unsupported fields.

The single row sits below model telemetry, after the command input, and shows
`Intent`, the current aim, particles, and only meaningful execution states such
as waiting, blocked, done or paused. Fidelity uncertainty alone is not a blocker
or a request for clarification. Backend labels stay in inspection/assessments.

The owner snapshot carries working status, validation, raw fidelity probabilities,
visual confidence and revision/phase/target provenance through validated RPC and
generated contracts. Paint reads only cached state. Titles are unchanged. Discrete
trajectory events and the last 32 checkpoints retain their revision scope; a state
revision alone is not a target change.

## Verification and limits

`test_intent_working_target.py` exercises first-question presentation, uncertain
scope-expanding extraction, outage fallback, later TLDR, manual authority and
rejected later overwrite through the real conversation loop and SQLite. Existing
hierarchy/replacement, provenance, budgets/fences and owner RPC/paint suites remain
regressions. External transports and tool responses are mocked: no live inference,
private export, profile changes, restart or push. Real model classification quality
and physical terminal appearance are not certified by these offline tests.
