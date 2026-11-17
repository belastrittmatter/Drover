#!/usr/bin/env python3
"""drover-commit-style.py — the repo's commit subject style (specs/conventions.md FR-CNV-1/2).

Reads the base branch's last 50 commit subjects (merges skipped), classifies each as
conventional, bracket-tag, plain (imperative) or other, and picks the majority. Fewer than
10 subjects, or no style over half of them, gives conventional.

Usage:
  drover-commit-style.py [--root DIR] [--base BRANCH] [--config CFG] [--prompt]

  --base    branch to read (default: config base_branch, else HEAD)
  --config  a drover config; its `commit_style` wins over detection
  --prompt  print the one line a worker prompt carries, instead of JSON

Prints JSON {"commit_style": ..., "examples": [up to 3 subjects in that style]}.
Onboard stores `commit_style`; the lane prompts carry the --prompt line.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

STYLES = ("conventional", "bracket-tag", "plain", "other")
CONVENTIONAL = re.compile(r"^[a-z]+(\([^)]+\))?!?: \S")
BRACKET = re.compile(r"^(\S+ )?\[[a-z][a-z0-9-]*\] \S")
PLAIN = re.compile(r"^[A-Z][a-z]+ \S")
MERGE = re.compile(r"^(Merge (pull request|branch|remote-tracking)|Revert \")")
SAMPLE, MIN_SUBJECTS = 50, 10


def style_of(subject: str) -> str:
    if CONVENTIONAL.match(subject):
        return "conventional"
    if BRACKET.match(subject):
        return "bracket-tag"
    first = subject.split(" ", 1)[0]
    # Imperative: "Add", "Fix", "Show" — not "Added", "Fixes", "Adding".
    if PLAIN.match(subject) and not re.search(r"(ed|es|ing)$", first):
        return "plain"
    return "other"


def classify(subjects: list[str]) -> str:
    subjects = [s for s in subjects if s and not MERGE.match(s)]
    if len(subjects) < MIN_SUBJECTS:
        return "conventional"
    counts = {st: 0 for st in STYLES}
    for s in subjects:
        counts[style_of(s)] += 1
    best = max(STYLES, key=lambda st: counts[st])
    return best if counts[best] * 2 > len(subjects) else "conventional"


def examples(subjects: list[str], style: str, n: int = 3) -> list[str]:
    return [s for s in subjects if not MERGE.match(s) and style_of(s) == style][:n]


def subjects_on(root: str, ref: str) -> list[str]:
    r = subprocess.run(["git", "-C", root, "log", f"-{SAMPLE}", "--no-merges", "--format=%s", ref],
                       capture_output=True, text=True)
    return r.stdout.splitlines() if r.returncode == 0 else []


def prompt_line(style: str, ex: list[str]) -> str:
    shown = " | ".join(ex) if ex else "none on the base branch yet"
    return (f"Commit style: {style} (writing-standard.md section 1). Write commit subjects and the "
            f"PR title in this style, like these subjects from this repo: {shown}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--base")
    ap.add_argument("--config")
    ap.add_argument("--prompt", action="store_true")
    a = ap.parse_args(argv)
    cfg = {}
    if a.config:
        try:
            with open(a.config, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
    base = a.base or cfg.get("base_branch") or "HEAD"
    subjects = subjects_on(a.root, base) or subjects_on(a.root, f"origin/{base}")
    style = cfg.get("commit_style") if cfg.get("commit_style") in STYLES else classify(subjects)
    ex = examples(subjects, style)
    if a.prompt:
        print(prompt_line(style, ex))
    else:
        print(json.dumps({"commit_style": style, "examples": ex}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
