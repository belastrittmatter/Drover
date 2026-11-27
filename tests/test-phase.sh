#!/usr/bin/env bash
# Tests `drover phase <name>` (lib/drover-phase.sh) and its fold into state.json by
# write_run_state (specs/runner.md FR-RUN-18). Offline: a throwaway repo with a linked worktree.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fail() { echo "FAIL: $1" >&2; exit 1; }
PHASE="$REPO_ROOT/lib/drover-phase.sh"

# A project repo whose worker runs in a linked worktree, as the runner starts it.
git init -q "$TMP/proj" && git -C "$TMP/proj" commit -q --allow-empty -m init
git -C "$TMP/proj" worktree add -q "$TMP/proj/.drover/worktrees/issue-14-build" -b issue-14-x
WT="$TMP/proj/.drover/worktrees/issue-14-build"
LOG="$TMP/proj/.drover/runs/phases/14.jsonl"

# 1 — inside a worker: appends {name, at} for the worker's issue, in the main checkout's runs dir.
(cd "$WT" && DROVER_ISSUE=14 bash "$PHASE" plan && DROVER_ISSUE=14 bash "$PHASE" tests) || fail "phase call failed"
[ "$(wc -l < "$LOG" | tr -d ' ')" = 2 ] || fail "two lines expected: $(cat "$LOG")"
jq -se '[.[].name] == ["plan","tests"] and all(.[]; .at | test("^[0-9-]+T[0-9:]+Z$"))' "$LOG" >/dev/null \
  || fail "entries: $(cat "$LOG")"

# 2 — invalid names exit 64 and write nothing.
for bad in "" Tests 1plan "plan tests" "a/b" "$(printf 'x%.0s' {1..33})" '-x'; do
  (cd "$WT" && DROVER_ISSUE=14 bash "$PHASE" "$bad" 2>/dev/null); rc=$?
  [ "$rc" = 64 ] || fail "'$bad' exited $rc, want 64"
done
(cd "$WT" && DROVER_ISSUE=14 bash "$PHASE" a b 2>/dev/null); [ $? = 64 ] || fail "two args must exit 64"
[ "$(wc -l < "$LOG" | tr -d ' ')" = 2 ] || fail "invalid names wrote: $(cat "$LOG")"
(cd "$WT" && DROVER_ISSUE=14 bash "$PHASE" "$(printf 'x%.0s' {1..32})") || fail "32-char name must pass"

# 3 — no worker issue: never fails the caller, records nothing.
(cd "$WT" && env -u DROVER_ISSUE bash "$PHASE" tests 2>/dev/null) || fail "no DROVER_ISSUE must exit 0"
(cd "$WT" && DROVER_ISSUE=../x bash "$PHASE" tests 2>/dev/null) || fail "bad DROVER_ISSUE must exit 0"
[ ! -e "$TMP/proj/.drover/runs/phases/x.jsonl" ] || fail "bad issue wrote a file"

# 4 — the runner folds the log into state.json: latest phase + ordered history since the worker started.
export SB_LIB_ONLY=1 SB_GITHUB_HALT_FILE="$TMP/halt.json"
# shellcheck disable=SC1091
. "$REPO_ROOT/lib/drover-run.sh"
set +e
INFLIGHT_DIR="$TMP/inflight"; RUNS_DIR="$TMP/runs"; mkdir -p "$INFLIGHT_DIR" "$RUNS_DIR/phases"
CONFIG_SLUG=drover; TICK=1
PROJECT_ITEMS_JSON='{"items":[{"status":"Building","content":{"type":"Issue","number":14}}]}'
write_lock "$INFLIGHT_DIR/14" "HANDLE=process:5150:1
LANE=build
STARTED=2026-11-19T09:58:40Z
COLUMN=Ready
"
write_run_state
jq -e '.cards[0].phase == null and .cards[0].phases == []' "$RUNS_DIR/state.json" >/dev/null \
  || fail "no log: $(cat "$RUNS_DIR/state.json")"
{ echo '{"name":"tests","at":"2026-11-19T09:00:00Z"}'   # an earlier worker's phase: not this one's
  echo '{"name":"plan","at":"2026-11-19T10:00:00Z"}'
  echo 'not json'
  echo '{"name":"tests","at":"2026-11-19T10:05:00Z"}'; } > "$RUNS_DIR/phases/14.jsonl"
write_run_state
jq -e '.cards[0].phase == "tests"
  and .cards[0].phases == [{"name":"plan","at":"2026-11-19T10:00:00Z"},{"name":"tests","at":"2026-11-19T10:05:00Z"}]' \
  "$RUNS_DIR/state.json" >/dev/null || fail "fold: $(cat "$RUNS_DIR/state.json")"

echo "PASS: phase"
