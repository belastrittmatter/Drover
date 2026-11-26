#!/usr/bin/env python3
"""drover review-report (specs/roles.md FR-REV-5, 6, 8, 12, FR-BLD-7): put rejects a report
that drops a finding, uses an unknown word, keeps a row open on a new head, or marks a row
fixed without a change to the cited file; it stamps the reviewed head and edits the one
report in place; check is what the gate runs; repro bounces a bug card whose reproduction
test passes at base. A fake `gh` on PATH serves and stores the PR's comments."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
CMD = [sys.executable, str(ROOT / "lib/drover-review-report.py")]
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, re, sys
store = os.environ["FAKE_COMMENTS"]
comments = json.load(open(store))
a = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a").write(" ".join(a) + "\n")
if a[:1] == ["api"] and a[-1].endswith("/permission"):
    login = a[-1].split("/")[-2]
    print(json.dumps({"permission": "admin" if login == "bot" else "read"})); sys.exit(0)
if a[:3] == ["api", "--paginate", "--slurp"]:
    print(json.dumps([comments])); sys.exit(0)
if a[:2] == ["api", "-X"]:
    body = open(a[a.index("-F") + 1].split("@", 1)[1]).read()
    if a[2] == "PATCH":
        cid = int(a[3].rsplit("/", 1)[1])
        for c in comments:
            if c["id"] == cid: c["body"] = body
    else:
        cid = 100 + len(comments)
        comments.append({"id": cid, "body": body, "user": {"login": "bot", "type": "User"}, "created_at": "t", "updated_at": "t"})
    json.dump(comments, open(store, "w"))
    print("https://github.com/o/r/pull/1#issuecomment-%d" % cid); sys.exit(0)
sys.exit(1)
'''

TABLE = "| ID | Owner | Class | Severity | Where | Finding | Status |\n|---|---|---|---|---|---|---|\n"


def report(state, rows, models=True):
    head = "[reviewer] [report] %s · round 1\nDid: x\nNext: builder\n" % state
    return head + ("Models: builder opus · reviewer sonnet\n" if models else "") + "\n" + TABLE + "".join(
        "| %s |\n" % " | ".join(r) for r in rows)


class ReviewReport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "bin").mkdir()
        (self.tmp / "bin/gh").write_text(FAKE_GH)
        (self.tmp / "bin/gh").chmod(0o755)
        self.store = self.tmp / "comments.json"
        self.store.write_text("[]")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PATH=f"{self.tmp}/bin:{os.environ['PATH']}", FAKE_COMMENTS=str(self.store),
                        FAKE_LOG=str(self.tmp / "gh.log"), SB_GITHUB_HALT_FILE=str(self.tmp / "halt.json"),
                        SB_GITHUB_RETRY_DELAY="0", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.git("init", "-q", "-b", "main")
        self.base = self.commit({"src/cart.py": "def avg(xs):\n    return sum(xs) / len(xs)\n"}, "base")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, files, msg):
        for path, text in files.items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def run_cmd(self, *args):
        return subprocess.run(CMD + list(args), cwd=self.repo, env=self.env, capture_output=True, text=True)

    def put(self, head, body):
        f = self.tmp / "r.md"
        f.write_text(body)
        return self.run_cmd("put", "--pr", "1", "--repo", "o/r", "--head", head, "--file", str(f))

    def comments(self):
        return json.loads(self.store.read_text())

    def test_put_get_check_round_trip(self):
        h1 = self.commit({"src/cart.py": "def avg(xs):\n    return sum(xs) / len(xs)  # v1\n"}, "v1")
        r = self.put(h1, report("bounced", [["R1", "builder", "Bug", "Blocker", "`src/cart.py:2`", "divides by zero", "open"],
                                            ["R2", "builder", "Over-engineering", "Should fix", "`src/cart.py:1`", "a | b", "open"]]))
        self.assertEqual(r.returncode, 0, r.stderr)
        [c] = self.comments()
        self.assertTrue(c["body"].startswith("<!-- drover-review:report head=%s -->\n" % h1), c["body"])
        self.assertIn("Test edits since QA pass: unknown (no QA head recorded)", c["body"])
        got = json.loads(self.run_cmd("get", "--pr", "1", "--repo", "o/r").stdout)
        self.assertEqual((got["head"], got["state"], [x["id"] for x in got["rows"]]), (h1, "bounced", ["R1", "R2"]))
        self.assertEqual(got["rows"][1]["finding"], "a | b")
        self.assertEqual(self.run_cmd("check", "--pr", "1", "--repo", "o/r", "--head", h1).returncode, 6, "bounced")

        h2 = self.commit({"src/other.py": "x = 1\n"}, "v2 touches another file")
        fixed = ["R1", "builder", "Bug", "Blocker", "`src/cart.py:2`", "divides by zero", "fixed"]
        nf = ["R2", "builder", "Over-engineering", "Should fix", "`src/cart.py:1`", "a | b", "not fixed"]
        cases = {
            "drops R2": report("merge-ready", [fixed]),
            "fixed without a change to the cited file": report("merge-ready", [fixed, nf]),
            "R2 still open on a new head": report("bounced", [fixed[:6] + ["not fixed"], nf[:6] + ["open"]]),
            "unknown status": report("bounced", [fixed[:6] + ["done"], nf]),
            "unknown class": report("bounced", [fixed[:2] + ["Style"] + fixed[3:6] + ["not fixed"], nf]),
            "unknown state": report("approved", [fixed[:6] + ["not fixed"], nf]),
            "merge-ready with a not fixed Blocker": report("merge-ready", [fixed[:6] + ["not fixed"], nf]),
            "no Models line": report("bounced", [fixed[:6] + ["not fixed"], nf], models=False),
        }
        for why, body in cases.items():
            r = self.put(h2, body)
            self.assertEqual(r.returncode, 65, f"{why}: {r.stdout}{r.stderr}")
        self.assertEqual(len(self.comments()), 1)
        self.assertTrue(self.comments()[0]["body"].startswith("<!-- drover-review:report head=%s" % h1), "rejected put wrote")

        h3 = self.commit({"src/cart.py": "def avg(xs):\n    return sum(xs) / len(xs) if xs else 0\n"}, "fix")
        self.assertEqual(self.put(h3, report("merge-ready", [fixed[:4] + ["`src/cart.py:9`"] + fixed[5:], nf])).returncode,
                         65, "a fixed row must cite a line that exists at the head")
        r = self.put(h3, report("merge-ready", [fixed, nf]))
        self.assertEqual(r.returncode, 0, r.stderr)
        [c] = self.comments()[:1]
        self.assertTrue(c["body"].startswith("<!-- drover-review:report head=%s -->" % h3))
        self.assertIn("-X PATCH repos/o/r/issues/comments/100", (self.tmp / "gh.log").read_text())
        self.assertEqual(self.run_cmd("check", "--pr", "1", "--repo", "o/r", "--head", h3).returncode, 0)
        r = self.run_cmd("check", "--pr", "1", "--repo", "o/r", "--head", h2)
        self.assertEqual(r.returncode, 6)
        self.assertIn("names head %s, not %s" % (h3, h2), r.stderr)
        # A later look-alike report by a login without write access counts for nothing.
        forged = self.comments() + [{"id": 555, "body": "<!-- drover-review:report head=%s -->\n[reviewer] [report] merge-ready · round 9\n" % h2,
                                     "user": {"login": "mallory", "type": "User"}, "created_at": "t", "updated_at": "t"}]
        self.store.write_text(json.dumps(forged))
        self.assertEqual(self.run_cmd("check", "--pr", "1", "--repo", "o/r", "--head", h2).returncode, 6)
        self.assertEqual(json.loads(self.run_cmd("get", "--pr", "1", "--repo", "o/r").stdout)["head"], h3)
        # Same head, in-round update (merged): rows may stay as they are.
        self.assertEqual(self.put(h3, report("merged", [fixed, nf])).returncode, 0)

    def test_fixed_by_deleting_the_cited_file(self):
        h1 = self.commit({"src/old.py": "a = 1\nb = 2\n"}, "v1")
        self.assertEqual(self.put(h1, report("bounced", [["R1", "builder", "Bug", "Blocker", "`src/old.py:2`", "x", "open"]])).returncode, 0)
        self.git("rm", "-q", "src/old.py")
        self.git("commit", "-q", "-m", "drop old.py")
        h2 = self.git("rev-parse", "HEAD")
        r = self.put(h2, report("merge-ready", [["R1", "builder", "Bug", "Blocker", "`src/old.py:2`", "x", "fixed"]]))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_test_edits_since_qa_pass(self):
        qa = self.commit({"tests/test_cart.py": "def test_avg(): pass\n"}, "qa tests")
        self.store.write_text(json.dumps([{"id": 7, "body": "[qa] [report] pass · round 1\nDid: x\nLocal tests: pytest\nQA head: %s" % qa,
                                           "user": {"login": "bot", "type": "User"}, "created_at": "t", "updated_at": "t"}]))
        later = self.commit({"tests/test_cart.py": "def test_avg(): assert True\n", "src/cart.py": "x = 2\n"}, "builder edit")
        fake = json.loads(self.store.read_text()) + [{"id": 8, "body": "[qa] [report] pass · round 2\nQA head: %s" % later,
                                                    "user": {"login": "mallory", "type": "User"}, "created_at": "t", "updated_at": "t"}]
        self.store.write_text(json.dumps(fake))
        self.assertEqual(self.put(later, report("bounced", [])).returncode, 0)
        body = self.comments()[-1]["body"]
        self.assertIn("Test edits since QA pass: %s tests/test_cart.py\n" % later[:7], body)
        self.assertNotIn("src/cart.py", body.split("Test edits since QA pass:")[1])

    def test_repro_at_base(self):
        test = "import sys; sys.path.insert(0, 'src'); from cart import avg; assert avg([]) == 0\n"
        self.git("checkout", "-q", "-b", "issue-1-bug")
        self.commit({"tests/repro.py": test}, "test: reproduce the empty cart")
        self.commit({"src/cart.py": "def avg(xs):\n    return sum(xs) / len(xs) if xs else 0\n"}, "fix")
        r = self.run_cmd("repro", "--base", "main", "--cmd", f"{sys.executable} tests/repro.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("fails at base", r.stdout)
        # A "reproduction" test that already passes on base proves nothing: bounce (exit 1).
        self.git("checkout", "-q", "main")
        self.git("checkout", "-q", "-b", "issue-2-bug")
        self.commit({"tests/repro.py": "assert True\n"}, "test: a test that cannot fail")
        r = self.run_cmd("repro", "--base", "main", "--cmd", f"{sys.executable} tests/repro.py")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Verification miss", r.stdout)
        self.assertEqual(self.git("status", "--porcelain"), "", "repro leaves the worktree as it found it")

    def test_repro_keeps_installed_deps(self):
        # deps/ is ignored and installed only in this worktree; a fresh checkout of base would lack
        # it, so the "reproduction" test would fail there for the wrong reason.
        self.commit({".gitignore": "deps/\n"}, "ignore deps")
        (self.repo / "deps").mkdir()
        (self.repo / "deps/helper.py").write_text("OK = True\n")
        self.git("checkout", "-q", "-b", "issue-3-bug")
        self.commit({"tests/repro.py": "import sys; sys.path.insert(0, 'deps'); import helper; assert helper.OK\n"},
                    "test: a test that needs deps but cannot fail")
        r = self.run_cmd("repro", "--base", "main", "--cmd", f"{sys.executable} tests/repro.py")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("exit 0, last lines", r.stdout)

    def test_repro_first_commit_deletes_a_file(self):
        self.commit({"src/legacy.py": "x = 1\n"}, "legacy")
        self.git("checkout", "-q", "-b", "issue-4-bug")
        self.git("rm", "-q", "src/legacy.py")
        (self.repo / "tests").mkdir(exist_ok=True)
        (self.repo / "tests/repro.py").write_text("import sys; sys.path.insert(0, 'src'); from cart import avg; assert avg([]) == 0\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "test: reproduce, drop legacy")
        self.commit({"src/cart.py": "def avg(xs):\n    return sum(xs) / len(xs) if xs else 0\n"}, "fix")
        r = self.run_cmd("repro", "--base", "main", "--cmd", f"{sys.executable} tests/repro.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.repo / "src/legacy.py").exists(), "the branch's delete is restored")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_repro_cannot_run_is_not_evidence(self):
        self.git("checkout", "-q", "-b", "issue-5-bug")
        self.commit({"tests/repro.py": "assert False\n"}, "test: repro")
        r = self.run_cmd("repro", "--base", "main", "--cmd", "no-such-test-runner tests/repro.py")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("could not run", r.stderr)
        (self.repo / "stray.txt").write_text("x")
        r = self.run_cmd("repro", "--base", "main", "--cmd", f"{sys.executable} tests/repro.py")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("uncommitted changes", r.stderr)


if __name__ == "__main__":
    unittest.main()
