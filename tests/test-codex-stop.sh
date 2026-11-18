#!/usr/bin/env bash
# Stop integration uses local dummy processes and gh/ps/pgrep stubs only.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
wave_pid=""
trap 'if [ -n "$wave_pid" ]; then kill "$wave_pid" 2>/dev/null || true; wait "$wave_pid" 2>/dev/null || true; fi; rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/.drover" "$TMP/.claude/drover/inflight/merge.lock"
printf '{"project":{"owner":"o","number":1},"notifications":{"bot_identity":"bot"}}\n' > "$TMP/.drover/config.json"
printf 'SLUG=test\nSTARTED=now\n' > "$TMP/.claude/drover/inflight/workflow-wave.lock"
printf 'PID=999999999\nLANE=build\nSTARTED=now\n' > "$TMP/.claude/drover/inflight/12"
export STOP_TRACE="$TMP/trace"
cat > "$TMP/bin/gh" <<'SH'
#!/usr/bin/env bash
printf 'gh %s\n' "$*" >> "$STOP_TRACE"
SH
cat > "$TMP/bin/pgrep" <<'SH'
#!/usr/bin/env bash
exit 1
SH
cat > "$TMP/bin/ps" <<'SH'
#!/usr/bin/env bash
echo "python3 - /kit/lib/drover-codex-wave.sh"
SH
chmod +x "$TMP/bin/"*
python3 - "$TMP" <<'PY' &
import os, pathlib, signal, sys, time
root = pathlib.Path(sys.argv[1])
def stop(*_):
    with (root / 'trace').open('a') as f:
        f.write('wave-stopped\n')
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
(root / '.claude/drover/codex-wave.pid').write_text(str(os.getpid()) + '\n')
while True:
    time.sleep(.05)
PY
wave_pid=$!
for attempt in {1..100}; do
  [ -f "$TMP/.claude/drover/codex-wave.pid" ] && break
  sleep .02
done
(cd "$TMP" && PATH="$TMP/bin:$PATH" bash "$ROOT/lib/drover-stop.sh" > stop.log)
wait "$wave_pid"
wave_pid=""
[ "$(head -1 "$STOP_TRACE")" = wave-stopped ] || { echo 'FAIL: claims released before wave stopped'; exit 1; }
grep -q '^gh issue comment 12 ' "$STOP_TRACE" || { echo 'FAIL: numeric issue not wrapped up'; exit 1; }
if grep -q 'workflow-wave.lock\|merge.lock' "$STOP_TRACE"; then
  echo 'FAIL: control marker treated as an issue'; exit 1
fi
[ -d "$TMP/.claude/drover/inflight/merge.lock" ] || { echo 'FAIL: merge lock removed'; exit 1; }
[ ! -e "$TMP/.claude/drover/codex-wave.pid" ] || { echo 'FAIL: Codex marker retained'; exit 1; }

# A live reused PID with a different command must not be signalled or released.
printf '%s\n' "$$" > "$TMP/.claude/drover/codex-wave.pid"
printf 'PID=999999999\nLANE=qa\n' > "$TMP/.claude/drover/inflight/13"
printf '#!/usr/bin/env bash\necho unrelated-process\n' > "$TMP/bin/ps"
: > "$STOP_TRACE"
rc=0
(cd "$TMP" && PATH="$TMP/bin:$PATH" bash "$ROOT/lib/drover-stop.sh" > stop.log) || rc=$?
[ "$rc" -eq 1 ] || { echo 'FAIL: reused PID not refused'; exit 1; }
[ ! -s "$STOP_TRACE" ] || { echo 'FAIL: reused PID released claims'; exit 1; }
[ -f "$TMP/.claude/drover/inflight/13" ] || { echo 'FAIL: reused PID removed worker lock'; exit 1; }
echo 'PASS: test-codex-stop.sh (stop order, control markers, reused PID)'
