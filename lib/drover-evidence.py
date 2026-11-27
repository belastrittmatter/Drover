#!/usr/bin/env python3
"""drover-evidence.py — commit QA evidence to the configured location (specs/conventions.md FR-EVD).

    drover evidence push <issue> <version> <files...>

Mode `branch` (default): the orphan branch `evidence.branch` (default drover-evidence), files
under issue-<N>/<version>/. Mode `pr`: origin's tip of the current branch, files under
<evidence.path>/issue-<N>/<version>/. Prints one SHA-pinned raw URL per file.

github.com only: URLs are built as https://github.com/<owner>/<repo>/raw/<sha>/<path>, no GitHub
Enterprise host. Never touches the working tree, the index or a local branch: the commit is built in a private
index (GIT_INDEX_FILE) and pushed by sha. A push rejected because the remote moved is rebuilt
on the new tip, at most 5 attempts (FR-EVD-7); any other push failure stops at once.

Exit: 0 pushed · 1 push failed after retries · 64 usage · 65 bad input (file, image size, config).
`drover evidence prune` is not built yet (FR-EVD-9). Stdlib only (Python 3.9).
"""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
ATTEMPTS = 5
IMAGE_MAX = 5 * 1024 * 1024
IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
# git's wording when the remote ref moved under us. Not bare "rejected": a hook decline is "[remote rejected]".
# Anything else (auth, protection, hooks) is final.
RACE = ("[rejected]", "cannot lock ref", "non-fast-forward", "fetch first", "failed to update ref")


def load_config():
    spec = importlib.util.spec_from_file_location("drover_config", HERE / "drover-config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_config()


def die(msg, code):
    print(f"drover evidence: {msg}", file=sys.stderr)
    sys.exit(code)


def git(*args, env=None, check=True):
    r = subprocess.run(["git", *args], capture_output=True, text=True, env=env)
    if check and r.returncode:
        die(f"git {' '.join(args)}: {r.stderr.strip()}", 1)
    return r


def owner_repo():
    url = git("remote", "get-url", "origin").stdout.strip()
    parts = [p for p in re.split(r"[/:]", re.sub(r"\.git/?$", "", url.rstrip("/"))) if p]
    if len(parts) < 2:
        die(f"cannot read owner/repo from origin {url}", 65)
    return parts[-2], parts[-1]


def remote_tip(branch):
    r = git("ls-remote", "--exit-code", "origin", f"refs/heads/{branch}", check=False)
    if r.returncode == 2:
        return None
    if r.returncode:
        die(f"git ls-remote origin: {r.stderr.strip()}", 1)
    sha = r.stdout.split()[0]
    git("fetch", "-q", "--no-tags", "origin", f"refs/heads/{branch}")
    return sha


def build(parent, blobs, message):
    """A commit of parent's tree plus blobs ({path: sha}), in a throwaway index."""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        git("read-tree", *([parent] if parent else ["--empty"]), env=env)
        for path, blob in blobs.items():
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        tree = git("write-tree", env=env).stdout.strip()
    return git("commit-tree", tree, *(["-p", parent] if parent else []), "-m", message).stdout.strip()


def push(argv):
    if len(argv) < 3:
        die("usage: drover evidence push <issue> <version> <files...>", 64)
    issue, version, files = argv[0], argv[1], argv[2:]
    if not issue.isdigit() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        die("issue must be a number and version a name like v1", 64)
    cfg = load_config()
    ev = cfg.get("evidence") or {"mode": "branch"}
    if ev["mode"] == "pr":
        base = ev.get("path", "docs/drover/evidence").strip("/")
        if not base or ".." in base.split("/"):
            die(f"evidence.path must stay inside the repo: {base}", 65)
        branch = git("symbolic-ref", "-q", "--short", "HEAD", check=False).stdout.strip()
        if not branch:
            die("pr mode needs the PR branch checked out (HEAD is detached)", 65)
        prefix = f"{base}/issue-{issue}/{version}"
    else:
        branch = ev.get("branch", "drover-evidence")
        if branch in {cfg.get("base_branch", "main"), "main", "master"}:
            die(f"evidence.branch {branch} is a base branch: pick another (default drover-evidence)", 65)
        prefix = f"issue-{issue}/{version}"

    blobs = {}
    for f in files:
        p = Path(f)
        if not p.is_file():
            die(f"not a file: {f}", 65)
        if p.suffix.lower() in IMAGES and p.stat().st_size > IMAGE_MAX:
            die(f"{f} is over 5 MB: downscale it to at most 1920 px wide first (FR-EVD-4)", 65)
        path = f"{prefix}/{p.name}"
        if path in blobs:
            die(f"two files named {p.name}", 65)
        blobs[path] = git("hash-object", "-w", "--no-filters", "--", f).stdout.strip()

    owner, repo = owner_repo()
    message = f"chore(evidence): issue-{issue} {version}"
    for _ in range(ATTEMPTS):
        parent = remote_tip(branch)
        if ev["mode"] == "pr" and parent is None:
            die(f"push the PR branch {branch} to origin first", 65)
        commit = build(parent, blobs, message)
        r = git("push", "-q", "origin", f"{commit}:refs/heads/{branch}", check=False)
        if r.returncode == 0:
            for path in blobs:
                print(f"https://github.com/{owner}/{repo}/raw/{commit}/{quote(path)}")
            return 0
        err = r.stderr.strip()
        if not any(word in err for word in RACE):
            die(f"push to {branch} failed: {err}", 1)
        print(f"drover evidence: push to {branch} rejected, retrying on the new tip: {err}", file=sys.stderr)
    die(f"push to {branch} failed after {ATTEMPTS} attempts: {r.stderr.strip()}", 1)


def main(argv):
    if argv[:1] == ["push"]:
        return push(argv[1:])
    die("usage: drover evidence push <issue> <version> <files...>  (prune is not built yet)", 64)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
