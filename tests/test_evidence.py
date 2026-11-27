#!/usr/bin/env python3
"""drover evidence push (specs/conventions.md FR-EVD-5..7), against a local bare repo as origin:
the first push creates the orphan branch, a second keeps the first's files, the caller's branch,
index and working tree stay as they were, URLs pin the commit sha, pr mode writes under
evidence.path on the PR branch, and a push that loses a race is rebuilt on the new tip."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
DROVER = ROOT / "bin/drover"
# Moves the branch once, mid-push, as a second lane would: the push then fails its ref update.
RACE_HOOK = r'''#!/bin/sh
[ -f "$GIT_DIR/raced" ] && exit 0
touch "$GIT_DIR/raced"
unset GIT_QUARANTINE_PATH GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES  # git forbids ref updates in quarantine
while read old new ref; do
  blob=$(echo other-lane | git hash-object -w --stdin)
  export GIT_INDEX_FILE="$GIT_DIR/race-index"
  git read-tree "$old"
  git update-index --add --cacheinfo "100644,$blob,issue-7/v1/other.log"
  c=$(git commit-tree "$(git write-tree)" -p "$old" -m race)
  git update-ref "$ref" "$c" "$old"
done
exit 0
'''


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class EvidencePush(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x", GIT_CONFIG_GLOBAL="/dev/null")
        self.env.pop("DROVER_CONFIG", None)
        self.env.pop("DROVER_ROLE", None)
        self.bare = self.tmp / "acme" / "app.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True, env=self.env)
        self.repo = self.tmp / "work"
        subprocess.run(["git", "init", "-q", "-b", "issue-7-cart", str(self.repo)], check=True, env=self.env)
        self.g("remote", "add", "origin", str(self.bare))
        (self.repo / "app.txt").write_text("app\n")
        self.g("add", "app.txt")
        self.g("commit", "-qm", "init")
        self.g("push", "-q", "origin", "issue-7-cart")
        self.files = self.tmp / "shots"
        self.files.mkdir()
        for name in ("desktop.png", "REPORT.md"):
            (self.files / name).write_text(name)
        self.config({"mode": "branch", "branch": "drover-evidence"})

    def g(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True,
                              env=self.env).stdout.strip()

    def config(self, evidence):
        d = self.repo / ".drover"
        d.mkdir(exist_ok=True)
        (d / "config.json").write_text(json.dumps({"project": {"owner": "acme", "number": 1}, "evidence": evidence}))
        self.g("add", ".drover/config.json")
        self.g("commit", "-qm", "config")

    def push(self, version, *names):
        r = subprocess.run([str(DROVER), "evidence", "push", "7", version, *(str(self.files / n) for n in names)],
                           cwd=self.repo, capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.stderr = r.stderr
        return r.stdout.split()

    def tree(self, ref):
        return sorted(subprocess.run(["git", "ls-tree", "-r", "--name-only", ref], cwd=self.bare, check=True,
                                     capture_output=True, text=True).stdout.split())

    def test_branch_mode_creates_then_appends_and_leaves_caller_alone(self):
        (self.repo / "app.txt").write_text("dirty\n")  # an uncommitted edit must survive
        before = (self.g("rev-parse", "HEAD"), self.g("symbolic-ref", "HEAD"), self.g("status", "--porcelain"))
        urls = self.push("v1", "desktop.png", "REPORT.md")
        tip = git(self.bare, "rev-parse", "refs/heads/drover-evidence")
        self.assertEqual(urls, [f"https://github.com/acme/app/raw/{tip}/issue-7/v1/desktop.png",
                                f"https://github.com/acme/app/raw/{tip}/issue-7/v1/REPORT.md"])
        self.assertEqual(git(self.bare, "rev-list", "--count", "drover-evidence"), "1")  # orphan: no parent
        self.push("v2", "REPORT.md")
        self.assertEqual(self.tree("drover-evidence"),
                         ["issue-7/v1/REPORT.md", "issue-7/v1/desktop.png", "issue-7/v2/REPORT.md"])
        self.assertEqual(before, (self.g("rev-parse", "HEAD"), self.g("symbolic-ref", "HEAD"),
                                  self.g("status", "--porcelain")))
        self.assertEqual((self.repo / "app.txt").read_text(), "dirty\n")
        self.assertEqual(self.tree("issue-7-cart"), ["app.txt"])  # FR-EVD-3: the PR branch holds no evidence
        self.assertNotIn("drover-evidence", self.g("branch", "--list"))

    def test_pr_mode_writes_under_path_on_the_pr_branch(self):
        self.config({"mode": "pr", "path": "docs/qa"})
        self.g("push", "-q", "origin", "issue-7-cart")
        urls = self.push("v1", "REPORT.md")
        tip = git(self.bare, "rev-parse", "refs/heads/issue-7-cart")
        self.assertEqual(urls, [f"https://github.com/acme/app/raw/{tip}/docs/qa/issue-7/v1/REPORT.md"])
        self.assertIn("docs/qa/issue-7/v1/REPORT.md", self.tree("issue-7-cart"))
        self.assertEqual(git(self.bare, "rev-parse", "issue-7-cart~1"), self.g("rev-parse", "HEAD"))

    def test_race_is_retried_and_keeps_both_lanes_files(self):
        self.push("v1", "desktop.png")
        hook = self.bare / "hooks/pre-receive"
        hook.write_text(RACE_HOOK)
        hook.chmod(0o755)
        urls = self.push("v1", "REPORT.md")
        self.assertIn("retrying on the new tip", self.stderr)
        tip = git(self.bare, "rev-parse", "refs/heads/drover-evidence")
        self.assertEqual(urls, [f"https://github.com/acme/app/raw/{tip}/issue-7/v1/REPORT.md"])
        self.assertEqual(self.tree("drover-evidence"),
                         ["issue-7/v1/REPORT.md", "issue-7/v1/desktop.png", "issue-7/v1/other.log"])

    def test_refuses_an_image_over_5_mb(self):
        (self.files / "huge.png").write_bytes(b"0" * (5 * 1024 * 1024 + 1))
        r = subprocess.run([str(DROVER), "evidence", "push", "7", "v1", str(self.files / "huge.png")],
                           cwd=self.repo, capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 65, r.stderr)
        self.assertIn("1920", r.stderr)


if __name__ == "__main__":
    unittest.main()
