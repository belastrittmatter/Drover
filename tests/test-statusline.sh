#!/usr/bin/env bash
# Tests `drover statusline` (lib/drover-statusline.py, specs/runner.md FR-RUN-19).
# Offline: a throwaway repo with a hand-written state.json; no config, no network.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fail() { echo "FAIL: $1" >&2; exit 1; }
git init -q "$TMP/proj"; mkdir -p "$TMP/proj/.drover/runs"
STATE="$TMP/proj/.drover/runs/state.json"
line() { (cd "$TMP/proj" && bash "$REPO_ROOT/bin/drover" statusline); }

# 1 — no state file: nothing, exit 0 (also outside a git repo).
out=$(line); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] || fail "no state: rc=$rc out=$out"
out=$(cd "$TMP" && bash "$REPO_ROOT/bin/drover" statusline); rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] || fail "no repo: rc=$rc out=$out"

# 2 — two build cards and one review card; the oldest card shows with its phase.
cat >"$STATE" <<'JSON'
{"repo":"drover","pid":1,"tick":3,"updated":"2026-11-27T03:00:00Z","paused":null,"cards":[
 {"issue":15,"lane":"build","phase":null,"since":"2026-11-27T02:00:00Z","phases":[]},
 {"issue":14,"lane":"review","phase":"tests","since":"2026-11-27T01:00:00Z","phases":[]},
 {"issue":16,"lane":"build","phase":"plan","since":"2026-11-27T02:30:00Z","phases":[]}]}
JSON
[ "$(line)" = "drover: 2 build · 1 review · #14 tests" ] || fail "counts: $(line)"

# 3 — oldest card without a phase: just the number.
jq '.cards |= map(.phase = null)' "$STATE" >"$STATE.new" && mv "$STATE.new" "$STATE"
[ "$(line)" = "drover: 2 build · 1 review · #14" ] || fail "no phase: $(line)"

# 4 — paused run says so.
jq '.paused = "2026-11-27T05:00:00Z"' "$STATE" >"$STATE.new" && mv "$STATE.new" "$STATE"
[ "$(line)" = "drover: paused · 2 build · 1 review · #14" ] || fail "paused: $(line)"

# 5 — stopped runner, or an unreadable file: nothing, exit 0.
jq '.stopped = true' "$STATE" >"$STATE.new" && mv "$STATE.new" "$STATE"
out=$(line); rc=$?; [ "$rc" = 0 ] && [ -z "$out" ] || fail "stopped: rc=$rc out=$out"
echo '{not json' >"$STATE"
out=$(line); rc=$?; [ "$rc" = 0 ] && [ -z "$out" ] || fail "garbage: rc=$rc out=$out"

# 6 — no cards: idle. Works from a linked worktree too (state lives in the main checkout).
echo '{"cards":[]}' >"$STATE"
[ "$(line)" = "drover: idle" ] || fail "idle: $(line)"
git -C "$TMP/proj" commit -q --allow-empty -m init
git -C "$TMP/proj" worktree add -q "$TMP/proj/.drover/worktrees/w" -b w
out=$(cd "$TMP/proj/.drover/worktrees/w" && bash "$REPO_ROOT/bin/drover" statusline)
[ "$out" = "drover: idle" ] || fail "worktree: $out"

# 7 — under 100 ms: the script alone (no bash wrapper), best of 15 so a loaded suite run does not fail it.
echo '{"cards":[]}' >"$STATE"
best=$(python3 - "$REPO_ROOT/lib/drover-statusline.py" "$TMP/proj" <<'PY'
import subprocess, sys, time
t = []
for _ in range(15):
    s = time.perf_counter()
    subprocess.run(["python3", sys.argv[1]], cwd=sys.argv[2], capture_output=True)
    t.append((time.perf_counter() - s) * 1000)
print(int(min(t)))
PY
)
[ "$best" -lt 100 ] || fail "too slow: ${best} ms"

# 8 — no network: the script imports no networking module.
! grep -Eq '^\s*(import|from)\s+(urllib|http|socket|requests)' "$REPO_ROOT/lib/drover-statusline.py" || fail "network import"

echo "PASS: statusline"
