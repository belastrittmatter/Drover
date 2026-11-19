#!/usr/bin/env python3
"""Worker prompt -> guard -> Git -> cleanup contract. No live workers or GitHub."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import subprocess
import tempfile
import unittest

PACK = Path(__file__).resolve().parents[1]


class WorktreePaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "repo"
        self.root.mkdir()
        self.env = dict(os.environ, GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example.com",
                        GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test@example.com",
                        CLEANUP_WT_GH="/nonexistent")
        self.run_cmd("git", "init", "-q", "-b", "main")
        (self.root / ".gitignore").write_text(".drover/worktrees/\n.worktrees/\n.planning/\n")
        self.run_cmd("git", "add", ".gitignore")
        self.run_cmd("git", "commit", "-qm", "fixture")
        # The recovery lookup uses ls-remote; keep it on this local fixture.
        self.run_cmd("git", "remote", "add", "origin", str(self.root))

    def run_cmd(self, *cmd, cwd=None, input=None, check=True):
        return subprocess.run(cmd, cwd=cwd or self.root, env=self.env, input=input,
                              text=True, capture_output=True, check=check)

    def stub_workers(self):
        bin_dir = Path(self.tmp.name) / "bin"
        bin_dir.mkdir()
        for name, body in {
            "gh": '#!/bin/sh\nprintf \'%s\\n\' \'{"number":9,"title":"fixture","body":"offline","labels":[]}\'\n',
            "claude": '#!/bin/sh\ncat >/dev/null\npwd -P > "$WORKER_CWD"\ngit commit -q --allow-empty -m "Build card 9" -m "Drover: done #9"\n',
        }.items():
            path = bin_dir / name
            path.write_text(body)
            path.chmod(0o755)
        self.run_cmd(sys.executable, str(PACK / 'tests/fixtures/install_worker_skills.py'), str(self.root))
        helper_bin = self.root / '.claude/bin'; helper_bin.mkdir(parents=True)
        for name in ['drover-github-read.py', 'drover-skills-ready.py']:
            shutil.copy2(PACK / 'lib' / name, helper_bin / name)
        self.env['DROVER_SKILLS_HOME'] = str(Path(self.tmp.name) / 'home')
        Path(self.env['DROVER_SKILLS_HOME']).mkdir()
        self.env.update(PATH=str(bin_dir) + os.pathsep + self.env["PATH"], BASE_BRANCH="main",
                        REPO_DIR=str(self.root), WORKER_CWD=str(Path(self.tmp.name) / "worker-cwd"))

    def dispatch(self):
        return self.run_cmd("bash", str(PACK / "skills/drover-build/scripts/drover-build-dispatch.sh"),
                            "9", check=False)

    def test_legacy_dispatch_creates_canonical_worktree(self):
        self.stub_workers()
        result = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(Path(self.env["WORKER_CWD"]).read_text().strip(),
                         str(self.root / ".drover/worktrees/issue-9"))

    def test_legacy_checkout_is_not_abandoned_or_clobbered(self):
        self.legacy_dispatch_halts(".worktrees/issue-9")

    def test_pre_rename_checkout_is_not_abandoned_or_clobbered(self):
        self.legacy_dispatch_halts(".claude/worktrees/issue-9")

    def legacy_dispatch_halts(self, legacy):
        self.stub_workers()
        old = self.root / legacy
        self.run_cmd("git", "worktree", "add", "-b", "previous-" + legacy.replace("/", "-"), str(old), "main")
        (old / "unfinished.txt").write_text("keep this work")
        result = self.dispatch()
        self.assertEqual(result.returncode, 64, result.stdout + result.stderr)
        self.assertIn("legacy worktree", result.stderr)
        self.assertEqual((old / "unfinished.txt").read_text(), "keep this work")
        self.assertFalse((self.root / ".drover/worktrees/issue-9").exists())
        self.assertFalse(Path(self.env["WORKER_CWD"]).exists())

    def runner_helper(self, body):
        return self.run_cmd("bash", "-c", 'SB_LIB_ONLY=1 . "$1"\n' + body, "fixture",
                            str(PACK / "lib/drover-run.sh"), check=False)

    def test_runner_halts_for_legacy_checkout_without_removing_it(self):
        old = self.root / ".worktrees/issue-7-build"
        self.run_cmd("git", "worktree", "add", "-b", "issue-7", str(old), "main")
        (old / "unfinished.txt").write_text("keep this work")
        result = self.runner_helper("check_legacy_worktrees")
        self.assertEqual(result.returncode, 73, result.stdout + result.stderr)
        self.assertIn("legacy worktree .worktrees/issue-7-build", result.stdout)
        self.assertEqual((old / "unfinished.txt").read_text(), "keep this work")

    def test_runner_halts_for_pre_rename_card_worktree_without_removing_it(self):
        old = self.root / ".claude/worktrees/issue-7-build"
        self.run_cmd("git", "worktree", "add", "-b", "issue-7", str(old), "main")
        (old / "unfinished.txt").write_text("keep this work")
        result = self.runner_helper("check_legacy_worktrees")
        self.assertEqual(result.returncode, 73, result.stdout + result.stderr)
        self.assertIn("legacy worktree .claude/worktrees/issue-7-build", result.stdout)
        self.assertIn(".drover/worktrees/", result.stdout)
        self.assertEqual((old / "unfinished.txt").read_text(), "keep this work")

    def test_reclaim_uses_canonical_path_and_keeps_dirty_work(self):
        clean = self.root / ".drover/worktrees/issue-7-build"
        dirty = self.root / ".drover/worktrees/issue-8-build"
        for n, path in ((7, clean), (8, dirty)):
            self.run_cmd("git", "worktree", "add", "-b", "issue-" + str(n), str(path), "main")
        (dirty / "unfinished.txt").write_text("keep this work")
        result = self.runner_helper(r'''
gh() { return 0; }
issue_locked() { return 1; }
set_card_status() { printf '%s %s\n' "$1" "$2" >> moved; }
PROJECT_ITEMS_JSON='{"items":[
 {"id":"clean","status":"Building","content":{"type":"Issue","number":7}},
 {"id":"dirty","status":"Building","content":{"type":"Issue","number":8}}]}'
reclaim_stranded_building
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(clean.exists(), "clean canonical worktree should be released")
        self.assertEqual((dirty / "unfinished.txt").read_text(), "keep this work")
        self.assertEqual((self.root / "moved").read_text(), "clean Ready\n")

    def test_stale_scan_never_deletes_unregistered_or_unrelated_folders(self):
        for name in ("issue-7-build", "another-agent"):
            path = self.root / ".drover/worktrees" / name
            path.mkdir(parents=True)
            (path / "unfinished.txt").write_text("keep this work")
        result = self.runner_helper("cleanup_stale_worktrees")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ("issue-7-build", "another-agent"):
            self.assertEqual((self.root / ".drover/worktrees" / name / "unfinished.txt").read_text(),
                             "keep this work")

    def test_stale_scan_keeps_detached_commits(self):
        path = self.root / ".drover/worktrees/issue-7-build"
        self.run_cmd("git", "worktree", "add", "--detach", str(path), "main")
        self.run_cmd("git", "commit", "--allow-empty", "-qm", "unreferenced work", cwd=path)
        result = self.runner_helper("cleanup_stale_worktrees")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.run_cmd("git", "log", "-1", "--format=%s", cwd=path).stdout.strip(),
                         "unreferenced work")

    def test_reclaim_keeps_detached_commits_and_building_status(self):
        path = self.root / ".drover/worktrees/issue-7-build"
        self.run_cmd("git", "worktree", "add", "--detach", str(path), "main")
        self.run_cmd("git", "commit", "--allow-empty", "-qm", "unreferenced work", cwd=path)
        result = self.runner_helper(r'''
gh() { return 0; }
issue_locked() { return 1; }
set_card_status() { printf '%s %s\n' "$1" "$2" >> moved; }
PROJECT_ITEMS_JSON='{"items":[
 {"id":"detached","status":"Building","content":{"type":"Issue","number":7}}]}'
reclaim_stranded_building
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(path.exists(), "detached commits need their existing worktree HEAD")
        self.assertEqual(self.run_cmd("git", "log", "-1", "--format=%s", cwd=path).stdout.strip(),
                         "unreferenced work")
        self.assertFalse((self.root / "moved").exists(), "do not redispatch a held card")

    def test_generated_lane_paths_pass_guard_and_are_cleaned(self):
        # The worktree each lane's rules (run.md, which every runner lane prompt names) prescribe.
        rules = (PACK / "skills/drover/references/run.md").read_text()
        paths = dict((lane, root) for root, lane in
                     re.findall(r"worktree = (\S+/)issue-<N>-(build|qa|review)/", rules))
        for lane in ("build", "qa", "review"):
            with self.subTest(lane=lane):
                self.assertIn(lane, paths, "lane must prescribe its worktree location")
                path = paths[lane] + "issue-7-" + lane
                command = "git worktree add -b issue-7-" + lane + " " + shlex.quote(path) + " main"
                payload = json.dumps({"tool_name": "Bash", "cwd": str(self.root),
                                      "tool_input": {"command": command}})
                verdict = self.run_cmd("python3", str(PACK / "hooks/guard-worktree-path.py"),
                                       input=payload).stdout
                self.assertEqual(verdict, "", "the installed guard must allow the lane's command")
                self.run_cmd(*shlex.split(command))
                for meta in (self.root / ".git/worktrees").iterdir():
                    for name in ("gitdir", "commondir"):
                        os.utime(meta / name, (1577836800, 1577836800))
                self.run_cmd("python3", str(PACK / "hooks/cleanup-wt.py"), "--post-merge",
                             "--no-fetch", "--base", "main", input="")
                self.assertFalse((self.root / path).exists(), "cleanup must see the lane's worktree")


if __name__ == "__main__":
    unittest.main()
