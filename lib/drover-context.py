#!/usr/bin/env python3
"""drover context: the context pack a lane worker starts from (specs/roles.md FR-CTX).

    drover context build|qa|review <issue> [--repo O/R]
    drover context review <issue> --claims [--repo O/R]

Prints, as one Markdown document: the role's rules (skills/drover/references/rules/<role>.md),
the shared rules (rules/shared.md, the block template, the markers, the repo's commit style
with 3 real subjects), the project instructions file and the role's configured skills, the
issue, the PR diff against base, and per role (FR-CTX-2):

    build   unresolved [builder] threads, its own open / not fixed review rows
    qa      unresolved [qa] threads, the builder report, its own open / not fixed rows
    review  the full previous review report; with --claims also every unresolved thread,
            the builder report and the QA report with its test command

`--claims` refuses (exit 6) until the review report for the PR's current head records a
`Hypotheses:` line (FR-CTX-3), so the reviewer's own pass comes before the builder's account.
Only comments by authors with write access count (drover-review-report.py's rule).
Exit: 0 ok · 6 --claims refused · 64 usage · 79 GitHub reads halted.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REF = HERE.parent / "skills/drover/references"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rr = load("review_report", "drover-review-report.py")
config = load("drover_config", "drover-config.py")
github_read = rr.github_read
M = rr.M
OWNER = {"build": "builder", "qa": "qa"}
PREFIX = {"build": "[builder]", "qa": "[qa]"}
THREADS = ("query($owner:String!,$repo:String!,$pr:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$pr)"
           "{reviewThreads(first:100){nodes{isResolved path line comments(first:1){nodes{body}}}}}}}")


def section(title, body):
    return f"## {title}\n\n{(body or '').strip() or '(none)'}\n"


def latest(comments, header, permission):
    hits = [c for c in comments if c["body"].startswith(header) and rr.trusted(c, permission)]
    return hits[-1]["body"] if hits else ""


def project_files(root):
    for name in ("CLAUDE.md", "AGENTS.md"):
        p = root / name
        if p.is_file():
            return f"`{name}`:\n\n{p.read_text(encoding='utf-8')}"
    return ""


def skills(cfg, role, labels):
    names = list(((cfg.get("roles") or {}).get(role) or {}).get("skills", []))
    for label in labels:
        names += [s for s in ((cfg.get("labels") or {}).get(label) or {}).get(role, []) if s not in names]
    return ", ".join(names)


def find_pr(issue):
    _, prs = github_read.read(["pr", "list", "--state", "open", "--json", "number,title,body,url,headRefName,files"],
                              "prs-open")
    return next((p for p in prs if p["headRefName"].startswith(f"issue-{issue}-")), None)


def diff(pr):
    _, meta = github_read.read(["pr", "view", str(pr), "--json", "files,labels,body,additions,deletions,changedFiles"],
                               "metadata")
    text, _ = github_read.read(["pr", "diff", str(pr)], "diff", meta)
    return text


def threads(repo, pr, prefix):
    owner, name = repo.split("/")
    _, value = github_read.read(["api", "graphql", "-f", f"query={THREADS}", "-F", f"owner={owner}",
                                 "-F", f"repo={name}", "-F", f"pr={pr}"], "object")
    out = []
    for t in value["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]:
        body = (t["comments"]["nodes"] or [{"body": ""}])[0]["body"]
        if not t["isResolved"] and (prefix is None or body.startswith(prefix)):
            out.append(f"- `{t['path']}:{t.get('line') or '?'}` {body.splitlines()[0] if body else ''}")
    return "\n".join(out)


def own_rows(report, owner):
    rows = [r for r in report["rows"] if r.get("owner") == owner and r.get("status") in ("open", "not fixed")]
    return "\n".join(f"- {r['id']} {r.get('class')} · {r.get('severity')} · {r.get('where')} · {r.get('finding')} "
                     f"({r.get('status')})" for r in rows)


def pack(role, issue, claims, repo):
    root = config.root()
    cfg = config.load_config()
    pr = rr.PR(0, repo)  # resolves the repo and the trust lookup; the number is set below
    _, card = github_read.read(["issue", "view", str(issue), "--json", "number,title,body,labels"], "issue")
    labels = [l["name"] if isinstance(l, dict) else l for l in card.get("labels") or []]
    open_pr = find_pr(issue)
    comments, report, rep_comment = [], None, None
    if open_pr:
        pr.pr = open_pr["number"]
        comments = pr.comments()
        rep_comment, report = pr.latest(comments)
    perm = pr.gh.permission

    if claims:
        if not open_pr:
            return None, "no open PR for this card"
        _, head = github_read.read(["pr", "view", str(pr.pr), "--json", "body,headRefOid"], "body")
        hyp = next((l for l in (report or {}).get("lines", []) if l.startswith(M["hypotheses_line"])), "")
        if not report or report["head"] != head["headRefOid"] or not hyp[len(M["hypotheses_line"]):].strip():
            return None, (f"record your hypotheses for head {head['headRefOid']} first: put the review report "
                          f"with a `{M['hypotheses_line'].strip()}` line (drover review-report put)")
        return "\n".join([
            f"# drover context review #{issue} --claims (PR #{pr.pr}, head {head['headRefOid']})\n",
            section("Unresolved threads", threads(pr.repo, pr.pr, None)),
            section("Builder report", latest(comments, "[builder] [report]", perm)),
            section("QA report and test command", latest(comments, "[qa] [report]", perm)),
        ]), None

    style = subprocess.run([sys.executable, str(HERE / "drover-commit-style.py"), "--root", str(root),
                            "--config", str(config.config_path()), "--prompt"], capture_output=True, text=True).stdout
    parts = [
        f"# drover context {role} #{issue}" + (f" (PR #{pr.pr})" if open_pr else " (no PR yet)") + "\n",
        section(f"Role rules: rules/{role}.md", (REF / f"rules/{role}.md").read_text(encoding="utf-8")),
        section("Shared rules: rules/shared.md", (REF / "rules/shared.md").read_text(encoding="utf-8")),
        section("Block format: block-template.md", (REF / "block-template.md").read_text(encoding="utf-8")),
        section("Markers: lib/drover-markers.json", "```json\n" + (HERE / "drover-markers.json").read_text() + "```"),
        section("Commit style", style),
        section("Project instructions", project_files(root)),
        section("Configured skills", skills(cfg, role, labels)),
        section(f"Issue #{issue}: {card['title']}", card["body"]),
        section("Diff against base" + (" (own branch)" if role == "build" else ""), diff(pr.pr) if open_pr else ""),
    ]
    if role in OWNER:
        parts.append(section(f"Unresolved {PREFIX[role]} threads", threads(pr.repo, pr.pr, PREFIX[role]) if open_pr else ""))
    if role == "qa":
        parts.append(section("Builder report", latest(comments, "[builder] [report]", perm)))
    if role in OWNER:
        parts.append(section(f"Your open review findings ({OWNER[role]})", own_rows(report, OWNER[role]) if report else ""))
    else:
        parts.append(section("Previous review report", rep_comment["body"] if rep_comment else ""))
        parts.append("Next: check each previous finding against the diff, write 2-4 hypotheses of your own, put the "
                     f"report with a `{M['hypotheses_line'].strip()}` line, then run "
                     f"`drover context review {issue} --claims` for the builder and QA reports and the threads.\n")
    return "\n".join(parts), None


def main(argv):
    p = argparse.ArgumentParser(prog="drover context", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("role", choices=("build", "qa", "review"))
    p.add_argument("issue", type=int)
    p.add_argument("--claims", action="store_true")
    p.add_argument("--repo")
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return 64 if e.code else 0
    if args.claims and args.role != "review":
        print("drover context: --claims is for the review role", file=sys.stderr)
        return 64
    try:
        out, refused = pack(args.role, args.issue, args.claims, args.repo)
    except github_read.ReadHalted as e:
        print(f"drover context: HALT: {e}", file=sys.stderr)
        return github_read.HALTED
    except ValueError as e:
        print(f"drover context: {e}", file=sys.stderr)
        return 64
    if refused:
        print(f"drover context: refused: {refused}", file=sys.stderr)
        return 6
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
