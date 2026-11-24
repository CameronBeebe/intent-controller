# Classic CLI intent convergence row

The intent widget is **one row**, immediately below the model/status telemetry, after the command input. Session-backed layouts use input → telemetry → intent, without a duplicate above the input. Model/token telemetry contents and session titles are unchanged. At ordinary widths a 21-cell particle field (15 cells at narrower widths) sits beside the current aim. Small windows omit the field before clipping the aim by terminal cell width; `~` marks clipped text.

Fixed particle seeds collapse toward the center as the owner's cached `visual_confidence` rises. Overlapping particles become denser and high confidence yields a crisp `●` target. Missing/stale confidence remains diffuse and neutral, with `?` beside the label; it is not substituted with zero. Known low (< .5), middle (< .8), and high signals use the theme's red, yellow, and green status colors, including light-mode variants. No escape strings or new model calls are involved. This intent signal is the existing fidelity projection, not percent complete or measured specificity; it is not forced to increase. Its exact basis (including proposed versus retained specification) stays in inspection. Only execution labels such as `waiting`, `blocked`, `done`, or `paused` appear after `|`. Manual edits remain identifiable; provisional/proposal/uncertain labels do not appear in the row.

While working, a deterministic half-second phase changes particle brightness without changing spread, confidence, or the target position. The existing CLI spinner cadence supplies invalidation; there is no new thread, timer, RPC, inference, or status polling. Idle and paused rows are stable. Paint reads only the owner/session cache. Control events invalidate the row, and switching sessions detaches its old listener. `/intent inspect` and `/intent history` retain detailed subgoals, proposals, raw probabilities, and trajectory; these no longer consume a multi-row panel.

## Actual renderer samples — illustrative cached fixtures

These were produced by `FormattedTextControl.create_content`, not live inference. Left-hand confidence labels are annotations for this example, not part of the UI.

```text
unknown  Intent r2? .  ·  · .   .  ·  · . Track Brood War
.25      Intent r2   · .  · ·· . · . ·   Track Brood War
.60      Intent r2       ··.·•·.··       Track Brood War
.90      Intent r2          :●:          Track Brood War
```

Next working phase (same confidence, no progress implied):

```text
unknown  Intent r2? ·  .  · ·   ·  .  · · Track Brood War
.25      Intent r2   · ·  . ·· · · · ·   Track Brood War
.60      Intent r2       ·.··•.···       Track Brood War
.90      Intent r2          :●•          Track Brood War
```

38-cell fallback:

```text
Intent r2 Track Brood War
```

## Offline verification

```bash
scripts/run_tests.sh tests/hermes_cli/test_cli_intent_basin.py tests/hermes_cli/test_cli_intent_toolbar.py tests/gateway/test_intent_commands.py tests/hermes_cli/test_cli_extension_hooks.py tests/hermes_cli/test_cli_modern_layout.py --file-retries 0 -q
```

**46 passed, 0 failed** for the UI-only command above. The combined intent lifecycle + UI regression run passed **99 tests** across eight files. Coverage includes actual renders at unknown/.25/.6/.9 and different phases, neutral/red/yellow/green theme styles in dark/light modes, deterministic spread, unknown versus zero, completion-independent geometry, idle/paused stability, existing cadence, one-row layout, hostile text/CJK/combining marks and allocated widths down to zero. The owner integration exercises input → telemetry → intent in both session-backed layouts, resume → cached row, authenticated idle edits, notification → event pump → confidence target, stale score after edit, session switching, and disable. No live model calls or session restart were performed.
