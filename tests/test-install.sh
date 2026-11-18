#!/usr/bin/env bash
# Tests install.sh and `drover skills install` (specs/config.md FR-CFG-7): install.sh only
# links bin/drover into ~/.local/bin and copies nothing into a project; skills install links
# each skill into the harness's user skill folder and never replaces or deletes anything
# that is not its own link. (An older copying installer once emptied a whole skills tree
# through a mis-resolved symlink; the decoys below guard against anything like it.)
set -euo pipefail
cd "$(dirname "$0")/.."
KIT="$PWD"
fail() { echo "FAIL: $1" >&2; exit 1; }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export HOME="$T/home"; mkdir -p "$HOME"
unset DROVER_CONFIG

# 1 — install.sh links bin/drover, and the link finds its install folder.
OUT=$(bash install.sh)
[ -L "$HOME/.local/bin/drover" ] || fail "install.sh should link ~/.local/bin/drover: $OUT"
[ "$(cd -P "$(dirname "$(readlink "$HOME/.local/bin/drover")")" && pwd)" = "$KIT/bin" ] || fail "link should point at bin/drover"
[ "$("$HOME/.local/bin/drover" version)" = "$(tr -d '[:space:]' < VERSION)" ] || fail "drover version through the link"
[ "$("$HOME/.local/bin/drover" config harness claude | jq -r .name)" = claude ] || fail "drover through the link should find harnesses/"
bash install.sh >/dev/null || fail "a second install must succeed"

# 2 — install.sh takes no project and writes nothing into one.
P="$T/project"; mkdir -p "$P"; echo keep > "$P/AGENTS.md"
RC=0; (cd "$P" && bash "$KIT/install.sh" "$P" >/dev/null 2>&1) || RC=$?
[ "$RC" -eq 64 ] || fail "install.sh <project> should refuse with 64, got $RC"
[ "$(ls -A "$P")" = AGENTS.md ] || fail "install.sh wrote into the project: $(ls -A "$P")"

# 3 — a real file at the link path is left alone.
rm "$HOME/.local/bin/drover"; echo mine > "$HOME/.local/bin/drover"
RC=0; bash install.sh >/dev/null 2>&1 || RC=$?
[ "$RC" -eq 1 ] && [ "$(cat "$HOME/.local/bin/drover")" = mine ] || fail "a real file at the link path must stay (rc $RC)"
rm "$HOME/.local/bin/drover"; bash install.sh >/dev/null

# 4 — skills install links every skill, keeps decoys, a foreign folder and a foreign link.
S="$HOME/.claude/skills"; mkdir -p "$S/decoy-one" "$S/visual" "$T/elsewhere"
touch "$S/decoy-one/SKILL.md"; echo mine > "$S/visual/SKILL.md"
ln -s "$T/elsewhere" "$S/git-sync"
ln -s "$T/gone" "$S/drover-qa"   # dangling: an older install folder that moved
RC=0; OUT=$("$HOME/.local/bin/drover" skills install 2>&1) || RC=$?
[ "$RC" -eq 1 ] || fail "skills install should report the folders it left alone (exit 1), got $RC: $OUT"
for s in drover drover-build drover-qa drover-review drover-collect ui-refine-loop pr-security-review; do
  [ -L "$S/$s" ] && [ "$(cd -P "$S/$s" && pwd)" = "$KIT/skills/$s" ] || fail "$s should link to the install folder"
done
[ "$(cat "$S/visual/SKILL.md")" = mine ] || fail "a real skill folder of the same name must stay"
[ "$(readlink "$S/git-sync")" = "$T/elsewhere" ] || fail "a link to somewhere else must stay"
[ -f "$S/decoy-one/SKILL.md" ] || fail "DECOY DESTROYED"
echo "$OUT" | grep -q "left alone $S/visual" || fail "the kept folder should be named: $OUT"
rm -rf "$S/visual" "$S/git-sync"
"$HOME/.local/bin/drover" skills install >/dev/null || fail "a clean skills install should exit 0"
"$HOME/.local/bin/drover" skills install | grep -q "^ok drover$" || fail "a second run keeps the links"
"$HOME/.local/bin/drover" skills install --dest "$T/other" >/dev/null && [ -L "$T/other/drover" ] || fail "--dest should be honoured"

# 5 — unknown commands and an invalid config stop before anything runs.
RC=0; "$HOME/.local/bin/drover" no-such-thing >/dev/null 2>&1 || RC=$?
[ "$RC" -eq 64 ] || fail "unknown command should exit 64, got $RC"
RC=0; "$HOME/.local/bin/drover" ../lib/drover-run >/dev/null 2>&1 || RC=$?
[ "$RC" -eq 64 ] || fail "a path as command should exit 64, got $RC"
mkdir -p "$P/.drover"; echo '{"project":{"owner":"o","number":"3"},"roles":{"build":{"max_workers":0}}}' > "$P/.drover/config.json"
RC=0; ERR=$(cd "$P" && "$HOME/.local/bin/drover" status 2>&1) || RC=$?
[ "$RC" -eq 65 ] || fail "an invalid config should stop the command with 65, got $RC: $ERR"
echo "$ERR" | grep -q "project.number: expected integer" || fail "the failing field should be named: $ERR"
echo "$ERR" | grep -q "roles.build.max_workers: must be >= 1" || fail "every failing field should be named: $ERR"
echo '{"project":{"owner":"o","number":3},"worker_backend":"claude-p"}' > "$P/.drover/config.json"
ERR=$(cd "$P" && "$HOME/.local/bin/drover" config validate 2>&1) && fail "a removed key must fail validation"
echo "$ERR" | grep -q "worker_backend: unknown key" || fail "a removed key should be named: $ERR"

# 6 — `drover gh-guard` calls of one worker share one budget (keyed on role and issue).
B="$T/budget"; mkdir -p "$B"
for n in 5 2; do TMPDIR="$B" DROVER_ROLE=build DROVER_ISSUE=7 "$HOME/.local/bin/drover" gh-guard spend "$n"; done
[ "$(TMPDIR="$B" DROVER_ROLE=build DROVER_ISSUE=7 "$HOME/.local/bin/drover" gh-guard budget)" = 143 ] || fail "separate calls must share the budget"
[ "$(TMPDIR="$B" DROVER_ROLE=qa DROVER_ISSUE=7 "$HOME/.local/bin/drover" gh-guard budget)" = 150 ] || fail "another worker has its own budget"

# 7 — edge cases: a flag without a value, an unreadable config, a git dir kept elsewhere.
RC=0; "$HOME/.local/bin/drover" skills install --dest >/dev/null 2>&1 || RC=$?
[ "$RC" -eq 64 ] || fail "--dest without a value should exit 64, got $RC"
echo '{"project":{"owner":"o","number":3}}' > "$P/.drover/config.json"; chmod 000 "$P/.drover/config.json"
if [ ! -r "$P/.drover/config.json" ]; then  # root can read anything; skip there
  RC=0; ERR=$(cd "$P" && "$HOME/.local/bin/drover" config validate 2>&1) || RC=$?
  [ "$RC" -eq 65 ] && echo "$ERR" | grep -q "cannot be read" || fail "an unreadable config should exit 65 and say so, got $RC: $ERR"
fi
chmod 644 "$P/.drover/config.json"
G="$T/sep"; mkdir -p "$G/work"; git init -q --separate-git-dir "$G/gitdir" "$G/work"
[ "$(cd "$G/work" && "$HOME/.local/bin/drover" config path)" = "$(cd "$G/work" && pwd -P)/.drover/config.json" ] \
  || fail "a separate git dir should still find the checkout: $(cd "$G/work" && "$HOME/.local/bin/drover" config path)"

echo "PASS: test-install.sh (link, no project writes, kept files, skills links, validation, gh-guard budget, edge cases)"
