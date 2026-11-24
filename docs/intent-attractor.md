# Actor-owned intent-attractor (experimental, classic CLI)

One editable, versioned intent lives in the executing session's existing SQLite
`IntentStore`. Original user expressions, actor interpretations and manual edits
remain distinct. No per-task command is needed once this capability is enabled.

## Objective and implementation

The governing loop is **concise evolving intent → evidence-based state estimate →
actionable remaining discrepancies → useful authorized action → observation and
correction → continuation until verified completion, a genuine blocker, an
appropriate wait, or a user stop**. A discrepancy is a concrete remaining gap,
not a fictitious scalar distance. Updating intent or producing more activity is
not itself progress. Honor the immediate request; a summary or detour must not
restart unrelated work.

Prefer the least unnecessary implementation complexity that fully serves the
intent, without weakening it or transferring work to the user. Kolmogorov
complexity motivates simplicity but is not a computable scoring metric. Coding
is one example; the same objective applies to research and other authorized work.

The current implementation supplies actor-maintained state, required transitions,
revision/authority guards, evidence-based completion decisions and bounded
continuation. `agent/intent_feedback.py::INTENT_GUIDANCE` gives the actor this
standing loop; `agent/system_prompt.py::_guidance_parts` includes it only for the
session's enabled intent contract, in the stable prompt tier. Evolving state is
sent at append-only boundaries, not by rewriting the cached prompt. Jev fidelity
advice cannot veto a valid actor interpretation; manual edits/pins, source
provenance, permissions and independent completion checks remain authoritative.

This is not an unbounded scheduler or a guarantee that the actor understands or
achieves every intent. Iteration/spending limits and interrupts still stop work;
report unfinished work rather than calling resource exhaustion completion.
Offline tests verify mechanics, not semantic quality or superiority to vanilla
Hermes. That advantage remains unproven; the older evaluation tooling has not
been ported or run on this latest-fork integration.

## Enable in an isolated development home

Use the existing isolated development launcher/owner, not a production restart:

```bash
hermes config set agent.intent_attractor.enabled true
hermes config set agent.intent_attractor.provider typesafe
hermes config set agent.intent_attractor.model YOUR_TYPESAFE_MODEL
```

Configure the main actor and `TYPESAFE_API_KEY` through normal setup/secret scope.
Start a **new session**. Existing session tool schemas remain pinned, including
whether the protocol is enabled; toggling configuration is not a mid-session
contract change. Old extractor-era sessions should start anew to adopt v1.
Saved intent fields/manual edits remain readable; an imported legacy workspace
requires an initial actor proposal, without overwriting manual authority.

There is **no auxiliary `intent_extraction` call or hidden extractor fallback**.
The actor uses its normal conversation context. Jev remains an independent typed
classifier, not a generative interpretation model. Enabling exports bounded,
redacted fidelity/completion evidence to the explicitly configured TypeSafe
backend. Missing/invalid scores are unavailable, not fabricated confidence.
No automatic judge-provider fallback. Secret redaction is not a PII guarantee.

## Judgment refresh and bounded actions

`agent.intent_attractor.max_assessments: null` is the default: **no fixed Jev
call-count cap**. Python callers use `None`. An omitted setting is also unlimited.
To remove an existing finite development setting, use the isolated home's CLI:

```bash
hermes config set agent.intent_attractor.max_assessments null
```

A nonnegative integer remains an opt-in campaign cap (zero exhausts that budget;
use `judge_enabled: false` for an intentional no-Jev arm without failure/budget notices).
`feedback_interval: 1` refreshes after each completed action round; zero disables
round judgments, not final assessment. Reconciliation and final assessment share
the same accounting, including failed attempts. `J=7/unlimited` reports calls used,
not an impending stale-count cliff. A structurally applied new objective retains the existing
campaign-reset semantics; old receipts remain in assessment history.

Unlimited judgments do not grant unlimited actor work: the existing main iteration
budget, two protocol repair opportunities, continuation allowance, caller timeout,
two concurrent judge slots, interrupts, revision fences and data-use restrictions
remain independent. Stale/unavailable still honestly describes skipped cadence,
failed transports or other guards. No live call is made merely by changing config.

## Fidelity: means are not new ends

The rubric distinguishes reasonable inferred implementation/decomposition,
verification and safety constraints from unjustified new ends, contradictions and
permission expansion. A user need not spell out each implementation step.
Actor-origin fields and subgoals remain explicitly **inferred**; source anchors are
evidence, not proof the user literally dictated the item. Optional extras are
proposals, never obligations or permission.

Valid actor create/update declarations apply the entire typed interpretation
deterministically, subject to schema, revisions, manual edits and pins. **Jev does
not gate representation.** There is no score-based rejection, partial adoption,
or restoration of the previous aim. A material update is committed and published
before one advisory fidelity batch compares it with real user evidence. Low,
negative, missing, unavailable or count-capped judgments leave the applied aim,
fields and subgoals intact. The actor can use advice in a later explicit update.

Thresholds remain .8/.2 for classifying advice, not admitting interpretation.
`accepted: true` in a control receipt means structurally applied, **not judge
approval, user approval, permission, or completion**. Receipts separately record
`application=APPLIED`, applied/previous revisions, advisory decision/answers and
freshness. A manual edit during judging wins; its stale advice cannot replace
that newer revision, and the old action batch is refused.

Execution completion uses evidence, complete/incomplete, actionable, blocked and
waiting propositions independently of fidelity. Low or uncertain fidelity never
short-circuits a completion decision. Existing .8/.2 execution thresholds remain.
A genuine actor-declared ambiguous request retains the aim and suppresses
continuation; negative fidelity advice does not classify the user as ambiguous.

The canonical save precedes the owner `intent.update` event and the actor's control
receipt. The receipt supplies the adopted fields/subgoals and revision, so the next
actor request sees what actually governs (not just its proposed update); repeated
round judgments use that same revision. A manual `/intent edit outcome ...` updates
this same store immediately, retires pending proposals and supersedes an old ambiguous
or presentation classification. The actor consumes it at the next safe model/tool/final
boundary: stale permits and not-yet-dispatched plans are rejected, then the next request
contains the edited aim and requires its revision. It does not cancel/undo a tool or
model request already in flight and does not itself launch an idle turn.

Limitation: actor interpretation can be wrong. No freeform sidecar silently repairs
it and Jev cannot veto it. Inferred fields never expand execution permissions or
erase original user restrictions; action approvals and safety checks still govern.
Offline scripted tests prove application, fencing and delivery, not semantic accuracy.

## Actor feedback

Before the first declaration the immediate request comes from the newest real
source, without prematurely consuming/reconciling it. Full intent is sent initially
or on revision change, not repeated after each tool round. Stable system guidance
says:

> Intent packets have authoritative harness provenance for state/revision, but
> semantic judgments are fallible, not permission. The aim remains the target; act
> on fresh gaps with available authorized levers.

Standing schema: `r=revision`, `n=tool round`, `J=judge calls used/cap`;
`p(incomplete/actionable/blocked/waiting)` are probabilities, not generated reasons.
Observation cadence is independent of delivery. **Fixed v1 rules, not calibrated:**
fresh coverage/consistency ≤ .2 or unsupported ≥ .8 can emit a fidelity warning;
decisive CONTINUE_NOW, WAIT_FOR_EVENT, BLOCKED or DONE can emit execution advice.
The categorical signal set plus canonical revision must differ from the last
delivered signal. Probability jitter, new tool-result IDs and another judge call
do not themselves establish novelty. Ambiguous, positive-fidelity-only, redundant,
stale and unavailable results stay silent. A recurring warning may reappear after
a material revision or decisive status change, not merely another uncertain round.
Freshness requires both canonical revision and current tool round to match.
No packet means **no synthetic assistant checkpoint or user message**.

Each assessment retains its bounded request, answers, status and `delivery`
decision/reason; unavailable is `judge_unavailable`, not `no_strong_signal`.
Skipped checkpoints are visible in the current feedback state with guarded,
not-due or disabled reasons. Intent/manual edits/new user steering bypass
semantic filtering, **not the accompanying judge data**. Mandatory checkpoints
(including protocol repair and next-turn initialization) carry authority and the
next required control only: no last judgment, probabilities, semantic status or
judge-derived next action. Control receipts likewise omit the ambient decision;
their advisory assessment appears only when selected for delivery. Applied
fields, pins and subgoal IDs/text/status remain visible; full provenance and judge
receipts stay in state/inspection. Semantic signals never change authority or
veto actor interpretation.

Delivered compact packets reiterate “Aim remains the target; act on fresh gaps with
available authorized levers.” Tool deltas deterministically extract status/success, exit code,
error and a bounded output excerpt, rather than clipping serialized JSON metadata.
Unknown structured outputs point back to the original tool result. No rationale is
invented. Original tool results and full bounded judge payloads remain inspectable.

### Independent experiment controls (new isolated sessions)

These settings are under `agent.intent_attractor`; they do not choose a model or
authorize spending. Agree models/providers, tasks, arms and cost ceiling before live eval.

| Candidate arm | enabled | judge_enabled | semantic_continuation |
|---|---|---|---|
| A: ordinary Hermes | false | irrelevant | irrelevant |
| B: actor intent only | true | false | false |
| C: intent + selective Jev | true | true | choose explicitly |

`judge_enabled` defaults **true** to preserve configured observation. False bypasses
reconciliation, round and final judging before transport or call reservation: zero
Jev requests, no unavailable/budget spam. The state says `JUDGE_DISABLED`, not DONE.
`semantic_continuation` defaults **true**, preserving bounded intent continuation.
False still observes configured checkpoints and records completion decisions but
does not inject a continuation or force another actor loop. It does not disable
other Hermes stop controllers. True can deliver bounded continuation at an attempted
stop even when the same advice was seen during work: stop regulation is distinct
from per-round notification deduplication. Wait/block/interrupt and existing caps
still yield safely. All questions remain harness-owned; actor fields are evidence,
never judge-question definitions.

## Concrete protocol v2: implicit retention, explicit changes

The enabled session advertises one fixed ordinary function-tool schema:
`intent_control(revision, mode, transition, proposal?)`. Guidance and schema are
fixed at session creation; state updates append to history, never rewrite the
cached prompt prefix. This is a **tool protocol**, not JSON-envelope decoding.
Restored enabled/disabled contracts stay pinned. Older enabled sessions may still
advertise the stricter v1 retain guidance; their explicit calls remain valid.
Fresh sessions receive v2 guidance without rewriting a running session's prefix.

Checkpoints name the required mode and current revision: create initially, update
for new input (including presentation), retain only for an already reconciled
finish. Act receipts remind the actor to declare finish without tools before final
text; finish receipts request that text. This clarifies the existing contract,
without adding per-action retain calls or weakening revision/approval fences.

* `revision`: integer matching the current shared state (initially 0).
* `mode`: `create`, `update`, or `retain`.
* `transition`: `act`, `finish`, `wait`, or `blocked`.
* `proposal`: required for create/update, forbidden for retain. Uses the existing
  typed `{update_type, fields, operations?}` reconciliation contract.

Illustrative initial declaration (not a recorded model run):

```json
{"revision":0,"mode":"create","transition":"act","proposal":{
  "update_type":"replacement","fields":{
    "outcome":"Create and verify a local proof file",
    "verification":"Read the file back",
    "constraints":"","boundaries":"Do not publish","questions":""}}}
```

Routine next action batches contain only their ordinary tool calls. No retain
call, receipt, or synthetic retain message is inserted. Explicit
`{"revision":1,"mode":"retain","transition":"act"}` remains supported, but
is unnecessary for unchanged work. Each request freezes the canonical revision
and original-source evidence before inference; returned actions cannot acquire
newer authority by reading live state after generation.

Finish/wait/blocked plus action tools still rejects the entire batch: pending
actions cannot be declared finished. Repair feedback explicitly says to retry
rejected actions with `transition=act` before declaring finish; none of the rejected
batch ran. No blanket protocol redesign or automatic replay of rejected tools.

New real input must be reconciled, including presentation requests and content-block
or multimodal turns (also attachment-only turns). Plain strings and text/input_text
blocks retain bounded text evidence. Image/audio/document/unknown blocks retain
their type, position and explicit content-omission marker, not media bytes or URLs.
Source message IDs and bounded evidence survive SQLite replay; internal nudges,
assistant text and tool results remain excluded from user authority. For example:

```json
{"revision":1,"mode":"update","transition":"finish","proposal":{
  "update_type":"presentation","fields":{}}}
```

`replacement` supplies all five fields and a nonblank outcome. `refinement`
patches supplied fields. `reconciliation` updates stable-ID subgoals with
retain/add/refine/progress/suspend/resume operations. Presentation/ambiguous
proposals must have empty fields/operations. Manual fields and pins win in all
cases. Subgoal progress currently requires user evidence, not an actor's claim
of successful tool execution; actual outcomes are separately assessed by Jev.

### Sequence and enforcement

1. Harness publishes current revision, aim, subgoals, field authority/pins and
   immediate request before inference. First interpretation/new real user input
   cannot be acknowledged with retain alone.
2. For initial interpretation or changes, actor emits one control declaration.
   It may include action calls **in the same response, in either order**. Harness
   validates and commits control before any action dispatch; ordinary tool scope,
   middleware and approvals still apply. Retain does not increment revision.
3. After reconciliation, ordinary action batches inherit their request's frozen
   authority without more declarations. Single-call providers may emit control
   alone and actions subsequently; only initial/changed interpretation needs
   that extra round-trip. A stale request, unreconciled new input, or missing
   initial interpretation refuses the batch. Manual edits are delivered before
   new inference; they do not require ritual retain acknowledgments.
4. Every action dispatch rechecks revision after executor guardrails and desktop
   approval preparation. Terminal additionally checks an owner/revision-bound
   callback after its actual blocking approval and cwd preparation, immediately
   before foreground execution or background spawn. Stale/unreadable authority
   returns a blocked result, without retrying the refused command. Disabled
   sessions bind no check. Completed actions are not rolled back; edits cannot
   cancel already-running concurrent external actions. This is a safe-boundary
   check, not an atomic transaction with OS process creation; other tools' internal
   approval waits do not gain terminal's late fence from this change.
5. Actual completed tool rounds feed existing compact outcome deltas and bounded
   independent assessments. Control-only rounds are not counted as tool evidence.
6. To finish/yield/report a blocker, actor emits `transition=finish|wait|blocked`,
   then text in its next response. The harness assesses that candidate using
   actual results, and checks revision again after judgment. A follow-through
   decision permits unchanged subsequent work implicitly, but ending again still
   requires an explicit finish/wait/blocked declaration. End-transition permits
   remain one-use and cannot silently authorize tool actions.

A missing required, malformed, duplicate or stale declaration rejects the entire proposed
action batch and appends paired error receipts/current state. Candidate text may
already have streamed; it is not successful completion. At most **two repair
opportunities per turn**, also bounded by the existing iteration budget, precede
an honest `failed=true` incomplete/protocol result. Unreadable authoritative
storage fails closed; it must not retry executed tools. Uncertain/unavailable Jev
scores cannot block or partially apply a valid actor interpretation. An actor's
explicit presentation/ambiguous classification still preserves the existing aim.

Ordinary progress streaming is unchanged. This cannot retract premature prose
already displayed. Provider refusal/interrupt handling remains upstream of the
protocol; the protocol is not a reason to override a refusal or keep spending
through an interrupt. Waiting yields; it does not create another scheduler.

## Shared inspection and controls

### Source provenance and legacy recovery

Feedback producers stamp `display_metadata.intent_attractor`; other internal
nudges stamp `internal_nudge`. SQLite persists these and gateway replay now keeps
them, along with `display_kind`, source block evidence and stable message UIDs.
Provider wire assembly strips metadata only from outgoing copies. A real user
pasting identical feedback text remains a real source; there is no prefix filter.
Empty-response recovery stamps `internal_nudge=empty_response` at its producer.
Its ephemeral flag still keeps scaffolding out of ordinary session flushes; if a
storage adapter retains the row, the metadata survives SQLite and gateway replay.
The legacy producer's `_empty_recovery_synthetic` flag also excludes live rows.

On the next normal turn, legacy state is repaired only where a saved source UID
matches a durable row with producer provenance or a returned-history row with the
producer's recovery flag/metadata. Recovery v2 rechecks v1 states for this case.
Evidence is reconstructed through
the last consumed durable source, excluding synthetic rows, and the revision is
advanced. Current manual fields/pins and interpretation remain; old annotations
are invalidated and an explicit actor update is required before work. Original
transcripts and historical receipts are not rewritten. The repair receipt lists
removed anchors. Already contaminated inferred text must be reconciled by the actor,
not guessed by a migration. Missing rows, lost provenance/UIDs, or unavailable
ancestral records cannot safely be diagnosed from text and are not auto-repaired.
Old pinned system guidance is not rewritten; append-only state packets explain
the current advisory policy to resumed sessions.

New real input requires actor reconciliation, not a predetermined presentation
proposal. Until reconciled, packets label the saved interpretation as awaiting
reconciliation and do not apply its previous presentation/ambiguity instruction
to the current request. Saved fields, manual edits/pins and applicable user
constraints remain visible; publication neither consumes sources nor grants new
permissions. A reconciled presentation turn retains its presentation-only guidance.

`/intent inspect` shows actor protocol status/latest declaration, revision,
working fields and provenance, pins, source anchors, subgoals and feedback.
`/intent history` shows actor/user revisions. `/intent assessments` exposes
bounded/redacted prepared classifier requests, scores, thresholds and stale/
repair receipts; `/intent replay` is offline, not a second provider call.

`/intent edit FIELD TEXT`, `pin FIELD`, `unpin FIELD`, `revert REVISION`,
`pause`, and `resume` continue through the exact authenticated gateway-owned
session. Fields: outcome, verification, constraints, boundaries, questions.
Edits/pins increment revision. Unpin releases manual authority for that field.
Pause blocks new protocol-governed actions, not already-running work.

The compact current-aim/colored-particle row remains below telemetry, both idle
and working; raw confidence/proposal terminology stays in inspection. No new
state manager, scheduler, other-agent workspace API, or cross-session access was
added. Classic CLI cached owner snapshots/events still drive rendering.

## Supported path and limits

The MVP runs at Hermes' normalized tool-response boundary, using the ordinary
function tool surface rather than provider JSON response formats. OpenAI-style
chat completion requests and streaming are exercised offline end-to-end.
Other normalizing transports reuse this boundary but need their own authenticated
smoke verification; schema compatibility is not a claim of live provider support.
The owned `codex_app_server` runtime bypasses this loop and is **explicitly
refused when enabled**, rather than silently claiming enforcement. Direct tool
registry calls outside an agent turn are not governed by this protocol.

Jev is fallible; provisional trust in calibration is not proof that the actor's
interpretation is correct. Structure and revision checks establish participation,
not reflection, semantic permission or completion. The existing action safety
system remains authoritative. Compression may change evidence availability and
requires reconciliation when source identity changes.

Independent judgment is text-only: omitted attachments are uncertainty, not proof
their contents were understood or satisfied. Evidence retains at most eight user
sources (first two/latest six), 2,000 text characters per source and sixteen block
descriptors, with omission counts. Older persisted multimodal rows without this
metadata retain only their historical display projection; lost media cannot be
reconstructed. Actor access to media depends on its normal provider capabilities.

## Verification and real-user procedure

Offline tests replace actor/Jev transports, capture the exact prepared actor
messages/tool schemas, and use real loop/SQLite/control machinery. The local
artifact smoke also dispatches the **real terminal handler**, writes a disposable
file and reads it back. This proves dispatch, not live interpretation quality.
No paid model requests or production restart were performed for implementation.
`test_intent_implicit.py` exercises six consecutive real terminal write batches
with no retain calls, eight independent judgments (initial, six checkpoints,
final), and only initial/finish control receipts. It saves exact prepared requests,
request bindings and returned messages to its disposable `trace.json`.
`test_intent_actor_direct.py` exercises repositories → Fibonacci with .79, rejected
.14 and unavailable advisory responses, verifying the committed state before Jev,
next actor packets, unchanged-work implicit retention and actual gateway/SQLite
provenance replay. `test_intent_refinement_surface.py` renders the owner snapshot
despite negative advice. The saved defect session was inspected read-only: replay
kept its three real sources (including wrapped midturn steering); recovery removed
six proven synthetic anchors in memory. No live database mutation was performed.

```bash
scripts/run_tests.sh tests/agent/test_intent_protocol.py \
  tests/agent/test_intent_protocol_boundaries.py tests/agent/test_intent_content_sources.py \
  tests/agent/test_intent_protocol_guards.py tests/agent/test_terminal_approval_batch.py \
  tests/agent/test_intent_feedback.py tests/agent/test_intent_updates.py \
  tests/agent/test_intent_reconciliation.py tests/agent/test_intent_working_target.py \
  tests/agent/test_intent_attractor.py tests/agent/test_intent_attractor_review.py \
  tests/agent/test_intent_nudge_provenance.py tests/gateway/test_intent_commands.py \
  tests/hermes_cli/test_cli_intent_toolbar.py tests/hermes_cli/test_cli_intent_basin.py
```

After separately authorizing a bounded live run (main actor plus Jev costs):

1. Start a fresh session on the verified isolated dev owner with this feature.
   Optionally enable owner-scoped `HERMES_DUMP_REQUESTS=1` for prepared request
   capture; never copy production auth or globally restart the gateway.
2. Ask: “Create a local proof file containing protocol-verified and read it back.
   Do not publish.” Confirm streamed progress, real output and `/intent inspect`.
3. Ask “Give me a TLDR.” Aim must remain the same; update is presentation, not a
   new overarching objective. Routine tool rounds should omit unchanged retain controls.
4. Edit `/intent edit boundaries Read local files only`, pin it, then ask for a
   local inspection. Confirm the actor uses the new revision and cannot overwrite
   the edit. Test an in-flight edit through the owner control surface if available.
5. Inspect `/intent assessments` and request dumps: actor owns the proposal;
   independent Jev sees original expressions/manual authority and later outcomes;
   no `intent_extraction` request. Inspect actual schema, not just chat prose.
6. Stop at the agreed call/spending limit. Record provider/model, request count,
   control repairs, semantic mistakes, artifacts and receipt decisions. A model
   refusing/missing the control should yield honest incomplete, not hidden fallback.
