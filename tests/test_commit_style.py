"""Commit style detection (specs/conventions.md FR-CNV-1/2): scripts/drover-commit-style.py.

Run directly:  python3 tests/test_commit_style.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "drover-commit-style.py"
_spec = importlib.util.spec_from_file_location("commit_style", SCRIPT)
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

CONV = ["fix(receipts): show a size error over 10 MB", "feat: stream replies", "chore(deps): bump vite",
        "refactor!: drop the v1 API", "test(cart): cover empty carts"]
BRACKET = ["[fix] receipts: show a size error over 10 MB", "🐛 [fix] cart: clamp discount",
           "[feat] chat: stream replies", "✨ [feat] export: csv", "[docs] readme: setup steps"]
PLAIN = ["Show a size error for receipts over 10 MB", "Add CSV export", "Fix the empty cart crash",
         "Remove the v1 API", "Bump vite to 5.4"]
OTHER = ["wip", "updated stuff", "Fixed the thing", "Adding tests", "misc"]


def test_each_shape():
    for s in CONV:
        assert cs.style_of(s) == "conventional", s
    for s in BRACKET:
        assert cs.style_of(s) == "bracket-tag", s
    for s in PLAIN:
        assert cs.style_of(s) == "plain", s
    for s in OTHER:
        assert cs.style_of(s) == "other", s


def test_case_insensitive_prefixes():
    for s in ("Fix: x", "Feat(scope): x", "FIX(api)!: drop v1"):
        assert cs.style_of(s) == "conventional", s
    for s in ("[FIX] x", "[Feat] chat: stream"):
        assert cs.style_of(s) == "bracket-tag", s
    assert cs.style_of("Fixed: the thing") == "conventional", "a colon right after the word is a type prefix"
    assert cs.style_of("Fixed the thing") == "other", "the -ed heuristic is unchanged"


def test_majority_wins():
    assert cs.classify(CONV * 3) == "conventional"
    assert cs.classify(BRACKET * 2 + CONV[:1]) == "bracket-tag"
    assert cs.classify(PLAIN * 2 + OTHER[:2]) == "plain"
    assert cs.classify(OTHER * 3) == "other"


def test_few_commits_or_no_majority_is_conventional():
    assert cs.classify(PLAIN[:4] * 2) == "conventional", "8 subjects is under 10"
    assert cs.classify(PLAIN * 2 + BRACKET * 2) == "conventional", "50/50 is no clear majority"


def test_merges_do_not_count():
    merges = ["Merge pull request #5 from x/y", "Merge branch 'main' into dev"] * 10
    assert cs.classify(merges + PLAIN) == "conventional", "only 5 real subjects left"
    assert cs.examples(merges + PLAIN, "plain") == PLAIN[:3]


def git(d, *a):
    return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, check=True).stdout


def test_cli_reads_the_base_branch_and_prompt_has_three_subjects():
    with tempfile.TemporaryDirectory() as d:
        git(d, "init", "-q", "-b", "main")
        for s in reversed(PLAIN * 2 + ["Merge branch 'x'"]):
            git(d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", s)
        out = json.loads(subprocess.run([sys.executable, str(SCRIPT), "--root", d, "--base", "main"],
                                        capture_output=True, text=True, check=True).stdout)
        assert out == {"commit_style": "plain", "examples": PLAIN[:3]}, out
        line = subprocess.run([sys.executable, str(SCRIPT), "--root", d, "--base", "main", "--prompt"],
                              capture_output=True, text=True, check=True).stdout
        assert line.startswith("Commit style: plain"), line
        assert all(s in line for s in PLAIN[:3]), line
        cfg = Path(d) / "cfg.json"
        cfg.write_text(json.dumps({"base_branch": "main", "commit_style": "conventional"}))
        out = json.loads(subprocess.run([sys.executable, str(SCRIPT), "--root", d, "--config", str(cfg)],
                                        capture_output=True, text=True, check=True).stdout)
        assert out["commit_style"] == "conventional", "a stored commit_style wins over detection"


def test_lane_prompts_carry_the_style():
    for rel in ("scripts/drover-run.sh", "scripts/drover-lane.sh",
                "skills/drover-build/scripts/drover-build-dispatch.sh", "workflows/drover-wave.js"):
        assert "drover-commit-style.py" in (ROOT / rel).read_text(encoding="utf-8"), f"{rel} prompt lacks the commit style"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed.")
    sys.exit(1 if failed else 0)
