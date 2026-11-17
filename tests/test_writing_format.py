"""Format tests for the writing standard (skills/drover/references/writing-standard.md).

Pins the shapes scripts and agents depend on: the commit subject, the ticket's required
sections, the comment header, the PR body markers. Each test reads the shipped docs and
scripts, so a template that drifts from the standard fails here.

Run directly:  python3 tests/test_writing_format.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "skills" / "drover" / "references"
STANDARD = (REF / "writing-standard.md").read_text(encoding="utf-8")

EMOJI = {
    "feat": "✨", "fix": "🐛", "chore": "🔧", "refactor": "♻️", "test": "🧪", "perf": "⚡",
    "docs": "📝", "ci": "👷", "ui": "💄", "security": "🔒", "revert": "⏪", "wip": "🚧",
}
SUBJECT = re.compile(
    r"^(?P<emoji>✨|🐛|🔧|♻️|🧪|⚡|📝|👷|💄|🔒|⏪|🚧) \[(?P<type>feat|fix|chore|refactor|test|perf|docs|ci|ui|security|revert|wip)\] "
    r"[a-z0-9][a-z0-9._/-]*: \S"
)
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


def subject_ok(s: str) -> bool:
    m = SUBJECT.match(s)
    return bool(m) and EMOJI[m["type"]] == m["emoji"] and len(s) <= 72


def fenced(text: str) -> list[str]:
    return re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.S)


# ── commit ───────────────────────────────────────────────────────────────────

def test_subject_regex():
    good = [
        "🐛 [fix] receipts: show a size error over 10 MB",
        "🔧 [chore] loop: close #42 — stream replies",
        "🚧 [wip] loop: #42 partial — schema only",
        "🧪 [test] drover-qa: iter 3 (2 bugs, 9 items, 1 PRs opened)",
        "💄 [ui] reports: before/after screenshots",
    ]
    bad = [
        "fix(receipts): show a size error",          # conventional commits
        "🐛 fix receipts: show a size error",          # no brackets
        "✨ [fix] receipts: show a size error",        # emoji does not match type
        "🐛 [bugfix] receipts: show a size error",     # unknown type
        "🐛 [fix] Receipts: show a size error",        # scope not lowercase
        "🐛 [fix] receipts: " + "x" * 80,              # too long
    ]
    for s in good:
        assert subject_ok(s), f"should pass: {s}"
    for s in bad:
        assert not subject_ok(s), f"should fail: {s}"


def test_emoji_map_matches_standard():
    rows = dict((t, e) for e, t in re.findall(r"^\| (\S+) \| `([a-z]+)` \|", STANDARD, flags=re.M))
    assert rows == EMOJI, f"writing-standard.md emoji table drifted: {rows}"
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for t, e in EMOJI.items():
        assert f"{e} {t}" in claude_md, f"CLAUDE.md writing table lacks '{e} {t}'"


def test_standard_examples_are_valid_subjects():
    seen = 0
    for block in fenced(STANDARD):
        first = block.splitlines()[0] if block.strip() else ""
        if re.match(r"^[^\s\[<]\S* \[[a-z]+\] ", first):
            if re.match(r"^\S+ \[(bug|feat|ui|test|docs|refactor|security)\] ", first) and "## Problem" in block:
                continue  # a ticket title, checked below
            assert subject_ok(first), f"example subject is off-standard: {first}"
            seen += 1
    assert seen >= 1, "expected a commit example in writing-standard.md"


def test_dispatcher_reads_the_done_trailer_not_the_subject():
    """FR-CNV-6: done / partial come from the `Drover:` body trailer; the subject is free."""
    src = (ROOT / "skills" / "drover-build" / "scripts" / "drover-build-dispatch.sh").read_text(encoding="utf-8")
    pats = dict(re.findall(r'--grep="\^Drover: (done|partial) (#\$N\[\[:space:\]\]\*\\\$)"', src))
    assert set(pats) == {"done", "partial"}, f"expected the done and partial trailer greps, found {pats}"
    with tempfile.TemporaryDirectory() as d:
        def git(*a):
            return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, check=True).stdout
        git("init", "-q")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base")
        base = git("rev-parse", "HEAD").strip()
        for msg in ("chore(loop): close #42 — subject only, no trailer",
                    "feat(cart): other card\n\n- bullet\nCloses #421\nDrover: done #421",
                    "Add schema for carts\n\n- bullet\nDrover: partial #42"):
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", msg)

        def found(kind: str) -> str:
            pat = "^Drover: " + kind + " " + pats[kind].replace("$N", "42").replace("\\$", "$")
            return git("log", "-E", "--grep=" + pat, "--format=%s", f"{base}..HEAD").strip()

        assert found("done") == "", "a subject or another card's trailer must not count as done"
        assert found("partial") == "Add schema for carts"
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
            "-m", "Show a size error for receipts over 10 MB\n\n- client checks size\nCloses #42\nDrover: done #42")
        assert found("done") == "Show a size error for receipts over 10 MB"


def test_no_old_commit_shapes_in_templates():
    old = re.compile(r"chore\(loop\)|wip\(loop\)|fix\(drover-qa\)|feat\(drover-qa\)|refine\(\$?\{?[a-z<]")
    allowed: set[str] = set()
    for p in list((ROOT / "skills").rglob("*.md")) + list((ROOT / "skills").rglob("*.sh")) + list((ROOT / "workflows").glob("*.js")):
        if p.name in allowed or "visual" in p.parts:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            assert not old.search(line), f"old commit shape at {p.relative_to(ROOT)}:{n}: {line.strip()}"


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
    src = (ROOT / "scripts" / "drover-qa-file-bug.sh").read_text(encoding="utf-8")
    for s in ["Problem", "Context", "Evidence", "Fix", "Acceptance Criteria", "Risk"]:
        assert f'"{s}"' in src, f"drover-qa-file-bug.sh does not require {s}"
    rows = re.search(r'EVIDENCE_ROWS="([^"]+)"', src)[1].split("|")
    assert rows == [r.lower() for r in EVIDENCE_ROWS], f"filer evidence rows drifted: {rows}"
    table = re.findall(r"^\| ([A-Za-z][^|]+?) \| ", STANDARD.split("**Bug Evidence**", 1)[1], flags=re.M)
    table = [t for t in table if t != "Row"][:12]
    assert table == EVIDENCE_ROWS, f"writing-standard.md evidence rows drifted: {table}"


def test_ticket_title_shape():
    title = re.compile(r"^(🐛 \[bug\]|✨ \[feat\]|💄 \[ui\]|🧪 \[test\]|📝 \[docs\]|♻️ \[refactor\]|🔒 \[security\]) [a-z0-9][a-z0-9._/-]*: \S")
    assert title.match("🐛 [bug] receipts: spinner never stops over 10 MB")
    assert not title.match("🐛 Bug /imports — CSV upload silently drops rows")
    src = (ROOT / "scripts" / "drover-qa-file-bug.sh").read_text(encoding="utf-8")
    assert 'FULL_TITLE="${BADGE} ${SCOPE}: ${TITLE}"' in src


# ── comment ──────────────────────────────────────────────────────────────────

def test_comment_header_regex():
    assert COMMENT_HEADER.match("[qa] [report] ❌ failing · round 1")
    assert COMMENT_HEADER.match("[reviewer] [blocker] 🛑 blocked · 🔐 missing key")
    assert not COMMENT_HEADER.match("🔍 drover · QA fail · v1")
    assert not COMMENT_HEADER.match("[QA] [report] ❌ failing")
    assert not COMMENT_HEADER.match("[qa] [fyi] ❌ failing")


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


REASONS = json.loads((ROOT / "scripts" / "drover-reasons.json").read_text(encoding="utf-8"))


def test_reason_tags_are_defined_once():
    """Every tag a writer or reader names is in drover-reasons.json, and no file keeps its own table."""
    known = set(REASONS["tags"]) | {REASONS["dropped"]}
    tag = re.compile(r"\[(?:blocked:[a-z-]+|needs-you|dropped)\]")
    paths = [p for p in list((ROOT / "skills").rglob("*")) + list((ROOT / "scripts").glob("*")) + list((ROOT / "workflows").glob("*"))
             if p.is_file() and p.suffix in (".md", ".sh", ".py", ".js")]
    for p in paths:
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for t in tag.findall(line):
                assert t in known, f"unknown reason tag {t} at {p.relative_to(ROOT)}:{n}"
    template = (REF / "block-template.md").read_text(encoding="utf-8")
    listed = [t for t in REASONS["tags"] if t in template]
    assert len(listed) < len(REASONS["tags"]) // 2, "block-template.md copies the tag list; point at drover-reasons.json"
    for script in ("drover-status.py", "drover-approval.py", "drover-deps.sh"):
        src = (ROOT / "scripts" / script).read_text(encoding="utf-8")
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
    out = subprocess.run(["bash", str(ROOT / "scripts" / "drover-pr-body.sh"), "--skeleton"],
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
    std_risk = next(b for b in fenced(STANDARD) if b.startswith("## Risk\n🟢"))
    run_risk = run_md[run_md.index("<!-- sb:risk -->\n## Risk"):run_md.index("<!-- /sb:risk -->")]
    for name, risk in (("writing-standard.md", std_risk), ("run.md", run_risk)):
        assert "**Door:**" in risk, f"{name} Risk lacks Door"
        assert "**Blast radius:**" in risk, f"{name} Risk lacks Blast radius"
    assert "one-way door is never 🟢" in STANDARD


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
