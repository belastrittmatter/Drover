#!/usr/bin/env python3
"""drover review-report: the reviewer's one PR comment, enforced in code (specs/roles.md
FR-REV-5, 6, 8, 12, FR-BLD-7; specs/gate.md exit 6).

    drover review-report get   --pr N [--repo O/R]
        print the latest report as JSON ({} when there is none)
    drover review-report put   --pr N --head SHA --file REPORT.md [--repo O/R]
        validate REPORT.md against the previous report, stamp the reviewed head in the
        marker, add the `Test edits since QA pass:` line, then edit the report in place
        (or post the first one)
    drover review-report check --pr N --head SHA [--repo O/R]
        the gate's check: the latest report names SHA, is merge-ready, and has no
        Blocker row that is open or not fixed
    drover review-report repro --base REF --cmd "<test command>" [--commit SHA]
        bug cards: run the builder's reproduction test (the first commit after REF, or
        SHA) at that commit's parent

Run from the PR's worktree: put and repro read local git. The marker, lists and machine
lines come from drover-markers.json (FR-CNV-4).
Exit: 0 ok · 1 repro passes at base (bounce) · 6 check refused · 64 usage · 65 report
rejected · 79 GitHub reads halted.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("github_read", HERE / "drover-github-read.py")
github_read = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(github_read)

M = json.loads((HERE / "drover-markers.json").read_text(encoding="utf-8"))["review_report"]
SHA = re.compile(r"[0-9a-f]{40}\Z")
MARKER = re.compile(re.escape(M["marker"]) + r"(?: head=([0-9a-f]{40}))? -->")
HEADER = re.compile(r"^\[reviewer\] \[report\] ([a-z-]+)")
WHERE = re.compile(r"^`?([^`\s:]+):(\d+)")
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)/|[._-](test|spec)\.[A-Za-z0-9]+$|(^|/)test_[^/]+$")


class Rejected(Exception):
    pass


def git(*args, check=True):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and r.returncode:
        raise Rejected(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def parse(body):
    """{head, state, rows, lines} of a report body; None when it is not one."""
    lines = body.splitlines()
    m = MARKER.match(lines[0].strip()) if lines else None
    if not m:
        return None
    state = next((h.group(1) for h in map(HEADER.match, lines) if h), None)
    rows, cols = [], None
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else []
        if cells[:1] == ["ID"]:
            cols = cells
        elif cols and cells and re.fullmatch(r"R\d+", cells[0]):
            n = len(cols)
            if len(cells) > n:  # a `|` inside the Finding cell
                at = cols.index("Finding")
                cells = cells[:at] + [" | ".join(cells[at:at + len(cells) - n + 1])] + cells[at + len(cells) - n + 1:]
            row = dict(zip([c.lower() for c in cols], cells))
            if "severity" not in row:  # a report from before the Severity column
                row["severity"] = "Blocker" if "blocker" in row.get("finding", "").lower() else "Should fix"
            rows.append(row)
    return {"head": m.group(1), "state": state, "rows": rows, "lines": lines}


class PR:
    def __init__(self, pr, repo):
        self.pr, self.repo = pr, repo or "{owner}/{repo}"

    def comments(self):
        _, pages = github_read.read(["api", "--paginate", "--slurp",
                                     f"repos/{self.repo}/issues/{self.pr}/comments"], "comments")
        return [c for page in pages for c in page]

    def latest(self, comments):
        found = [(c, parse(c["body"])) for c in comments]
        found = [(c, r) for c, r in found if r]
        return found[-1] if found else (None, None)

    def write(self, comment, body):
        github_read.check_halt()
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(body)
        try:
            path = (f"repos/{self.repo}/issues/comments/{comment['id']}" if comment
                    else f"repos/{self.repo}/issues/{self.pr}/comments")
            r = subprocess.run(["gh", "api", "-X", "PATCH" if comment else "POST", path, "-F", f"body=@{f.name}",
                                "--jq", ".html_url"], capture_output=True, text=True)
        finally:
            os.unlink(f.name)
        if r.returncode:
            raise Rejected(f"GitHub refused the report write: {r.stderr.strip()}")
        return r.stdout.strip()


def validate(new, prev, head):
    """Every way `new` breaks FR-REV-5/6 against `prev`, as a list of lines."""
    out = []
    if new["state"] not in M["states"]:
        out.append(f"header state {new['state']!r} is not one of {', '.join(M['states'])}")
    if not any(line.startswith(M["models_line"] + "builder ") and " · reviewer " in line for line in new["lines"]):
        out.append(f"missing the `{M['models_line']}builder <model> · reviewer <model>` line")
    ids = [r["id"] for r in new["rows"]]
    if len(ids) != len(set(ids)):
        out.append("finding ids repeat")
    for r in new["rows"]:
        for key, allowed in (("owner", M["owners"]), ("class", M["classes"]),
                             ("severity", M["severities"]), ("status", M["statuses"])):
            if r.get(key) not in allowed:
                out.append(f"{r['id']}: {key} {r.get(key)!r} is not one of {', '.join(allowed)}")
    if new["state"] == "merge-ready":
        out += [f"{r['id']}: merge-ready with a Blocker that is {r['status']}" for r in new["rows"]
                if r.get("severity") == "Blocker" and r.get("status") in ("open", "not fixed")]
    if not prev:
        return out
    by_id = {r["id"]: r for r in new["rows"]}
    out += [f"{r['id']}: dropped (rows are never deleted)" for r in prev["rows"] if r["id"] not in by_id]
    if prev["head"] == head:
        return out  # same head: an in-round update (merged, a header change)
    changed = None
    if prev["head"] and git("cat-file", "-e", prev["head"] + "^{commit}", check=False).returncode == 0:
        changed = set(git("diff", "--name-only", prev["head"], head).stdout.split())
    elif prev["head"]:
        # shortcut: a previous head gone from local git (a force push) skips the changed-file check.
        print(f"review-report: previous head {prev['head']} is not in local git; changed-file check skipped",
              file=sys.stderr)
    for old in prev["rows"]:
        row = by_id.get(old["id"])
        if not row:
            continue
        if row.get("status") == "open":
            out.append(f"{row['id']}: still open on a new head; mark it fixed, not fixed or no longer applies")
        if row.get("status") != "fixed" or old.get("status") == "fixed":
            continue
        w = WHERE.match(row.get("where", ""))
        if not w:
            out.append(f"{row['id']}: fixed needs a file:line in Where, got {row.get('where')!r}")
            continue
        path, line = w.group(1), int(w.group(2))
        shown = git("show", f"{head}:{path}", check=False)
        if shown.returncode or not 1 <= line <= max(1, len(shown.stdout.splitlines())):
            out.append(f"{row['id']}: fixed cites {path}:{line}, which is not in the reviewed head")
        elif changed is not None and path not in changed:
            out.append(f"{row['id']}: fixed cites {path}, which did not change since {prev['head'][:12]}")
    return out


def test_edits(comments, head):
    """The `Test edits since QA pass:` line (FR-REV-12): test files changed by commits after the
    head QA last passed (the QA head line of the latest `[qa] [report] pass` comment)."""
    qa = None
    for c in comments:
        lines = c["body"].splitlines()
        if lines and lines[0].startswith("[qa] [report] pass"):
            qa = next((l[len(M["qa_head_line"]):].strip() for l in lines if l.startswith(M["qa_head_line"])), qa)
    if not qa or git("cat-file", "-e", qa + "^{commit}", check=False).returncode:
        return M["test_edits_line"] + "unknown (no QA head recorded)"
    log = git("log", "--format=@%h", "--name-only", f"{qa}..{head}").stdout
    hits, sha = [], ""
    for line in log.splitlines():
        if line.startswith("@"):
            sha = line[1:]
        elif line and TEST_PATH.search(line):
            hits.append(f"{sha} {line}")
    return M["test_edits_line"] + (", ".join(hits) or "none")


def put(args):
    head = git("rev-parse", "--verify", args.head + "^{commit}").stdout.strip()
    text = Path(args.file).read_text(encoding="utf-8")
    lines = [l for l in text.strip("\n").splitlines()
             if not MARKER.match(l.strip()) and not l.startswith(M["test_edits_line"])]
    pr = PR(args.pr, args.repo)
    comments = pr.comments()
    body = "\n".join([f"{M['marker']} head={head} -->", *lines, "", test_edits(comments, head)]) + "\n"
    new = parse(body)
    comment, prev = pr.latest(comments)
    problems = validate(new, prev, head)
    if problems:
        raise Rejected("\n".join(problems))
    print(pr.write(comment, body) or "review report written")
    return 0


def check(args):
    _, rep = PR(args.pr, args.repo).latest(PR(args.pr, args.repo).comments())
    why = []
    if not rep:
        why.append("no review report on this PR")
    else:
        if rep["head"] != args.head:
            why.append(f"the latest review report names head {rep['head'] or 'none'}, not {args.head}")
        if rep["state"] != "merge-ready":
            why.append(f"the latest review report says {rep['state'] or 'nothing'}, not merge-ready")
        why += [f"{r['id']} is a Blocker that is {r['status']}" for r in rep["rows"]
                if r.get("severity") == "Blocker" and r.get("status") in ("open", "not fixed")]
    for w in why:
        print(f"review-report: {w}", file=sys.stderr)
    return 6 if why else 0


def repro(args):
    commit = args.commit or (git("rev-list", "--reverse", "--first-parent", f"{args.base}..HEAD").stdout.split() or [""])[0]
    if not commit:
        raise Rejected(f"no commit after {args.base}")
    base = git("rev-parse", f"{commit}^").stdout.strip()
    files = git("diff", "--name-only", base, commit).stdout.split()
    scratch = tempfile.mkdtemp()
    try:
        git("worktree", "add", "--detach", "--quiet", scratch, base)
        if files:
            subprocess.run(["git", "-C", scratch, "checkout", commit, "--", *files], check=True, capture_output=True)
        rc = subprocess.run(args.cmd, shell=True, cwd=scratch, capture_output=True).returncode
    finally:
        git("worktree", "remove", "--force", scratch, check=False)
    if rc == 0:
        print(f"repro: the reproduction test from {commit[:12]} passes at base {base[:12]}: "
              f"Verification miss for the builder, bounce the card")
        return 1
    print(f"repro: the reproduction test from {commit[:12]} fails at base {base[:12]} (exit {rc}), as it should")
    return 0


def main(argv):
    p = argparse.ArgumentParser(prog="drover review-report", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="action", required=True)
    for name in ("get", "put", "check"):
        s = sub.add_parser(name)
        s.add_argument("--pr", required=True, type=int)
        s.add_argument("--repo")
        if name != "get":
            s.add_argument("--head", required=True)
        if name == "put":
            s.add_argument("--file", required=True)
    s = sub.add_parser("repro")
    s.add_argument("--base", required=True)
    s.add_argument("--cmd", required=True)
    s.add_argument("--commit")
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return 64 if e.code else 0
    try:
        if args.action == "get":
            comment, rep = PR(args.pr, args.repo).latest(PR(args.pr, args.repo).comments())
            print(json.dumps({"id": comment["id"], "url": comment.get("html_url"), "head": rep["head"],
                              "state": rep["state"], "rows": rep["rows"], "body": comment["body"]} if rep else {}))
            return 0
        if args.action == "check" and not SHA.match(args.head):
            print("review-report: --head must be a full 40-hex sha", file=sys.stderr)
            return 64
        return {"put": put, "check": check, "repro": repro}[args.action](args)
    except github_read.ReadHalted as e:
        print(f"review-report: HALT: {e}", file=sys.stderr)
        return github_read.HALTED
    except Rejected as e:
        print("review-report: rejected:\n" + "\n".join("  " + l for l in str(e).splitlines()), file=sys.stderr)
        return 65
    except subprocess.CalledProcessError as e:
        print(f"review-report: {e}", file=sys.stderr)
        return 64
    except ValueError as e:  # a malformed read the reader refused
        print(f"review-report: {e}", file=sys.stderr)
        return 65 if args.action == "put" else 6


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
