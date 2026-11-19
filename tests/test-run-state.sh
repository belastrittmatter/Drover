#!/usr/bin/env bash
# Tests write_run_state in lib/drover-run.sh: the live run snapshot .drover/runs/state.json
# (specs/runner.md FR-RUN-17). Offline: the board is a JSON fixture, locks are files.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fail() { echo "FAIL: $1" >&2; exit 1; }

export SB_LIB_ONLY=1 SB_GITHUB_HALT_FILE="$TMP/halt.json"
# shellcheck disable=SC1091
. "$REPO_ROOT/lib/drover-run.sh"
set +e
INFLIGHT_DIR="$TMP/inflight"; RUNS_DIR="$TMP/runs"; mkdir -p "$INFLIGHT_DIR" "$RUNS_DIR"
CONFIG_SLUG=drover; TICK=1
STATE="$RUNS_DIR/state.json"
board() { printf '{"items":[{"status":"%s","content":{"type":"Issue","number":14}}]}' "$1"; }
lock() { write_lock "$INFLIGHT_DIR/14" "HANDLE=process:5150:1
LANE=build
STARTED=2026-11-19T09:58:40Z
COLUMN=Ready
"; }

# 1 — one in-flight worker: its card with lane, worker handle and since.
lock; PROJECT_ITEMS_JSON=$(board Building)
write_run_state
jq -e --argjson pid "$$" '.repo == "drover" and .pid == $pid and .tick == 1 and .paused == null
  and (.updated | test("^[0-9-]+T[0-9:]+Z$"))
  and .cards == [{"issue":14,"lane":"build","worker":"process:5150:1","phase":null,
                  "since":"2026-11-19T09:58:40Z","phases":[]}]' "$STATE" >/dev/null \
  || fail "one worker: $(cat "$STATE")"

# 2 — the card left its lane (worker in its grace tick): gone on the next tick.
TICK=2; PROJECT_ITEMS_JSON=$(board QA)
write_run_state
jq -e '.tick == 2 and .cards == []' "$STATE" >/dev/null || fail "left lane: $(cat "$STATE")"
# ... and a reaped lock is gone too.
rm -f "$INFLIGHT_DIR/14"; PROJECT_ITEMS_JSON=$(board Building)
write_run_state
jq -e '.cards == []' "$STATE" >/dev/null || fail "reaped: $(cat "$STATE")"

# 3 — written via temp file + rename: no temp file left, never a half file.
lock; write_run_state
[ -z "$(find "$RUNS_DIR" -name '*.tmp.*')" ] || fail "temp file left behind"
grep -A25 '^write_run_state()' "$REPO_ROOT/lib/drover-run.sh" | grep -c 'write_lock "$file"' | grep -qx 2 || fail "state.json must go through write_lock (tmp + mv)"

# 4 — paused run names the reset time.
USAGE_STATE=pause USAGE_UNTIL=1795000000 write_run_state
jq -e '.paused == "2026-11-18T11:06:40Z"' "$STATE" >/dev/null || fail "paused: $(cat "$STATE")"

# 5 — runner exit: stopped, no pid, last cards kept.
write_run_state stopped
jq -e '.stopped == true and (has("pid") | not) and (.cards | length) == 1' "$STATE" >/dev/null \
  || fail "stopped: $(cat "$STATE")"
# halt_cleanup calls it, for the runner that owns the lock only.
grep -A12 '^halt_cleanup()' "$REPO_ROOT/lib/drover-run.sh" | grep -q 'RUNNER_LOCK_OWNED.*write_run_state stopped' \
  || fail "halt_cleanup must mark state.json stopped"

echo "PASS: run state"
