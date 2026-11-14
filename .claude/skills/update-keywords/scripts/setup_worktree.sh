#!/usr/bin/env bash
# New branch from origin/main in its own worktree, wired to the pulled data and the main
# checkout's node_modules (both links are git-excluded, so they are never committed).
# Usage: setup_worktree.sh BRANCH WORKTREE_DIR DATA_DIR
set -euo pipefail
BRANCH="${1:?usage: setup_worktree.sh BRANCH WORKTREE_DIR DATA_DIR}"
DIR="${2:?worktree dir}"
DATA="${3:?data dir (from pull_data.sh)}"
REPO="$(git rev-parse --show-toplevel)"

git -C "$REPO" fetch -q origin
git -C "$REPO" worktree add -q -b "$BRANCH" "$DIR" origin/main
ln -s "$(cd "$DATA" && pwd)" "$DIR/workspace"
[ -d "$REPO/node_modules" ] && ln -s "$REPO/node_modules" "$DIR/node_modules"

EXCLUDE="$(git -C "$REPO" rev-parse --git-common-dir)/info/exclude"
for p in /workspace /node_modules; do
    grep -qxF "$p" "$EXCLUDE" 2>/dev/null || echo "$p" >> "$EXCLUDE"
done
git -C "$DIR" log --oneline -1
echo "Worktree ready: $DIR (workspace -> $DATA)"
