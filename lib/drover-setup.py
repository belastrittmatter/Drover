#!/usr/bin/env python3
"""drover-setup.py — the deterministic half of `/drover onboard` (`drover setup ...`).

Onboard asks the questions; this script finds things out, fixes the must-haves and writes
the config, so the Checks step can say "Fixed for you" without asking anything.

    drover onboard [--from STAGED.json] [--owner O --number N] [--title T] [--base B] [--verify CMD]...
                   [--evidence branch[:NAME]|pr:PATH] [--bot LOGIN] [--merge auto|human]
                   [--harness claude] [--no-hooks]
                                                                       write .drover/config.json, the git
                                                                       excludes and the harness hooks
    drover setup check   [--root DIR] [--pack DIR]            what is missing / old (read-only)
    drover setup fix     [--root DIR] [--pack DIR] [--no-helpers] [--dry-run]
                                                                       apply every must-have, then re-check
    drover setup migrate-config <config.json> [--dry-run]     old config keys → current keys
    drover setup names   [--root DIR]                          two board-name suggestions
    drover setup branch  [--root DIR]                          branches, deploy source, recommendation
    drover setup board-rank    --owner O                       the user's boards, best column match first
    drover setup board-migrate (--config C | --owner O --number N --repo R) [--qa-all] [--prune-empty] [--root DIR] [--dry-run]
                                                                       columns + labels + Skipped → Done

Every command prints one JSON object. `--text` on check/fix prints the short ✓ lists the
onboard screen shows instead.

`onboard` is the only writer of `.drover/config.json`, the one tracked file drover adds to a
project (FR-CFG-1). It keeps every key already set (`--from` adds the wizard's staged keys;
an older `.claude/drover/configs/` config is the starting point when there is none), fills
the starting skill routing
(lib/drover-routing.json), the detected `commit_style`, the evidence location (asked on a
terminal, else `--evidence`, else the drover-evidence branch), validates the result against
config-schema.json and writes nothing when it fails. It then adds `.drover/runs/`,
`.drover/worktrees/`, `.claude/settings.local.json*` and `.claude/drover/` to
.git/info/exclude and merges the harness's guard hooks into its untracked settings file.

What `fix` does, with NO question (all of it backed up first to
.claude/drover/backup/<ts>/):
  - links drover's skills into the harness's user skill folder (`drover skills install`) and
    wires the guard hooks into .claude/settings.local.json when any is missing
  - removes what an older drover copied into the project (.claude/ and .agents/ skills, bin,
    workflows, hooks), its hook entries in .claude/settings.json, and the folders of skills
    that no longer exist: super-refine, cleanup-wt, arch-loop
  - moves an older .claude/drover/configs/<slug>.json (the active or sole one) to
    .drover/config.json and migrates it to the current keys (migrate-config)
  - `git init` when the folder is not a repo
  - adds Matt Pocock's helper skills with npx when Node is present and they are missing
It never installs a system tool or signs in: those come back in `needs` with the exact
command for this OS, for onboard to ask about.

`board-migrate` is the board half (needs `gh` with project scope): adds the missing
Status options (--prune-empty also drops unused extras such as a new board's Todo),
creates the three labels, maps old type labels, labels every card `qa`
on a board that was "qa-only" (--qa-all), moves Skipped cards to Done, removes the Skipped
option, and puts back any card status the option rewrite cleared. Before any write it saves
all cards and conversion intent in .claude/drover/migrations/ (under --root, the config project root, or cwd).
Re-run the same command from the same root to recover an interrupted upgrade. Keep the
board idle during migration: GitHub has no atomic compare-and-swap for field rewrites.
Before the first option rewrite it also saves the original card statuses to
.claude/drover/backup/board-<number>-<ts>.json for manual recovery.
No card is deleted. Detected concurrent edits are preserved or halt recovery.

Exit: 0 ok · 1 check found red items · 2 a gh call failed · 64 usage · 66 pack not found.
Stdlib only.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import datetime as dt
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys

COLUMNS = ["Backlog", "Ready", "Building", "QA", "Review", "Blocked", "Done"]
LABELS = {
    "qa": ("0e8a16", "Test what exists; skips Building (drover)"),
    "bug": ("d73a4a", "Something is broken; built, tested, reviewed (drover)"),
    "feature": ("1d76db", "New behaviour; built, tested, reviewed (drover)"),
}
# Type labels from older packs and common trackers → the three v3 labels.
OLD_LABELS = {
    "build": "feature", "type:feature": "feature", "kind:feature": "feature", "enhancement": "feature",
    "bug-fix": "bug", "bugfix": "bug", "type:bug": "bug", "kind:bug": "bug",
    "qa-only": "qa", "type:qa": "qa", "kind:qa": "qa",
}
SKILLS = ["drover", "drover-build", "drover-qa", "drover-review", "drover-collect", "visual", "git-sync", "ui-refine-loop", "pr-security-review"]
OLD_SKILL_DIRS = ["super-refine", "cleanup-wt", "arch-loop"]
ROUTING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drover-routing.json")
WORKFLOWS = ["drover-wave.js", "ui-refine-loop.js"]
HELPERS_CMD = "npx -y skills@latest add mattpocock/skills --skill '*' -a claude-code -y"
# Cloudflare's standalone whole-repo audit. Optional: installed by default, never
# required by a lane, so a missing copy is a note, not a red check.
SECURITY_AUDIT_CMD = "npx -y skills@latest add cloudflare/security-audit-skill --skill security-audit -a claude-code -y"

# System tools: why the board needs each, and the install command per OS family.
TOOLS = {
    "git": ("branches, worktrees and merges", {"mac": "brew install git", "apt": "sudo apt-get install -y git",
            "dnf": "sudo dnf install -y git", "win": "winget install --id Git.Git -e"}),
    "gh": ("reads and moves cards on GitHub", {"mac": "brew install gh", "apt": "sudo apt-get install -y gh",
           "dnf": "sudo dnf install -y gh", "win": "winget install --id GitHub.cli -e"}),
    "jq": ("the board scripts read JSON with it", {"mac": "brew install jq", "apt": "sudo apt-get install -y jq",
           "dnf": "sudo dnf install -y jq", "win": "winget install --id jqlang.jq -e"}),
    "node": ("the helper installer; actual skill files are checked separately",
             {"mac": "brew install node", "apt": "sudo apt-get install -y nodejs npm",
              "dnf": "sudo dnf install -y nodejs npm", "win": "winget install --id OpenJS.NodeJS.LTS -e"}),
}


# ── small helpers ──────────────────────────────────────────────────────────────

def out(obj, code=0):
    print(json.dumps(obj, indent=1))
    return code


def run(cmd, cwd=None, check=False, input=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, input=input)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def have(tool):
    return shutil.which(tool) is not None


def os_family():
    sysname = platform.system()
    if sysname == "Darwin":
        return "mac"
    if sysname == "Windows" or os.environ.get("OS") == "Windows_NT" or re.match(r"(MINGW|MSYS|CYGWIN)", sysname):
        return "win"
    if have("apt-get"):
        return "apt"
    if have("dnf"):
        return "dnf"
    return "apt"


def vtuple(v):
    try:
        return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)
    except ValueError:
        return (0,)


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def find_pack(explicit=None):
    """The drover install folder (bin/drover, skills/, hooks/). None when not found."""
    for c in [explicit, os.environ.get("DROVER_PACK"), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]:
        if c and os.path.isfile(os.path.join(c, "bin", "drover")) and os.path.isdir(os.path.join(c, "skills", "drover")):
            return os.path.abspath(c)
    return None


def drover_config():
    """lib/drover-config.py: schema validation, harness entries, skill links."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("drover_config", os.path.join(os.path.dirname(os.path.abspath(__file__)), "drover-config.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def helper_readiness(root, host="claude", home=None, catalog=None, config=None):
    command = [sys.executable, os.path.join(os.path.dirname(__file__), "drover-skills-ready.py"),
               "--root", root, "--host", host, "--profile", "setup"]
    if home:
        command += ["--home", home]
    if catalog:
        command += ["--catalog", catalog]
    if config:
        command += ["--config", config]
    process = run(command)
    try:
        result = json.loads(process.stdout)
    except ValueError:
        result = {"ready": False, "entries": [], "error": process.stderr.strip()}
    return result


def security_audit_present(root, home=None):
    """Cloudflare's security-audit (optional, run on demand) counts as installed when
    skills-lock.json lists it or a skill folder holds it."""
    if "cloudflare/security-audit-skill" in read(os.path.join(root, "skills-lock.json")):
        return True
    return any(os.path.isfile(os.path.join(d, ".claude", "skills", "security-audit", "SKILL.md"))
               for d in (root, home or os.path.expanduser("~")))


def is_git_repo(root):
    return have("git") and run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root).returncode == 0


def snippet_commands(pack, snippets=("hooks/settings-snippet.json",)):
    """The hook commands the harness entry installs, with {drover_home} filled in."""
    cmds = []
    for rel in snippets if pack else []:
        snip = load_json(os.path.join(pack, rel)) or {"hooks": {}}
        cmds += [h["command"].replace("{drover_home}", pack) for entries in snip["hooks"].values()
                 for e in entries for h in e.get("hooks", [])]
    return cmds


def settings_commands(path):
    s = load_json(path) or {}
    return {h.get("command") for entries in (s.get("hooks") or {}).values() for e in entries
            for h in e.get("hooks", [])}


# Hook commands an older drover wrote into the tracked settings.json; they point at
# .claude/hooks/ copies that drover no longer makes, so fix removes them.
OLD_HOOK = re.compile(r"\.claude/hooks/(guard-[a-z-]+|cleanup-wt)\.py")


def old_copies(root):
    """drover files an older install copied into the project (now run from the install folder)."""
    claude, agents = os.path.join(root, ".claude"), os.path.join(root, ".agents")
    found = [os.path.join(d, "skills", s) for d in (claude, agents) for s in SKILLS]
    found += glob.glob(os.path.join(claude, "bin", "drover-*")) + glob.glob(os.path.join(agents, "bin", "drover-*"))
    found += [os.path.join(d, "workflows", w) for d in (claude, agents) for w in WORKFLOWS]
    found += [p for d in (claude, agents) for p in glob.glob(os.path.join(d, "hooks", "*.py"))
              if re.fullmatch(r"(guard-[a-z-]+|cleanup-wt)\.py", os.path.basename(p))]
    return sorted(p for p in found if os.path.lexists(p))


def legacy_config(root):
    """The config an older drover kept in .claude/drover/configs/: the active one, else the sole one."""
    base = os.path.join(root, ".claude", "drover")
    configs = sorted(glob.glob(os.path.join(base, "configs", "*.json")))
    slug = read(os.path.join(base, "active"))
    if slug and os.path.join(base, "configs", slug + ".json") in configs:
        return os.path.join(base, "configs", slug + ".json"), configs
    return (configs[0] if len(configs) == 1 else None), configs


# ── config migration ───────────────────────────────────────────────────────────

def machine_tz():
    if os.environ.get("TZ"):
        return os.environ["TZ"]
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


REMOVED_KEYS = ("worker_backend", "codex", "usage_fallback", "truth_gate", "truth_threshold", "calibration")


def migrate_config(cfg, host=None):
    """Return (new config, [human-readable change lines]). Pure; never drops a project setting
    that still means something. `host` (the actual worker harness) fills a missing `harness`
    key; a set one is never replaced. specs/config.md "Keys" lists the renames and removals."""
    c = json.loads(json.dumps(cfg))
    changes = []
    variant = c.pop("variant", None)
    if variant is not None:
        changes.append(f"variant \"{variant}\" removed — labels route cards now"
                       + (" (cards get the qa label)" if variant == "qa-only" else ""))
        if variant == "qa-only":
            c["_qa_all"] = True  # read by board-migrate --qa-all, then dropped
    cols = c.get("columns")
    if cols != COLUMNS:
        extra = [x for x in (cols or []) if x not in COLUMNS and x != "Skipped"]
        c["columns"] = COLUMNS + extra
        if cols and "Skipped" in cols:
            changes.append("Skipped column removed from config")
        missing = [x for x in COLUMNS if x not in (cols or [])]
        if missing:
            changes.append("columns added: " + ", ".join(missing))
    tgt = c.get("target") or {}
    if tgt.get("type") == "url":
        c["target"] = {"type": "repo+url" if c.get("repo") else "repo", "url": tgt.get("url")}
        changes.append("URL-only target → repo board (a live site alone: /drover-qa <url>)")
    col = c.get("collect")
    if isinstance(col, dict) and any(k in col for k in ("errors", "feedback_paths", "lookback_runs")):
        errors = col.pop("errors", None)
        col.pop("feedback_paths", None)
        col.pop("lookback_runs", None)
        if "sources" not in col:
            col["sources"] = (["sentry"] if errors in ("auto", "sentry") else []) + ["github", "prs", "architecture"]
        changes.append("collect: intake/lookback keys → sources " + ", ".join(col["sources"]))
    ref = c.get("refine")
    if isinstance(ref, dict) and "qa_hook_rounds" in ref:
        ref.pop("qa_hook_rounds")
        changes.append("refine.qa_hook_rounds removed (the board no longer runs ui-refine-loop)")
    if "host" in c:
        old = c.pop("host")
        c.setdefault("harness", old)
        changes.append(f"host \"{old}\" renamed harness")
    if host and "harness" not in c:
        c["harness"] = host
        changes.append(f"harness \"{host}\" added")
    # A non-Claude harness leaves the default unset: it merges by hand (#47).
    if "merge_policy" not in c and (c.get("human_approves_merge") or c.get("harness") in (None, "claude")):
        c["merge_policy"] = {"default": "human" if c.get("human_approves_merge") else "auto"}
        changes.append(f"merge rule: merge_policy.default \"{c['merge_policy']['default']}\" (from human_approves_merge)")
    if "migrations" not in c:
        c["migrations"] = {"allowed_envs": ["test", "staging"],
                           "target_env": "live" if c.get("base_branch", "main") in ("main", "master") else "staging"}
        changes.append("migrations: test + staging allowed (defaults)")
    n = c.get("notifications")
    if isinstance(n, dict) and n.get("channel") not in (None, "session"):
        changes.append(f"notifications: \"{n.get('channel')}\" → session (drover sends no messages)")
        n["channel"], n["chat_id"] = "session", None
    if "bot_identity" in c:
        c.setdefault("notifications", {}).setdefault("bot_identity", c["bot_identity"])
        del c["bot_identity"]
        changes.append("bot_identity moved to notifications.bot_identity")
    if not c.get("timezone"):
        c["timezone"] = machine_tz()
        changes.append(f"timezone {c['timezone']} added")
    gone = [k for k in REMOVED_KEYS if k in c]
    for k in gone:
        del c[k]
    if gone:
        changes.append("removed keys: " + ", ".join(gone))
    if "max_workers" in c:
        cap = c.pop("max_workers")
        changes.append("max_workers moved to roles.<role>.max_workers (default 1)")
        if isinstance(cap, int) and cap > 3:
            for role in ("build", "qa", "review"):
                c.setdefault("roles", {}).setdefault(role, {}).setdefault("max_workers", max(1, cap // 3))
    if isinstance(c.get("labels"), list):
        del c["labels"]
        changes.append("labels list replaced by the skill routing map")
    if isinstance(c.get("paths"), dict) and "runs_dir" in c["paths"]:
        del c["paths"]["runs_dir"]
        changes.append("paths.runs_dir removed (fixed run folder)")
    routing = load_json(ROUTING) or {}
    for key in ("roles", "labels"):
        if key not in c:
            c[key] = routing.get(key, {})
            changes.append(f"{key}: starting skill routing added")
        elif key == "roles":
            for role, entry in routing.get("roles", {}).items():
                if "skills" not in c["roles"].setdefault(role, {}):
                    c["roles"][role]["skills"] = entry["skills"]
                    changes.append(f"roles.{role}.skills: starting skill routing added")
    return c, changes


# ── check / fix ────────────────────────────────────────────────────────────────

def installed_version(root):
    return read(os.path.join(root, ".claude", "skills", "drover", "VERSION")) or None


def user_skill_dir(host, home=None):
    folder = drover_config().harness(host)["user_skill_dir"]
    return os.path.join(home, folder[2:]) if home and folder.startswith("~/") else os.path.expanduser(folder)


def config_problems(root, cfg_path, host):
    """Migration changes, verification, harness and schema problems of the project config."""
    changes, verification, host_errors, schema = {}, {}, {}, {}
    if not os.path.isfile(cfg_path):
        return changes, verification, host_errors, schema
    rel = os.path.relpath(cfg_path, root)
    cfg = load_json(cfg_path)
    if not isinstance(cfg, dict):
        host_errors[rel] = "not a JSON object"
        return changes, verification, host_errors, schema
    problems = schema_errors(cfg)
    if problems:
        schema[rel] = problems
    ready = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "drover-verification-ready.py"),
                            "--config", cfg_path], capture_output=True, text=True)
    if ready.returncode:
        verification[rel] = ready.stderr.strip()
    name = cfg.get("harness", cfg.get("host"))
    if name is not None and not os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harnesses", f"{name}.json")):
        host_errors[rel] = f"harness \"{name}\" has no harnesses/{name}.json entry (v1 ships claude)"
    _, ch = migrate_config(cfg, host)
    if ch:
        changes[rel] = ch
    return changes, verification, host_errors, schema


def schema_errors(cfg):
    """'field: message' for each way cfg fails config-schema.json."""
    dc = drover_config()
    return dc.errors(cfg, load_json(str(dc.SCHEMA)))


def check(root, pack, host="claude", home=None, catalog=None):
    claude = os.path.join(root, ".claude")
    cfg_path = os.path.join(root, ".drover", "config.json")
    pack_version = read(os.path.join(pack, "VERSION")) if pack else None
    have_v = installed_version(root)
    upgrade_rec = load_json(os.path.join(claude, "drover", "upgrade.json")) or {}
    skills_dir = user_skill_dir(host if host != "codex" else "claude", home)
    missing = [f"skill {s}" for s in SKILLS if not os.path.isfile(os.path.join(skills_dir, s, "SKILL.md"))]
    entry = drover_config().harness(host if host != "codex" else "claude")
    wired = settings_commands(os.path.join(root, entry["hooks"]["file"]))
    unwired = [c for c in snippet_commands(pack, entry["hooks"]["snippets"]) if c not in wired]
    if unwired:
        missing.append(f"{entry['hooks']['file']}: {len(unwired)} guard entr{'y' if len(unwired) == 1 else 'ies'}")
    old_dirs = [d for d in OLD_SKILL_DIRS if os.path.isdir(os.path.join(claude, "skills", d))]
    copies = old_copies(root)
    old_hooks = [c for c in settings_commands(os.path.join(claude, "settings.json")) if c and OLD_HOOK.search(c)]
    legacy, legacy_all = legacy_config(root)
    configs, verification, host_errors, schema = config_problems(root, cfg_path, host)
    if not os.path.isfile(cfg_path) and legacy_all:
        rel = os.path.relpath(legacy or os.path.dirname(legacy_all[0]), root)
        if legacy:
            configs[rel] = ["config moves to .drover/config.json"] + migrate_config(load_json(legacy) or {}, host)[1]
        else:
            host_errors[rel] = "several configs and no active pointer: move the one you use to .drover/config.json"
    older = bool(upgrade_rec.get("from")) or bool(old_dirs) or bool(copies) or bool(legacy_all) or any(
        "variant" in " ".join(ch) or "Skipped" in " ".join(ch) or "collect:" in " ".join(ch) for ch in configs.values())
    if have_v and pack_version and vtuple(have_v) < vtuple(pack_version):
        older = True
    from_v = upgrade_rec.get("from") or have_v
    fam = os_family()
    needs = []
    for t in ("git", "gh", "jq", "node"):
        if not have(t if t != "node" else "npx"):
            why, cmds = TOOLS[t]
            cmd = cmds[fam]
            if t == "node":
                cmd += " && " + HELPERS_CMD + " && " + SECURITY_AUDIT_CMD
            needs.append({"kind": "tool", "name": t, "why": why, "command": cmd})
    readiness = helper_readiness(root, host, home, catalog, cfg_path if os.path.isfile(cfg_path) else None)
    helpers = readiness["ready"]
    security_audit = security_audit_present(root, home)
    git = is_git_repo(root)
    ok = []
    if not missing:
        ok.append("drover skills linked and safety guards on")
    if git:
        ok.append("git repo found")
    if helpers:
        ok.append("required worker skills resolved for " + host)
    if security_audit:
        ok.append("security-audit skill installed (optional, run on demand)")
    red = bool(missing or old_dirs or copies or old_hooks or configs or verification or host_errors or schema
               or not git or needs or not helpers)
    return {
        "root": root, "pack": pack, "pack_version": pack_version, "installed_version": have_v,
        "upgrade": older, "from": from_v or ("older" if older else None),
        "added_skills": upgrade_rec.get("added_skills", []),
        "missing": missing, "old_dirs": old_dirs, "old_copies": [os.path.relpath(p, root) for p in copies],
        "old_hooks": old_hooks, "configs": configs, "verification": verification,
        "host_errors": host_errors, "schema_errors": schema, "git": git, "skills_dir": skills_dir,
        "helper_skills": helpers, "security_audit": security_audit, "helper_readiness": readiness, "needs": needs, "os": fam, "ok": ok, "green": not red,
    }


def backup(root, paths, stamp):
    base = os.path.join(root, ".claude", "drover", "backup", stamp)
    dest, n = base, 1
    while os.path.exists(dest):  # a second fix in the same second gets its own folder
        n += 1
        dest = f"{base}-{n}"
    os.makedirs(dest)
    for p in paths:
        if not os.path.lexists(p):
            continue
        rel = os.path.relpath(p, root)
        target = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.islink(p):
            os.symlink(os.readlink(p), target)
        else:
            (shutil.copytree if os.path.isdir(p) else shutil.copy2)(p, target)
    return os.path.relpath(dest, root) + "/"


def remove(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.lexists(path):
        os.remove(path)


def install_hooks(root, pack, host):
    """Wire the harness entry's guard hooks into its untracked settings file (FR-HRN-1). The
    push and merge guards are part of the default set; SB_ALLOW_PROTECTED_PUSH=1 or
    --no-hooks opts out."""
    entry = drover_config().harness(host)["hooks"]
    snippets = [os.path.join(pack, s) for s in entry["snippets"]]
    r = run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "drover-settings.py"),
             "hooks", os.path.join(root, entry["file"]), *snippets])
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"{entry['file']}: hook merge failed")
    return entry["file"]


def drop_old_hooks(root):
    """Remove an older drover's .claude/hooks/ commands from the tracked settings.json."""
    path = os.path.join(root, ".claude", "settings.json")
    s = load_json(path)
    if not isinstance(s, dict):
        return 0
    n = 0
    for event, entries in list((s.get("hooks") or {}).items()):
        for e in entries:
            keep = [h for h in e.get("hooks", []) if not OLD_HOOK.search(h.get("command") or "")]
            n += len(e.get("hooks", [])) - len(keep)
            e["hooks"] = keep
        s["hooks"][event] = [e for e in entries if e.get("hooks")]
        if not s["hooks"][event]:
            del s["hooks"][event]
    if n:
        write_json(path, s)
    return n


def fix(root, pack, helpers=True, dry=False, host="claude", home=None, catalog=None):
    before = check(root, pack, host, home, catalog)
    fixed, notes = [], []
    claude = os.path.join(root, ".claude")
    cfg_path = os.path.join(root, ".drover", "config.json")
    legacy, legacy_all = legacy_config(root)
    # The config fix is checked first: a migrated config that fails the schema stops fix
    # before anything is removed, naming the field, and every legacy file stays.
    source = legacy if not os.path.isfile(cfg_path) and legacy else cfg_path if os.path.isfile(cfg_path) else None
    if source and isinstance(load_json(source), dict):
        problems = schema_errors(migrate_config(load_json(source), host)[0])
        if problems:
            return {**before, "fixed": [], "notes": [], "backup": None,
                    "error": f"config not migrated, {os.path.relpath(source, root)} would fail the schema: "
                             + "; ".join(problems) + ". Fix it by hand; nothing was changed."}
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    to_backup = [os.path.join(claude, "settings.json"), os.path.join(claude, "settings.local.json"), cfg_path,
                 os.path.join(claude, "drover", "configs"), os.path.join(claude, "drover", "active")]
    to_backup += [os.path.join(claude, "skills", d) for d in before["old_dirs"]]
    to_backup += [os.path.join(root, p) for p in before["old_copies"]]
    bdir = None
    if not dry and (before["missing"] or before["old_dirs"] or before["old_copies"] or before["old_hooks"]
                    or before["configs"] or before["upgrade"]):
        bdir = backup(root, to_backup, stamp)

    if before["missing"]:
        if not pack:
            return {**before, "fixed": [], "error": "pack-not-found"}
        if any(m.startswith("skill ") for m in before["missing"]):
            if not dry:
                with contextlib.redirect_stdout(io.StringIO()):
                    drover_config().skills_install(before["skills_dir"], host if host != "codex" else "claude")
            fixed.append(f"drover skills linked into {before['skills_dir']}")
        if any(not m.startswith("skill ") for m in before["missing"]):
            if not dry:
                try:
                    f = install_hooks(root, pack, host if host != "codex" else "claude")
                except RuntimeError as e:
                    return {**before, "fixed": fixed, "error": str(e)}
            n = len(snippet_commands(pack))
            fixed.append(f"{n} safety guards switched on ({drover_config().harness('claude')['hooks']['file']})")
    if before["upgrade"]:
        added = before["added_skills"]
        fixed.insert(0, "skills updated" + (f"; added {', '.join(added)}" if added else ""))

    if before["old_dirs"]:
        if not dry:
            for d in before["old_dirs"]:
                shutil.rmtree(os.path.join(claude, "skills", d), ignore_errors=True)
        fixed.append("removed old folders: " + ", ".join(before["old_dirs"]))
    if before["old_copies"]:
        if not dry:
            for p in before["old_copies"]:
                remove(os.path.join(root, p))
        fixed.append(f"removed {len(before['old_copies'])} files an older drover copied into the project")
    if before["old_hooks"]:
        n = drop_old_hooks(root) if not dry else len(before["old_hooks"])
        fixed.append(f"removed {n} old hook entr{'y' if n == 1 else 'ies'} from .claude/settings.json")

    if not os.path.isfile(cfg_path) and legacy:
        new, ch = migrate_config(load_json(legacy) or {}, host)
        if not dry:
            os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
            write_json(cfg_path, new)
            for p in legacy_all:
                os.remove(p)
            remove(os.path.join(claude, "drover", "active"))
        fixed.append("config moved to .drover/config.json (from " + os.path.relpath(legacy, root) + ")")
        notes += ch
    elif os.path.isfile(cfg_path) and before["configs"]:
        new, ch = migrate_config(load_json(cfg_path), host)
        if not dry:
            write_json(cfg_path, new)
        fixed.append(f"config moved to the new keys ({len(ch)} change{'s' if len(ch) != 1 else ''})"
                     if before["upgrade"] else "config repaired: .drover/config.json")
        notes += ch

    if not before["git"] and have("git"):
        if not dry:
            run(["git", "init", "-q"], cwd=root)
        fixed.append("git repo created (git init)")

    if helpers and not before["helper_skills"] and have("npx"):
        if not dry:
            run(["bash", "-c", HELPERS_CMD], cwd=root, input="")
            confirmed = helper_readiness(root, host, home, catalog, cfg_path if os.path.isfile(cfg_path) else None)
            if confirmed["ready"]:
                fixed.append("worker skills re-resolved after helper installation")
            else:
                notes.append("helper install attempted; required skills remain unavailable: " +
                             ", ".join(e["name"] for e in confirmed.get("entries", []) if e["status"] == "missing"))
        else:
            notes.append("would attempt helper installation, then re-resolve actual host-visible files")

    if helpers and not before["security_audit"] and have("npx"):
        if not dry:
            run(["bash", "-c", SECURITY_AUDIT_CMD], cwd=root, input="")
            if security_audit_present(root, home):
                fixed.append("security-audit skill installed (Cloudflare, optional)")
            else:
                notes.append("security-audit install attempted and not found; optional, retry: " + SECURITY_AUDIT_CMD)
        else:
            notes.append("would install the optional security-audit skill")

    if not dry:
        up = os.path.join(claude, "drover", "upgrade.json")
        if os.path.exists(up):
            os.remove(up)
    after = check(root, pack, host, home, catalog) if not dry else before
    return {**after, "upgraded": before["upgrade"], "from": before["from"], "fixed": fixed,
            "notes": notes, "backup": bdir}


# ── onboard: write .drover/config.json ─────────────────────────────────────────

EXCLUDES = (".drover/runs/", ".drover/worktrees/", ".claude/settings.local.json*", ".claude/drover/")


def git_exclude(root, lines=EXCLUDES):
    """Add run folders and the untracked settings file to .git/info/exclude (FR-CFG-1), once each."""
    common = run(["git", "rev-parse", "--git-common-dir"], cwd=root).stdout.strip()
    path = os.path.join(root, common, "info", "exclude")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    have_lines = read(path).splitlines()
    new = [l for l in lines if l not in have_lines]
    if new:
        with open(path, "a") as f:
            f.write(("" if not have_lines or read(path).endswith("\n") else "\n") + "\n".join(new) + "\n")
    return new


def parse_evidence(value):
    """`branch`, `branch:<name>` or `pr:<path>` (FR-EVD-1)."""
    mode, _, rest = (value or "").partition(":")
    if mode == "branch":
        return {"mode": "branch", "branch": rest or "drover-evidence"}
    if mode == "pr" and rest:
        return {"mode": "pr", "path": rest}
    raise ValueError("--evidence must be branch, branch:<name> or pr:<path>")


def ask_evidence():
    print("Where should QA evidence (screenshots, logs) go?\n"
          "  1  an orphan branch, drover-evidence (Recommended: the PR diff stays clean)\n"
          "  2  the PR branch, under a folder you name", file=sys.stderr)
    choice = input("evidence [1]: ").strip() or "1"
    if choice in ("1", "branch"):
        name = input("branch [drover-evidence]: ").strip()
        return {"mode": "branch", "branch": name or "drover-evidence"}
    path = input("folder on the PR branch [docs/drover/evidence]: ").strip()
    return {"mode": "pr", "path": path or "docs/drover/evidence"}


def onboard(root, rest, pack):
    """Write .drover/config.json (the only tracked file drover adds), the git excludes and the
    harness hooks. Re-running keeps every key already set; flags override. Nothing is written
    when the result fails the schema."""
    if not is_git_repo(root):
        raise ValueError(f"{root} is not a git repository: run git init first")
    cfg_path = os.path.join(root, ".drover", "config.json")
    if os.path.isfile(cfg_path):
        base = load_json(cfg_path)
        if not isinstance(base, dict):
            raise ValueError(f"{cfg_path} is not a JSON object; fix or remove it, then re-run")
    else:
        legacy, _ = legacy_config(root)
        base = load_json(legacy) if legacy else {}
    if flag(rest, "--from"):  # the wizard's staged answers: their top-level keys win
        staged = load_json(flag(rest, "--from"))
        if not isinstance(staged, dict):
            raise ValueError(f"--from {flag(rest, '--from')} is not a JSON object")
        base = {**(base or {}), **staged}
    cfg, _ = migrate_config(base or {}, flag(rest, "--harness", "claude"))
    owner, number = flag(rest, "--owner"), flag(rest, "--number")
    if owner or number:
        cfg["project"] = {**(cfg.get("project") or {}), **({"owner": owner} if owner else {}),
                          **({"number": int(number)} if number else {})}
    if flag(rest, "--title"):
        cfg.setdefault("project", {})["title"] = flag(rest, "--title")
    if not (cfg.get("project") or {}).get("owner") or not (cfg.get("project") or {}).get("number"):
        raise ValueError("the board is unknown: pass --owner <github-owner> --number <project-number>")
    cfg["base_branch"] = flag(rest, "--base") or cfg.get("base_branch") or branch(root).get("default") or "main"
    verify = [rest[i + 1] for i, a in enumerate(rest[:-1]) if a == "--verify"]
    if verify:
        cfg["verify_commands"] = verify
    cfg.setdefault("verify_commands", [])
    if flag(rest, "--bot"):
        cfg.setdefault("notifications", {})["bot_identity"] = flag(rest, "--bot")
    if flag(rest, "--merge"):
        cfg.setdefault("merge_policy", {})["default"] = flag(rest, "--merge")
    if "commit_style" not in cfg or "--base" in rest:
        r = run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "drover-commit-style.py"),
                 "--root", root, "--base", cfg["base_branch"]])
        cfg["commit_style"] = (json.loads(r.stdout or "{}") if r.returncode == 0 else {}).get("commit_style", "conventional")
    if flag(rest, "--evidence"):
        cfg["evidence"] = parse_evidence(flag(rest, "--evidence"))
    elif "evidence" not in cfg:
        cfg["evidence"] = ask_evidence() if sys.stdin.isatty() else {"mode": "branch", "branch": "drover-evidence"}
    problems = drover_config().errors(cfg, load_json(drover_config().SCHEMA))
    if problems:
        raise ValueError("config not written, it fails the schema: " + "; ".join(problems))
    first = ("project", "harness", "base_branch", "verify_commands", "commit_style", "evidence", "roles", "labels")
    cfg = {**{k: cfg[k] for k in first if k in cfg}, **cfg}
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    write_json(cfg_path, cfg)
    excluded = git_exclude(root)
    hooks = None if "--no-hooks" in rest else install_hooks(root, pack, cfg.get("harness", "claude"))
    ready = run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "drover-verification-ready.py"),
                 "--config", cfg_path])
    return {"config": os.path.relpath(cfg_path, root), "excluded": excluded, "hooks": hooks,
            "evidence": cfg["evidence"], "commit_style": cfg["commit_style"],
            "verify_ready": ready.returncode == 0, "verify_error": ready.stderr.strip() or None}


def text_report(res):
    lines = []
    if res.get("upgraded"):
        lines.append(f"Upgraded for you (backup: {res.get('backup') or '—'})")
    else:
        lines.append("Fixed for you:" if res.get("fixed") else "Checks:")
    done = res.get("fixed", []) + [o for o in res.get("ok", []) if not res.get("upgraded")
                                    and not any(o.split()[0] in f for f in res.get("fixed", []))]
    for f in done:
        lines.append(f"  ✓ {f}")
    if res.get("green"):
        lines.append("  ✓ re-checked: all green")
    elif "fixed" not in res:
        red = res.get("missing", [])[:3] + [f"old folder {d}" for d in res.get("old_dirs", [])] + \
            ([f"{len(res['old_copies'])} files an older drover copied into the project"] if res.get("old_copies") else []) + \
            ([".claude/settings.json has old drover hook entries"] if res.get("old_hooks") else []) + \
            [f"config {c} uses old keys" for c in res.get("configs", {})] + ([] if res.get("git") else ["not a git repo"])
        lines += [f"  ✗ {r}" for r in red]
        if red:
            lines.append("  → fix: drover setup fix")
    for entry in res.get("helper_readiness", {}).get("entries", []):
        if entry["status"] == "missing":
            lines.append(f"  ✗ missing {entry['name']} for {res['helper_readiness']['host']}; install it in that host and restart")
    if res.get("helper_readiness", {}).get("error"):
        lines.append("  ✗ " + res["helper_readiness"]["error"])
    for path, error in {**res.get("verification", {}), **res.get("host_errors", {})}.items():
        lines.append(f"  ✗ {path}: {error}")
    for path, errors in res.get("schema_errors", {}).items():
        lines += [f"  ✗ {path}: {e}" for e in errors]
    if res.get("error"):
        lines.append(f"  ✗ {res['error']}")
    for n in res.get("needs", []):
        lines.append(f"  Needs your machine: {n['name']} isn't installed. It's needed for {n['why']}.")
        lines.append(f"    {n['command']}")
    return "\n".join(lines)


# ── board names and branches ──────────────────────────────────────────────────

def names(root):
    pkg = load_json(os.path.join(root, "package.json")) or {}
    base = (pkg.get("name") or "").split("/")[-1]
    if not base:
        m = re.search(r'^name\s*=\s*"([^"]+)"', read(os.path.join(root, "pyproject.toml")), re.M)
        base = m.group(1) if m else ""
    readme_title = ""
    for f in ("README.md", "readme.md", "README"):
        m = re.search(r"^#\s+(.+)$", read(os.path.join(root, f)), re.M)
        if m:
            readme_title = re.split(r"\s+[—–-]\s+|:\s", m.group(1).strip())[0].strip()
            break
    folder = os.path.basename(os.path.abspath(root))
    cap = lambda s: s[:1].upper() + s[1:]
    first = cap(base or readme_title or folder)
    second = cap(readme_title or base or folder)
    empty = not base and not readme_title and not os.listdir(root) if os.path.isdir(root) else True
    if empty:
        return {"names": [f"{folder} board", "My first board"], "from": "folder"}
    n1, n2 = f"{first} board", f"{second} build board"
    return {"names": [n1, n2], "from": "package.json" if base else ("README" if readme_title else "folder")}


def branch(root):
    git = lambda *a: run(["git", *a], cwd=root)
    if not is_git_repo(root):
        return {"git": False, "branches": [], "staging": False, "deploy": None,
                "recommend": "main", "create_staging": False}
    local = [b for b in git("branch", "--format=%(refname:short)").stdout.split() if b]
    remote = [b.split("/", 1)[1] for b in git("branch", "-r", "--format=%(refname:short)").stdout.split()
              if "/" in b and not b.endswith("/HEAD")]
    allb = sorted(set(local + remote))
    head = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip().split("/", 1)[-1]
    default = head or ("main" if "main" in allb else ("master" if "master" in allb else (local[0] if local else "main")))
    staging = next((b for b in ("staging", "develop", "dev") if b in allb), None)
    deploy = None
    if os.path.exists(os.path.join(root, "vercel.json")) or os.path.isdir(os.path.join(root, ".vercel")):
        deploy = f"Vercel deploys {default}"
    elif os.path.exists(os.path.join(root, "netlify.toml")):
        deploy = f"Netlify deploys {default}"
    else:
        for wf in glob.glob(os.path.join(root, ".github", "workflows", "*.y*ml")):
            body = read(wf)
            if re.search(r"deploy|vercel|netlify|fly|render|heroku|pages", body, re.I) and re.search(
                    rf"branches:\s*\[?\s*['\"]?{re.escape(default)}\b", body):
                deploy = f"{os.path.basename(wf)} deploys {default}"
                break
    return {"git": True, "branches": [b for b in allb if not b.startswith(("issue-", "feat/", "fix/"))][:8],
            "default": default, "staging": staging, "deploy": deploy,
            "recommend": staging or "staging", "create_staging": staging is None,
            "create_command": None if staging else f"git push origin {default}:staging"}


# ── GitHub board: rank and migrate ────────────────────────────────────────────

def gh_json(*args, input=None):
    r = run(["gh", *args], input=input)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {(r.stderr or r.stdout).strip()[:300]}")
    return json.loads(r.stdout or "{}")


def status_field(owner, number):
    fields = gh_json("project", "field-list", str(number), "--owner", owner, "--format", "json").get("fields", [])
    return next((f for f in fields if f.get("name") == "Status"), None)


def board_rank(owner):
    projects = gh_json("project", "list", "--owner", owner, "--format", "json", "--limit", "50").get("projects", [])
    ranked = []
    for p in projects:
        if p.get("closed"):
            continue
        f = status_field(owner, p["number"]) or {}
        have = [o["name"] for o in f.get("options", [])]
        low = {h.lower() for h in have}
        match = [c for c in COLUMNS if c.lower() in low]
        ranked.append({"number": p["number"], "title": p.get("title"), "url": p.get("url"),
                       "cards": (p.get("items") or {}).get("totalCount", 0),
                       "matches": len(match), "missing": [c for c in COLUMNS if c not in match],
                       "extra": [h for h in have if h.lower() not in {c.lower() for c in COLUMNS}]})
    ranked.sort(key=lambda r: (-r["matches"], -r["cards"], -r["number"]))
    best = ranked[0] if ranked and ranked[0]["matches"] >= 4 else None
    return {"owner": owner, "boards": ranked, "recommend": best and best["number"]}


OPTIONS_Q = "query($id:ID!){node(id:$id){... on ProjectV2SingleSelectField{options{id name color description}}}}"
UPDATE_M = ("mutation($id:ID!,$opts:[ProjectV2SingleSelectFieldOptionInput!]!){updateProjectV2Field(input:"
            "{fieldId:$id,singleSelectOptions:$opts}){projectV2Field{... on ProjectV2SingleSelectField{options{id name}}}}}")
COLORS = {"Backlog": "GRAY", "Ready": "BLUE", "Building": "YELLOW", "QA": "ORANGE", "Review": "PURPLE",
          "Blocked": "RED", "Done": "GREEN"}


def gql(query, variables):
    return gh_json("api", "graphql", "--input", "-", input=json.dumps({"query": query, "variables": variables}))


def item_labels(it):
    raw = (it.get("labels") or []) + ((it.get("content") or {}).get("labels") or [])
    return {(x.get("name") if isinstance(x, dict) else str(x)).lower() for x in raw}


# Explicit cursors keep the recovery snapshot complete on boards of any size.
ITEMS_Q = '''query($id:ID!,$after:String){node(id:$id){... on ProjectV2{
items(first:100,after:$after){totalCount pageInfo{hasNextPage endCursor} nodes{
id updatedAt type status:fieldValueByName(name:"Status"){
... on ProjectV2ItemFieldSingleSelectValue{name optionId}}
content{__typename ... on Issue{id number repository{nameWithOwner}
labels(first:100){totalCount pageInfo{hasNextPage endCursor} nodes{name}}}}}}}}}'''
ITEM_Q = '''query($id:ID!){node(id:$id){... on ProjectV2Item{
id updatedAt type status:fieldValueByName(name:"Status"){
... on ProjectV2ItemFieldSingleSelectValue{name optionId}}}}}'''
LABELS_Q = '''query($id:ID!,$after:String){node(id:$id){... on Issue{
labels(first:100,after:$after){totalCount pageInfo{hasNextPage endCursor} nodes{name}}}}}'''


def node_query(query, variables):
    result = gql(query, variables)
    if not isinstance(result, dict) or result.get('errors'):
        raise RuntimeError('GitHub returned an incomplete GraphQL response')
    node = (result.get('data') or {}).get('node')
    if not isinstance(node, dict):
        raise RuntimeError('GitHub did not return the requested board, field or item')
    return node


def connection_page(connection, seen, expected=None):
    if not isinstance(connection, dict):
        raise RuntimeError('GitHub omitted a required page')
    total, page, nodes = connection.get('totalCount'), connection.get('pageInfo'), connection.get('nodes')
    if (type(total) is not int or total < 0 or not isinstance(nodes, list) or
            not isinstance(page, dict) or type(page.get('hasNextPage')) is not bool or
            (expected is not None and total != expected)):
        raise RuntimeError('GitHub returned an incomplete or changing page count')
    cursor = page.get('endCursor') if page['hasNextPage'] else None
    if page['hasNextPage'] and (not isinstance(cursor, str) or not cursor or cursor in seen or not nodes):
        raise RuntimeError('GitHub pagination did not advance')
    return nodes, total, cursor


def issue_labels(issue_id, first=None):
    labels, seen, total, cursor = [], set(), None, None
    while True:
        conn = first if first is not None else node_query(LABELS_Q, {'id': issue_id, 'after': cursor}).get('labels')
        first = None
        nodes, total, cursor = connection_page(conn, seen, total)
        for label in nodes:
            if not isinstance(label, dict) or not isinstance(label.get('name'), str):
                raise RuntimeError('GitHub omitted an issue label')
            labels.append(label['name'])
        if cursor is None:
            break
        seen.add(cursor)
    if len(labels) != total or len(set(labels)) != total:
        raise RuntimeError('GitHub returned incomplete or duplicate issue labels')
    return sorted(labels)


def item_state(node):
    if (not isinstance(node, dict) or not isinstance(node.get('id'), str) or
            not isinstance(node.get('updatedAt'), str) or 'status' not in node):
        raise RuntimeError('GitHub returned an incomplete board item')
    status = node['status']
    if status is not None and (not isinstance(status, dict) or not isinstance(status.get('name'), str)
                               or not isinstance(status.get('optionId'), str)):
        raise RuntimeError('GitHub omitted the item Status value')
    return {'id': node['id'], 'status': status and status['name'], 'updatedAt': node['updatedAt']}


def board_items(pid):
    items, seen, total, cursor = [], set(), None, None
    while True:
        conn = node_query(ITEMS_Q, {'id': pid, 'after': cursor}).get('items')
        nodes, total, cursor = connection_page(conn, seen, total)
        for node in nodes:
            item = item_state(node)
            content = node.get('content')
            types = {'ISSUE': 'Issue', 'PULL_REQUEST': 'PullRequest', 'DRAFT_ISSUE': 'DraftIssue', 'REDACTED': None}
            kind = node.get('type')
            if kind not in types or 'content' not in node or (
                    kind == 'REDACTED' and content is not None) or (
                    kind != 'REDACTED' and (not isinstance(content, dict) or content.get('__typename') != types[kind])):
                raise RuntimeError('GitHub omitted the board item type or content; cannot safely migrate its labels')
            if isinstance(content, dict) and content.get('__typename') == 'Issue':
                repo = (content.get('repository') or {}).get('nameWithOwner')
                if not content.get('id') or type(content.get('number')) is not int or not repo:
                    raise RuntimeError('GitHub omitted the issue identity')
                item['issue'] = {'id': content['id'], 'number': content['number'], 'repo': repo,
                                 'labels': issue_labels(content['id'], content.get('labels'))}
            items.append(item)
        if cursor is None:
            break
        seen.add(cursor)
    if len(items) != total or len({it['id'] for it in items}) != total:
        raise RuntimeError('GitHub returned incomplete or duplicate board items')
    return items


def field_options(fid):
    opts = node_query(OPTIONS_Q, {'id': fid}).get('options')
    if not isinstance(opts, list) or not opts or any(
            not isinstance(o, dict) or any(not isinstance(o.get(k), str) for k in ('id', 'name', 'color', 'description'))
            for o in opts):
        raise RuntimeError('GitHub returned incomplete Status options')
    if len({o['name'].lower() for o in opts}) != len(opts) or len({o['id'] for o in opts}) != len(opts):
        raise RuntimeError('GitHub returned ambiguous Status options')
    return opts


def option_values(opts):
    return [{k: o[k] for k in ('name', 'color', 'description')} for o in opts]


def recovery_digest(saved):
    payload = {k: v for k, v in saved.items() if k != 'checksum'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def save_recovery(path, saved):
    saved['checksum'] = recovery_digest(saved)
    durable_json(path, saved)


def durable_json(path, data):
    """Commit and read back each checkpoint before allowing the next remote write."""
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    temp = path + '.tmp'
    with open(temp, 'w') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    if os.name != 'nt':
        fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    if load_json(path) != data:
        raise RuntimeError('Could not verify the saved upgrade recovery record')


@contextlib.contextmanager
def migration_lock(path):
    """OS-released lock: a crashed upgrade never leaves a stale lock blocking recovery."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    # Upgrades of existing installations need this before onboard reaches its ignore-file step.
    try:
        with open(os.path.join(directory, '.gitignore'), 'x') as ignore:
            ignore.write('*\n')
    except FileExistsError:
        pass
    with open(path + '.lock', 'a+b') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                stream.write(b'\0'); stream.flush(); stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Another upgrade is using this board recovery record') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def board_migrate(owner, number, repo, qa_all=False, dry=False, prune_empty=False, root=None, config_path=None):
    """Save first, reconcile each write, resume an interrupted upgrade without re-snapshotting."""
    proj = gh_json('project', 'view', str(number), '--owner', owner, '--format', 'json')
    if not isinstance(proj, dict) or not isinstance(proj.get('id'), str) or not proj.get('url'):
        raise RuntimeError('GitHub omitted the project identity')
    pid = proj['id']
    path = os.path.join(root or os.getcwd(), '.claude', 'drover', 'migrations',
                        hashlib.sha256(pid.encode()).hexdigest()[:24] + '.json')
    binding = {'project': pid, 'owner': owner, 'number': number, 'repo': repo}
    if dry:
        return prepare_migration(binding, proj['url'], qa_all, prune_empty)['result']
    try:
        with migration_lock(path):
            saved = load_json(path)
            if os.path.exists(path) and not isinstance(saved, dict):
                raise RuntimeError('Recovery record is unreadable; restore it from backup before retrying')
            if os.path.exists(path) and saved.get('checksum') != recovery_digest(saved):
                raise RuntimeError('Recovery record is damaged; restore it from backup before retrying')
            if saved and not saved.get('complete'):
                if saved.get('version') != 1 or saved.get('binding') != binding:
                    raise RuntimeError('Recovery record does not match this board/repository')
            else:
                saved = prepare_migration(binding, proj['url'], qa_all, prune_empty)
                save_recovery(path, saved)  # no GitHub mutation has occurred
            saved['result']['recovery_file'] = path
            resume_migration(saved, path)
            if config_path:
                current = load_json(config_path)
                if not isinstance(current, dict):
                    raise RuntimeError('Config is unreadable; keeping the upgrade pending')
                configured = current.get('project') or {}
                if configured.get('owner') != owner or int(configured.get('number', 0)) != number:
                    raise RuntimeError('Board config changed during upgrade; keeping the recovery record')
                if current.pop('_qa_all', None) is not None:
                    durable_json(os.path.abspath(config_path), current)
            saved['complete'] = True
            save_recovery(path, saved)
            return saved['result']
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise RuntimeError(f'{exc}. Upgrade incomplete; keep {path}, fix the error and re-run the same board-migrate command. '
                           'Do not run the board until recovery succeeds.') from exc


def prepare_migration(binding, url, qa_all, prune_empty):
    field = status_field(binding['owner'], binding['number'])
    if not field or not field.get('id'):
        raise RuntimeError('project has no Status field')
    opts = field_options(field['id'])
    items = board_items(binding['project'])
    names = {o['name'].lower(): o for o in opts}
    in_use = {it['status'] for it in items}
    if any(s and s.lower() not in names for s in in_use):
        raise RuntimeError('Board Status values changed while taking the snapshot')
    desired = []
    for c in COLUMNS:
        desired.append(option_values([names[c.lower()]])[0] if c.lower() in names else
                       {'name': c, 'color': COLORS[c], 'description': ''})
    desired += option_values([o for o in opts if o['name'].lower() not in {c.lower() for c in COLUMNS}
                              and o['name'].lower() != 'skipped' and (not prune_empty or o['name'] in in_use)])
    result = {'url': url, 'added_columns': [c for c in COLUMNS if c.lower() not in names],
              'labels_created': [], 'labels_mapped': 0, 'qa_labelled': 0,
              'skipped_moved': 0, 'skipped_removed': 'skipped' in names, 'restored': 0,
              'preserved_edits': [], 'removed_items': [], 'status_backup': None}
    for item in items:
        item['want'] = 'Done' if (item['status'] or '').lower() == 'skipped' else item['status']
        result['skipped_moved'] += int(item['want'] != item['status'])
        issue = item.get('issue')
        if not issue:
            if qa_all and item['status'] not in ('Done', 'Skipped'):
                raise RuntimeError('QA-only conversion needs an accessible issue for every active card')
            continue
        before = issue['labels']
        after = set(before)
        for label in before:
            if label.lower() in OLD_LABELS:
                after.discard(label)
                after.add(OLD_LABELS[label.lower()])
                result['labels_mapped'] += 1
        if qa_all and item['status'] not in ('Done', 'Skipped') and not {n.lower() for n in after} & set(LABELS):
            after.add('qa')
            result['qa_labelled'] += 1
        issue['want'] = sorted(after)
        if before != issue['want'] and issue['repo'].lower() != binding['repo'].lower():
            raise RuntimeError('Board has an issue from another repository needing label conversion; migrate it in its own repository first')
    labels = repository_labels(binding['repo'])
    result['labels_created'] = [n for n in LABELS if n not in {s.lower() for s in labels}]
    return {'version': 1, 'qa_all': qa_all, 'prune_empty': prune_empty, 'binding': binding, 'field': field['id'], 'options': opts, 'desired': desired,
            'items': items, 'result': result, 'options_started': False, 'options_done': False,
            'labels_done': [], 'statuses_done': [], 'complete': False}


def repository_labels(repo):
    # --slurp yields every page separately, so a capped CLI list cannot hide a label.
    pages = gh_json('api', f'repos/{repo}/labels?per_page=100', '--paginate', '--slurp')
    if not isinstance(pages, list) or not pages or any(not isinstance(p, list) for p in pages):
        raise RuntimeError('GitHub returned incomplete repository labels')
    labels = [label for page in pages for label in page]
    if any(not isinstance(l, dict) or not isinstance(l.get('name'), str) for l in labels):
        raise RuntimeError('GitHub omitted a repository label')
    return [l['name'] for l in labels]


def resume_migration(saved, path):
    repo, pid, fid = saved['binding']['repo'], saved['binding']['project'], saved['field']
    checkpoint = lambda: save_recovery(path, saved)
    # Reconcile first; a lost response never causes a blind create/edit replay.
    for name, (color, desc) in LABELS.items():
        if name not in {s.lower() for s in repository_labels(repo)}:
            run(['gh', 'label', 'create', name, '--repo', repo, '--color', color, '--description', desc])
            if name not in {s.lower() for s in repository_labels(repo)}:
                raise RuntimeError(f'Could not verify creation of label {name}')
    for item in saved['items']:
        issue = item.get('issue')
        if not issue or issue['labels'] == issue['want']:
            continue
        current = issue_labels(issue['id'])
        if item['id'] in saved['labels_done']:
            if current != issue['want']:
                raise RuntimeError('Issue labels changed after conversion; review the saved recovery record')
            continue
        if current == issue['want']:
            saved['labels_done'].append(item['id']); checkpoint(); continue
        if current != issue['labels']:
            raise RuntimeError('Issue labels changed during upgrade; preserving the edit and stopping')
        checkpoint()  # persist the original and desired labels before the mutation
        args = ['gh', 'issue', 'edit', str(issue['number']), '--repo', issue['repo']]
        added, removed = set(issue['want']) - set(current), set(current) - set(issue['want'])
        if added:
            args += ['--add-label', ','.join(sorted(added))]
        if removed:
            args += ['--remove-label', ','.join(sorted(removed))]
        run(args)
        if issue_labels(issue['id']) != issue['want']:
            raise RuntimeError('Could not verify issue label conversion')
        saved['labels_done'].append(item['id']); checkpoint()

    opts = field_options(fid)
    rewrite = option_values(saved['options']) != saved['desired']
    if not saved['options_done']:
        if saved['options_started'] and option_values(opts) == saved['desired']:
            saved['options_done'] = True; checkpoint()
        else:
            if opts != saved['options']:
                raise RuntimeError('Status options changed during upgrade; preserving the edit and stopping')
            now = board_items(pid)
            signature = lambda items: sorted((i['id'], i['status'], i['updatedAt']) for i in items)
            if signature(now) != signature(saved['items']):
                raise RuntimeError('Board cards changed before the Status rewrite; preserving edits and stopping')
            if rewrite:
                if not saved['result'].get('status_backup'):
                    # Keep the original manual backup alongside the resumable recovery record.
                    directory = os.path.join(os.path.dirname(os.path.dirname(path)), 'backup')
                    backup = os.path.join(directory, f"board-{saved['binding']['number']}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json")
                    durable_json(backup, {item['id']: item['status'] for item in saved['items']})
                    saved['result']['status_backup'] = backup
                saved['options_started'] = True; checkpoint()
                try:
                    gql(UPDATE_M, {'id': fid, 'opts': saved['desired']})
                except (RuntimeError, ValueError):
                    pass  # the server may have applied it; only read-back settles this
                opts = field_options(fid)
                if option_values(opts) != saved['desired']:
                    raise RuntimeError('Could not verify the Status options rewrite')
            saved['options_done'] = True; checkpoint()
    if option_values(opts) != saved['desired']:
        raise RuntimeError('Status options changed after the rewrite; recovery will not overwrite them')
    ids = {o['name'].lower(): o['id'] for o in opts}
    # One complete read also distinguishes removed cards from unavailable partial responses.
    now = {it['id']: it for it in board_items(pid)}
    for item in saved['items']:
        key, want = item['id'], item['want']
        if key in saved['statuses_done']:
            continue  # later user edits, including clearing Status, belong to the user
        if key not in now:
            saved['result']['removed_items'].append(key)
            saved['statuses_done'].append(key); checkpoint(); continue
        current = item_state(node_query(ITEM_Q, {'id': key}))
        if current['status'] == want or want is None:
            saved['statuses_done'].append(key); checkpoint(); continue
        if current['status'] is not None and not (current['status'] == item['status'] and (current['status'] or '').lower() == 'skipped'):
            saved['result']['preserved_edits'].append(key)
            saved['statuses_done'].append(key); checkpoint(); continue
        pending = saved.get('pending_status')
        if pending and pending['id'] == key and current != pending:
            raise RuntimeError('An unfinished status restore conflicts with a newer edit; review it before resuming')
        saved['pending_status'] = current; checkpoint()
        run(['gh', 'project', 'item-edit', '--id', key, '--project-id', pid, '--field-id', fid,
             '--single-select-option-id', ids[want.lower()]])
        if item_state(node_query(ITEM_Q, {'id': key}))['status'] != want:
            raise RuntimeError('Could not verify a restored task status')
        saved['result']['restored'] += 1
        saved['statuses_done'].append(key)
        saved.pop('pending_status', None); checkpoint()
    # The upgrade is not complete until labels and columns still read back correctly.
    if option_values(field_options(fid)) != saved['desired']:
        raise RuntimeError('Status options changed before upgrade completion')
    for item in saved['items']:
        issue = item.get('issue')
        if issue and issue['labels'] != issue['want'] and issue_labels(issue['id']) != issue['want']:
            raise RuntimeError('Issue labels changed before upgrade completion')


# ── CLI ────────────────────────────────────────────────────────────────────────

def flag(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 64
    cmd, rest = argv[0], argv[1:]
    root = os.path.abspath(flag(rest, "--root", os.getcwd()))
    dry = "--dry-run" in rest
    try:
        if cmd in ("check", "fix"):
            pack = find_pack(flag(rest, "--pack"))
            host, home, catalog = flag(rest, "--host", "claude"), flag(rest, "--home", os.environ.get("DROVER_SKILLS_HOME")), flag(rest, "--catalog", os.environ.get("DROVER_SKILLS_CATALOG"))
            if host not in ("claude", "codex"):
                raise ValueError("--host must be claude or codex")
            res = check(root, pack, host, home, catalog) if cmd == "check" else fix(root, pack, "--no-helpers" not in rest, dry, host, home, catalog)
            if res.get("error") == "pack-not-found":
                print(json.dumps(res, indent=1))
                return 66
            if "--text" in rest:
                print(text_report(res))
                return 0 if res["green"] else 1
            return out(res, 0 if res["green"] else 1)
        if cmd == "onboard":
            pack = find_pack(flag(rest, "--pack"))
            if not pack:
                return out({"error": "pack-not-found"}, 66)
            return out(onboard(root, rest, pack))
        if cmd == "migrate-config":
            path = rest[0] if rest and not rest[0].startswith("-") else None
            if not path:
                return 64
            new, changes = migrate_config(load_json(path) or {})
            if not dry and changes:
                write_json(path, new)
            return out({"config": path, "changes": changes, "written": bool(changes and not dry)})
        if cmd == "names":
            return out(names(root))
        if cmd == "branch":
            return out(branch(root))
        if cmd == "board-rank":
            owner = flag(rest, "--owner")
            if not owner:
                return 64
            return out(board_rank(owner))
        if cmd == "board-migrate":
            # --config fills owner / number / repo and the qa-all flag an upgraded
            # "qa-only" config left behind (`_qa_all`, removed once applied).
            cfg_path = flag(rest, "--config")
            if cfg_path and "--root" not in rest:
                config_dir = os.path.dirname(os.path.abspath(cfg_path))
                suffix = '.drover'
                root = config_dir[:-len(suffix)].rstrip(os.sep) if config_dir.endswith(os.sep + suffix) else config_dir
            cfg = load_json(cfg_path) if cfg_path else {}
            cfg = cfg if isinstance(cfg, dict) else {}
            proj = cfg.get("project") or {}
            m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", ((cfg.get("repo") or {}).get("remote") or ""))
            owner = flag(rest, "--owner") or proj.get("owner")
            number = flag(rest, "--number") or proj.get("number")
            repo = flag(rest, "--repo") or (m.group(1) if m else None)
            if not (owner and number and repo):
                return 64
            qa_all = "--qa-all" in rest or bool(cfg.get("_qa_all"))
            res = board_migrate(owner, int(number), repo, qa_all, dry, "--prune-empty" in rest, root, cfg_path)
            return out(res)
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as e:
        return out({"error": str(e)}, 2)
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
