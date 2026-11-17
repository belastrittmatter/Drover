#!/usr/bin/env bash
# Offline checks. Keep test-* / test_* suites offline.
# Run every suite even after a failure, then fail the whole gate if any suite failed.
set -uo pipefail
cd "$(dirname "$0")/.."

for tool in bash python3 git jq node curl tar; do
  command -v "$tool" >/dev/null || { echo "Missing test dependency: $tool" >&2; exit 1; }
done

shopt -s nullglob
suites=(tests/test-*.sh tests/test_*.py)
[ "${#suites[@]}" -gt 0 ] || { echo 'No safety suites found' >&2; exit 1; }
jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT
# Each worker writes <i>.log and <i>.rc; xargs -P keeps it Bash 3.2 friendly.
for i in "${!suites[@]}"; do echo "$i ${suites[$i]}"; done |
  xargs -n2 -P "$jobs" bash -c 's=$SECONDS
    case "$2" in *.sh) bash "$2" ;; *.py) python3 "$2" ;; esac >"$0/$1.log" 2>&1 </dev/null
    rc=$?; echo "$rc" >"$0/$1.rc"
    [ "$rc" -eq 0 ] && r=ok || r=FAIL; echo "$r $2 $((SECONDS - s))s"' "$tmp"
failed=()
for i in "${!suites[@]}"; do
  suite=${suites[$i]}
  echo "::group::$suite"
  cat "$tmp/$i.log"
  echo '::endgroup::'
  rc=$(cat "$tmp/$i.rc" 2>/dev/null || echo 1)
  if [ "$rc" -ne 0 ]; then
    failed+=("$suite")
    echo "::error::$suite failed (exit $rc)"
  fi
done
if [ "${#failed[@]}" -gt 0 ]; then
  printf 'Failed suite: %s\n' "${failed[@]}" >&2
  exit 1
fi
echo "PASS: ${#suites[@]} safety suites"
