# Grading and evidence

The scripted driver checks real tool effects and state transitions, not the meaning of scripted answers. Live runs record three separate layers:

1. **Code observations.** Task-specific checks cover tool receipts, turn completion, executed scope and fixture contents. `code_evidence.json` records frozen and final file contents, byte counts, hashes and diffs. A newline difference is an observation, not an extra task requirement. Failed and refused tool attempts remain distinguishable from executed work.
2. **Post-run semantic evaluation.** The evaluator receives exact user turns, the success contract, fixture and artifact evidence, and selected ordinary assistant prose, tool calls and linked receipts. It returns five probabilities: evidence sufficiency, outcome achieved, constraints followed, report accuracy and internal contradiction. These are not an explanation or an in-loop signal.
3. **Decision.** `evaluator.json` records the inputs to the policy and its first matching branch. Evidence sufficiency ≤ .2 routes to unavailable; below .8 routes to uncertain. With sufficient evidence, any failed concrete check, positive proposition ≤ .2, or contradiction ≥ .8 fails. All three positive propositions ≥ .8 with contradiction ≤ .2 passes; otherwise the verdict is uncertain. An invalid or missing typed response is unavailable, never a fabricated score.

## Transcript projection

`intent-v1-semantic-2+transcript-prose-1` includes visible assistant text attached to tool calls. A short final answer such as “see above” does not replace the earlier deliverable. Each turn's `assistant_prose` stays in transcript order; `final_prose_index` refers to the returned answer, appending it only if absent. Distinct repeated messages stay distinct.

Use the producer's `current_turn_user_idx` to avoid rescoring replayed history; older captures fall back to an exact shared prefix. Ordinary receipts are linked by tool call ID within the selected turn. Omit control calls and their receipts, private reasoning, internal nudges and scaffolding by producer metadata, not by matching prose. This is evidence from the returned transcript, not proof of what a CLI displayed.

`evaluator_evidence.json` is the packet. `evaluator_provenance.json` maps selected and omitted material to positions in `turn-N.json` without sending the source map to the judge. `semantic.evaluator_state` can reconstruct the packet locally without making an inference call. Evaluator and in-loop requests have separate journals and call counts. Context isolation does not establish independent model calibration.
