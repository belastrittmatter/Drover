#!/usr/bin/env bash
# drover-worker.sh — the one worker interface (specs/runner.md FR-WRK-1, FR-WRK-2).
#
#   drover worker start ROLE ISSUE PROMPT LOG   start a headless worker, print its handle
#   drover worker alive HANDLE                  exit 0 while the worker runs, 1 otherwise
#   drover worker stop HANDLE [GRACE]           TERM, wait up to GRACE s (default 1), then KILL
#   drover worker lock FILE                     print the handle an in-flight lock records
#
# The runner and `drover stop` source this file for the same functions. No other code
# starts, signals or detects worker processes.
#
# A handle is "<backend>:<id>". Only the `process` backend exists: <id> is the pid of the
# harness's headless worker command (harnesses/<name>.json), with output appended to LOG.
# Lock files hold HANDLE=<handle>, LANE=, STARTED= (and the runner's COLUMN=); the older
# PID=<pid> and bare-pid formats still read.

WORKER_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# worker_start ROLE ISSUE PROMPT LOG: sets WORKER_HANDLE. Nonzero (66) when the harness
# gives no command.
worker_start() {
  local role="$1" issue="$2" prompt="$3" log="$4" arg
  local -a argv=()
  WORKER_HANDLE=""
  while IFS= read -r -d '' arg; do argv+=("$arg"); done \
    < <(python3 "$WORKER_LIB/drover-config.py" worker-argv "$role" "$prompt")
  [ "${#argv[@]}" -gt 1 ] || { echo "drover worker: no worker command from the harness entry" >&2; return 66; }
  # No subshell or `exec` wrapper, so $! is the worker itself. stdin is /dev/null: a headless
  # claude otherwise waits 3 s for input. DROVER_ROLE / DROVER_ISSUE: guard-merge lets only the
  # review lane run the gate, and drover gh-guard keys the worker's call budget on them.
  DROVER_ROLE="$role" DROVER_ISSUE="$issue" nohup "${argv[@]}" </dev/null >>"$log" 2>&1 &
  WORKER_HANDLE="process:$!"
}

worker_alive() {
  local pid
  case "${1:-}" in
    process:*) pid="${1#process:}" ;;
    *) return 1 ;;
  esac
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$pid" 2>/dev/null
}

# worker_stop HANDLE [GRACE]: sets WORKER_STOPPED_BY to none (was not alive), TERM or KILL.
worker_stop() {
  local handle="$1" grace="${2:-1}" waited=0 pid
  WORKER_STOPPED_BY=none
  worker_alive "$handle" || return 0
  pid="${handle#process:}"
  kill -TERM "$pid" 2>/dev/null || true
  WORKER_STOPPED_BY=TERM
  while worker_alive "$handle" && [ "$waited" -lt "$grace" ]; do
    sleep 1
    waited=$((waited + 1))
  done
  if worker_alive "$handle"; then
    kill -KILL "$pid" 2>/dev/null || true
    WORKER_STOPPED_BY=KILL
  fi
  return 0
}

# worker_lock FILE: sets HANDLE, LANE, STARTED, COLUMN from an in-flight lock.
worker_lock() {
  local key val
  HANDLE=""; LANE=""; STARTED=""; COLUMN=""
  [ -f "$1" ] || return 1
  while IFS='=' read -r key val || [ -n "$key" ]; do
    case "$key" in
      HANDLE) HANDLE="$val" ;;
      PID) [ -n "$HANDLE" ] || HANDLE="process:$val" ;;
      LANE) LANE="$val" ;;
      STARTED) STARTED="$val" ;;
      COLUMN) COLUMN="$val" ;;
    esac
  done < "$1"
  # Pre v1.3.0 locks hold the pid alone.
  if [ -z "$HANDLE" ] && ! grep -q = "$1" 2>/dev/null; then
    HANDLE="process:$(tr -d '[:space:]' < "$1")"
  fi
  return 0
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -euo pipefail
  case "${1:-}" in
    start) [ $# -eq 5 ] || { echo "usage: drover worker start ROLE ISSUE PROMPT LOG" >&2; exit 64; }
           worker_start "$2" "$3" "$4" "$5"; echo "$WORKER_HANDLE" ;;
    alive) [ $# -eq 2 ] || { echo "usage: drover worker alive HANDLE" >&2; exit 64; }
           worker_alive "$2" ;;
    stop)  [ $# -ge 2 ] || { echo "usage: drover worker stop HANDLE [GRACE]" >&2; exit 64; }
           worker_stop "$2" "${3:-1}"; echo "$WORKER_STOPPED_BY" ;;
    lock)  [ $# -eq 2 ] || { echo "usage: drover worker lock FILE" >&2; exit 64; }
           worker_lock "$2" || exit 1; echo "$HANDLE" ;;
    *) sed -n '3,7p' "$0" >&2; exit 64 ;;
  esac
fi
