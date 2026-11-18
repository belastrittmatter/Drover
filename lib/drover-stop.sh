#!/usr/bin/env bash
# drover-stop.sh — graceful shutdown of an in-flight drover run.
#
# What it does:
#   1. Stop this repo's runner (.claude/drover/runner.lock) first, then find every
#      in-flight worker (lock files under
#      .claude/drover/inflight/). Only these are touched (FR-WRK-8): no process search, so
#      another repo's runner and workers are never signalled, and a pid whose start time no
#      longer matches its handle (recycled) is never signalled either.
#   2. For each worker, post a "stopped mid-flight" comment on the issue AND its PR
#      (with last commit, lane, and resume hint) — so context survives the kill.
#   3. Release the GitHub assignee mutex on each claimed issue.
#   4. SIGTERM → 1s → SIGKILL each worker through lib/drover-worker.sh.
#   5. Remove the locks of workers that are no longer alive. Leave worktrees in place.
#   6. Print summary + resume command.
#
# What it does NOT do:
#   - Wait for workers to reach a clean stopping point (a headless worker has no SIGTERM
#     handler that flushes a partial commit). Unsaved in-memory work can be lost;
#     uncommitted files remain in the preserved worktree for inspection.
#   - Touch worktrees. Leaving them lets the next worker pick up faster.
#   - Touch branches or PRs. State lives on the GitHub Project board.
#
# Resume:
#   drover run            — same command. The board is the state. Cards sit
#                                in whichever column they were in. Workers re-claim
#                                from the "stopped" comment context.
#
# Usage:
#   drover stop
#
# Exit codes:
#   0  clean stop (or nothing to stop)
#   64 no .drover/config.json, or an argument given

set -euo pipefail

# ───────────────────────────── args + paths ─────────────────────────────
if [ $# -gt 0 ]; then
  echo "usage: drover stop  (one config per repo: .drover/config.json)" >&2
  exit 64
fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH=$(python3 "$HERE/drover-config.py" path)
if [ ! -f "$CONFIG_PATH" ]; then
  echo "no config: $CONFIG_PATH (run drover onboard)" >&2
  exit 64
fi
CONFIG_SLUG=$(python3 "$HERE/drover-config.py" slug)

# ───────────────────────────── config read ─────────────────────────────
BOT_LOGIN=$(jq -r '.notifications.bot_identity // .bot_identity // ""' "$CONFIG_PATH")
INFLIGHT_DIR=".claude/drover/inflight"
RUN_DATE=$(date +%Y-%m-%d)
RUN_MANIFEST=".drover/runs/${RUN_DATE}-${CONFIG_SLUG}.md"
RUNNER_LOCK=".claude/drover/runner.lock"
# shellcheck source=drover-worker.sh
. "$HERE/drover-worker.sh"
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# ───────────────────────────── helpers ─────────────────────────────
log() {
  printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"
  [ -f "$RUN_MANIFEST" ] && printf '[%s] STOP %s\n' "$(date +%H:%M:%S)" "$*" >> "$RUN_MANIFEST" || true
}

# Look up the PR number for an issue's branch (if any). One gh call.
pr_for_issue() {
  local issue="$1"
  gh pr list --state open --json number,headRefName \
    --jq ".[] | select(.headRefName | startswith(\"issue-${issue}-\")) | .number" 2>/dev/null | head -1
}

# Latest commit on the issue's branch (local or remote). Best-effort.
last_commit_on_branch() {
  local issue="$1" branch
  branch=$(git branch -a --list "*issue-${issue}-*" 2>/dev/null | head -1 | sed 's|^[* ]*||;s|^remotes/[^/]*/||')
  [ -z "$branch" ] && { echo "(no branch yet)"; return; }
  git log -1 --format='%h %s' "$branch" 2>/dev/null || echo "(branch found but no commits)"
}

post_stop_comment() {
  local issue="$1" lane="$2" pid="$3" pr commit body
  commit=$(last_commit_on_branch "$issue")
  pr=$(pr_for_issue "$issue" || echo "")

  # Comment format: writing-standard.md § 4.
  body="[orchestrator] [report] stopped · ${lane:-unknown} lane
Did: \`drover stop\` ended worker PID ${pid:-unknown} at ${TS}
Done: resume point is the last pushed commit — ${commit}
Not done: interrupted work may be uncommitted; inspect the preserved worktree
Next: preserve edits before \`drover run\`; dirty or legacy worktrees need inspection"

  gh issue comment "$issue" --body "$body" >/dev/null 2>&1 \
    && log "  issue comment posted on #${issue}" \
    || log "  failed to comment on issue #${issue} (continuing)"

  if [ -n "$pr" ]; then
    gh pr comment "$pr" --body "$body" >/dev/null 2>&1 \
      && log "  PR comment posted on #${pr}" \
      || log "  failed to comment on PR #${pr} (continuing)"
  fi
}

release_claim() {
  local issue="$1"
  [ -z "$BOT_LOGIN" ] && return 0
  gh issue edit "$issue" --remove-assignee "$BOT_LOGIN" >/dev/null 2>&1 || true
  for label in loop:in-build loop:in-qa loop:in-review; do
    gh issue edit "$issue" --remove-label "$label" >/dev/null 2>&1 || true
  done
  log "  released assignee + lane labels on #${issue}"
}

stop_handle() {
  local handle="$1" name="${2:-process}" grace="${3:-1}" pid
  pid=$(worker_pid "$handle" || true)
  worker_stop "$handle" "$grace"
  case "$WORKER_STOPPED_BY" in
    none) log "  ${name} pid=${pid:-empty} already dead (or the pid now names another process)" ;;
    refused) log "  ${name} pid=${pid} not signalled: an older PID= lock has no start time to prove it is drover's; check it by hand" ;;
    KILL) log "  ${name} pid=${pid} SIGKILLed (didn't respond to SIGTERM)" ;;
    *)    log "  ${name} pid=${pid} SIGTERMed" ;;
  esac
}

# ───────────────────────────── main ─────────────────────────────
echo "════════════════════════════════════════════════════════"
echo "  drover stop · ${CONFIG_SLUG}"
echo "════════════════════════════════════════════════════════"

DISPATCHER=""
if worker_lock "$RUNNER_LOCK" && worker_alive "$HANDLE"; then DISPATCHER="$HANDLE"; fi
# 1. The runner first, so it cannot start a new worker while the others are wrapped up;
#    the worker inventory comes after, so a worker it started meanwhile is not missed.
if [ -n "$DISPATCHER" ]; then
  log "stopping dispatcher loop"
  stop_handle "$DISPATCHER" "dispatcher"
  worker_alive "$DISPATCHER" || rm -f "$RUNNER_LOCK"
fi

WORKERS=()
if [ -d "$INFLIGHT_DIR" ]; then
  for lock in "$INFLIGHT_DIR"/*; do
    [ -f "$lock" ] || continue
    issue=$(basename "$lock")
    # Wave/merge control markers are not GitHub issue numbers.
    case "$issue" in ''|*[!0-9]*) continue ;; esac
    worker_lock "$lock"
    WORKERS+=("${issue}|${LANE:-unknown}|${HANDLE}")
  done
fi

if [ "${#WORKERS[@]}" -eq 0 ] && [ -z "$DISPATCHER" ]; then
  echo "  ✓ nothing to stop — no dispatcher, no in-flight workers"
  echo "════════════════════════════════════════════════════════"
  exit 0
fi

# 2. Per-worker wrap-up: comment, release claim, kill.
if [ "${#WORKERS[@]}" -gt 0 ]; then
  log "${#WORKERS[@]} in-flight worker(s) to wrap up"
  for entry in "${WORKERS[@]}"; do
    IFS='|' read -r issue lane handle <<< "$entry"
    log ""
    pid=$(worker_pid "$handle" || true)
    log "─── #${issue} (lane=${lane}, pid=${pid:-unknown}) ───"
    post_stop_comment "$issue" "$lane" "$pid"
    # FactoryBench (metrics.md FR-MET-5): the attempt waits; the next claim resumes it.
    python3 "$HERE/drover-metrics.py" waiting "$issue" stopped || true
    release_claim "$issue"
    stop_handle "$handle" "worker(${lane}/#${issue})"
  done
fi

# 3. Clear the locks of stopped workers (FR-WRK-1: a live worker keeps its lock).
if [ "${#WORKERS[@]}" -gt 0 ]; then
  KEPT=0
  for entry in "${WORKERS[@]}"; do
    IFS='|' read -r issue lane handle <<< "$entry"
    if worker_alive "$handle"; then
      KEPT=$((KEPT + 1)); log "  kept the lock of #${issue}: its worker is still alive"
    else
      rm -f "$INFLIGHT_DIR/$issue"
    fi
  done
  log ""
  log "cleared $INFLIGHT_DIR (${KEPT} lock(s) kept)"
fi

# 4. Summary.
DISP_COUNT=0
[ -z "$DISPATCHER" ] || DISP_COUNT=1
echo ""
echo "════════════════════════════════════════════════════════"
echo "  stopped: ${#WORKERS[@]} worker(s), ${DISP_COUNT} dispatcher(s)"
echo "  wrap-up comments posted on each in-flight issue + PR"
echo "  Resume: drover run"
echo "════════════════════════════════════════════════════════"
