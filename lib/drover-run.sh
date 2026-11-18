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
#  11. Dependencies (FR-BRD-5/7/8) — each fresh board read with Ready or Blocked cards also reads
#      the dependency graph (drover-deps.sh). A Ready card is claimed only when its blockers are
#      all closed and its `## Blocked by` line parses; a Blocked card whose blockers closed goes
#      back to Ready; an approved [needs-you] card (done on the current PR head) goes to Review.
#  12. Pre-flight and classifier (FR-RUN-5/7/15) — before a build claim, one short agent judges a
#      batch of up to 5 Ready cards; held and sequenced cards go to Blocked. Before any Ready claim,
#      a fast agent picks type, complexity (the model tier) and missing risk labels.
#  13. Usage pause (FR-RUN-8) — the harness's usage probe runs before each dispatch; at or above
#      usage_pause_pct no new worker starts until usage drops.
#  14. FactoryBench (metrics.md FR-MET-4..12) — attempt events through lib/drover-metrics.py, a
#      no-op without `factorybench` on PATH and FACTORYBENCH_URL.
#
# Workers per role: roles.<role>.max_workers (default 1, FR-RUN-2), counted from the live locks.
#
# Exit codes: 0 finished or a runaway/ceiling halt · 9/10 verification or skills readiness ·
#   64 usage, no config or no GitHub repo · 65 old config · 66 no worker command · 73 leftover
#   runner, worker or legacy worktree · 74 workflow wave in flight · 75 would auto-merge to production main ·
#   76 failed-dispatch halt (FR-RUN-13; other workers keep running) · 79 GitHub read halt ·
#   143 stopped by TERM/INT.

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
HARNESS_JSON=$(python3 "$DROVER_LIB/drover-config.py" harness "$HARNESS") || exit $?
# FR-RUN-8: the harness's usage probe ("" = none); a leading `drover` is this install's.
USAGE_PROBE=$(printf '%s' "$HARNESS_JSON" | jq -r '.usage_probe // ""')
USAGE_PCT=$(jq -r '.usage_pause_pct // 95' "$CONFIG_PATH")

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
MODEL_TIER=$(jq -r '.model_tier // "medium"' "$CONFIG_PATH")
AUTO_MAX_LINES=$(jq -r '.merge_policy.auto_max_lines // 400' "$CONFIG_PATH")
COMMIT_EMAIL=$(jq -r '.metrics.commit_email // ""' "$CONFIG_PATH")
# The GitHub repo (owner/name) the dependency graph and pre-flight read: repo.remote, else origin.
REPO_URL=$(jq -r '.repo.remote // ""' "$CONFIG_PATH")
[ -n "$REPO_URL" ] || REPO_URL=$(git remote get-url origin 2>/dev/null || true)
REPO_SLUG=$(printf '%s' "$REPO_URL" | sed -nE 's#^.*github\.com[:/]+([^/]+/[^/]+)$#\1#p' | sed 's#\.git$##')

python3 "$(dirname "$SB_GITHUB_READ")/drover-verification-ready.py" --config "$CONFIG_PATH"
if [ -z "$REPO_SLUG" ]; then
  echo "no GitHub repo: set repo.remote in $CONFIG_PATH or add a github.com origin remote" >&2
  exit 64
fi

RUN_DATE=$(date +%Y-%m-%d)
# The manifest and worker logs live in the git-excluded .drover/runs/ (FR-MET-3).
RUN_MANIFEST=".drover/runs/${RUN_DATE}-${CONFIG_SLUG}.md"
# Per-worker logs (issue #13). Workers used to be spawned with >/dev/null, so the run
# log could never show what a worker was doing during a 13-minute silence.
WORKER_LOG_DIR=".drover/runs/${RUN_DATE}-${CONFIG_SLUG}-workers"
INFLIGHT_DIR=".claude/drover/inflight"
RUNS_DIR=".drover/runs"
RUNNER_LOCK=".claude/drover/runner.lock"
mkdir -p "$WORKER_LOG_DIR" .drover/worktrees "$INFLIGHT_DIR"
fi  # end non-lib-only setup

# shellcheck source=drover-worker.sh
. "$DROVER_LIB/drover-worker.sh"

# Defaults so the helpers below are safe to source under `set -u` in lib-only mode.
: "${RUN_MANIFEST:=/dev/null}" ; : "${RUNS_DIR:=/dev/null}"
: "${WORKER_LOG_DIR:=/dev/null}"
: "${INFLIGHT_DIR:=/dev/null}"
: "${PROJECT_OWNER:=}" ; : "${PROJECT_NUMBER:=0}" ; : "${BOT_LOGIN:=}"
: "${CAP_build:=1}" ; : "${CAP_qa:=1}" ; : "${CAP_review:=1}" ; : "${BASE_BRANCH:=main}"
# Issues already looked at this tick (dispatched, skipped, claim lost): never picked twice.
TICK_SEEN=" "
MY_HANDLES=" "   # every worker this runner started: the only ones a halt stops (FR-WRK-8)
: "${REPO_SLUG:=}" ; : "${MODEL_TIER:=medium}" ; : "${COMMIT_EMAIL:=}" ; : "${USAGE_PROBE:=}"
: "${USAGE_PCT:=95}" ; : "${AUTO_MAX_LINES:=400}" ; : "${HARNESS:=claude}"
WORKER_ENV=()
DEPS_JSON="{}"     # the dependency graph of this tick's board read (drover-deps.sh)
FB_ON=0            # 1 when FactoryBench is configured (drover-metrics.py enabled)

# ───────────────────────────── helpers ─────────────────────────────
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$RUN_MANIFEST"; }
# The runner's own output only: the manifest holds FR-MET-1/2 and FR-RUN-16 line types.
say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
metrics() { [ "$FB_ON" = 1 ] || return 0; python3 "$DROVER_LIB/drover-metrics.py" "$@" || true; }

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

# A closed issue's reason and its comments not written by drover: drover's comments open
# with a `[<role>] ` header (writing-standard.md section 4).
CLOSED_INFO_JQ='{reason: (.stateReason // ""), comments: ([.comments[] | select(.body | test("^\\[(builder|qa|reviewer|orchestrator|collect)\\] ") | not)] | length)}'
CLOSED_TARGETS=" "   # "<issue>:<Done|Blocked>" decided this run, so a failed move re-reads nothing
CLOSED_MOVED=" "     # closed issues whose card this run already moved: never moved or commented again
closed_card_column() {
  # $1 = a CLOSED issue. Prints where its card belongs (FR-BRD-3): Done when a PR from its
  # issue-<N>-* branch is merged into the base, or it was closed as not planned with a comment
  # from a person (drover's own role, orchestrator and stop comments do not count);
  # Blocked otherwise. Exit 79 when GitHub cannot be read. "Dropped" = Done by a not-planned close.
  local info heads
  info=$(python3 "$SB_GITHUB_READ" --kind object -- issue view "$1" --json stateReason,comments \
    --jq "$CLOSED_INFO_JQ") || return 79
  if [ "$(echo "$info" | jq -r '.reason == "NOT_PLANNED" and .comments > 0')" = true ]; then
    echo Dropped; return 0
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
    case "$TICK_SEEN$CLOSED_MOVED" in *" $issue "*) continue ;; esac
    issue_locked "$issue" && continue
    local issue_rc=0 target=""
    case "$CLOSED_TARGETS" in *" $issue:"*) target="${CLOSED_TARGETS#* $issue:}"; target="${target%% *}"; issue_rc=1 ;; esac
    [ -n "$target" ] || issue_is_open "$issue" || issue_rc=$?
    [ "$issue_rc" -le 1 ] || exit "$issue_rc"
    if [ "$issue_rc" -ne 0 ]; then
      if [ -z "$target" ]; then
        target=$(closed_card_column "$issue") || exit $?
        CLOSED_TARGETS="${CLOSED_TARGETS}${issue}:${target} "
      fi
      if [ "$target" = Done ] || [ "$target" = Dropped ]; then
        log "reconciled closed #${issue} -> Done (was '${col}') — dispatching 0 workers for it"
        set_card_status "$item_id" "Done" || { gh_checkpoint; log "  ↳ reconcile of #${issue} could not be written to the board"; return 1; }
        [ "$target" = Done ] || metrics finish "$issue" failed "closed not planned"
      else
        block_closed_unmerged "$issue" "$item_id" "$col" || return 1
      fi
      gh_checkpoint
      [ -n "$BOT_LOGIN" ] && gh_write_once issue edit "$issue" --remove-assignee "$BOT_LOGIN" >/dev/null 2>&1 || true
      # The board cache can still show the card here (a second scan of this column in the
      # same tick once a lane frees up, or a stale read later): it is moved once per run.
      CLOSED_MOVED="${CLOSED_MOVED}${issue} "
      continue
    fi
    if [ "$col" = Ready ] && ! card_free "$issue"; then
      TICK_SEEN="${TICK_SEEN}${issue} "
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
  # FactoryBench (FR-MET-5/7/11): the card's attempt, its OTLP variables, the commit email.
  WORKER_ENV=()
  if [ "$FB_ON" = 1 ]; then
    metrics claim "$issue" "$REPO_SLUG" "$(printf '%s' "$card" | jq -r '[.[0].labels[] | (if type == "object" then .name else . end)] | join(",")')" \
      "$(git rev-parse --verify -q "$BASE_BRANCH" 2>/dev/null || true)" "$FB_VERSION" >/dev/null
    local kv
    while IFS= read -r kv; do [ -n "$kv" ] && WORKER_ENV+=("$kv"); done <<EOF
$(metrics env "$issue" "$lane")
EOF
  fi
  [ -z "$COMMIT_EMAIL" ] || WORKER_ENV+=("GIT_AUTHOR_EMAIL=$COMMIT_EMAIL" "GIT_COMMITTER_EMAIL=$COMMIT_EMAIL")
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
  local started column
  started=$(date -u +%FT%TZ); column=$(issue_status "$issue")
  # A TERM or INT between the worker start and its lock would leave an untracked worker:
  # hold the signal until the lock is written, then act on it.
  local held=""
  trap 'held=TERM' TERM; trap 'held=INT' INT
  # The harness's worker command with the role's model and the headless permissions
  # (FR-HRN-4): a worker never waits on an approval prompt.
  worker_start "$lane" "$issue" "$prompt" "$worker_log" "$(card_model "$issue" "$lane")" || { trap - TERM INT; log "worker command: no argv from harness ${HARNESS:-claude}"; exit 66; }
  WORKER_ENV=()
  # Lock format: HANDLE / LANE / STARTED for `drover stop`, COLUMN (the card's column at
  # dispatch) for the failed-dispatch check. Readers still accept the older PID= line.
  write_lock "$INFLIGHT_DIR/$issue" "$(printf 'HANDLE=%s\nLANE=%s\nSTARTED=%s\nCOLUMN=%s' \
    "$WORKER_HANDLE" "$lane" "$started" "$column")
"
  MY_HANDLES="${MY_HANDLES}${WORKER_HANDLE} "
  trap - TERM INT
  [ -z "$held" ] || exit 143
  DISPATCH_COUNT=$((DISPATCH_COUNT + 1))
  DISPATCH_LOG="${DISPATCH_LOG}${issue}
"
  # pid= stays a bare number (metrics.md FR-MET-2): the process backend's id.
  log "dispatch lane=${lane} issue=#${issue} pid=$(worker_pid "$WORKER_HANDLE" || echo 0) claim=${BOT_LOGIN:-local-only}"
}

forget_handle() {
  # A reaped or stopped worker leaves the set a halt stops.
  MY_HANDLES="${MY_HANDLES/ $1 / }"
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
  # then it is stopped and its lane freed. Runs only right after a fresh board read, so both
  # the mark and the stop rest on fresh data; a mark survives the cap-reached ticks between.
  # A card back in its lane drops its mark.
  local issue cur seen pid marks=" "
  for issue in $(issue_locks); do
    worker_lock "$INFLIGHT_DIR/$issue" || continue
    worker_alive "$HANDLE" || continue     # already dead → reap_finished_locks handles it
    cur=$(issue_status "$issue")
    [ -n "$cur" ] || continue               # not in cache (closed/deleted/race) → don't stop
    case " $(lane_columns "$LANE") ${COLUMN} " in *" $cur "*) continue ;; esac
    seen=""
    case "$LEFT_LANE" in *" $issue:"*) seen="${LEFT_LANE#* $issue:}"; seen="${seen%% *}" ;; esac
    pid=$(worker_pid "$HANDLE" || echo 0)
    if [ -z "$seen" ]; then
      # Not "card moved to": FR-MET-1 readers take that phrase as the worker's outcome.
      log "${LANE} worker on #${issue} (pid=${pid}) left its lane (now '${cur}'); one more tick to finish its handoff"
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
    forget_handle "$HANDLE"
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

JUDGE_QUEUE=""   # "<issue>|<lane>|<column at dispatch>|<started>" per worker that exited this run
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
    pid=$(worker_pid "$HANDLE" || true)
    gh_checkpoint
    if [ -n "$BOT_LOGIN" ]; then
      gh_checkpoint
      gh_write_once issue edit "$issue" --remove-assignee "$BOT_LOGIN" >/dev/null 2>&1 || true
      log "reaped stale lock + swept assignee on #${issue} (pid=${pid:-empty})"
    else
      log "reaped stale lock for #${issue} (pid=${pid:-empty})"
    fi
    [ "${1:-}" = judge ] && [ -n "$COLUMN" ] && JUDGE_QUEUE="${JUDGE_QUEUE} ${issue}|${LANE}|${COLUMN}|${STARTED}"
    gh_checkpoint
    rm -f "$INFLIGHT_DIR/$issue"
    forget_handle "$HANDLE"
    REAP_COUNT=$((REAP_COUNT + 1))
  done
}

role_reported() {
  # $1 = issue, $2 = lane, $3 = the worker's STARTED time. 0 when the lane's role posted a
  # `[<role>] [report]` or `[<role>] [blocker]` comment on the issue since then (FR-RUN-13).
  local role pages
  case "$2" in build) role=builder ;; review) role=reviewer ;; *) role="$2" ;; esac
  pages=$(python3 "$SB_GITHUB_READ" --kind comments -- api "repos/{owner}/{repo}/issues/$1/comments" \
    --paginate --slurp) || exit 79
  [ "$(printf '%s' "$pages" | jq --arg s "$3" --arg re "^\\[${role}\\] \\[(report|blocker)\\]" \
    '[.[][] | select(.created_at >= $s and (.body | test($re)))] | length')" -gt 0 ]
}

judge_finished_workers() {
  # FR-RUN-13, on a fresh board read: a worker that exited with its card still in the
  # column it was dispatched from, or in Building (no lane picks a card up there; the next
  # reclaim moves it back to Ready), and that posted no report of its role since it started,
  # is a failed dispatch (the board decides, FR-WRK-6; the report read is one GitHub call per
  # such worker). Returns 1 (halt) after two failed dispatches in a row on one card, with
  # the tail of its worker log.
  local entry issue lane col started cur n worker_log
  for entry in $JUDGE_QUEUE; do
    IFS='|' read -r issue lane col started <<EOF
$entry
EOF
    n=0
    case "$FAILS" in *" $issue:"*) n="${FAILS#* $issue:}"; n="${n%% *}" ;; esac
    FAILS=$(printf '%s' "$FAILS" | sed "s/ $issue:[0-9]* / /")
    cur=$(issue_status "$issue")
    fb_outcome "$issue" "$lane" "$cur"
    [ -n "$cur" ] && { [ "$cur" = "$col" ] || [ "$cur" = Building ]; } || continue
    role_reported "$issue" "$lane" "$started" && continue
    n=$((n + 1))
    FAILS="${FAILS}${issue}:${n} "
    log "failed dispatch lane=${lane} issue=#${issue} — worker exited, card in '${cur}', no report (${n} in a row)"
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
  # Card worktrees only: .claude/worktrees/ also holds unrelated agent worktrees.
  local wt
  for wt in .worktrees/issue-* .claude/worktrees/issue-*; do
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
  # At run start and after every fresh board read. A card in Building with no in-flight
  # lock and no assignee but ours was stranded by a run that stopped mid-build, or by a
  # builder that exited mid-build (FR-RUN-13 counts that). No lane selects from Building, so
  # left alone it sits there forever and the run can never reach "all columns
  # empty". Move it back to Ready and say so; the issue branch is kept, so the
  # next Builder continues on it instead of starting over.
  local issue item_id branch wt wt_branch
  while IFS=$'\t' read -r issue item_id; do
    [ -n "$issue" ] || continue
    issue_locked "$issue" && continue
    # No origin (or no network) is "no branch found", not a halt.
    branch=$(git ls-remote --heads origin "issue-${issue}-*" 2>/dev/null | head -1 | sed 's#.*refs/heads/##' || true)
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
Did: found no live worker; the last builder stopped mid-build
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

# ───────────────────────── dependencies (FR-BRD-5, 7, 8) ─────────────────────────
FLAGGED=" "   # Ready cards whose `## Blocked by` line could not be read, commented on once per run
refresh_deps() {
  # The dependency graph for this board read: one GraphQL read of open issues (plus the
  # approval reads for [needs-you] cards), only while Ready or Blocked holds a card.
  DEPS_JSON="{}"
  if [ "$(echo "$PROJECT_ITEMS_JSON" | jq '[.items[] | select(.status == "Ready" or .status == "Blocked")] | length')" -gt 0 ]; then
    gh_checkpoint
    DEPS_JSON=$(bash "$DROVER_LIB/drover-deps.sh" --repo "$REPO_SLUG") || exit $?
  fi
  printf '%s' "$DEPS_JSON" > "$RUNS_DIR/deps.json"
}

card_free() {
  # 0 when Ready card $1 may be claimed (FR-BRD-7): its blockers are closed, the line parses and
  # no person must act. Unknown to the graph is not free. An unreadable line is flagged once.
  local d
  d=$(echo "$DEPS_JSON" | jq -c --arg n "$1" '.[$n] // null')
  [ "$(echo "$d" | jq -r '.runnable // false')" = true ] && return 0
  if [ "$(echo "$d" | jq -r 'if . == null then true else .parseable end')" = false ]; then
    case "$FLAGGED" in *" $1 "*) return 1 ;; esac
    FLAGGED="${FLAGGED}$1 "
    gh_checkpoint
    gh_write_once issue comment "$1" --body "[orchestrator] [report] not claimed · dependency line unreadable
Did: read the card's dependencies before claiming it
Done: nothing; the card stays in Ready
Not done: $(echo "$d" | jq -r .why)
Next: owner writes \`- #N\` bullets or \`- None.\` under \`## Blocked by\`" >/dev/null 2>&1 || true
    say "not claimed #$1: dependency line unreadable"
  fi
  return 1
}

cache_set_status() {
  # Card $1 moved to column $2 by this runner: the cached board read says so too.
  PROJECT_ITEMS_JSON=$(echo "$PROJECT_ITEMS_JSON" | jq -c --argjson n "$1" --arg s "$2" \
    '.items |= map(if .content.number == $n then .status = $s else . end)')
}

sweep_blocked() {
  # FR-BRD-5 and FR-BRD-8 over the Blocked column. A card whose blockers all closed goes back to
  # Ready; an owner-approved [needs-you] merge goes to Review; one whose PR head moved while
  # Blocked goes to Review for a fresh request, with no approval. Moved cards wait for the next tick.
  local kind issue item extra
  while IFS=$'\t' read -r kind issue item extra; do
    [ -n "$kind" ] || continue
    case "$kind" in
      sweep)
        set_card_status "$item" Ready || continue
        gh_checkpoint
        gh_write_once issue comment "$issue" --body "[orchestrator] [report] moved back to Ready · blocker closed
Did: found every issue in blocked-by closed (#${extra})
Done: card moved to Ready
Next: builder (next tick)" >/dev/null 2>&1 || true
        log "unblocked #${issue}: blocker #${extra} closed, back to Ready"
        cache_set_status "$issue" Ready ;;
      resume)
        set_card_status "$item" Review || continue
        log "approval resume #${issue}: owner approved head ${extra:0:7}, back to Review"
        cache_set_status "$issue" Review ;;
      refresh)
        set_card_status "$item" Review || continue
        gh_checkpoint
        gh_write_once issue comment "$issue" --body "[orchestrator] [report] moved back to Review · PR head changed while Blocked
Did: found the approval request pinned to an older PR head
Done: card moved to Review; the old approval does not cover the new head
Next: reviewer reviews the new head and posts a fresh approval request" >/dev/null 2>&1 || true
        say "approval refresh #${issue}: PR head changed while Blocked, back to Review for a fresh request"
        cache_set_status "$issue" Review ;;
    esac
    TICK_SEEN="${TICK_SEEN}${issue} "
  done <<EOF
$(echo "$PROJECT_ITEMS_JSON" | jq -r --argjson deps "$DEPS_JSON" '
    .items[] | select(.status == "Blocked" and .content.type == "Issue")
    | (.content.number | tostring) as $n | $deps[$n] as $d | select($d != null)
    | if ($d.needsYouDone // false) then ["resume", $n, .id, ($d.approvalHead // "")]
      elif ($d.approvalRefresh // false) then ["refresh", $n, .id, "-"]
      elif $d.runnable and (($d.blockers // []) | length) > 0 then ["sweep", $n, .id, ($d.blockers | map(tostring) | join(", #"))]
      else empty end | @tsv')
EOF
}

# ───────────────────────── short agents: pre-flight, classifier ─────────────────────────
AGENT_TIMEOUT="${DROVER_AGENT_TIMEOUT:-600}"
AGENT_JSON_PY='import json, sys
key, text, found = sys.argv[1], sys.stdin.read(), ""
for line in text.splitlines():
    line = line.strip()
    if line.startswith("{"):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and key in value:
            found = line
if not found and "{" in text:
    try:
        value = json.loads(text[text.index("{"):text.rindex("}") + 1])
        found = json.dumps(value) if isinstance(value, dict) and key in value else ""
    except ValueError:
        pass
print(found)'

run_agent() {
  # $1 = role (preflight|classify), $2 = log name, $3 = prompt, $4 = model, $5 = the JSON key its
  # answer carries. Runs one short headless worker through the worker interface, waits for it and
  # sets AGENT_OUT to the last JSON line holding $5 ("" when it gave none).
  local role="$1" log="${WORKER_LOG_DIR}/$1-$2.log" mark
  printf '=== %s %s at %s ===\n' "$role" "$2" "$(date -u +%FT%TZ)" >> "$log"
  mark=$(wc -c < "$log" | tr -d ' ')
  WORKER_ENV=()
  worker_start "$role" 0 "$3" "$log" "$4" || { log "worker command: no argv from harness ${HARNESS}"; exit 66; }
  MY_HANDLES="${MY_HANDLES}${WORKER_HANDLE} "
  worker_wait "$WORKER_HANDLE" "$AGENT_TIMEOUT"
  forget_handle "$WORKER_HANDLE"
  AGENT_OUT=$(tail -c "+$((mark + 1))" "$log" | python3 -c "$AGENT_JSON_PY" "$5")
}

PF_CLEARED=" "   # Ready cards the latest pre-flight batch cleared, not yet dispatched
preflight_ok() {
  # FR-RUN-5: 0 when the latest pre-flight batch cleared Ready card $1 for a builder. Otherwise
  # one agent judges $1 and up to 4 more free Ready build cards; held and sequenced cards go to
  # Blocked, a card with no verdict is not built this tick. The batch's builders may run side
  # by side (FR-RUN-2).
  local first="$1" batch n inflight prompt v verdict tag detail by summary="" reason
  case "$PF_CLEARED" in *" $first "*) return 0 ;; esac
  PF_CLEARED=" "
  batch="$first"
  for n in $(echo "$PROJECT_ITEMS_JSON" | jq -r --argjson f "$first" '
      .items[] | select(.status == "Ready" and .content.type == "Issue" and .content.number != $f)
      | select((.content.assignees // []) | length == 0) | .content.number'); do
    [ "$(echo "$batch" | tr ',' '\n' | grep -c .)" -lt 5 ] || break
    case "$TICK_SEEN$CLOSED_MOVED" in *" $n "*) continue ;; esac
    [ -f "$INFLIGHT_DIR/$n" ] && continue
    [ "$(card_lane "$n")" = build ] || continue
    [ "$(echo "$DEPS_JSON" | jq -r --arg n "$n" '.[$n].runnable // false')" = true ] || continue
    local rc=0; issue_is_open "$n" || rc=$?
    [ "$rc" -le 1 ] || exit "$rc"
    [ "$rc" -eq 0 ] && batch="$batch,$n"
  done
  inflight="$RUNS_DIR/preflight-inflight.json"
  echo "$PROJECT_ITEMS_JSON" | jq '[.items[] | select(.content.type == "Issue")
    | select(.status as $s | ["Ready","Building","QA","Review"] | index($s))
    | {number: .content.number, title: (.content.title // ""), status}]' > "$inflight"
  prompt="Builder pre-flight for issues $(echo "$batch" | sed 's/^/#/; s/,/, #/g') for drover run. Config: ${CONFIG_PATH}.
Read ${DROVER_HOME}/skills/drover/references/run.md → \"Builder pre-flight\" and judge each card as it says. You write no code and make no GitHub writes: drover moves held and sequenced cards and posts their Block comments.
Run once for the whole batch: drover preflight --repo ${REPO_SLUG} --issues ${batch} --inflight ${PWD}/${inflight}. Then judge its candidates semantically, and add too-big: likely over ${AUTO_MAX_LINES} changed lines, or spans many areas.
Two Ready cards overlapping each other with no open PR: the lower-numbered one proceeds; sequence only the other, blockedBy [the lower]. Never both.
Required GitHub reads use drover github-read; exit 79 means verdict halted for every card.
End your reply with one line of JSON and nothing after it, one verdict per issue, never dropping one:
{\"verdicts\":[{\"number\":N,\"verdict\":\"proceed|hold|sequence|halted\",\"tag\":\"done|in-progress|unclear|too-big|overlap|clean\",\"blockedBy\":[M],\"detail\":\"one line\"}]}"
  run_agent preflight "${batch%%,*}" "$prompt" "$([ "$MODEL_TIER" = low ] && echo haiku || echo sonnet)" verdicts
  for n in ${batch//,/ }; do
    v=$(printf '%s' "${AGENT_OUT:-"{}"}" | jq -c --argjson n "$n" '[(.verdicts // [])[] | select(.number == $n)][0] // {}' 2>/dev/null || echo '{}')
    verdict=$(echo "$v" | jq -r '.verdict // "skipped"')
    tag=$(echo "$v" | jq -r '.tag // ""')
    detail=$(echo "$v" | jq -r '.detail // "" | gsub("[\n\t]"; " ")')
    by=$(echo "$v" | jq -r '(.blockedBy // []) | map(tostring) | join(", ")')
    reason=""
    case "$verdict" in
      halted)
        log "preflight batch $(echo "$batch" | sed 's/^/#/; s/,/ #/g'): halted #${n} (${detail:-required GitHub read failed})"
        exit 79 ;;
      proceed) PF_CLEARED="${PF_CLEARED}${n} " ;;
      sequence)
        if [ -z "$by" ]; then
          # Nothing to wait on: it builds, and the merge gate rebases (run.md → Builder pre-flight).
          verdict=proceed; PF_CLEARED="${PF_CLEARED}${n} "
          gh_checkpoint
          gh_write_once issue comment "$n" --body "[orchestrator] [report] pre-flight · expected conflict
Did: pre-flight found an overlap with nothing to wait on: ${detail}
Done: card stays in Ready and builds
Next: builder; the merge gate rebases" >/dev/null 2>&1 || true
        else
          reason="$detail"; block_preflight "$n" "[blocked:sequenced]" "$detail" "$by"
        fi ;;
      hold)
        reason="$detail"
        case "$tag" in
          done|in-progress) block_preflight "$n" "[blocked:duplicate]" "$detail" "${by:--}" ;;
          too-big) block_preflight "$n" "[blocked:too-big]" "$detail" - ;;
          *) block_preflight "$n" "[blocked:unclear]" "$detail" - ;;
        esac ;;
      *) verdict=skipped; reason="no verdict"; TICK_SEEN="${TICK_SEEN}${n} " ;;
    esac
    summary="${summary}${summary:+, }${verdict} #${n}${reason:+ (${reason})}"
  done
  log "preflight batch $(echo "$batch" | sed 's/^/#/; s/,/ #/g'): ${summary}"
  case "$PF_CLEARED" in *" $first "*) return 0 ;; esac
  return 1
}

block_preflight() {
  # $1 = issue, $2 = reason tag, $3 = the pre-flight's one line, $4 = blocked-by. Comment:
  # block-template.md. The card waits in Blocked; the sweep frees a sequenced one.
  local issue="$1" tag="$2" why="$3" by="$4" item
  TICK_SEEN="${TICK_SEEN}${issue} "
  item=$(echo "$PROJECT_ITEMS_JSON" | jq -r --argjson n "$issue" '[.items[] | select(.content.number == $n) | .id][0] // ""')
  set_card_status "$item" Blocked || return 0
  cache_set_status "$issue" Blocked
  gh_checkpoint
  gh_write_once issue comment "$issue" --body "[orchestrator] [blocker] blocked ${tag} ${why}
Card:        #${issue}
Reason tag:  ${tag}
Why blocked: pre-flight: ${why}
Evidence:    drover preflight --repo ${REPO_SLUG} --issues ${issue}, judged by the pre-flight agent
Checked:     merged and open PRs, closed issues, cards in flight, acceptance criteria
What blocks: $(jq -r --arg t "$tag" '.tags[$t] // "-"' "$DROVER_LIB/drover-reasons.json")
Why I (bot) cannot decide:
             building now would duplicate or collide with other work, or guess at the scope
To unblock:  [ ] resolve what the reason names
             [ ] drag the card to Ready
Owner:       $([ "$by" = - ] && echo owner || echo "nobody: the sweep moves it back when #${by} closes")
Move back:   drag this card to Ready after the steps above are done
blocked-by:  ${by}" >/dev/null 2>&1 || true
}

CLASSES=" "   # "<issue>:<complexity>" for each Ready card classified this run
classify_card() {
  # FR-RUN-7 / FR-RUN-15, before a Ready card is claimed: a fast agent picks the type when the
  # card has none, the complexity (the model tier) and the risk labels it needs. Labels are only
  # added, never removed, and never `qa`; one comment says why when one was added.
  local issue="$1" labels typed out kind complexity why add="" l
  case "$CLASSES" in *" $issue:"*) return 0 ;; esac
  labels=$(echo "$PROJECT_ITEMS_JSON" | jq -r --argjson n "$issue" '[.items[] | select(.content.number == $n)
    | ((.labels // []) + (.content.labels // []))[] | (if type == "object" then .name else . end) | ascii_downcase] | unique | join(" ")')
  typed=""
  for l in qa bug feature; do case " $labels " in *" $l "*) typed="$l"; break ;; esac; done
  run_agent classify "$issue" "Classify GitHub issue #${issue} for drover run. Config: ${CONFIG_PATH}.
Read its body and comments: drover github-read --kind issue -- issue view ${issue} --json number,title,body, and drover github-read --kind comments -- api repos/{owner}/{repo}/issues/${issue}/comments --paginate --slurp. You write no code and make no GitHub writes.
kind: $([ -n "$typed" ] && echo "its label says \"${typed}\": return kind \"${typed}\"; the label routes the card." || echo "it has no type label: return feature or bug, never qa.")
complexity: low, medium or high, by the scope of the change.
risk: each of security, migration, payments, auth the change touches (security: secrets, input handling, access control; migration: database schema or data migration; payments: money, billing; auth: login, sessions, permissions). Empty when none.
why: one line naming what decides the kind, complexity and risk.
End your reply with one line of JSON and nothing after it:
{\"kind\":\"feature|bug|qa\",\"complexity\":\"low|medium|high\",\"risk\":[],\"why\":\"one line\"}" \
    "$([ "$MODEL_TIER" = high ] && echo sonnet || echo haiku)" complexity
  out="${AGENT_OUT:-"{}"}"
  kind=$(echo "$out" | jq -r '.kind // ""' 2>/dev/null || true)
  complexity=$(echo "$out" | jq -r '.complexity // "" | select(. == "low" or . == "medium" or . == "high")' 2>/dev/null || true)
  why=$(echo "$out" | jq -r '.why // "" | gsub("[\n\t]"; " ")' 2>/dev/null || true)
  CLASSES="${CLASSES}${issue}:${complexity:-none} "
  [ -n "$typed" ] || case "$kind" in feature|bug) add="$kind" ;; esac
  for l in $(echo "$out" | jq -r '(.risk // [])[] | strings | ascii_downcase' 2>/dev/null || true); do
    case "$l" in security|migration|payments|auth) ;; *) continue ;; esac
    case " $labels $add " in *" $l "*) continue ;; esac
    add="${add:+$add }$l"
  done
  [ -n "$add" ] || return 0
  gh_checkpoint
  # A label the repo lacks fails the edit: no comment then, and the card is still claimed.
  gh issue edit "$issue" --add-label "${add// /,}" >/dev/null 2>&1 || { say "classify #${issue}: could not add label ${add// /, }"; return 0; }
  gh_checkpoint
  gh_write_once issue comment "$issue" --body "[orchestrator] [classify] added label ${add// /, }
Did: classified as ${kind:-${typed:-unknown}}, complexity ${complexity:-unknown}
Why: ${why:-no reason given}" >/dev/null 2>&1 || true
  PROJECT_ITEMS_JSON=$(echo "$PROJECT_ITEMS_JSON" | jq -c --argjson n "$issue" --arg add "$add" \
    '.items |= map(if .content.number == $n then .labels = ((.labels // []) + ($add | split(" "))) else . end)')
}

card_model() {
  # The model for lane $2 on card $1: the run tier's ladder cell for the card's complexity
  # (build and qa lanes only; review and unclassified cards take the harness default). A role's
  # configured model wins inside drover-config.py.
  local c=""
  case "$2" in build|qa) ;; *) return 0 ;; esac
  case "$CLASSES" in *" $1:"*) c="${CLASSES#* $1:}"; c="${c%% *}" ;; esac
  case "${MODEL_TIER}:${c}" in
    low:low) echo haiku ;; low:medium|medium:low) echo sonnet ;;
    low:high|medium:medium|high:*) echo opus ;;
  esac
}

# ───────────────────────── usage pause (FR-RUN-8) ─────────────────────────
USAGE_PAUSED=0
USAGE_SAID=0
usage_ok() {
  # 0 when a new worker may start. While the probe reports usage at or above usage_pause_pct,
  # 1; running workers finish. No probe, or a probe with no reading: said once, never a pause.
  local out rc=0 used
  local -a probe=()
  if [ -z "$USAGE_PROBE" ]; then
    [ "$USAGE_SAID" = 1 ] || say "usage: harness ${HARNESS} has no usage probe; running without a usage pause"
    USAGE_SAID=1; return 0
  fi
  read -r -a probe <<< "$USAGE_PROBE"
  [ "${probe[0]}" != drover ] || probe[0]="$DROVER_HOME/bin/drover"
  out=$("${probe[@]}" --threshold "$USAGE_PCT" 2>/dev/null) || rc=$?
  used=$(printf '%s' "$out" | jq -r '.used // 0' 2>/dev/null || echo 0)
  if [ "$rc" -eq 10 ]; then
    [ "$USAGE_PAUSED" = 1 ] || log "usage pause: ${used}% >= ${USAGE_PCT}%, no new workers"
    USAGE_PAUSED=1; return 1
  fi
  if [ "$rc" -ne 0 ] && [ "$USAGE_SAID" = 0 ]; then
    say "usage: no reading from the usage probe ($(printf '%s' "$out" | jq -r '.reason // "no output"' 2>/dev/null || echo 'no output')); running without a usage pause"
    USAGE_SAID=1
  fi
  [ "$USAGE_PAUSED" = 0 ] || log "usage resume: ${used}%"
  USAGE_PAUSED=0
  return 0
}

# ───────────────────────── FactoryBench outcomes (FR-MET-5) ─────────────────────────
fb_outcome() {
  # $1 = issue, $2 = the lane of a worker that exited, $3 = its card's column now.
  [ "$FB_ON" = 1 ] || return 0
  local url
  case "$2:$3" in
    build:QA)
      url=$(python3 "$SB_GITHUB_READ" --kind array -- pr list --state open --json headRefName,url --limit 100 2>/dev/null \
        | jq -r --arg p "issue-$1-" '[.[] | select(.headRefName | startswith($p)) | .url][0] // ""' 2>/dev/null || true)
      [ -z "$url" ] || metrics pr "$1" "$url" ;;
    qa:Review) metrics gate "$1" qa true "QA passed" ;;
    qa:Ready|qa:Building) metrics gate "$1" qa false "bounced to $3" ;;
    review:Done) metrics gate "$1" review true "review passed"; metrics gate "$1" merge true "merged" ;;
    review:Ready|review:Building|review:QA) metrics gate "$1" review false "bounced to $3" ;;
    review:Blocked) metrics gate "$1" merge false "card moved to Blocked" ;;
  esac
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
  worker_lock "$INFLIGHT_DIR/$issue" && worker_alive "$HANDLE" || continue
  ORPHANS=$((ORPHANS + 1))
  if worker_legacy "$HANDLE"; then
    log "    lock #${issue} is an older PID= lock (pid $(worker_pid "$HANDLE")): drover never signals it; check that process by hand"
  fi
done
if [ "$ORPHANS" -gt 0 ]; then
  log "refusing to start: ${ORPHANS} drover workers already running (live locks in $INFLIGHT_DIR)."
  log "    Stop them first: drover stop"
  log "    Then re-run: drover run"
  exit 73
fi
write_lock "$RUNNER_LOCK" "HANDLE=$(process_handle "$$")
STARTED=$(date -u +%FT%TZ)
"
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

# FactoryBench (FR-MET-4/8/12): off without the CLI and FACTORYBENCH_URL; spool flushed once.
if python3 "$DROVER_LIB/drover-metrics.py" enabled; then
  FB_ON=1
  FB_VERSION=$(python3 "$DROVER_LIB/drover-metrics.py" config-version)
  metrics flush
fi

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
  while lane_has_room "$1" && usage_ok && top_card_in_column "$2"; do
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

  # ── Free pre-check: count live workers from the locks (no API calls).
  lane_states

  # ── Cheap-tick path: workers at cap → skip GraphQL fetch entirely.
  #    The board can't change in a way that helps us until a lane frees up.
  #    A worker in its grace tick (LEFT_LANE) needs a fresh read to be stopped, so it
  #    forces the fetch.
  if [ "$ACTIVE_WORKERS" -ge "$MAX_WORKERS" ] && [ "$LEFT_LANE" = " " ]; then
    log "tick — cap reached (${ACTIVE_WORKERS}/${MAX_WORKERS} busy) — skipping GraphQL fetch, sleeping ${TICK_SECONDS}s"
    sleep "$TICK_SECONDS"
    continue
  fi

  # ── Expensive-tick path: we have capacity, fetch real state.
  gh_rate_guard
  fetch_project_items

  # FR-RUN-13: judge the workers that exited, against the fresh board. Exit 76: other
  # lanes' workers keep running with their locks.
  refresh_deps
  judge_finished_workers || exit 76
  # After the judge, so a worker's last gate lands before its card's Done ends the attempt.
  metrics board "$RUNS_DIR/deps.json" <<EOF
$PROJECT_ITEMS_JSON
EOF
  # A builder that exited mid-build left its card in Building, where no lane looks.
  reclaim_stranded_building

  # Zombie sweep (FR-WRK-10), on fresh data only: catches workers whose card already
  # moved out of the lane's source column but whose process didn't exit.
  sweep_lane_zombies
  sweep_blocked
  lane_states
  DISPATCHED_BEFORE=$DISPATCH_COUNT

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
    # A build card needs the pre-flight's go first; every Ready card is classified before its claim.
    while { lane_has_room build || lane_has_room qa; } && usage_ok && top_card_in_column "Ready"; do
      LANE_FOR=$(card_lane "$TOP_ISSUE")
      if ! lane_has_room "$LANE_FOR"; then
        TICK_SEEN="${TICK_SEEN}${TOP_ISSUE} "
      elif [ "$LANE_FOR" = build ] && ! preflight_ok "$TOP_ISSUE"; then
        TICK_SEEN="${TICK_SEEN}${TOP_ISSUE} "
      else
        classify_card "$TOP_ISSUE"
        dispatch_lane "$LANE_FOR" "$TOP_ISSUE"
      fi
    done
  fi

  gh_checkpoint

  # ── Landed-work halt gate (issue #8).
  #    Progress = the Done set changed since the last dispatch cycle. Lane
  #    occupancy is deliberately NOT part of this condition: the runaway this gate
  #    exists to catch is a pipeline that is 100% busy and lands nothing, and the old
  #    `AND no lane active` clause made that case unhaltable (293 ticks, 0 merges).
  # A paused tick that started nothing is not a dispatch cycle: a long pause is no runaway.
  if [ "$USAGE_PAUSED" = 1 ] && [ "$DISPATCH_COUNT" -eq "$DISPATCHED_BEFORE" ]; then
    sleep "$TICK_SECONDS"
    continue
  fi
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
