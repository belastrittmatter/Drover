#!/usr/bin/env bash
# drover installer.
# Copies skills/, scripts/, workflows/ and the guard hooks once into the target
# project's shared .agents/ tree, links them into each agent host's folder, and
# merges the hook settings into .claude/settings.json (backed up first, never duplicated).
#
# Usage:
#   ./install.sh [--no-hooks] [--protect-main] [--hosts claude,codex,opencode] [target-project-dir]
# Defaults to the current working directory. --no-hooks skips the guard hooks
# and leaves settings.json untouched. --protect-main also wires the opt-in
# guard that blocks direct and force pushes to main/master/base_branch; with
# --no-hooks it installs that one guard alone. --hosts (or --hosts=…) adds host
# folders to link; .claude/ is always linked. The installer asks nothing:
# drover onboard asks the questions (protect main is one of them).
#
# Layout: .agents/{skills/<skill>,bin,workflows,hooks} hold the files. Each host
# folder (.claude/ always; .codex/ and .opencode/ when that folder already exists,
# a config "host" key names it, drover-host.sh reports it, or --hosts asks)
# gets one link per skill under skills/ and one link per file under bin/,
# workflows/ and hooks/. Nothing that is not drover's is touched. Where a
# link cannot be made (Windows without symlink rights) the file is copied.
# Settings and run state (.claude/settings.json, .claude/drover/) stay put.
#
# Output is grouped with emojis, one line per group of files (failures still name
# the file). Environment: DROVER_QUIET_NEXT=1 prints only the "🔧 installing"
# group, for a wrapper that prints its own header and summary around it.
#
# An older drover already in the target (a different skills/drover/VERSION,
# or one with no VERSION) is recorded in .claude/drover/upgrade.json
# {from, to, added_skills}; onboard's 🔍 Checks finishes the upgrade and lists it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS=1
PROTECT=0
TARGET=""
ASKED_HOSTS=""
want_hosts=0
for arg in "$@"; do
  if [ "$want_hosts" -eq 1 ]; then ASKED_HOSTS="$ASKED_HOSTS,$arg"; want_hosts=0; continue; fi
  case "$arg" in
    --no-hooks) HOOKS=0 ;;
    --protect-main) PROTECT=1 ;;
    --hosts) want_hosts=1 ;;
    --hosts=*) ASKED_HOSTS="$ASKED_HOSTS,${arg#--hosts=}" ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 64 ;;
    *) TARGET="$arg" ;;
  esac
done
[ "$want_hosts" -eq 0 ] || { echo "--hosts needs a list, e.g. --hosts claude,codex" >&2; exit 64; }
TARGET="${TARGET:-$PWD}"

# Primary: the board and its lanes. Secondary: standalone helpers the lanes
# can call (visual, git-sync, ui-refine-loop, pr-security-review). Worktree cleanup (cleanup-wt) is a
# hook, not a skill: it ships with the guard hooks below.
PRIMARY_SKILLS="drover drover-build drover-qa drover-review drover-collect"
SECONDARY_SKILLS="visual git-sync ui-refine-loop pr-security-review"

if [ ! -d "$TARGET" ]; then
  echo "target directory not found: $TARGET" >&2
  exit 64
fi

# Hosts: claude always; codex/opencode when asked, configured or detected.
HOSTS="claude"
add_host() { case " $HOSTS " in *" $1 "*) ;; *) HOSTS="$HOSTS $1" ;; esac; }
for h in $(echo "$ASKED_HOSTS" | tr ',' ' '); do
  case "$h" in
    claude|codex|opencode) add_host "$h" ;;
    *) echo "unknown host: $h (use claude, codex or opencode)" >&2; exit 64 ;;
  esac
done
for h in codex opencode; do [ ! -d "$TARGET/.$h" ] || add_host "$h"; done
for h in $(python3 - "$TARGET/.claude/drover/configs" <<'PY'
import glob, json, os, sys
for p in sorted(glob.glob(os.path.join(sys.argv[1], "*.json"))):
    try:
        h = json.load(open(p)).get("host")
    except Exception:
        continue
    if h in ("codex", "opencode"):
        print(h)
PY
); do add_host "$h"; done
[ "$(bash "$REPO_ROOT/scripts/drover-host.sh" 2>/dev/null || true)" != codex ] || add_host codex

QUIET="${DROVER_QUIET_NEXT:-}"
PACK_VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION" 2>/dev/null || echo '?')"
SHARED="$TARGET/.agents"

# Record an upgrade BEFORE the copy overwrites the old VERSION. An install made
# before .agents/ existed keeps its VERSION under .claude/skills/drover.
PREV_VERSION=""
for d in "$SHARED/skills/drover" "$TARGET/.claude/skills/drover"; do
  if [ -d "$d" ]; then
    PREV_VERSION="$(tr -d '[:space:]' < "$d/VERSION" 2>/dev/null || echo 'older')"
    [ -n "$PREV_VERSION" ] || PREV_VERSION="older"
    break
  fi
done
ADDED_SKILLS=""
if [ -n "$PREV_VERSION" ]; then
  for skill in $PRIMARY_SKILLS $SECONDARY_SKILLS; do
    [ -e "$SHARED/skills/$skill" ] || [ -e "$TARGET/.claude/skills/$skill" ] || ADDED_SKILLS="$ADDED_SKILLS $skill"
  done
fi

[ -n "$QUIET" ] || echo "🧩 drover $PACK_VERSION → $TARGET"
echo "🔧 installing"

# Resolve all skill paths before replacing anything. A destination can contain
# the pack (or a linked source skill); clearing it would destroy our input.
# A destination inside its source would instead recursively copy into itself.
python3 - "$REPO_ROOT" "$TARGET" "$HOSTS" $PRIMARY_SKILLS $SECONDARY_SKILLS <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2])
folders = [".agents"] + ["." + h for h in sys.argv[3].split()]
for skill in sys.argv[4:]:
    src = (root / "skills" / skill).resolve()
    for folder in folders:
        dest = (target / folder / "skills" / skill).resolve()
        if src == dest:
            continue
        if dest == root or dest in root.parents or dest in src.parents or src in dest.parents:
            sys.exit(f"    ✗ refusing to install {skill}: source and destination overlap ({dest})")
PY

mkdir -p "$SHARED/skills" "$SHARED/bin" "$SHARED/workflows"

# Copy one skill into dest. A project may keep the real skill directories
# elsewhere and symlink them in — a layout some repos use to share one tree
# between agent hosts. `cp -R src dest/` fails outright on such a target ("Not a
# directory"), so resolve the link and write to where it actually points. That
# keeps the project's chosen layout instead of replacing it with a plain copy.
# Returns 1 when the skill was skipped.
install_skill() {
  local skill="$1" dest="$2" note="" resolved
  if [ -L "$dest" ]; then
    # `cd -P` walks the link to its physical target in one step. Do NOT rebuild
    # the path from `readlink` output: `cd ""` SUCCEEDS in bash, so an empty
    # readlink silently leaves you in the parent, and the rm below then empties
    # the skills folder instead of one skill. That is not hypothetical — it deleted
    # 190 entries on a real repo on 2026-08-20 before this comment existed.
    resolved=$(cd -P "$dest" 2>/dev/null && pwd) || resolved=""
    if [ -z "$resolved" ]; then
      echo "    ✗ $skill is a symlink pointing nowhere — skipping rather than replacing it" >&2
      return 1
    fi
    dest="$resolved"
    note=" (via symlink → ${resolved#$TARGET/})"
  fi

  # Belt and braces: whatever the resolution produced, it must be a path ENDING
  # in this skill's own name. Anything else is a bug in the line above, and the
  # blast radius of being wrong here is the user's entire skills tree.
  case "$dest" in
    */"$skill") : ;;
    *) echo "    ✗ refusing to write $skill: resolved to '$dest', which is not a $skill directory" >&2
       return 1 ;;
  esac

  # A self-vendored project can expose this very source through one or more
  # links. There is nothing to copy in that case; clearing dest erases src too.
  if [ "$REPO_ROOT/skills/$skill" -ef "$dest" ]; then
    echo "   ✓ $skill already uses the source directory"
    return 0
  fi

  # Replace the contents, not the directory itself, so a symlink stays a symlink.
  mkdir -p "$dest"
  rm -rf "${dest:?}/"* 2>/dev/null || true
  cp -R "$REPO_ROOT/skills/$skill/." "$dest/"
  [ -z "$note" ] || echo "   ✓ $skill$note"
}

# File destinations can be links into the pack too. -ef follows symlinks and
# also recognizes hard links, so cp never tries to overwrite its own input.
copy_file() {
  if [ ! "$1" -ef "$2" ]; then
    cp "$1" "$2"
  fi
}

# Relative path from a host folder (physical) to the shared folder, so links
# survive a moved or freshly cloned project. Prints e.g. ../../.agents/skills.
relpath() {
  python3 - "$1" "$2" <<'PY'
import os, sys
print(os.path.relpath(os.path.realpath(sys.argv[1]), os.path.realpath(sys.argv[2])))
PY
}

COPIED=0
# Point <host>/<group>/<name> at .agents/<group>/<name>. Already resolving there
# (a link, or a whole folder linked into .agents/) → nothing to do. A user's own
# link elsewhere → keep it and write through it, as before .agents/ existed.
# drover's own real file or directory → replaced by the link.
link_entry() {
  local host="$1" group="$2" name="$3" src_in_pack="$4" kind="$5" rel="$6"
  local shared="$SHARED/$group/$name" dest="$TARGET/.$host/$group/$name"
  [ -e "$shared" ] || return 0
  [ "$dest" -ef "$shared" ] && return 0
  if [ -L "$dest" ]; then
    if [ "$kind" = dir ]; then install_skill "$name" "$dest" || true
    elif [ ! "$dest" -ef "$src_in_pack" ]; then copy_file "$src_in_pack" "$dest"
    fi
    return 0
  fi
  case "$dest" in "$TARGET/.$host/$group/$name") : ;; *) return 0 ;; esac
  rm -rf "${dest:?}"
  ln -s "$rel/$name" "$dest" 2>/dev/null && return 0
  COPIED=1
  if [ "$kind" = dir ]; then mkdir -p "$dest" && cp -R "$shared/." "$dest/"; else cp "$shared" "$dest"; fi
}

for skill in $PRIMARY_SKILLS $SECONDARY_SKILLS; do
  if [ ! -d "$REPO_ROOT/skills/$skill" ]; then
    echo "    ✗ missing $skill in repo — skipping" >&2
    continue
  fi
  install_skill "$skill" "$SHARED/skills/$skill" || true
done

SCRIPTS="drover-project-verifier.py drover-skills-ready.py drover-verification-ready.py drover-github-read.py drover-collect-state.py drover-run.sh drover-gh-guard.sh drover-card.sh drover-status.py drover-wave-plan.sh drover-lane.sh drover-codex-lane.sh drover-codex-wave.sh drover-deps.sh drover-preflight.sh drover-merge-gate.sh drover-merge-policy.py drover-approval.py drover-env-check.sh drover-settings.py drover-setup.py drover-usage.sh drover-throttle.sh drover-host.sh drover-pr-body.sh drover-review-file-refactor.sh drover-qa-file-bug.sh drover-stop.sh drover-reasons.json drover-commit-style.py"
for script in $SCRIPTS; do
  if [ -f "$REPO_ROOT/scripts/$script" ]; then
    copy_file "$REPO_ROOT/scripts/$script" "$SHARED/bin/$script"
    chmod +x "$SHARED/bin/$script"
  fi
done
WORKFLOWS="drover-wave.js ui-refine-loop.js"
for wf in $WORKFLOWS; do
  if [ -f "$REPO_ROOT/workflows/$wf" ]; then
    copy_file "$REPO_ROOT/workflows/$wf" "$SHARED/workflows/$wf"
  fi
done

HOOK_FILES=""
if [ "$HOOKS" -eq 1 ]; then
  for h in "$REPO_ROOT"/hooks/*.py; do [ -f "$h" ] && HOOK_FILES="$HOOK_FILES $(basename "$h")"; done
elif [ "$PROTECT" -eq 1 ]; then
  HOOK_FILES="guard-protected-push.py"
fi
if [ -n "$HOOK_FILES" ]; then
  mkdir -p "$SHARED/hooks"
  for h in $HOOK_FILES; do
    copy_file "$REPO_ROOT/hooks/$h" "$SHARED/hooks/$h"
    chmod +x "$SHARED/hooks/$h"
  done
fi

GROUPS_="skills bin workflows"
[ -z "$HOOK_FILES" ] || GROUPS_="$GROUPS_ hooks"
LINKED=""
for host in $HOSTS; do
  LINKED="${LINKED:+$LINKED, }.$host"
  for group in $GROUPS_; do
    mkdir -p "$TARGET/.$host/$group"
    rel=$(relpath "$SHARED/$group" "$TARGET/.$host/$group")
    case "$group" in
      skills) for skill in $PRIMARY_SKILLS $SECONDARY_SKILLS; do
                link_entry "$host" skills "$skill" "$REPO_ROOT/skills/$skill" dir "$rel"; done ;;
      bin) for script in $SCRIPTS; do link_entry "$host" bin "$script" "$REPO_ROOT/scripts/$script" file "$rel"; done ;;
      workflows) for wf in $WORKFLOWS; do link_entry "$host" workflows "$wf" "$REPO_ROOT/workflows/$wf" file "$rel"; done ;;
      hooks) for h in $HOOK_FILES; do link_entry "$host" hooks "$h" "$REPO_ROOT/hooks/$h" file "$rel"; done ;;
    esac
  done
done
if [ "$COPIED" -eq 1 ]; then
  echo "   ✓ skills, scripts and workflows → .agents/ (copied into $LINKED: no symlinks here)"
else
  echo "   ✓ skills, scripts and workflows → .agents/ (linked from $LINKED)"
fi

SETTINGS_PY="$REPO_ROOT/scripts/drover-settings.py"
# Stdlib-only merge (scripts/drover-settings.py): keeps every existing key
# and hook, adds each guard command once per event + matcher, backs the file up
# before writing, and leaves an invalid settings.json untouched (exit 2).
# Prints one summary word for the settings line; the helper's own lines (an
# invalid-JSON refusal) go to stderr untouched.
merge_settings() {
  local had=0 res rc=0
  [ -f "$TARGET/.claude/settings.json" ] && had=1
  res=$(python3 "$SETTINGS_PY" hooks "$TARGET/.claude/settings.json" "$@" 2>&1) || rc=$?
  if [ "$rc" -eq 2 ]; then printf '%s\n' "$res" >&2; echo "not valid JSON — left untouched"
  elif echo "$res" | grep -q "already present"; then echo "already present"
  elif [ "$had" -eq 1 ]; then echo "backup kept"
  else echo "new file"; fi
}

if [ "$HOOKS" -eq 1 ]; then
  set -- $HOOK_FILES; n=$#
  SNIPPETS="$REPO_ROOT/hooks/settings-snippet.json"
  if [ "$PROTECT" -eq 1 ]; then SNIPPETS="$SNIPPETS $REPO_ROOT/hooks/settings-protect-main.json"; fi
  # shellcheck disable=SC2086
  echo "   🛡️  $n guard hooks → .claude/settings.json ($(merge_settings $SNIPPETS))"
elif [ "$PROTECT" -eq 1 ]; then
  # --no-hooks --protect-main: the one guard asked for, nothing else. This is the
  # command onboard names when its protect-main question finds no guard script.
  echo "   🛡️  --no-hooks: only the push guard → .claude/settings.json ($(merge_settings "$REPO_ROOT/hooks/settings-protect-main.json"))"
else
  echo "   ⏭️  guard hooks skipped (--no-hooks)"
fi

# Upgrade record for onboard's 🔍 Checks (it migrates config, labels, columns).
if [ -n "$PREV_VERSION" ] && [ "$PREV_VERSION" != "$PACK_VERSION" ]; then
  mkdir -p "$TARGET/.claude/drover"
  python3 - "$TARGET/.claude/drover/upgrade.json" "$PREV_VERSION" "$PACK_VERSION" $ADDED_SKILLS <<'PY'
import json, sys, time
path, frm, to, added = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
try:
    old = json.load(open(path))
except Exception:
    old = {}
rec = {"from": old.get("from", frm), "to": to, "added_skills": sorted(set(old.get("added_skills", []) + added)),
       "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
json.dump(rec, open(path, "w"), indent=2)
PY
  echo "   ⬆️  upgrade from $PREV_VERSION — onboard finishes it (config, labels, columns)"
fi

if [ -z "$QUIET" ]; then
  echo "🎉 drover $PACK_VERSION is installed"
  echo "👉 next: open Claude Code here and run /drover onboard"
fi
