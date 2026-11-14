#!/usr/bin/env bash
# Rules-only snapshot of every message's decision, run inside the worktree.
#   evaluate.sh save SNAPSHOT.json        before editing the rules
#   evaluate.sh diff SNAPSHOT.json        after: what changed (also saves SNAPSHOT.after.json)
# Prints accuracy against the owner's Telegram corrections either way. Judges the last EVAL_DAYS
# days (default 120, about a minute); EVAL_DAYS=0 judges the whole history (15+ minutes).
set -euo pipefail
MODE="${1:?usage: evaluate.sh save|diff SNAPSHOT.json}"
SNAP="${2:?snapshot path}"
PY="${PYTHON:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3}"
[ -x "$PY" ] || PY=python3
export REPORTS_SOURCE=sqlite PYTHONPATH="$PWD"
export REPORTS_DB_PATH="$PWD/workspace/reports_corrections.db"
DAYS="${EVAL_DAYS:-120}"
RANGE=()
[ "$DAYS" != 0 ] && RANGE=(--days "$DAYS")

case "$MODE" in
    save) "$PY" -m reports_engine.evaluate --save "$SNAP" ${RANGE[@]+"${RANGE[@]}"} 2>/dev/null ;;
    diff) "$PY" -m reports_engine.evaluate --diff "$SNAP" --save "${SNAP%.json}.after.json" ${RANGE[@]+"${RANGE[@]}"} 2>/dev/null ;;
    *) echo "mode must be save or diff" >&2; exit 2 ;;
esac
