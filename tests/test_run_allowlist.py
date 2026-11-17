#!/usr/bin/env python3
"""The run allowlist covers every .claude/bin script that pre-flight and the lanes call (#38).

Onboard writes the base list from run-workflow.md -> "Mid-run permission prompts", so a
script missing there stalls a fresh install on a prompt (and a headless run never starts).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_WORKFLOW = ROOT / "skills/drover/references/run-workflow.md"
CALLERS = [
    RUN_WORKFLOW,
    ROOT / "workflows/drover-wave.js",
    ROOT / "skills/drover-build/SKILL.md",
    ROOT / "skills/drover-qa/SKILL.md",
    ROOT / "skills/drover-review/SKILL.md",
]
# Not on the base list on purpose: the legacy dispatcher is spawned by the user, and the
# merge gate is offered with the merge lines (onboard -> Permissions), listed below the base.
EXEMPT = {"drover-run.sh"}
REF = re.compile(r"(?:(bash|python3|source) )?\.claude/bin/([A-Za-z0-9_-]+\.(?:sh|py))")
RULE = re.compile(r'"(Bash\([^"]*\))"')

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


text = RUN_WORKFLOW.read_text()
section = text.split("## Mid-run permission prompts", 1)[1].split("\n## ", 1)[0]
base = section.split("plus your project's test runners", 1)[0]
rules = set(RULE.findall(section))  # base list + merge lines
base_rules = set(RULE.findall(base))


def covered(script, form, pool):
    forms = [form] if form else ["bash", "python3", "source", None]
    for f in forms:
        target = f".claude/bin/{script}" if f is None else f"{f} .claude/bin/{script}"
        if f"Bash({target}:*)" in pool:
            return True
    return False


for path in CALLERS:
    for form, script in set(REF.findall(path.read_text())):
        if script in EXEMPT:
            continue
        pool = rules if script == "drover-merge-gate.sh" else base_rules
        check(covered(script, form or None, pool),
              f"{path.relative_to(ROOT)} calls {(form + ' ') if form else ''}.claude/bin/{script}"
              " but the run allowlist has no rule for it")

for rule in ("Bash(python3 .claude/bin/drover-github-read.py:*)",
             "Bash(.claude/bin/drover-github-read.py:*)"):
    check(rule in base_rules, f"base allowlist misses {rule}")

check(re.search(r'claude -p "/drover run"', section) is not None,
      "run-workflow.md must say whether claude -p \"/drover run\" is supported")
check(".claude/drover/" in section and ".drover/worktrees/" in section,
      "run-workflow.md must say how a headless run writes .claude/drover/ and .drover/worktrees/")

if failures:
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    sys.exit(1)
print("PASS: test_run_allowlist.py")
