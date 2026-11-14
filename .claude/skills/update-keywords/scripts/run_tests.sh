#!/usr/bin/env bash
# Every check the PR needs, run inside the worktree: Python tests, TypeScript type check, TS tests.
# Exit code 1 if anything failed (the failing output is printed).
set -uo pipefail
PY="${PYTHON:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3}"
[ -x "$PY" ] || PY=python3
FAIL=0
LOG="$(mktemp)"

if "$PY" -m pytest -q tests >"$LOG" 2>&1; then tail -1 "$LOG"; else tail -40 "$LOG"; FAIL=1; fi
if npx tsc --noEmit -p . >"$LOG" 2>&1; then echo "tsc ok"; else cat "$LOG"; FAIL=1; fi
for t in tests/test_*.ts; do
    case "$t" in *test_helpers.ts) continue ;; esac
    if PYTHON_PATH="$PY" npx tsx "$t" >"$LOG" 2>&1; then echo "$(basename "$t") PASS"; else echo "$(basename "$t") FAIL"; tail -15 "$LOG"; FAIL=1; fi
done
rm -rf workspace/test_scratch "$LOG"
exit $FAIL
