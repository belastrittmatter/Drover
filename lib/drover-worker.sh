#!/usr/bin/env bash
# drover-worker.sh — the one worker interface (specs/runner.md FR-WRK-1, FR-WRK-2).
#
#   drover worker start ROLE ISSUE PROMPT LOG   start a headless worker, print its handle
#   drover worker alive HANDLE                  exit 0 while the worker runs, 1 otherwise
#   drover worker stop HANDLE [GRACE]           TERM, wait up to GRACE s (default 1), then KILL
#   drover worker lock FILE                     print the handle an in-flight lock records
#
# The headless runner and `drover stop` source this file for the same functions; in them
# no other code starts, signals or detects worker processes.
#
# A handle is "process:<pid>:<start>" — the `process` backend is the only one. <pid> is the
# harness's headless worker command (harnesses/<name>.json), output appended to LOG; <start>
# is its start time from `ps -o lstart=`, so a pid the OS has since given to another process
# reads as dead and is never signalled. Lock files hold HANDLE=, LANE=, STARTED= (and the
# runner's COLUMN=). Older PID=<pid> and bare-pid locks still read as "process:<pid>": alive
# while that pid exists (so a run refuses to start), but never signalled.

WORKER_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Start time of pid $1 as one word, empty when the pid does not exist.
process_start() {
  LC_ALL=C ps -o lstart= -p "$1" 2>/dev/null | awk '{$1=$1; gsub(" ", "_"); print}'
}

# The handle of an already running pid $1 (the runner's own lock).
process_handle() {
  echo "process:$1:$(process_start "$1")"
}

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
  # A worker that already exited gets an empty start: its handle reads as dead.
  WORKER_HANDLE=$(process_handle "$!")
}

# worker_pid HANDLE: prints the pid when it is a real process id (>= 2; kill 0 would hit
# the process group).
worker_pid() {
  local rest pid
  case "${1:-}" in process:*) rest="${1#process:}" ;; *) return 1 ;; esac
  pid="${rest%%:*}"
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  [ "$pid" -ge 2 ] || return 1
  echo "$pid"
}

# worker_legacy HANDLE: 0 for a handle without a start time (an older PID= lock).
worker_legacy() {
  case "${1#process:}" in *:*) return 1 ;; esac
  return 0
}

worker_alive() {
  local pid
  pid=$(worker_pid "${1:-}") || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  worker_legacy "$1" && return 0
  local want="${1#process:*:}"
  [ -n "$want" ] && [ "$(process_start "$pid")" = "$want" ]
}

# worker_stop HANDLE [GRACE]: sets WORKER_STOPPED_BY to none (not alive), TERM, KILL, or
# refused (an older handle with no start time: never signalled). Returns 0.
worker_stop() {
  local handle="$1" grace="${2:-1}" waited=0 pid
  WORKER_STOPPED_BY=none
  worker_alive "$handle" || return 0
  if worker_legacy "$handle"; then WORKER_STOPPED_BY=refused; return 0; fi
  pid=$(worker_pid "$handle")
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

# write_lock FILE CONTENT: tmp file then mv, so a reader never sees half a lock.
write_lock() {
  printf '%s' "$2" > "$1.tmp.$$" && mv -f "$1.tmp.$$" "$1"
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
