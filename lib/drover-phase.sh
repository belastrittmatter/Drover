#!/usr/bin/env bash
# drover-phase.sh — a worker says what it is doing inside its lane.
#
# Usage: drover phase <name>      name: ^[a-z][a-z0-9-]{0,31}$ (plan, tests, verify, ci, ...)
#
# Appends {"name", "at"} to .drover/runs/phases/<DROVER_ISSUE>.jsonl in the main checkout
# (the worker runs in a linked worktree). The runner folds the log into state.json
# cards[].phase and cards[].phases on its next tick (specs/runner.md FR-RUN-18).
# Exit: 64 bad usage or name (nothing written) · 0 otherwise: a phase that cannot be
# recorded never fails the worker.
set -uo pipefail

[ $# -eq 1 ] && [[ "$1" =~ ^[a-z][a-z0-9-]{0,31}$ ]] \
  || { echo "usage: drover phase <name>  (name matches ^[a-z][a-z0-9-]{0,31}\$)" >&2; exit 64; }
issue="${DROVER_ISSUE:-}"
[[ "$issue" =~ ^[0-9]+$ ]] || { echo "drover phase: no worker issue (DROVER_ISSUE), not recorded" >&2; exit 0; }

common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) \
  || { echo "drover phase: not in a git repo, not recorded" >&2; exit 0; }
dir="$(dirname "$common")/.drover/runs/phases"
# One short line per append: concurrent writers never interleave within it.
mkdir -p "$dir" && jq -nc --arg name "$1" --arg at "$(date -u +%FT%TZ)" '{name: $name, at: $at}' >> "$dir/$issue.jsonl" \
  || echo "drover phase: could not write $dir/$issue.jsonl, not recorded" >&2
exit 0
