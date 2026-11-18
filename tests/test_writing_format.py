"""Format tests for the writing standard (skills/drover/references/writing-standard.md).

Pins the shapes scripts and agents depend on: the commit subject, the ticket's required
sections, the comment header, the PR body markers. Each test reads the shipped docs and
scripts, so a template that drifts from the standard fails here.

Run directly:  python3 tests/test_writing_format.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "skills" / "drover" / "references"
STANDARD = (REF / "writing-standard.md").read_text(encoding="utf-8")

TYPES = "feat|fix|chore|refactor|test|perf|docs|ci|ui|security|revert|wip"
# One subject regex per commit_style (writing-standard.md § 1). `other` copies the repo's examples.
SUBJECT = {
    "conventional": re.compile(rf"^({TYPES})(\([a-z0-9._/-]+\))?!?: \S"),
    "bracket-tag": re.compile(rf"^\[({TYPES})\] [a-z0-9][a-z0-9._/-]*: \S"),
    "plain": re.compile(r"^[A-Z][a-z]+ \S"),
}
STYLE_EXAMPLES = {
    "conventional": "fix(receipts): show a size error over 10 MB",
    "bracket-tag": "[fix] receipts: show a size error over 10 MB",
    "plain": "Show a size error for receipts over 10 MB",
}
_EMOJI_PROBE = re.compile("[\u2600-\u27bf\U0001F000-\U0001FAFF\ufe0f]")
COMMENT_HEADER = re.compile(
    r"^\[(builder|qa|reviewer|collect|orchestrator)\] \[(blocker|issue|suggestion|nit|question|praise|report)\] \S"
)
TICKET_SECTIONS = ["Problem", "Context", "Fix", "Acceptance Criteria", "Risk", "Blocked by"]
EVIDENCE_ROWS = [
    "Error + stack", "Request / trace ID", "Sentry", "PostHog replay", "Logs", "Screenshots",
    "HAR / API sample", "Env + release", "First / last seen", "Users affected", "Steps",
    "Expected / actual",
]
PR_BLOCKS = ["status", "problem", "solution", "ac", "history", "visual", "risk"]


def subject_ok(s: str, style: str) -> bool:
    return bool(SUBJECT[style].match(s)) and len(s) <= 72 and not _EMOJI_PROBE.search(s)


def fenced(text: str) -> list[str]:
    return re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.S)


# ── commit ───────────────────────────────────────────────────────────────────

def test_subject_regex_per_style():
    good = {
        "conventional": ["fix(receipts): show a size error over 10 MB", "feat: stream replies",
                         "refactor(cart)!: drop the v1 totals", "test(drover-qa): iter 3 report"],
        "bracket-tag": ["[fix] receipts: show a size error over 10 MB", "[ui] reports: before/after screenshots"],
        "plain": ["Show a size error for receipts over 10 MB", "Add CSV export"],
    }
    bad = {
        "conventional": [
            "🐛 [fix] receipts: show a size error",       # upstream emoji format
            "fix receipts: show a size error",            # no colon after the type
            "bugfix(receipts): show a size error",        # unknown type
            "fix(Receipts): show a size error",           # scope not lowercase
            "fix(receipts): " + "x" * 80,                 # too long
        ],
        "bracket-tag": [
            "🐛 [fix] receipts: show a size error",       # emoji prefix
            "[bugfix] receipts: show a size error",       # unknown type
            "[fix] Receipts: show a size error",          # scope not lowercase
        ],
        "plain": [
            "show a size error",                          # not capitalized
            "fix(receipts): show a size error",           # a prefix
            "✨ Add CSV export",                           # emoji
        ],
    }
    for style, subjects in good.items():
        for s in subjects:
            assert subject_ok(s, style), f"{style} should pass: {s}"
    for style, subjects in bad.items():
        for s in subjects:
            assert not subject_ok(s, style), f"{style} should fail: {s}"


def test_style_table_matches_standard():
    """writing-standard.md names each commit_style once with an example that passes its regex."""
    rows = re.findall(r"^\| `([a-z-]+)`(?: \(default\))? \| .+? \| `([^`]+)` \|$", STANDARD, flags=re.M)
    assert dict(rows) == STYLE_EXAMPLES, f"writing-standard.md style table drifted: {rows}"
    for style, example in rows:
        assert subject_ok(example, style), f"{style} example is off-standard: {example}"
    assert "`other`" in STANDARD and "drover-commit-style.py" in STANDARD
    for t in TYPES.split("|"):
        assert f"`{t}`" in STANDARD, f"writing-standard.md lacks type {t}"
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "conventional" in claude_md, "CLAUDE.md writing table must name this repo's commit style"


def test_standard_commit_examples_are_valid_subjects():
    seen = 0
    for block in fenced(STANDARD):
        lines = block.splitlines()
        first = lines[0] if lines else ""
        if len(lines) > 2 and lines[1] == "" and lines[2].startswith("- ") and "## Problem" not in block \
                and not first.startswith("<"):
            assert subject_ok(first, "conventional"), f"example subject is off-standard: {first}"
            seen += 1
    assert seen >= 1, "expected a commit example in writing-standard.md"


def test_no_attribution_lines():
    """FR-CNV-5: no Co-Authored-By or generated-by footer in any shipped template."""
    footer = re.compile(r"^\W*(Co-Authored-By:|Generated (with|by) )", re.I)
    for p in list((ROOT / "skills").rglob("*.md")) + list((ROOT / "lib").glob("*")) + list((ROOT / "workflows").glob("*.js")):
        if "visual" in p.parts or not p.is_file():
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            assert not footer.search(line), f"attribution line at {p.relative_to(ROOT)}:{n}: {line.strip()}"


def test_dispatcher_reads_the_done_trailer_not_the_subject():
    """FR-CNV-6: done / partial come from the `Drover:` line in the commit body, never the subject."""
    src = (ROOT / "skills" / "drover-build" / "scripts" / "drover-build-dispatch.sh").read_text(encoding="utf-8")
    fn = re.search(r"^trailer_commit\(\) \{\n.*?^\}\n", src, flags=re.S | re.M)
    assert fn, "drover-build-dispatch.sh must define trailer_commit()"
    assert "trailer_commit done" in src and "trailer_commit partial" in src
    with tempfile.TemporaryDirectory() as d:
        def git(*a):
            return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, check=True).stdout

        def commit(msg):
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", msg)

        git("init", "-q")
        commit("before the range\n\nDrover: done #42")
        base = git("rev-parse", "HEAD").strip()
        for msg in ("chore(loop): close #42 — subject only, no trailer",
                    "Drover: done #42",
                    "feat(cart): other card\n\n- bullet\nCloses #421\nDrover: done #421",
                    "Add schema for carts\n\n- bullet\nDrover: partial #42"):
            commit(msg)

        def found(kind: str) -> str:
            script = fn.group(0) + f"trailer_commit {kind}\n"
            out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True,
                                 env={"PATH": os.environ["PATH"], "WORKTREE_DIR": d, "N": "42",
                                      "COMMITS_RANGE": f"{base}..HEAD"}).stdout.strip()
            return out.split(" ", 1)[1] if out else ""

        assert found("done") == "", "a subject, another card's trailer or a commit before the range is not done"
        assert found("partial") == "Add schema for carts"
        commit("Show a size error for receipts over 10 MB\n\n- client checks size\nCloses #42\nDrover: done #42")
        assert found("done") == "Show a size error for receipts over 10 MB"


def test_no_old_commit_shapes_in_templates():
    old = re.compile(r"chore\(loop\)|wip\(loop\)|fix\(drover-qa\)|feat\(drover-qa\)|refine\(\$?\{?[a-z<]")
    allowed: set[str] = set()
    for p in list((ROOT / "skills").rglob("*.md")) + list((ROOT / "skills").rglob("*.sh")) + list((ROOT / "workflows").glob("*.js")):
        if p.name in allowed or "visual" in p.parts:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            assert not old.search(line), f"old commit shape at {p.relative_to(ROOT)}:{n}: {line.strip()}"


def test_owner_word():
    """FR-CNV-7: the person who must act is `owner`, never a name."""
    assert "| Next | `builder` · `qa` · `reviewer` · `owner` · `none` |" in STANDARD
    name = re.compile(r"\bEric\b(?! Tech)")
    for p in list((ROOT / "skills").rglob("*.md")) + list((ROOT / "workflows").glob("*.js")) + list((ROOT / "lib").glob("*")):
        if p.is_file():
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                assert not name.search(line), f"names a person at {p.relative_to(ROOT)}:{n}: {line.strip()}"


# ── ticket ───────────────────────────────────────────────────────────────────

def headings(text: str) -> list[str]:
    return re.findall(r"^## (.+?)\s*$", text, flags=re.M)


def test_ticket_template_has_required_sections_in_order():
    for name, text in [("ticket-format.md", (REF / "ticket-format.md").read_text(encoding="utf-8")),
                       ("writing-standard.md example", next(b for b in fenced(STANDARD) if "## Problem" in b and "## Blocked by" in b))]:
        h = [x for x in headings(text) if x in TICKET_SECTIONS]
        assert h == TICKET_SECTIONS, f"{name}: sections {h} != {TICKET_SECTIONS}"
        assert re.search(r"^- \[ \] ", text, flags=re.M), f"{name}: Acceptance Criteria must be a checklist"
        assert re.search(r"^- \*\*Where:\*\*.*\n(  - [a-z]\. .+\n)+", text, flags=re.M), f"{name}: Context needs lettered steps under Where"


def test_bug_filer_matches_the_standard():
    src = (ROOT / "lib" / "drover-qa-file-bug.sh").read_text(encoding="utf-8")
    for s in ["Problem", "Context", "Evidence", "Fix", "Acceptance Criteria", "Risk"]:
        assert f'"{s}"' in src, f"drover-qa-file-bug.sh does not require {s}"
    rows = re.search(r'EVIDENCE_ROWS="([^"]+)"', src)[1].split("|")
    assert rows == [r.lower() for r in EVIDENCE_ROWS], f"filer evidence rows drifted: {rows}"
    table = re.findall(r"^\| ([A-Za-z][^|]+?) \| ", STANDARD.split("**Bug Evidence**", 1)[1], flags=re.M)
    table = [t for t in table if t != "Row"][:12]
    assert table == EVIDENCE_ROWS, f"writing-standard.md evidence rows drifted: {table}"


def test_ticket_title_shape():
    title = re.compile(r"^\[(bug|feat|ui|test|docs|refactor|security)\] [a-z0-9][a-z0-9._/-]*: \S")
    assert title.match("[bug] receipts: spinner never stops over 10 MB")
    assert not title.match("🐛 [bug] receipts: spinner never stops over 10 MB")
    assert not title.match("Bug /imports — CSV upload silently drops rows")
    example = next(b for b in fenced(STANDARD) if "## Problem" in b and "## Blocked by" in b).splitlines()[0]
    assert title.match(example), f"writing-standard.md ticket example title is off-standard: {example}"
    src = (ROOT / "lib" / "drover-qa-file-bug.sh").read_text(encoding="utf-8")
    assert 'FULL_TITLE="${BADGE} ${SCOPE}: ${TITLE}"' in src


# ── comment ──────────────────────────────────────────────────────────────────

def test_comment_header_regex():
    assert COMMENT_HEADER.match("[qa] [report] failing · round 1")
    assert COMMENT_HEADER.match("[reviewer] [blocker] blocked [blocked:creds] missing Stripe test key")
    assert not COMMENT_HEADER.match("drover · QA fail · v1")
    assert not COMMENT_HEADER.match("[QA] [report] failing")
    assert not COMMENT_HEADER.match("[qa] [fyi] failing")


def test_comment_examples_follow_the_header():
    """Lane comments have headers; author notes use three labels without a routing prefix."""
    machine = re.compile(r"^(Local tests|gh-quota-on-exit|blocked-by|root-cause-hash):|^\||^$|^<!--")
    run_md = (REF / "run.md").read_text(encoding="utf-8")
    checked = 0
    author_notes = 0
    for text in (STANDARD, run_md):
        for block in fenced(text):
            lines = block.splitlines()
            if lines and lines[0].startswith("<!-- drover:author-note "):
                assert lines[0].endswith(" -->"), "author-note marker must stay hidden"
                labels = re.findall(r"^\*\*(.+?):\*\* \S", block, flags=re.M)
                assert labels == ["Purpose", "What changed", "Why it matters"], labels
                assert len(lines) == 4, "author-note example must stay short"
                assert not any(COMMENT_HEADER.match(line) for line in lines), "author notes must not route as findings"
                author_notes += 1
                continue
            lines = [l for l in lines if not l.startswith("<!-- drover-review:report")]
            if "## Problem" in block:
                continue  # a ticket, its title is checked in test_ticket_title_shape
            if not lines or not re.match(r"^\[[a-z<]", lines[0]) or lines[0].startswith("[<"):
                continue
            assert COMMENT_HEADER.match(lines[0]), f"bad comment header: {lines[0]}"
            prose = [l for l in lines if not machine.match(l)]
            assert len(prose) <= 8, f"comment over 8 lines: {lines[0]}"
            checked += 1
    assert checked >= 6, f"expected comment samples, checked {checked}"
    assert author_notes == 1, "expected one shared author-note example"


def test_block_template_uses_the_header():
    text = (REF / "block-template.md").read_text(encoding="utf-8")
    heads = [b.splitlines()[0] for b in fenced(text) if b.startswith("[")]
    assert heads, "block-template.md has no header line"
    for h in heads:
        assert re.match(r"^\[(<role>|builder|qa|reviewer|collect|orchestrator)\] \[blocker\] \S", h), h
    assert "Reason tag:" in text, "drover-deps.sh parses 'Reason tag:'"
    assert "drover-reasons.json" in text, "block-template.md must point at the one tag list"


REASONS = json.loads((ROOT / "lib" / "drover-reasons.json").read_text(encoding="utf-8"))


def test_reason_tags_are_defined_once():
    """Every tag a writer or reader names is in drover-reasons.json, and no file keeps its own table."""
    known = set(REASONS["tags"]) | {REASONS["dropped"]}
    tag = re.compile(r"\[(?:blocked:[a-z-]+|needs-you|dropped)\]")
    paths = [p for p in list((ROOT / "skills").rglob("*")) + list((ROOT / "lib").glob("*")) + list((ROOT / "workflows").glob("*"))
             if p.is_file() and p.suffix in (".md", ".sh", ".py", ".js")]
    for p in paths:
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for t in tag.findall(line):
                assert t in known, f"unknown reason tag {t} at {p.relative_to(ROOT)}:{n}"
    template = (REF / "block-template.md").read_text(encoding="utf-8")
    listed = [t for t in REASONS["tags"] if t in template]
    assert len(listed) < len(REASONS["tags"]) // 2, "block-template.md copies the tag list; point at drover-reasons.json"
    for script in ("drover-status.py", "drover-approval.py", "drover-deps.sh"):
        src = (ROOT / "lib" / script).read_text(encoding="utf-8")
        assert "drover-reasons.json" in src, f"{script} must read the tags from drover-reasons.json"


def test_needs_you_comment_is_checklist_first():
    """The [needs-you] comment shows what to do first; why and evidence fold away; the planner's lines stay."""
    text = (REF / "block-template.md").read_text(encoding="utf-8")
    block = next(b for b in fenced(text) if "Your turn on" in b)
    lines = block.splitlines()
    assert re.match(r"^\[reviewer\] \[blocker\] Your turn on #<N> — ", lines[0]), lines[0]
    assert lines[1].startswith("- [ ] "), "the first line after the header is a checklist item"
    assert any(l == "- [ ] Comment `done` here" for l in lines), "the checklist ends with: comment done"
    fold = block.index("<details><summary>Why, and what I checked</summary>")
    assert block.index("- [ ] ") < fold, "checklist before the fold"
    assert re.search(r"Reason tag:[^\n]*\[needs-you\]", block[fold:]), "deps.sh reads 'Reason tag: [needs-you]'"
    assert re.search(r"^blocked-by: -$", block[fold:], flags=re.M), "deps.sh reads 'blocked-by: -'"



# ── PR body ──────────────────────────────────────────────────────────────────

def test_pr_body_markers():
    out = subprocess.run(["bash", str(ROOT / "lib" / "drover-pr-body.sh"), "--skeleton"],
                         capture_output=True, text=True, check=True).stdout
    for b in PR_BLOCKS:
        assert f"<!-- sb:{b} -->" in out and f"<!-- /sb:{b} -->" in out, f"skeleton lacks {b}"
    run_md = (REF / "run.md").read_text(encoding="utf-8")
    for b in ["status", "problem", "solution", "ac", "history", "risk"]:
        assert f"<!-- sb:{b} -->" in run_md and f"<!-- /sb:{b} -->" in run_md, f"run.md PR template lacks {b}"
    assert "## Not verified" not in run_md and "**Status:**" not in run_md


def test_history_is_folded_and_needs_you_sits_in_the_status_alert():
    """From the pr-skill template: history folds in <details>; a human step can ride the status alert."""
    run_md = (REF / "run.md").read_text(encoding="utf-8")
    for name, text in (("writing-standard.md", STANDARD), ("run.md", run_md)):
        hist = text[text.index("<!-- sb:history -->"):text.index("<!-- /sb:history -->")] if "<!-- sb:history -->\n" in text else \
            next(b for b in fenced(text) if "Iteration history" in b)
        assert "<details><summary>Iteration history</summary>" in hist, f"{name} history is not folded"
        assert hist.rstrip().endswith("</details>"), f"{name} history fold is not closed"
    assert "> needs-you: " in STANDARD, "writing-standard.md shows no needs-you line in the status alert"


def test_risk_names_door_and_blast_radius():
    """Risk carries the merge danger: a two-way or one-way door and a blast radius."""
    run_md = (REF / "run.md").read_text(encoding="utf-8")
    std_risk = next(b for b in fenced(STANDARD) if b.startswith("## Risk\n**Low**"))
    run_risk = run_md[run_md.index("<!-- sb:risk -->\n## Risk"):run_md.index("<!-- /sb:risk -->")]
    for name, risk in (("writing-standard.md", std_risk), ("run.md", run_risk)):
        assert "**Door:**" in risk, f"{name} Risk lacks Door"
        assert "**Blast radius:**" in risk, f"{name} Risk lacks Blast radius"
    assert "one-way door is never Low" in STANDARD


def test_status_alerts():
    for alert in ("> [!NOTE]", "> [!TIP]", "> [!WARNING]"):
        assert alert in STANDARD, f"writing-standard.md lacks {alert}"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"  ✅ {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {t.__name__}: {e}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed.")
    sys.exit(1 if failed else 0)
