#!/usr/bin/env python3
"""FactoryBench attempt events for the runner and `drover stop` (specs/metrics.md FR-MET-4..12).

  drover metrics enabled                          exit 0 when events are sent, 1 otherwise
  drover metrics config-version                   print factory_config_version (FR-MET-8)
  drover metrics claim ISSUE REPO LABELS BASE VER start an attempt, or resume a waiting one
  drover metrics env ISSUE ROLE                   print the worker's OTLP variables, one per line
  drover metrics pr ISSUE URL                     pr.linked, once per attempt
  drover metrics gate ISSUE NAME true|false WHY   gate result; rounds count per name
  drover metrics send-back ISSUE FROM TO WHY      send_back: a lane sent the card back to an earlier one
  drover metrics waiting ISSUE REASON             human.waiting
  drover metrics finish ISSUE STATUS [WHY]        attempt.finished; the next claim starts anew
  drover metrics board [DEPS_FILE] < ITEMS        column-driven events for every open attempt
  drover metrics pr-urls                          print the linked PR url of each open attempt
  drover metrics closed FILE                      finish (failed) attempts whose PR closed unmerged;
                                                  FILE: `gh pr list --state closed --json url,mergedAt`
  drover metrics flush                            factorybench emit --flush

Every command is a silent no-op unless `factorybench` is on PATH and FACTORYBENCH_URL is set
(FR-MET-4). Open attempts live in .drover/runs/attempts.json (FR-MET-6). Events carry ids,
labels, reasons and urls only: no prompts, code or diffs (FR-MET-10). Each `factorybench`
call runs in the background with its event time set here, so a slow or hung FactoryBench
never stalls the runner (it spools what it cannot send).
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
STATE = Path(".drover/runs/attempts.json")
PHASE = {"build": "implement", "qa": "verify", "review": "review"}


def enabled():
    return bool(shutil.which("factorybench") and os.environ.get("FACTORYBENCH_URL"))


def load():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}  # lost or unreadable: the next claim starts a new attempt


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(STATE.name + ".%d" % os.getpid())
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(STATE)


def background(argv):
    # Fire and forget: `factorybench` always exits 0 and spools; nothing here waits on it.
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except (OSError, subprocess.SubprocessError):
        pass


def emit(kind, attempt, key, payload):
    background(["factorybench", "emit", kind, "--attempt", attempt, "--key", key,
                "--json", json.dumps(payload, sort_keys=True),
                "--ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())])


def config_version():
    def out(cmd, cwd=None):
        try:
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=5,
                                  stdin=subprocess.DEVNULL).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    commit = out(["git", "rev-parse", "--short", "HEAD"], cwd=HERE.parent) or "unknown"
    cfg_path = Path(out(["python3", str(HERE / "drover-config.py"), "path"]) or ".drover/config.json")
    try:
        cfg = json.loads(cfg_path.read_text())
        digest = hashlib.sha256(cfg_path.read_bytes()).hexdigest()[:8]
    except (OSError, ValueError):
        cfg, digest = {}, "none"
    try:
        harness = json.loads((HERE.parent / "harnesses" / ("%s.json" % cfg.get("harness", "claude"))).read_text())
    except (OSError, ValueError):
        harness = {}
    version = (out(harness.get("version_command") or ["true"]).split() or ["unknown"])[0]
    return "drover@%s+%s@%s+cfg:%s" % (commit, harness.get("product", harness.get("name", "claude")), version, digest)


def waiting(state, issue, reason):
    a = state.get(issue)
    if not a or a.get("waiting"):
        return
    a["waits"] = a.get("waits", 0) + 1
    a["waiting"] = reason
    emit("human.waiting", a["id"], "waiting:%d" % a["waits"], {"reason": reason})


def resumed(state, issue):
    a = state.get(issue)
    if not a or not a.get("waiting"):
        return
    emit("human.resumed", a["id"], "resumed:%d" % a["waits"], {"reason": a.pop("waiting")})


def finish(state, issue, status, why=""):
    a = state.pop(issue, None)
    if a:
        emit("attempt.finished", a["id"], "finished", dict({"status": status}, **({"reason": why} if why else {})))


def main(argv):
    cmd, args = (argv[0] if argv else ""), argv[1:]
    if cmd not in ("enabled", "config-version", "claim", "env", "pr", "gate", "send-back", "waiting", "finish",
                   "board", "pr-urls", "closed", "flush"):
        print(__doc__, file=sys.stderr)
        return 64
    if not enabled():
        return 1 if cmd == "enabled" else 0
    if cmd == "enabled":
        return 0
    if cmd == "config-version":
        print(config_version())
        return 0
    if cmd == "flush":
        background(["factorybench", "emit", "--flush"])
        return 0
    state = load()
    if cmd == "claim":
        issue, repo, labels, base, version = args
        a = state.get(issue)
        if a:
            resumed(state, issue)  # a stop (or a Blocked spell) ends at the next claim
        else:
            a = state[issue] = {"id": "drover-" + uuid.uuid4().hex[:16]}
            payload = {"work_item": {"repo": repo, "issue": int(issue)}, "factory_config_version": version,
                       "labels": [l for l in labels.split(",") if l]}
            if base:
                payload["base_commit"] = base
            emit("attempt.started", a["id"], "started", payload)
        save(state)
        print(a["id"])
    elif cmd == "env":
        issue, role = args
        a = state.get(issue)
        if a:
            url = os.environ["FACTORYBENCH_URL"].rstrip("/")
            attrs = "factorybench.attempt=%s,factorybench.phase=%s,factorybench.step=%s" % (
                a["id"], PHASE.get(role, "implement"), role)
            print("CLAUDE_CODE_ENABLE_TELEMETRY=1\nOTEL_LOGS_EXPORTER=otlp\nOTEL_EXPORTER_OTLP_PROTOCOL=http/json")
            print("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=%s/v1/logs" % url)
            if os.environ.get("FACTORYBENCH_TOKEN"):
                print("OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer %s" % os.environ["FACTORYBENCH_TOKEN"])
            print("OTEL_RESOURCE_ATTRIBUTES=" + attrs)
    elif cmd == "pr":
        issue, url = args
        a = state.get(issue)
        if a and not a.get("pr"):
            a["pr"] = url
            emit("pr.linked", a["id"], "pr", {"pr_url": url})
    elif cmd == "gate":
        issue, name, passed, why = args
        a = state.get(issue)
        if a:
            rounds = a.setdefault("rounds", {})
            rounds[name] = rounds.get(name, 0) + 1
            emit("gate", a["id"], "gate:%s:%d" % (name, rounds[name]),
                 {"name": name, "passed": passed == "true", "reason": why})
    elif cmd == "send-back":
        issue, src, dst, why = args
        a = state.get(issue)
        if a:
            a["send_backs"] = a.get("send_backs", 0) + 1
            emit("send_back", a["id"], "send_back:%d" % a["send_backs"],
                 {"from": src, "to": dst, "kind": "fix-attempt", "reason": why})
    elif cmd == "pr-urls":
        for a in state.values():
            if a.get("pr"):
                print(a["pr"])
    elif cmd == "closed":
        try:
            closed = json.loads(Path(args[0]).read_text())
        except (OSError, ValueError):
            closed = []
        unmerged = {p.get("url") for p in closed if isinstance(p, dict) and not p.get("mergedAt")}
        for issue in [i for i, a in state.items() if a.get("pr") in unmerged]:
            finish(state, issue, "failed", "PR closed unmerged")
    elif cmd == "waiting":
        waiting(state, args[0], args[1])
    elif cmd == "finish":
        finish(state, args[0], args[1], args[2] if len(args) > 2 else "")
    elif cmd == "board":
        items = json.load(sys.stdin).get("items", [])
        deps = {}
        if args:
            try:
                deps = json.loads(Path(args[0]).read_text())
            except (OSError, ValueError):
                deps = {}
        cols = {str((i.get("content") or {}).get("number")): i.get("status") for i in items}
        for issue in list(state):
            col = cols.get(issue)
            if col == "Done":
                finish(state, issue, "completed")
            elif col == "Backlog":
                finish(state, issue, "failed", "moved back to Backlog")
            elif col == "Blocked":
                waiting(state, issue, (deps.get(issue) or {}).get("reasonTag") or "blocked")
            elif col and state[issue].get("waiting") not in (None, "stopped"):
                resumed(state, issue)  # a stopped card resumes at its next claim
    if cmd in ("pr", "gate", "send-back", "waiting", "finish", "board", "closed"):
        save(state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
