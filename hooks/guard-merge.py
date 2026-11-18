#!/usr/bin/env python3
"""PreToolUse(Bash) guard: merges go through the merge gate, and only the review lane runs it.

A headless worker's allow list is broad (harnesses/claude.json); this hook is the boundary
(specs/config.md FR-HRN-4). It denies:
- `gh pr merge`, with any global flags (`gh --repo x pr merge`), also inside `bash -c`,
  `sh -c`, `eval`, `env ...`, `xargs ...`, `sudo`, `command`, `exec`, `nohup`, `timeout`;
- `gh api` calls to `.../pulls/<n>/merge` or carrying a merge mutation
  (`mergePullRequest`, `enablePullRequestAutoMerge`);
- `drover gate` (or lib/drover-merge-gate.sh) when DROVER_ROLE is set and is not `review`.
  The runner sets DROVER_ROLE for every worker it starts.
The gate's own `gh pr merge` runs inside lib/drover-merge-gate.sh, in its own process, so
this hook never sees it: calling the gate is the way to merge. Quoted text (`echo "gh pr
merge"`) is not a command. Its own errors never block a tool call.
"""
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("guard_protected_push", os.path.join(HERE, "guard-protected-push.py"))
_push = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_push)

WRAPPERS = {"sudo", "command", "exec", "nohup", "time", "builtin"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
GH_VALUE_OPTS = {"-R", "--repo", "--hostname"}
MERGE_MUTATION = re.compile(r"mergePullRequest|enablePullRequestAutoMerge", re.I)
MERGE_PATH = re.compile(r"(^|/)pulls/[^/\s]+/merge\b")


def unwrap(tokens):
    """Drop env assignments and wrapper commands in front of the real command."""
    while tokens:
        t = tokens[0]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            tokens = tokens[1:]
        elif os.path.basename(t) in WRAPPERS:
            tokens = tokens[1:]
        elif os.path.basename(t) == "env":
            tokens = tokens[1:]
            while tokens and (tokens[0].startswith("-") or "=" in tokens[0]):
                tokens = tokens[1:]
        elif os.path.basename(t) == "timeout":
            tokens = tokens[1:]
            while tokens and (tokens[0].startswith("-") or re.match(r"^\d", tokens[0])):
                tokens = tokens[1:]
        elif os.path.basename(t) == "xargs":
            tokens = tokens[1:]
            while tokens and tokens[0].startswith("-"):
                tokens = tokens[2:] if tokens[0] in ("-I", "-n", "-L", "-P", "-d", "-s", "-E") else tokens[1:]
        else:
            break
    return tokens


def simple_commands(command, depth=0):
    """Token lists of every simple command, looking inside `bash -c '...'` and `eval`."""
    out = []
    for tokens in _push.segments(command):
        tokens = unwrap(tokens)
        if not tokens:
            continue
        name = os.path.basename(tokens[0])
        if depth < 4 and name in SHELLS and "-c" in tokens[1:]:
            i = tokens.index("-c")
            if i + 1 < len(tokens):
                out += simple_commands(tokens[i + 1], depth + 1)
            continue
        if depth < 4 and name == "eval":
            out += simple_commands(" ".join(tokens[1:]), depth + 1)
            continue
        out.append(tokens)
    return out


def check(command, role):
    for tokens in simple_commands(command):
        name, args = os.path.basename(tokens[0]), tokens[1:]
        if name == "gh":
            i = 0
            while i < len(args) and args[i].startswith("-"):
                i += 2 if args[i] in GH_VALUE_OPTS else 1
            rest = args[i:]
            if rest[:2] == ["pr", "merge"]:
                return "gh pr merge"
            if rest[:1] == ["api"] and (any(MERGE_PATH.search(a) for a in rest[1:])
                                         or any(MERGE_MUTATION.search(a) for a in rest[1:])):
                return "a gh api merge"
        gate = (name == "drover" and args[:1] in (["gate"], ["merge-gate"])) or name == "drover-merge-gate.sh" \
            or (name in SHELLS and args[:1] and os.path.basename(args[0]) == "drover-merge-gate.sh")
        if gate and role and role != "review":
            return f"drover gate from the {role} lane"
    return None


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        command = (payload.get("tool_input") or {}).get("command")
        if not isinstance(command, str):
            return
        hit = check(command, os.environ.get("DROVER_ROLE", ""))
        if hit:
            reason = (f"Blocked by guard-merge: {hit}. Merges go only through the merge gate "
                      "(`drover gate --pr <N> --expect-head <sha> ...`), and only the review lane runs it.")
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                     "permissionDecision": "deny",
                                                     "permissionDecisionReason": reason}}))
    except Exception:
        pass  # a broken guard must never block unrelated work


if __name__ == "__main__":
    main()
