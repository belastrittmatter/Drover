#!/usr/bin/env python3
"""drover evidence push (specs/conventions.md FR-EVD-5..7), against a local bare repo as origin:
the first push creates the orphan branch, a second keeps the first's files, the caller's branch,
index and working tree stay as they were, URLs pin the commit sha, pr mode writes under
evidence.path on the PR branch, a push that loses a race is rebuilt on the new tip (both modes),
and any other refused push stops after one attempt."""
import importlib.util
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


# A remote that refuses every push for a reason that is not a race (a policy hook).
DENY_HOOK = r'''#!/bin/sh
echo x >> "$GIT_DIR/denied"
echo "evidence pushes are disabled here" >&2
exit 1
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

    def run_push(self, version, *paths):
        return subprocess.run([str(DROVER), "evidence", "push", "7", version, *map(str, paths)],
                              cwd=self.repo, capture_output=True, text=True, env=self.env)

    def hook(self, text):
        hook = self.bare / "hooks/pre-receive"
        hook.write_text(text)
        hook.chmod(0o755)

    def push(self, version, *names):
        r = self.run_push(version, *(self.files / n for n in names))
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
        self.hook(RACE_HOOK)
        urls = self.push("v1", "REPORT.md")
        self.assertRegex(self.stderr, r"retrying on the new tip: .*(failed to update ref|cannot lock ref|\[rejected\])")
        tip = git(self.bare, "rev-parse", "refs/heads/drover-evidence")
        self.assertEqual(urls, [f"https://github.com/acme/app/raw/{tip}/issue-7/v1/REPORT.md"])
        self.assertEqual(self.tree("drover-evidence"),
                         ["issue-7/v1/REPORT.md", "issue-7/v1/desktop.png", "issue-7/v1/other.log"])

    def test_pr_mode_race_is_retried(self):
        self.config({"mode": "pr", "path": "docs/qa"})
        self.g("push", "-q", "origin", "issue-7-cart")
        self.hook(RACE_HOOK)
        urls = self.push("v1", "REPORT.md")
        self.assertRegex(self.stderr, r"retrying on the new tip: .*(failed to update ref|cannot lock ref|\[rejected\])")
        tip = git(self.bare, "rev-parse", "refs/heads/issue-7-cart")
        self.assertEqual(urls, [f"https://github.com/acme/app/raw/{tip}/docs/qa/issue-7/v1/REPORT.md"])
        self.assertTrue({"docs/qa/issue-7/v1/REPORT.md", "issue-7/v1/other.log"} <= set(self.tree("issue-7-cart")))

    def test_a_refused_push_that_is_no_race_stops_after_one_attempt(self):
        self.hook(DENY_HOOK)
        r = self.run_push("v1", self.files / "REPORT.md")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("evidence pushes are disabled here", r.stderr)
        self.assertNotIn("retrying", r.stderr)
        self.assertEqual((self.bare / "denied").read_text(), "x\n")

    def test_refuses_an_evidence_branch_that_is_a_base_branch(self):
        for name in ("main", "master"):
            self.config({"mode": "branch", "branch": name})
            r = self.run_push("v1", self.files / "REPORT.md")
            self.assertEqual(r.returncode, 65, r.stderr)
            self.assertIn("is a base branch", r.stderr)
        self.assertNotIn("main", git(self.bare, "branch", "--list"))

    def test_url_encodes_path_segments(self):
        (self.files / "before fix.png").write_text("x")
        urls = self.push("v1", "before fix.png")
        tip = git(self.bare, "rev-parse", "refs/heads/drover-evidence")
        self.assertEqual(urls, [f"https://github.com/acme/app/raw/{tip}/issue-7/v1/before%20fix.png"])
        self.assertEqual(git(self.bare, "ls-tree", "-r", "--name-only", "drover-evidence"), "issue-7/v1/before fix.png")

    def test_refuses_two_files_with_one_name(self):
        (self.files / "a").mkdir()
        (self.files / "a/REPORT.md").write_text("other")
        r = self.run_push("v1", self.files / "REPORT.md", self.files / "a/REPORT.md")
        self.assertEqual(r.returncode, 65, r.stderr)
        self.assertIn("two files named REPORT.md", r.stderr)

    def test_refuses_an_image_over_5_mb(self):
        (self.files / "huge.png").write_bytes(b"0" * (5 * 1024 * 1024 + 1))
        r = self.run_push("v1", self.files / "huge.png")
        self.assertEqual(r.returncode, 65, r.stderr)
        self.assertIn("1920", r.stderr)


class OwnerRepo(unittest.TestCase):
    def test_remote_url_forms(self):
        spec = importlib.util.spec_from_file_location("drover_evidence", ROOT / "lib/drover-evidence.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for url in ("git@github.com:acme/app.git", "https://github.com/acme/app", "https://github.com/acme/app.git/",
                    "ssh://git@github.com/acme/app.git"):
            mod.git = lambda *a, url=url, **k: subprocess.CompletedProcess(a, 0, url + "\n", "")
            self.assertEqual(mod.owner_repo(), ("acme", "app"), url)


if __name__ == "__main__":
    unittest.main()
