#!/usr/bin/env python3
"""Every command a headless worker is told to run is covered by its harness permissions
(specs/config.md FR-HRN-4, docs/audit.md section 11).

Stage 0 failed because the first command of every worker needed approval and the old
allowlist test never read the runner's worker prompt. This test reads the prompt the
runner builds (lib/drover-run.sh dispatch_lane), every file that prompt points a worker
at, and every role rules file (the drover-build, drover-qa and drover-review skills with
their references, plus the shared references they follow). From each fenced shell block
and each inline code span it takes the commands (split on pipes, `&&`, `||`, `;` and
`$(`), and checks the first word of each against harnesses/claude.json.

A first word counts as a command when it is a path to a script or one of the CLI names
in KNOWN below; prose fragments in backticks (`move <issue>`) are neither. Placeholders
(`<test command>`, `$VAR`) are the project's own commands: the runner adds the first word
of each `verify_commands` entry to the worker's rules (lib/drover-config.py allowed()).
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = json.loads((ROOT / "harnesses/claude.json").read_text())
PERMS = HARNESS["permissions"]
ALLOWED = set(PERMS["commands"])
DENIED = [re.match(r"Bash\((.*?)(?::\*)?\)$", d).group(1) for d in PERMS["deny"]]
RUNNER = ROOT / "lib/drover-run.sh"
# run.md sections for the orchestrator and the runner itself, not for a worker.
RUNNER_SECTIONS = re.compile(r"^##+ (Orchestrator delegation|Intro shown|Preconditions|Lanes and label routing|"
                             r"Dispatch allocation|Per-tick logic|Done conditions|Run manifest|Notification cadence|"
                             r"Anti-zombie addendum|Usage pause|The Blocked sweep|Classifier)")
ROLE_FILES = sorted(
    [ROOT / f"skills/drover-{r}/SKILL.md" for r in ("build", "qa", "review")]
    + [p for r in ("build", "qa", "review") for p in (ROOT / f"skills/drover-{r}/references").glob("*.md")]
    + [ROOT / "skills/pr-security-review/SKILL.md"]
    + [ROOT / f"skills/drover/references/{n}.md" for n in
       ("writing-standard", "rate-limit-etiquette", "block-template", "pr-author-notes")])
# A first word is command-shaped when it is a lower-case word or a script path; capitalised
# words, placeholders (`<test command>`, `$VAR`), flags and numbers are prose. Every
# command-shaped first word must be allowed by the harness or be in NON_COMMANDS: words
# the rules put in backticks or fences that no worker runs (labels, statuses, file names,
# template fields). A new word fails the test until it lands in one of the two.
NON_COMMANDS = {
    # harness CLIs the rules name; the runner starts them, a worker never does
    "claude",
    # functions of lib/drover-gh-guard.sh for scripts that source it
    "sb_gh_budget_init",
    # the project verifier's own runner arguments (`run --feature <id>`)
    "run",
    # commit subject types and scopes, comment roles and labels, statuses, template words
    "feat", "fix", "wip", "ui", "receipts", "qa", "builder", "reviewer", "orchestrator", "blocker", "nit",
    "praise", "question", "suggestion", "report", "issue", "check", "collect", "needs", "moved", "free",
    "human-gated", "skipped", "squash", "reviewed", "migrate", "migrated", "re-picked", "re-tried",
    "reconciled", "tracked", "unverified", "unknown", "status", "schema", "policy", "pass", "fail",
    "select", "save", "omit", "zombie", "killing", "fresh", "new", "last", "link", "size", "steady",
    "cosmetic", "comprehensive", "confidence", "async", "atomic", "auth", "blast", "block", "blocks",
    "bot", "branch", "browse", "build", "chat", "claims", "comment", "continue", "default", "drains",
    "expected", "filed", "fixes", "form", "grid", "hamburger", "horizontal", "level", "lined", "look",
    "move", "next_n", "no-staging-env", "one-line", "orders", "owner", "page", "parallel", "prefer",
    "prior", "pull_number", "read", "repo", "thread", "worktree", "slug", "existing", "max", "should",
    # prose words and file names in backticks
    "a", "add", "against", "all", "ambiguous", "and", "ask.", "cannot", "citing", "claims.", "closes",
    "contains", "exists", "for", "from", "in", "it", "itself", "list-page", "detail-page", "public-page",
    "settings-tab", "no", "one", "or", "see", "the", "this", "too", "verifies", "want", "with",
    "page.on", "drover-qa", "cleanup-wt", "pr-author-notes.md", "writing-standard.md",
}
SCRIPT = re.compile(r"^(?:\./|~/|/|\.\./|[\w.-]+/)[\w./-]*\.(?:sh|py|js|mjs)$")
COMMAND_SHAPED = re.compile(r"^(?:[a-z][a-z0-9._+-]*|\[)$")
FENCE = re.compile(r"^\s*(```|~~~)\s*([\w-]*)")
# Fences that hold data or code, not commands. Every other fence (bash, text, none) is read.
DATA_LANGS = {"json", "jsonc", "js", "javascript", "ts", "typescript", "python", "py", "yaml", "yml",
              "toml", "html", "css", "diff", "mermaid", "sql", "markdown", "md"}
NEGATION = re.compile(r"\b(never|not|don't|do not|no longer|instead of|forbidden|refuse)\b", re.I)
SUBCOMMANDS = ({p.stem[len("drover-"):] for p in (ROOT / "lib").glob("drover-*") if p.suffix in (".sh", ".py")}
               | {"gate", "onboard", "skills", "config", "version", "help"})


def segments(command):
    """First words of each simple command in a shell line."""
    out = []
    for part in re.split(r"\$\(|`|&&|\|\||[;|(){}]", command):
        words = part.strip().split()
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):  # VAR=value prefix
            words = words[1:]
        while words and words[0] in ("!", "then", "do", "else", "if", "while", "until", "time"):
            words = words[1:]
        if words:
            out.append((words[0], " ".join(words)))
    return out


def sentence_of(line, start):
    """The sentence of `line` around position `start`: negation counts only there."""
    begin = max(line.rfind(". ", 0, start), line.rfind("; ", 0, start))
    end = min([i for i in (line.find(". ", start), line.find("; ", start)) if i >= 0] or [len(line)])
    return line[begin + 1:end]


def commands(path):
    """(line number, first word, command text) for each command-shaped thing the file names."""
    found, fence, lang = [], None, ""
    lines = path.read_text(encoding="utf-8").splitlines()
    joined, skip = "", False
    for n, line in enumerate(lines, 1):
        if re.match(r"##+ ", line) and path.name == "run.md":
            skip = bool(RUNNER_SECTIONS.match(line))
        if skip:
            continue
        m = FENCE.match(line)
        if m:
            fence, lang = (None, "") if fence else (m.group(1), m.group(2).lower())
            continue
        if fence and lang in DATA_LANGS:
            continue
        # Fenced prose (tables, trees, templates with `code` in them) is read like a line of text.
        prose = not fence or "`" in line or line.lstrip().startswith("|") or re.search("[←├└│]", line)
        if fence and not prose:
            text = (joined + " " + line.strip()) if joined else line.strip()
            if text.endswith("\\"):
                joined = text[:-1]
                continue
            joined = ""
            if text.startswith("#"):
                continue
            found += [(n, first, cmd) for first, cmd in segments(re.sub(r"\s+#.*$", "", text))]
        elif prose:
            # An inline span is a command when it has arguments; one word is a name. A span
            # in a sentence that says never / not / instead of is what a worker must not run.
            for m2 in re.finditer(r"`([^`]+)`", line):
                span = m2.group(1)
                if len(span.split()) > 1 and not NEGATION.search(sentence_of(line, m2.start())):
                    found += [(n, first, cmd) for first, cmd in segments(span)]
    return [(n, f, c) for n, f, c in found if COMMAND_SHAPED.match(f) or SCRIPT.match(f)]


def worker_prompt():
    """The prompt text dispatch_lane builds, and the files it tells the worker to read."""
    src = RUNNER.read_text()
    body = src[src.index("dispatch_lane() {"):src.index("\nissue_status() {")]
    prompts = re.findall(r'\)\s+prompt="(Run drover-[^"]*)"\s*;;', body)
    reads = sorted({m for p in prompts for m in re.findall(r"Read \$\{DROVER_HOME\}/(\S+?) ", p)})
    return body, prompts, [ROOT / r for r in reads]


failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def covered(first, cmd):
    if any(cmd == d or cmd.startswith(d + " ") for d in DENIED):
        return False
    if first == "drover":
        words = cmd.split()
        sub = words[1] if len(words) > 1 else ""
        if sub not in SUBCOMMANDS or (sub == "skills" and words[2:3] != ["install"]):
            return False
    return first in ALLOWED


# 0 — the extractor finds what stage 0 tripped on: a script run by path, and a command the
#     harness does not allow, in a fence or an inline span; prose and names are left alone.
import tempfile
with tempfile.TemporaryDirectory() as tmp:
    sample = Path(tmp) / "run.md"
    sample.write_text("Run `kill -9 1234` now. The `vitest` runner. Never run `gh pr merge 3`. Then `gh pr ready 3`.\n"
                      "```bash\nprintf '%s' \"$card\" | .claude/bin/drover-skills-ready.py --root .\n```\n"
                      "```text\ndrover card move 3 QA\ndrover cards move 3\n```\n```json\nrm -rf x\n```\n")
    got = [(f, covered(f, c)) for _, f, c in commands(sample)]
    check(got == [("kill", False), ("gh", True), ("printf", True), (".claude/bin/drover-skills-ready.py", False),
                  ("drover", True), ("drover", False)], f"extractor self-check: {got}")

# 1 — the worker prompt itself asks for no command, and points only at files that exist.
body, prompts, reads = worker_prompt()
check(len(prompts) == 3, f"expected the three lane prompts in dispatch_lane, found {len(prompts)}")
check(reads and all(p.is_file() for p in reads), f"prompt reads must exist: {reads}")
check("First operation" not in body and ".claude/bin" not in body,
      "the worker prompt must not tell the worker to run a command before it starts (stage 0)")
check("worker_start" in body and "worker-argv" in (ROOT / "lib/drover-worker.sh").read_text(),
      "dispatch_lane must start workers through drover-worker.sh and the harness worker-argv")

# 2 — every command in the files the prompt names and in the role rules is covered.
scanned = sorted(set(reads + ROLE_FILES))
seen = {}
for path in scanned:
    for n, first, cmd in commands(path):
        seen.setdefault(first, 0)
        seen[first] += 1
        if first in NON_COMMANDS:
            continue
        if SCRIPT.match(first):
            failures.append(f"{path.relative_to(ROOT)}:{n}: runs a script by path ({first}); "
                            f"call it through an allowed command (drover <name>, python3, bash)")
        elif not covered(first, cmd):
            failures.append(f"{path.relative_to(ROOT)}:{n}: `{cmd[:80]}` is not covered by harnesses/claude.json")
check(len(scanned) >= 10 and seen.get("drover", 0) > 5 and seen.get("gh", 0) > 5,
      f"the scan should find the role commands (files {len(scanned)}, commands {seen})")

# 3 — the tools the role rules depend on are allowed.
for tool in ("Read", "Edit", "Write", "Task", "Skill"):
    check(tool in PERMS["tools"], f"harness permissions should allow the {tool} tool")
# Claude's Edit(path) rule covers every file-editing tool; Write/MultiEdit/NotebookEdit path rules are rejected.
check("{drover_home}" in PERMS["read_only_dirs"] and PERMS["read_only_deny"] == ["Edit"],
      "workers read the drover install folder (skills, run.md) and never edit it (one Edit(path) rule)")
check("add_dir_flag" not in PERMS, "no --add-dir: the install folder is read-only for workers")

if failures:
    print("\n".join("FAIL: " + f for f in failures), file=sys.stderr)
    sys.exit(1)
print(f"PASS: test_harness_permissions.py ({len(scanned)} files, "
      f"{sum(seen.values())} commands, {len(seen)} distinct: {', '.join(sorted(seen))})")
