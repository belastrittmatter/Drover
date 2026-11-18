#!/usr/bin/env bash
# drover-run.sh — headless autonomous runner.
# Spawned as `nohup drover run &` from the repo root (config: .drover/config.json).
# Pure shell while-loop. Dispatches `claude -p` workers per lane.
# Holds NO Claude session state — re-reads GitHub on every tick.
#
# Anti-zombie controls (added 2026-05-22 after #381 worker-storm incident):
#   1. Leftover-worker check on startup — refuses to start while a lock's worker is alive.
#   2. Issue-level lock files in .claude/drover/inflight/<N> — survives runner restart.
#      All worker start / liveness / signals go through lib/drover-worker.sh (FR-WRK-1).
#   3. Atomic GitHub assignee claim BEFORE spawning worker (closes 10-30s claude -p cold-start race).
#   4. Rate-limit guard — sleeps until reset when GraphQL remaining < 200.
#   5. Per-tick project-items cache — one gh call per tick, not per column lookup.
#   6. Tick interval bumped from 30s → 120s (GraphQL ProjectsV2 query is ~103 pts; 120s keeps usage <3.1k/hr vs 5k budget).
#   7. Lane-zombie watchdog (added 2026-05-24 after a first-run hang on a live board) — stops workers whose
#      claimed issue has moved out of the lane's source columns, one tick after it is first seen there
#      (FR-WRK-10: the worker gets that tick to finish its handoff). Uses the project-items cache only.
#   8. Closed-issue guard (added 2026-08-06, issue #10) — a card is selected by Status column, which
#      says nothing about whether the underlying issue is still open. Before every dispatch the
#      candidate's issue state is checked; a CLOSED issue sitting in a non-terminal column goes to
#      Done when a PR from its issue branch is merged into the base or it was closed as not planned
#      with a comment, and to Blocked [needs-you] otherwise (FR-BRD-3).
#   9. Landed-work halt gate (added 2026-08-06, issue #8) — forward progress is measured by the set of
#      cards in Done changing, NOT by lane occupancy. A pipeline that is 100% busy but lands nothing
#      is exactly the runaway this gate must catch, so lane state no longer suppresses it.
#      Config: no_progress_cycles (default 6), max_dispatches (default 0 = unlimited).
#  10. Failed-dispatch halt (FR-RUN-13) — a worker that exits with its card still in the column it
#      was dispatched from failed; two failures in a row on one card halt the run with its log tail.
#
# Workers per role: roles.<role>.max_workers (default 1, FR-RUN-2), counted from the live locks.

set -euo pipefail
SB_GITHUB_READ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/drover-github-read.py"
gh_checkpoint() { python3 "$SB_GITHUB_READ" --check || exit $?; }
gh_write_once() {
  gh_checkpoint
  gh "$@" || {
    python3 "$SB_GITHUB_READ" --halt "GitHub $1 $2 response failed; reconcile the write before restarting" >&2 || true
    exit 79
  }
}

# ───────────────────────────── args + paths ─────────────────────────────
# SB_LIB_ONLY=1 sources this file for its helpers only (gate tests). Config discovery,
# preconditions and the run loop are all skipped; the caller supplies whatever vars it needs.
DROVER_LIB="$(dirname "$SB_GITHUB_READ")"
DROVER_HOME="${DROVER_HOME:-$(dirname "$DROVER_LIB")}"
if [ "${SB_LIB_ONLY:-0}" = "1" ]; then
  CONFIG_SLUG="${CONFIG_SLUG:-lib}"
  CONFIG_PATH="${CONFIG_PATH:-/dev/null}"
elif [ $# -gt 0 ]; then
  echo "usage: drover run  (one config per repo: .drover/config.json)" >&2
  exit 64
fi

if [ "${SB_LIB_ONLY:-0}" != "1" ]; then
# One config per repo; the run slug in manifest and log names is the repo name (FR-CFG-1).
CONFIG_PATH=$(python3 "$DROVER_LIB/drover-config.py" path)
if [ ! -f "$CONFIG_PATH" ]; then
  echo "no config: $CONFIG_PATH (run drover onboard)" >&2
  exit 64
fi
CONFIG_SLUG=$(python3 "$DROVER_LIB/drover-config.py" slug)
# The worker harness (FR-HRN-1). A missing harnesses/<name>.json halts here, before any claim.
HARNESS=$(jq -r '.harness // "claude"' "$CONFIG_PATH")
python3 "$DROVER_LIB/drover-config.py" harness "$HARNESS" >/dev/null || exit $?

# ───────────────────────────── config read ─────────────────────────────
# `variant` was removed in v3.0.0: one board shape, labels route cards (a Ready card
# labelled `qa` goes to the Tester, everything else to the Builder). A config still
# saying "qa-only" is refused, same as the wave planner.
VARIANT=$(jq -r '.variant // "full"' "$CONFIG_PATH")
if [ "$VARIANT" != "full" ]; then
  echo "config still says variant \"$VARIANT\" — removed in v3.0.0. Run /drover onboard to upgrade." >&2
  exit 65
fi
PROJECT_OWNER=$(jq -r '.project.owner' "$CONFIG_PATH")
PROJECT_NUMBER=$(jq -r '.project.number' "$CONFIG_PATH")
BASE_BRANCH=$(jq -r '.base_branch // "main"' "$CONFIG_PATH")
HUMAN_APPROVES=$(jq -r '.human_approves_merge // false' "$CONFIG_PATH")
REBUILD_CAP=$(jq -r '.rebuild_cap // 2' "$CONFIG_PATH")
BLOCK_ALERT_PCT=$(jq -r '.block_rate_alert_pct // 30' "$CONFIG_PATH")
TICK_SECONDS=$(jq -r '.tick_seconds // 120' "$CONFIG_PATH")
# Workers per role (FR-RUN-2): roles.<role>.max_workers, default 1. MAX_WORKERS is their sum.
CAP_build=$(jq -r '.roles.build.max_workers // 1' "$CONFIG_PATH")
CAP_qa=$(jq -r '.roles.qa.max_workers // 1' "$CONFIG_PATH")
CAP_review=$(jq -r '.roles.review.max_workers // 1' "$CONFIG_PATH")
MAX_WORKERS=$((CAP_build + CAP_qa + CAP_review))
BOT_LOGIN=$(jq -r '.notifications.bot_identity // .bot_identity // ""' "$CONFIG_PATH")
# Runaway controls (issue #8). NO_PROGRESS_CYCLES counts dispatch cycles with no
# card reaching Done, regardless of lane occupancy. MAX_DISPATCHES=0 disables the ceiling.
NO_PROGRESS_CYCLES=$(jq -r '.no_progress_cycles // 6' "$CONFIG_PATH")
MAX_DISPATCHES=$(jq -r '.max_dispatches // 0' "$CONFIG_PATH")

python3 "$(dirname "$SB_GITHUB_READ")/drover-verification-ready.py" --config "$CONFIG_PATH"

RUN_DATE=$(date +%Y-%m-%d)
# The manifest and worker logs live in the git-excluded .drover/runs/ (FR-MET-3).
RUN_MANIFEST=".drover/runs/${RUN_DATE}-${CONFIG_SLUG}.md"
# Per-worker logs (issue #13). Workers used to be spawned with >/dev/null, so the run
# log could never show what a worker was doing during a 13-minute silence.
WORKER_LOG_DIR=".drover/runs/${RUN_DATE}-${CONFIG_SLUG}-workers"
INFLIGHT_DIR=".claude/drover/inflight"
RUNNER_LOCK=".claude/drover/runner.lock"
mkdir -p "$WORKER_LOG_DIR" .drover/worktrees "$INFLIGHT_DIR"
fi  # end non-lib-only setup

# shellcheck source=drover-worker.sh
. "$DROVER_LIB/drover-worker.sh"

# Defaults so the helpers below are safe to source under `set -u` in lib-only mode.
: "${RUN_MANIFEST:=/dev/null}"
: "${WORKER_LOG_DIR:=/dev/null}"
: "${INFLIGHT_DIR:=/dev/null}"
: "${PROJECT_OWNER:=}" ; : "${PROJECT_NUMBER:=0}" ; : "${BOT_LOGIN:=}"
: "${CAP_build:=1}" ; : "${CAP_qa:=1}" ; : "${CAP_review:=1}" ; : "${BASE_BRANCH:=main}"
# Issues already looked at this tick (dispatched, skipped, claim lost): never picked twice.
TICK_SEEN=" "
MY_HANDLES=" "   # every worker this runner started: the only ones a halt stops (FR-WRK-8)

# ───────────────────────────── helpers ─────────────────────────────
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$RUN_MANIFEST"; }

PROJECT_ITEMS_JSON=""
fetch_project_items() {
  # One board read per tick; all column lookups read from this cache.
  # drover-card.sh items: ~1 GraphQL point per 100 cards, where
  # `gh project item-list` spends 101 per 100.
  local fresh
  fresh=$("$(dirname "$SB_GITHUB_READ")/drover-card.sh" --owner "$PROJECT_OWNER" --number "$PROJECT_NUMBER" items) || return $?
  PROJECT_ITEMS_JSON="$fresh"
}

column_count() {
  echo "$PROJECT_ITEMS_JSON" | jq --arg col "$1" '[.items[] | select(.status == $col)] | length'
}

# ── Status-field resolution (issue #10 reconcile path).
# Option IDs are resolved BY NAME at runtime and cached for the run. Never hard-code
# them: `updateProjectV2Field` replaces the whole option list and remints every ID,
# so a hard-coded map goes stale silently the first time anyone edits the columns.
STATUS_FIELD_ID=""
STATUS_OPTIONS_JSON=""
resolve_status_field() {
  [ -n "$STATUS_FIELD_ID" ] && return 0
  local payload
  payload=$(python3 "$SB_GITHUB_READ" --kind fields -- project field-list "$PROJECT_NUMBER" --owner "$PROJECT_OWNER" --format json) || return $?
  STATUS_FIELD_ID=$(echo "$payload" | jq -r '.fields[] | select(.name == "Status") | .id')
  STATUS_OPTIONS_JSON=$(echo "$payload" | jq -c '.fields[] | select(.name == "Status") | .options')
  [ -n "$STATUS_FIELD_ID" ] && [ "$STATUS_FIELD_ID" != "null" ]
}

status_option_id() {
  # $1 = option name. Fails loud (exit 1 + empty output) when the name is absent
  # from the live option set, rather than mutating with a stale ID.
  local id
  id=$(echo "${STATUS_OPTIONS_JSON:-[]}" | jq -r --arg n "$1" '
    map(select(.name == $n)) | .[0].id // empty' 2>/dev/null)
  [ -n "$id" ] || return 1
  echo "$id"
}

set_card_status() {
  # $1 = project item id, $2 = target status name. Returns non-zero if unresolvable.
  local item_id="$1" name="$2" opt_id
  resolve_status_field || { gh_checkpoint; log "could not resolve Status field — skipping reconcile"; return 1; }
  opt_id=$(status_option_id "$name") || {
    log "Status option '${name}' not present on the board — skipping reconcile"; return 1; }
  local project_id
  project_id=$(project_node_id) || return $?
  gh_checkpoint
  gh_write_once project item-edit --id "$item_id" --project-id "$project_id" \
    --field-id "$STATUS_FIELD_ID" --single-select-option-id "$opt_id" >/dev/null 2>&1
}

PROJECT_NODE_ID=""
project_node_id() {
  if [ -z "$PROJECT_NODE_ID" ]; then
    local payload
    payload=$(python3 "$SB_GITHUB_READ" --kind project -- project view "$PROJECT_NUMBER" --owner "$PROJECT_OWNER" --format json) || return $?
    PROJECT_NODE_ID=$(echo "$payload" | jq -r .id)
  fi
  echo "$PROJECT_NODE_ID"
}

issue_is_open() {
  # $1 = issue number. Returns 0 when OPEN, 1 when CLOSED.
  # Unreadable is distinct from both OPEN and CLOSED: exit 79 halts the run.
  local state
  state=$(python3 "$SB_GITHUB_READ" --kind state -- issue view "$1" --json state -q '.state') || return 79
  [ "$state" != "CLOSED" ]
}

closed_card_column() {
  # $1 = a CLOSED issue. Prints where its card belongs (FR-BRD-3): Done when a PR from its
  # issue-<N>-* branch is merged into the base, or it was closed as not planned with a comment;
  # Blocked otherwise. Exit 79 when GitHub cannot be read.
  local info heads
  info=$(python3 "$SB_GITHUB_READ" --kind object -- issue view "$1" --json stateReason,comments \
    --jq '{reason: (.stateReason // ""), comments: (.comments | length)}') || return 79
  if [ "$(echo "$info" | jq -r '.reason == "NOT_PLANNED" and .comments > 0')" = true ]; then
    echo Done; return 0
  fi
  # shortcut: only the 200 newest merged PRs are searched; a card closed long after its merge
  # lands in Blocked (the safe side). Page through merged PRs if that ever shows up.
  heads=$(python3 "$SB_GITHUB_READ" --kind array -- pr list --state merged --base "$BASE_BRANCH" \
    --json headRefName --limit 200) || return 79
  if echo "$heads" | jq -e --arg p "issue-$1-" 'any(.[]; .headRefName | startswith($p))' >/dev/null; then
    echo Done
  else
    echo Blocked
  fi
}

block_closed_unmerged() {
  # $1 = issue, $2 = its board item, $3 = the column it sat in. Comment: block-template.md.
  local issue="$1" item_id="$2" col="$3"
  log "closed #${issue} without a merge into ${BASE_BRANCH} -> Blocked [needs-you] (was '${col}')"
  set_card_status "$item_id" "Blocked" || { gh_checkpoint; log "  ↳ move of #${issue} to Blocked could not be written to the board"; return 1; }
  gh_checkpoint
  gh_write_once issue comment "$issue" --body "[orchestrator] [blocker] blocked [needs-you] closed without a merge into ${BASE_BRANCH}
Card:        #${issue}
Reason tag:  [needs-you]
Why blocked: the issue is closed, but no PR from an issue-${issue}-* branch is merged into ${BASE_BRANCH}
Evidence:    gh pr list --state merged --base ${BASE_BRANCH}: no head branch issue-${issue}-*
Checked:     issue state and close reason; merged PRs into ${BASE_BRANCH}
What blocks: nothing shows the work landed, and it was not closed as not planned with a comment
Why I (bot) cannot decide:
             a close without a merge can mean done elsewhere or dropped; only the owner knows which
To unblock:  [ ] work landed another way: move the card to Done
             [ ] work dropped: add a comment, close it as not planned, move the card to Done
             [ ] work remains: reopen the issue
Owner:       owner
Move back:   drag this card to Ready after reopening the issue
blocked-by:  -" >/dev/null 2>&1 || true
}

TOP_ISSUE=""
TOP_ITEM_ID=""
card_lane() {
  # "qa" when issue $1 carries the `qa` label on the board snapshot, else "build".
  # Labels sit on the item (`labels`) or its content, as names or {name}.
  echo "$PROJECT_ITEMS_JSON" | jq -r --argjson n "$1" '
    [.items[] | select(.content.number == $n)
     | ((.labels // []) + (.content.labels // []))[]
     | (if type == "object" then .name else . end) | ascii_downcase]
    | if index("qa") then "qa" else "build" end' 2>/dev/null || echo build
}

top_card_in_column() {
  # Sets TOP_ISSUE / TOP_ITEM_ID to the FIRST dispatchable card in column $1:
  # no assignee, no local in-flight lock, not already looked at this tick (TICK_SEEN),
  # and the underlying issue still OPEN. A CLOSED issue found in a non-terminal column is
  # reconciled (Done or Blocked, FR-BRD-3) and skipped — dispatching a worker at it is the
  # single largest observed waste category (issue #10).
  #
  # The result is returned via TOP_ISSUE, NOT stdout: this function logs as it
  # reconciles, and log lines on stdout would be captured by a `$(...)` caller and
  # read back as a card number.
  local col="$1" issue item_id
  TOP_ISSUE=""; TOP_ITEM_ID=""
  while IFS=$'\t' read -r issue item_id; do
    [ -n "$issue" ] || continue
    case "$TICK_SEEN" in *" $issue "*) continue ;; esac
    issue_locked "$issue" && continue
    local issue_rc=0 target
    issue_is_open "$issue" || issue_rc=$?
    [ "$issue_rc" -le 1 ] || exit "$issue_rc"
    if [ "$issue_rc" -ne 0 ]; then
      target=$(closed_card_column "$issue") || exit $?
      if [ "$target" = Done ]; then
        log "reconciled closed #${issue} -> Done (was '${col}') — dispatching 0 workers for it"
        set_card_status "$item_id" "Done" || { gh_checkpoint; log "  ↳ reconcile of #${issue} could not be written to the board"; return 1; }
      else
        block_closed_unmerged "$issue" "$item_id" "$col" || return 1
      fi
      gh_checkpoint
      [ -n "$BOT_LOGIN" ] && gh_write_once issue edit "$issue" --remove-assignee "$BOT_LOGIN" >/dev/null 2>&1 || true
      rm -f "$INFLIGHT_DIR/$issue"
      continue
    fi
    TOP_ISSUE="$issue"; TOP_ITEM_ID="$item_id"
    return 0
  done <<EOF
$(echo "$PROJECT_ITEMS_JSON" | jq -r --arg col "$col" '
    .items[]
    | select(.status == $col and .content.type == "Issue")
    | select((.content.assignees // []) | length == 0)
    | [(.content.number | tostring), (.id // "")] | @tsv')
EOF
  return 1
}

issue_locked() {
  # Returns 0 while the issue has a lock. A dead worker's lock stays until the next
  # reap_finished_locks, which releases the claim and judges the dispatch (FR-RUN-13);
  # until then the card is not dispatched again.
  gh_checkpoint
  [ -f "$INFLIGHT_DIR/$1" ]
}

issue_locks() {
  # The issue numbers that hold an in-flight lock. Other files there (the workflow
  # backend's workflow-wave.lock, merge.lock) are not ours.
  local lock issue
  for lock in "$INFLIGHT_DIR"/*; do
    [ -f "$lock" ] || continue
    issue=$(basename "$lock")
    case "$issue" in *[!0-9]*|'') continue ;; esac
    echo "$issue"
  done
}

lane_running() {
  # Number of live workers in lane $1 (FR-RUN-2: the role's set is its live locks).
  local issue n=0
  for issue in $(issue_locks); do
    worker_lock "$INFLIGHT_DIR/$issue" || continue
    [ "$LANE" = "$1" ] && worker_alive "$HANDLE" && n=$((n + 1))
  done
  echo "$n"
}

lane_has_room() {
  local cap_var="CAP_$1"
  [ "$(lane_running "$1")" -lt "${!cap_var}" ]
}

gh_rate_guard() {
  # Sleep until rate limit resets if GraphQL remaining < 200.
  local payload remaining reset now wait
  payload=$(python3 "$SB_GITHUB_READ" --kind quota -- api rate_limit) || return $?
  # REST misreports the GraphQL bucket; drover-gh-guard.sh → sb_gh_quota_merge.
  payload=$(. "$(dirname "$SB_GITHUB_READ")/drover-gh-guard.sh" && sb_gh_quota_merge "$payload")
  remaining=$(echo "$payload" | jq -r '.resources.graphql.remaining // 5000')
  if [ "$remaining" -lt 200 ]; then
    reset=$(echo "$payload" | jq -r '.resources.graphql.reset // 0')
    now=$(date +%s)
    wait=$((reset - now + 10))
    [ "$wait" -lt 60 ] && wait=60
    log "GraphQL rate limit low (${remaining} left) — sleeping ${wait}s until reset"
    sleep "$wait"
  fi
}

try_claim_assignee() {
  # Atomic claim. Returns 0 if we won the claim, 1 if someone else beat us.
  # Skipped when bot_identity is unset (solo single-user runs rely on local locks only).
  # We rely on `top_card_in_column` having already filtered out cards with assignees
  # from the cached project item-list — so we attempt the edit directly without a
  # pre-check `gh issue view`. Saves one GraphQL call per dispatch. The edit is
  # idempotent for self-assign; on race-loss, gh returns non-zero and we skip.
  local issue="$1"
  [ -z "$BOT_LOGIN" ] && return 0
  gh_checkpoint
  gh_write_once issue edit "$issue" --add-assignee "$BOT_LOGIN" >/dev/null 2>&1 || {
    log "claim failed on #${issue} (race or gh api error) — skipping this tick"
    return 1
  }
  return 0
}

dispatch_lane() {
  # $1 = lane (build|qa|review); $2 = issue number
  local lane="$1" issue="$2" prompt
  TICK_SEEN="${TICK_SEEN}${issue} "
  if issue_locked "$issue"; then
    log "skip dispatch lane=${lane} issue=#${issue} — already locked"
    return 0
  fi
  # Authoritative closed-issue gate (issue #10). top_card_in_column also filters and
  # reconciles, but every dispatch path funnels through here — one guard, one place.
  gh_checkpoint
  local issue_rc=0
  issue_is_open "$issue" || issue_rc=$?
  [ "$issue_rc" -le 1 ] || exit "$issue_rc"
  if [ "$issue_rc" -ne 0 ]; then
    log "skip dispatch lane=${lane} issue=#${issue} — issue is CLOSED"
    return 0
  fi
  local card skills_rc=0 skills_report
  card=$(echo "$PROJECT_ITEMS_JSON" | jq -c --argjson n "$issue" --arg lane "$lane" '
    [.items[] | select(.content.number == $n) | {number:$n, status:(.status // "Ready"), labels:(.labels // .content.labels // []), lane:$lane}]')
  [ "$(printf '%s' "$card" | jq length)" -eq 1 ] || { log "skills-readiness: selected issue #${issue} missing from board cache"; exit 64; }
  # FR-CFG-3/4: the role, label and issue `Skills:` routing from the config, each checked in
  # the harness's skill folders. A missing skill halts here, before the claim.
  skills_report=$(printf '%s' "$card" | python3 "$DROVER_LIB/drover-skills-ready.py" \
    --root "$PWD" --host "${HARNESS:-claude}" --cards - --hydrate) || skills_rc=$?
  if [ "$skills_rc" -ne 0 ]; then
    log "skills-readiness: halt before claim lane=${lane} issue=#${issue}; ${skills_report}"
    exit "$skills_rc"
  fi
  if ! try_claim_assignee "$issue"; then
    return 0
  fi
  case "$lane" in
    build)  prompt="Run drover-build on issue #${issue} for drover run. Read ${DROVER_HOME}/skills/drover/references/run.md → Builder lifecycle. Config: ${CONFIG_PATH}." ;;
    qa)     prompt="Run drover-qa on issue #${issue} for drover run. Read ${DROVER_HOME}/skills/drover/references/run.md → Tester lifecycle. Config: ${CONFIG_PATH}." ;;
    review) prompt="Run drover-review on issue #${issue} for drover run. Read ${DROVER_HOME}/skills/drover/references/run.md → Reviewer lifecycle. Config: ${CONFIG_PATH}." ;;
    *) log "unknown lane: $lane"; return 1 ;;
  esac
  # FR-CNV-2: the repo's commit style and three real subjects ride in every lane prompt.
  prompt="$prompt $(python3 "$DROVER_LIB/drover-commit-style.py" --root "$PWD" --config "$CONFIG_PATH" --prompt 2>/dev/null || true)"
  # The skills checked above, by path: the worker loads them and runs no readiness command itself.
  prompt="Skills for this card (load each SKILL.md before you start): $(printf '%s' "$skills_report" | jq -r --arg lane "$lane" '
    [.entries[] | select(.lane == $lane and .status == "resolved") | "\(.name)=\(.resolved_path)"] | join(", ")'). $prompt"
  # Worker output goes to a per-worker log, not /dev/null (issue #13).
  local worker_log="${WORKER_LOG_DIR}/worker-${lane}-${issue}.log"
  {
    printf '=== dispatch lane=%s issue=#%s at %s ===\n' "$lane" "$issue" "$(date -u +%FT%TZ)"
    printf 'prompt: %s\n\n' "$prompt"
  } >> "$worker_log"
  gh_checkpoint
  # The harness's worker command with the role's model and the headless permissions
  # (FR-HRN-4): a worker never waits on an approval prompt.
  worker_start "$lane" "$issue" "$prompt" "$worker_log" || { log "worker command: no argv from harness ${HARNESS:-claude}"; exit 66; }
  MY_HANDLES="${MY_HANDLES}${WORKER_HANDLE} "
  DISPATCH_COUNT=$((DISPATCH_COUNT + 1))
  DISPATCH_LOG="${DISPATCH_LOG}${issue}
"
  # Lock format: HANDLE / LANE / STARTED for `drover stop`, COLUMN (the card's column at
  # dispatch) for the failed-dispatch check. Readers still accept the older PID= line.
  printf 'HANDLE=%s\nLANE=%s\nSTARTED=%s\nCOLUMN=%s\n' "$WORKER_HANDLE" "$lane" "$(date -u +%FT%TZ)" \
    "$(issue_status "$issue")" > "$INFLIGHT_DIR/$issue"
  # pid= stays a number (metrics.md FR-MET-2): the process backend's id.
  log "dispatch lane=${lane} issue=#${issue} pid=${WORKER_HANDLE#*:} claim=${BOT_LOGIN:-local-only}"
}

issue_status() {
  # Lookup issue #$1 in the cached project items; emit its current column name (or empty).
  echo "$PROJECT_ITEMS_JSON" | jq -r --arg n "$1" '
    .items[] | select(.content.number == ($n | tonumber)) | .status' | head -1
}

lane_columns() {
  # The columns a lane's card sits in while its worker works.
  case "$1" in
    build) echo "Ready Building" ;;
    qa) echo "QA" ;;
    review) echo "Review" ;;
  esac
}

TICK=0
LEFT_LANE=" "   # "<issue>:<tick>" for each live worker whose card was first seen out of its lane at <tick>
sweep_lane_zombies() {
  # A live worker whose card left its lane's columns (and the column it was dispatched
  # from) has done its logical work. It gets one more tick to finish its handoff (FR-WRK-10),
  # then it is stopped and its lane freed. Uses cached project items only (no extra API calls).
  local issue cur seen pid marks=" "
  for issue in $(issue_locks); do
    worker_lock "$INFLIGHT_DIR/$issue" || continue
    worker_alive "$HANDLE" || continue     # already dead → reap_finished_locks handles it
    cur=$(issue_status "$issue")
    [ -n "$cur" ] || continue               # not in cache (closed/deleted/race) → don't stop
    case " $(lane_columns "$LANE") ${COLUMN} " in *" $cur "*) continue ;; esac
    seen=""
    case "$LEFT_LANE" in *" $issue:"*) seen="${LEFT_LANE#* $issue:}"; seen="${seen%% *}" ;; esac
    pid="${HANDLE#*:}"
    if [ -z "$seen" ]; then
      log "${LANE} worker on #${issue} (pid=${pid}) — card moved to '${cur}'; one more tick to finish its handoff"
      marks="${marks}${issue}:${TICK} "
      continue
    fi
    if [ "$seen" -ge "$TICK" ]; then
      marks="${marks}${issue}:${seen} "
      continue
    fi
    gh_checkpoint
    log "zombie ${LANE} worker on #${issue} (pid=${pid}) — card moved to '${cur}'; killing"
    worker_stop "$HANDLE" 1
    if worker_alive "$HANDLE"; then
      marks="${marks}${issue}:${seen} "    # FR-WRK-1: a live worker keeps its lock
      continue
    fi
    gh_checkpoint
    [ -n "$BOT_LOGIN" ] && gh_write_once issue edit "$issue" --remove-assignee "$BOT_LOGIN" >/dev/null 2>&1 || true
    gh_checkpoint
    rm -f "$INFLIGHT_DIR/$issue"
  done
  LEFT_LANE="$marks"
}

done_signature() {
  # The set of cards that have LANDED, as a stable string. This — not lane
  # occupancy — is the run's definition of forward progress (issue #8).
  # A card deliberately dropped is closed and moved to Done too (v3.0.0: no Skipped column).
  echo "$PROJECT_ITEMS_JSON" | jq -r '
    [.items[] | select(.status == "Done") | .content.number // empty]
    | sort | @csv' 2>/dev/null || echo ""
}

record_cycle_progress() {
  # One dispatch cycle's verdict on forward progress (issue #8).
  # Reads/writes PREV_DONE_SIG + NO_PROGRESS_CYCLE_COUNT + LANDED_COUNT.
  # Returns 0 = keep running, 1 = halt (runaway).
  # $1 = the current done signature (injected so tests need no board).
  local cur="$1"
  if [ "$cur" != "$PREV_DONE_SIG" ]; then
    LANDED_COUNT=$((LANDED_COUNT + 1))
    NO_PROGRESS_CYCLE_COUNT=0
    PREV_DONE_SIG="$cur"
    return 0
  fi
  NO_PROGRESS_CYCLE_COUNT=$((NO_PROGRESS_CYCLE_COUNT + 1))
  [ "$NO_PROGRESS_CYCLE_COUNT" -lt "$NO_PROGRESS_CYCLES" ]
}

dispatch_ceiling_hit() {
  # Returns 0 when the hard spend ceiling is reached. MAX_DISPATCHES=0 disables it.
  [ "$MAX_DISPATCHES" -gt 0 ] && [ "$DISPATCH_COUNT" -ge "$MAX_DISPATCHES" ]
}

runaway_summary() {
  # Loud, actionable halt line: what the run spent, and on what.
  log "   dispatches=${DISPATCH_COUNT} reaps=${REAP_COUNT} cards-landed-this-run=${LANDED_COUNT}"
  local top
  top=$(printf '%s' "$DISPATCH_LOG" | grep -c . 2>/dev/null || echo 0)
  if [ "${top:-0}" -gt 0 ]; then
    log "   most re-dispatched issues (count issue):"
    printf '%s' "$DISPATCH_LOG" | grep . | sort | uniq -c | sort -rn | head -5 | while read -r line; do
      log "     $line"
    done
  fi
  log "   → inspect worker logs in ${WORKER_LOG_DIR}/"
}

JUDGE_QUEUE=""   # "<issue>|<lane>|<column at dispatch>" per worker that exited this run
FAILS=" "        # "<issue>:<failed dispatches in a row>"
reap_finished_locks() {
  # Sweep inflight/ for dead workers; remove locks AND sweep stale assignees so the
  # next dispatch can re-claim the card if the worker crashed without releasing.
  # The assignee remove is idempotent — no-op if the worker exited cleanly.
  # Issue locks only (issue_locks): the workflow backend's workflow-wave.lock is not
  # ours to reap — deleting it would dissolve the backend mutual exclusion mid-run.
  # $1 = "judge": queue each reaped worker of this run for judge_finished_workers.
  local issue pid
  for issue in $(issue_locks); do
    gh_checkpoint
    worker_lock "$INFLIGHT_DIR/$issue" || continue
    worker_alive "$HANDLE" && continue
    pid="${HANDLE#*:}"
    gh_checkpoint
    if [ -n "$BOT_LOGIN" ]; then
      gh_checkpoint
      gh_write_once issue edit "$issue" --remove-assignee "$BOT_LOGIN" >/dev/null 2>&1 || true
      log "reaped stale lock + swept assignee on #${issue} (pid=${pid:-empty})"
    else
      log "reaped stale lock for #${issue} (pid=${pid:-empty})"
    fi
    [ "${1:-}" = judge ] && [ -n "$COLUMN" ] && JUDGE_QUEUE="${JUDGE_QUEUE} ${issue}|${LANE}|${COLUMN}"
    gh_checkpoint
    rm -f "$INFLIGHT_DIR/$issue"
    REAP_COUNT=$((REAP_COUNT + 1))
  done
}

judge_finished_workers() {
  # FR-RUN-13, on a fresh board read: a worker that exited with its card still in the
  # column it was dispatched from is a failed dispatch (the board decides, FR-WRK-6).
  # Returns 1 (halt) after two failed dispatches in a row on one card, with its log tail.
  local entry issue lane col cur n worker_log
  for entry in $JUDGE_QUEUE; do
    IFS='|' read -r issue lane col <<EOF
$entry
EOF
    n=0
    case "$FAILS" in *" $issue:"*) n="${FAILS#* $issue:}"; n="${n%% *}" ;; esac
    FAILS=$(printf '%s' "$FAILS" | sed "s/ $issue:[0-9]* / /")
    cur=$(issue_status "$issue")
    [ -n "$cur" ] && [ "$cur" = "$col" ] || continue
    n=$((n + 1))
    FAILS="${FAILS}${issue}:${n} "
    log "failed dispatch lane=${lane} issue=#${issue} — worker exited, card still in '${col}' (${n} in a row)"
    if [ "$n" -ge 2 ]; then
      JUDGE_QUEUE=""
      worker_log="${WORKER_LOG_DIR}/worker-${lane}-${issue}.log"
      log "halt — failed dispatch: #${issue} failed ${n} dispatches in a row; last 20 lines of ${worker_log}:"
      tail -n 20 "$worker_log" 2>/dev/null | while IFS= read -r line; do log "   | $line"; done
      return 1
    fi
  done
  JUDGE_QUEUE=""
  return 0
}

check_legacy_worktrees() {
  # Never strand a previous run's edits by silently switching its folder.
  # Pre-rename card worktrees only: .claude/worktrees/ also holds unrelated agent worktrees.
  local wt
  for wt in .claude/worktrees/issue-*; do
    [ -e "$wt" ] || continue
    log "legacy worktree $wt exists — stop its worker and inspect/preserve its edits"
    log "    Move it with git worktree move to .drover/worktrees/ before resuming."
    return 73
  done
  return 0
}

cleanup_stale_worktrees() {
  # This directory also holds Claude's unrelated worktrees. Only board names
  # belong to this sweep, and a failed Git removal must never become rm -rf.
  local wt branch
  for wt in .drover/worktrees/issue-*; do
    [ -d "$wt" ] || continue
    [[ "${wt##*/}" =~ ^issue-[0-9]+(-(build|qa|review))?$ ]] || continue
    branch=$(git -C "$wt" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
    # Detached work may contain commits with no other ref; it is not proof of staleness.
    [ -n "$branch" ] || continue
    if ! git rev-parse --verify "refs/heads/$branch" >/dev/null 2>&1; then
      if git worktree remove "$wt" 2>/dev/null; then
        log "stale worktree: $wt (branch '$branch' missing) — removed"
      else
        log "kept worktree $wt — inspect its edits/lock before cleanup"
      fi
    fi
  done
  return 0
}

reclaim_stranded_building() {
  # Run start only (no worker of ours is alive yet — the orphan guard proved it).
  # A card in Building with no live in-flight lock and no assignee but ours was
  # stranded by a run that stopped mid-build. No lane selects from Building, so
  # left alone it sits there forever and the run can never reach "all columns
  # empty". Move it back to Ready and say so; the issue branch is kept, so the
  # next Builder continues on it instead of starting over.
  local issue item_id branch wt wt_branch
  while IFS=$'\t' read -r issue item_id; do
    [ -n "$issue" ] || continue
    issue_locked "$issue" && continue
    branch=$(git ls-remote --heads origin "issue-${issue}-*" 2>/dev/null | head -1 | sed 's#.*refs/heads/##')
    wt=".drover/worktrees/issue-${issue}-build"
    if [ -d "$wt" ]; then
      wt_branch=$(git -C "$wt" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
      if [ -z "$wt_branch" ] || ! git worktree remove "$wt" 2>/dev/null; then
        log "stranded #${issue}: kept $wt and Building status — inspect its detached commits/edits/lock before resuming"
        continue
      fi
    fi
    if ! set_card_status "$item_id" "Ready"; then
      gh_checkpoint
      log "stranded #${issue} in Building — could not move it to Ready; drag it by hand"
      continue
    fi
    [ -n "$BOT_LOGIN" ] && gh_write_once issue edit "$issue" --remove-assignee "$BOT_LOGIN" >/dev/null 2>&1 || true
    # Comment format: writing-standard.md § 4.
    gh_checkpoint
    gh_write_once issue comment "$issue" --body "[orchestrator] [report] moved back to Ready · stranded in Building
Did: found no live worker; the last run stopped mid-build
Done: card moved to Ready · branch ${branch:-none found}${branch:+ kept}
Next: builder (next tick${branch:+, continues on the branch})" >/dev/null 2>&1 || true
    log "stranded #${issue}: Building → Ready (branch ${branch:-none})"
  done <<EOF
$(echo "$PROJECT_ITEMS_JSON" | jq -r --arg bot "$BOT_LOGIN" '
    .items[]
    | select(.status == "Building" and .content.type == "Issue")
    | select(((.content.assignees // []) - [$bot]) | length == 0)
    | [(.content.number | tostring), (.id // "")] | @tsv')
EOF
}

# ───────────────────────── run counters (shared state) ─────────────────────────
DISPATCH_COUNT=0
REAP_COUNT=0
LANDED_COUNT=0
DISPATCH_LOG=""

# Sourced by the gate tests (`SB_LIB_ONLY=1 . lib/drover-run.sh`) to exercise
# the helpers above without starting a run. Executing the script normally is unaffected.
if [ "${SB_LIB_ONLY:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

# A readiness/read failure stops only workers launched by this dispatcher. Keep their
# locks, worktrees, claims, and approval evidence for explicit recovery.
halt_cleanup() {
  local rc=$? handle
  if [ "$rc" -eq 9 ] || [ "$rc" -eq 10 ] || ! python3 "$SB_GITHUB_READ" --check >/dev/null 2>&1; then
    for handle in $MY_HANDLES; do worker_stop "$handle" 1; done
  fi
  # Only our own runner lock: a second runner that refused to start leaves the first's alone.
  [ "${RUNNER_LOCK_OWNED:-0}" = 1 ] && rm -f "$RUNNER_LOCK"
  return 0
}
trap halt_cleanup EXIT
gh_checkpoint

# ───────────────────────────── preconditions ─────────────────────────────
log "drover run started — config=${CONFIG_SLUG} base=${BASE_BRANCH} tick=${TICK_SECONDS}s max_workers=${MAX_WORKERS} (build=${CAP_build} qa=${CAP_qa} review=${CAP_review}) no_progress_cycles=${NO_PROGRESS_CYCLES} max_dispatches=${MAX_DISPATCHES}"

# Leftover-worker guard: a live worker behind any lock (a crashed run's, or another runner's
# on this repo) would collide on claims and produce duplicate PRs.
if worker_lock "$RUNNER_LOCK" && worker_alive "$HANDLE"; then
  log "refusing to start: a drover runner is already running here (${HANDLE}, $RUNNER_LOCK)."
  log "    Stop it first: drover stop"
  exit 73
fi
ORPHANS=0
for issue in $(issue_locks); do
  worker_lock "$INFLIGHT_DIR/$issue" && worker_alive "$HANDLE" && ORPHANS=$((ORPHANS + 1))
done
if [ "$ORPHANS" -gt 0 ]; then
  log "refusing to start: ${ORPHANS} drover workers already running (live locks in $INFLIGHT_DIR)."
  log "    Stop them first: drover stop"
  log "    Then re-run: drover run"
  exit 73
fi
printf 'HANDLE=process:%s\nSTARTED=%s\n' "$$" "$(date -u +%FT%TZ)" > "$RUNNER_LOCK"
RUNNER_LOCK_OWNED=1

# Workflow-backend mutual exclusion (see references/run-workflow.md §Preconditions).
WAVE_LOCK=".claude/drover/inflight/workflow-wave.lock"
if [ -f "$WAVE_LOCK" ]; then
  log "refusing to start: workflow-backend wave in flight ($WAVE_LOCK exists)."
  log "    If no wave is actually running, remove the stale lock: rm $WAVE_LOCK"
  exit 74
fi

# Production-merge guard.
if [ "$BASE_BRANCH" = "main" ] && [ "$HUMAN_APPROVES" = "false" ]; then
  if rg -qU 'on:\s*\n?\s*push:\s*\n?\s*branches:[^a-z]*main' .github/workflows 2>/dev/null \
     || [ -f vercel.json ] || [ -f netlify.toml ]; then
    log "refusing to start: would auto-merge to production main."
    exit 75
  fi
fi

# Existing legacy work is inspected/moved by a human, never deleted on upgrade.
check_legacy_worktrees
cleanup_stale_worktrees

# Reap any leftover stale locks from a previous crashed run.
reap_finished_locks

# ───────────────────────────── main loop ─────────────────────────────
gh_rate_guard
fetch_project_items
reclaim_stranded_building; fetch_project_items
INITIAL_READY=$(column_count "Ready")
log "initial Ready count: $INITIAL_READY"

# Landed-work baseline (issue #8). Progress is a change in this set, not lane state.
PREV_DONE_SIG=$(done_signature)
INITIAL_DONE_SIG="$PREV_DONE_SIG"
NO_PROGRESS_CYCLE_COUNT=0

lane_states() {
  # Sets <LANE>_IDLE (1 when the lane runs no worker) and ACTIVE_WORKERS from the live locks.
  local n
  ACTIVE_WORKERS=0
  n=$(lane_running build);  BUILD_N=$n;  ACTIVE_WORKERS=$((ACTIVE_WORKERS + n))
  n=$(lane_running qa);     QA_N=$n;     ACTIVE_WORKERS=$((ACTIVE_WORKERS + n))
  n=$(lane_running review); REVIEW_N=$n; ACTIVE_WORKERS=$((ACTIVE_WORKERS + n))
}

fill_lane() {
  # Dispatch lane $1 from column $2 until the lane is at its cap or the column has no card left.
  while lane_has_room "$1" && top_card_in_column "$2"; do
    dispatch_lane "$1" "$TOP_ISSUE"
  done
}

while true; do
  TICK=$((TICK + 1)); TICK_SEEN=" "
  python3 "$(dirname "$SB_GITHUB_READ")/drover-verification-ready.py" --config "$CONFIG_PATH"
  gh_checkpoint
  # Workflow-backend mutual exclusion, re-checked every tick: the startup
  # check alone leaves a TOCTOU window where a workflow run starting at the
  # same moment as this dispatcher is never detected by either side.
  if [ -f "$WAVE_LOCK" ]; then
    log "workflow-backend wave appeared mid-run ($WAVE_LOCK) — halting for mutual exclusion."
    log "    Resume after the wave: drover run"
    exit 74
  fi

  reap_finished_locks judge  # cheap local sweep; runs every tick

  # ── Zombie sweep against the LAST cached project state (no extra API).
  #    Catches workers whose card already moved out of the lane's source column
  #    but whose claude -p process didn't exit. Runs every tick, even cheap ones,
  #    so a cap-reached pipeline can still self-heal when one lane is a zombie.
  sweep_lane_zombies

  # ── Free pre-check: count live workers from the locks (no API calls).
  lane_states

  # ── Cheap-tick path: workers at cap → skip GraphQL fetch entirely.
  #    The board can't change in a way that helps us until a lane frees up.
  if [ "$ACTIVE_WORKERS" -ge "$MAX_WORKERS" ]; then
    log "tick — cap reached (${ACTIVE_WORKERS}/${MAX_WORKERS} busy) — skipping GraphQL fetch, sleeping ${TICK_SECONDS}s"
    sleep "$TICK_SECONDS"
    continue
  fi

  # ── Expensive-tick path: we have capacity, fetch real state.
  gh_rate_guard
  fetch_project_items

  # FR-RUN-13: judge the workers that exited, against the fresh board.
  judge_finished_workers || break

  # Re-sweep zombies against fresh cache; the previous sweep used stale data.
  sweep_lane_zombies
  lane_states

  READY=$(column_count "Ready")
  BUILDING=0
  BUILDING=$(column_count "Building")
  QA=$(column_count "QA")
  REVIEW=$(column_count "Review")
  BLOCKED=$(column_count "Blocked")

  log "tick — Ready=$READY Building=$BUILDING QA=$QA Review=$REVIEW Blocked=$BLOCKED workers: build=${BUILD_N}/${CAP_build} qa=${QA_N}/${CAP_qa} review=${REVIEW_N}/${CAP_review}"

  if [ "$READY" -eq 0 ] && [ "$BUILDING" -eq 0 ] && [ "$QA" -eq 0 ] && [ "$REVIEW" -eq 0 ] \
     && [ "$ACTIVE_WORKERS" -eq 0 ]; then
    log "all active-pipeline columns empty and all lanes idle — exiting cleanly"
    break
  fi

  if [ "${BLOCK_ALERT_SENT:-0}" -eq 0 ] && [ "$INITIAL_READY" -gt 0 ] && [ "$BLOCK_ALERT_PCT" -gt 0 ]; then
    PCT=$(( BLOCKED * 100 / INITIAL_READY ))
    if [ "$PCT" -ge "$BLOCK_ALERT_PCT" ]; then
      log "block-rate alert: ${BLOCKED}/${INITIAL_READY} (${PCT}%)"
      BLOCK_ALERT_SENT=1
    fi
  fi

  # Downstream first (FR-RUN-3): Review, then QA, then Ready, each lane up to its cap.
  if [ "$REVIEW" -gt 0 ]; then fill_lane review "Review"; fi
  if [ "$QA" -gt 0 ]; then fill_lane qa "QA"; fi
  # Ready: each card's label picks the lane — `qa` skips Building (Tester), anything
  # else is built first. A card whose lane is full waits for the next tick.
  if [ "$READY" -gt 0 ]; then
    while { lane_has_room build || lane_has_room qa; } && top_card_in_column "Ready"; do
      LANE_FOR=$(card_lane "$TOP_ISSUE")
      if lane_has_room "$LANE_FOR"; then
        dispatch_lane "$LANE_FOR" "$TOP_ISSUE"
      else
        TICK_SEEN="${TICK_SEEN}${TOP_ISSUE} "
      fi
    done
  fi

  gh_checkpoint

  # ── Landed-work halt gate (issue #8).
  #    Progress = the Done set changed since the last dispatch cycle. Lane
  #    occupancy is deliberately NOT part of this condition: the runaway this gate
  #    exists to catch is a pipeline that is 100% busy and lands nothing, and the old
  #    `AND no lane active` clause made that case unhaltable (293 ticks, 0 merges).
  if ! record_cycle_progress "$(done_signature)"; then
    log "halt — RUNAWAY: no card reached Done in ${NO_PROGRESS_CYCLES} dispatch cycles"
    runaway_summary
    break
  fi

  # ── Hard dispatch ceiling. Bounds worst-case spend even if the board keeps
  #    trickling just enough Done movement to reset the gate above.
  if dispatch_ceiling_hit; then
    log "halt — dispatch ceiling reached (${DISPATCH_COUNT}/${MAX_DISPATCHES})"
    runaway_summary
    break
  fi

  sleep "$TICK_SECONDS"
done

log "drover run finished. manifest: $RUN_MANIFEST"
