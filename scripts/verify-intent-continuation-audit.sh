#!/usr/bin/env bash
# Offline audit checks; no model calls or production-state access.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${HERMES_AUDIT_PYTHON:?Set HERMES_AUDIT_PYTHON to an existing prepared Hermes test interpreter}"
: "${HERMES_AUDIT_STATE_ROOT:?Set HERMES_AUDIT_STATE_ROOT to a disposable scratch directory}"
# Prevent the canonical runner's provisioning fallback: this wrapper is offline.
if [[ "$HERMES_AUDIT_PYTHON" != /* || ! -x "$HERMES_AUDIT_PYTHON" ]]; then
    printf '%s\n' 'Audit requires an absolute executable prepared Python path; no provisioning attempted.' >&2
    exit 2
fi
if ! "$HERMES_AUDIT_PYTHON" -c 'import pytest' >/dev/null 2>&1; then
    printf '%s\n' 'Audit interpreter cannot import pytest; no provisioning attempted.' >&2
    exit 2
fi
mkdir -p "$HERMES_AUDIT_STATE_ROOT"/{home,profile,runtime,tmp}
export HOME="$HERMES_AUDIT_STATE_ROOT/home"
export HERMES_HOME="$HERMES_AUDIT_STATE_ROOT/profile"
export HERMES_RUNTIME_DIR="$HERMES_AUDIT_STATE_ROOT/runtime"
export TMPDIR="$HERMES_AUDIT_STATE_ROOT/tmp" TMP="$HERMES_AUDIT_STATE_ROOT/tmp" TEMP="$HERMES_AUDIT_STATE_ROOT/tmp"
export HERMES_PYTHON="$HERMES_AUDIT_PYTHON" HERMES_TEST_FILE_RETRIES=0
unset __HERMES_ACTIVATED HERMES_TUI_GATEWAY_URL
cd "$ROOT"
exec bash scripts/run_tests.sh -j 3 \
 tests/hermes_cli/test_intent_continuation_audit.py \
 tests/hermes_cli/test_goals.py \
 tests/hermes_cli/test_goal_dispatch.py \
 tests/hermes_cli/test_goal_gates.py \
 tests/hermes_cli/test_goal_judge_delegations.py \
 tests/agent/test_verification_stop.py \
 tests/agent/test_verification_stop_caching.py \
 tests/agent/test_kanban_stop.py \
 --tb=short "$@"
