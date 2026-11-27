#!/usr/bin/env python3
"""drover-status.py — read-only live snapshot of the drover pipeline.

Renders the richer status template defined in
`skills/drover/references/status.md` (fixed 80-col Kanban
plus dedicated Workers / Block-reasons / Recent / Health sections).

The same template can be hand-rendered by the model — but the token-by-
token box-drawing takes ≈3 min per invocation, which makes the richer
view impractical in practice. This script renders it in ≈1.3 s by doing
the layout as a single Python pass.

What it does:
  1. Read .drover/config.json; the run slug is the repo name.
  2. ONE GraphQL call for project items (number, title, labels, Status).
  3. ONE `gh issue view` per Blocked card for reason-tag extraction.
  4. Read today's manifest and pipe everything to the locked-template
     renderer that prints the 80-col snapshot matching the spec in
     references/status.md.

What it does NOT do:
  - Any GitHub mutations (read-only verb — same forbidden set as the skill).
  - Touch the manifest, locks, or worktrees.
  - Wait for or poll workers.

Cross-platform: pure Python 3 stdlib + `gh` CLI. Works on macOS, Linux,
Windows (PowerShell / CMD / Git Bash / WSL). No bash, no jq.

Usage:
  drover status

Exit codes:
  0  ok
  64 no .drover/config.json, or an argument given
  67 gh / network failure
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

# Make box-drawing chars render on Windows consoles too.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


# ───────────────────────────── lane constants ─────────────────────────────
# Used by dispatch parsing, kanban glyph lookup, and the workers section.
# Source of truth — never inline these as dict literals at call sites.

LANE_ORDER: tuple[str, ...] = ("build", "qa", "review")
LANE_GLYPH = {"build": "[B]", "qa": "[Q]", "review": "[R]"}
LANE_LABEL = {"build": "Building", "qa": "QA", "review": "Review"}
LANE_ROLE = {"build": "Builder ", "qa": "Tester  ", "review": "Reviewer"}

# Board columns, left to right as they sit on the GitHub project. The kanban
# prints in exactly this order. Backlog and Done collapse to one line of numbers.
BOARD_ORDER: tuple[str, ...] = (
    "Backlog", "Ready", "Building", "QA", "Review", "Blocked", "Done",
)
COLLAPSED_TAIL = {"Backlog": "(not started, collapsed)", "Done": "(squash-merged, collapsed)"}


# ───────────────────────────── manifest grammar ─────────────────────────────
# The dispatcher's log format lives in `lib/drover-run.sh`. These
# regexes are the contract between the two. When the dispatcher changes a
# log line, update the regex here AND add a fixture to `tests/` so the
# divergence can't sneak back in.

TS_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] (.*)$")
# Dispatcher emits: `dispatch lane=${lane} issue=#${issue} pid=${pid} claim=...`
# Lanes are restricted to the known set so a typo'd lane never reaches the
# LANE_GLYPH / LANE_ORDER lookups below.
DISPATCH_RE = re.compile(r"dispatch lane=(build|qa|review) issue=#(\d+) pid=(\d+)")
# Two reap variants in the dispatcher:
#   `reaped stale lock + swept assignee on #N (pid=P)`
#   `reaped stale lock for #N (pid=P)`
# for a worker that died without a handoff (a normal exit logs `worker done`, below).
# And `pid=${PID:-empty}` can expand to `pid=empty` when the PID was lost.
REAP_RE = re.compile(r"reaped stale lock\b[^#]*#(\d+) \(pid=(\d+|empty)\)")
# A worker that exited after handing its card on: `worker done #N lane=L (pid=P)`.
DONE_RE = re.compile(r"worker done #(\d+) lane=\w+ \(pid=(\d+|empty)\)")
ZOMBIE_RE = re.compile(r"zombie [a-z]+ worker on #(\d+) \(pid=(\d+)\)(.*)$")
ALERT_RE = re.compile(r"block-rate alert: (.+)$")


# ───────────────────────────── pure helpers ─────────────────────────────

# Control chars (C0 + DEL) sourced from GitHub-controlled issue titles would
# otherwise reach the terminal verbatim and could redraw the kanban frame
# (e.g. a title containing `\x1b[2K\r`). Strip them at ingestion. Tabs, LF,
# and CR are all in this range — none have a legitimate place in a one-line
# title display anyway.
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_title(s: str) -> str:
    return _CTRL_CHARS_RE.sub("", s)


def hms_to_epoch(today_iso: str, hms: str) -> int:
    """Combine an ISO date + HH:MM:SS local time into a Unix epoch.

    Pure: no module state, no I/O. `today_iso` is passed explicitly so the
    manifest parser can be unit-tested with a fixed date.
    """
    try:
        dt = datetime.datetime.strptime(f"{today_iso} {hms}", "%Y-%m-%d %H:%M:%S")
        return int(dt.timestamp())
    except ValueError:
        return 0


def parse_manifest(text: str, today_iso: str) -> dict[str, Any]:
    """Parse a drover run manifest into structured state.

    Pure function (no I/O, no global state) so it can be unit-tested with
    hand-crafted fixtures. Returns a dict with:
      inflight: {lane → [{pid, issue, ts}]}  — workers currently dispatched (max_workers per lane)
      recents: [{epoch, verb, issue, target, detail}, ...]
      last_tick: HH:MM:SS of the most recent `tick —` line, or None
      start_hms: HH:MM:SS of the most recent `drover run started`, or None
      exited: True if the run has logged `exiting cleanly`
      reaped_count: number of `reaped stale lock ...` and `worker done ...` lines seen
    """
    inflight: dict[str, list[dict[str, str]]] = {}

    def drop(keep) -> None:
        for lane in list(inflight):
            inflight[lane] = [v for v in inflight[lane] if keep(lane, v)]
            if not inflight[lane]:
                del inflight[lane]
    recents: list[dict[str, Any]] = []
    last_tick: str | None = None
    start_hms: str | None = None
    exited = False
    reaped_count = 0

    for line in (text or "").splitlines():
        m = TS_RE.match(line)
        if not m:
            continue
        h, mi, s, rest = m.groups()
        hms = f"{h}:{mi}:{s}"
        ep = hms_to_epoch(today_iso, hms)

        if "drover run started" in rest:
            start_hms = hms
            exited = False
            continue
        if "exiting cleanly" in rest:
            exited = True
            continue
        if rest.startswith("tick "):
            last_tick = hms
            continue

        if dm := DISPATCH_RE.search(rest):
            lane, issue, pid = dm.groups()
            # Clean lane handoffs (Build → QA → Review) don't emit reap/zombie
            # lines, so the prior lane's inflight entry would otherwise linger
            # and render as a phantom concurrent worker. A fresh dispatch into
            # a later lane for the same issue is proof the earlier lane
            # finished — drop the prior entry before recording the new one.
            # A re-dispatch in the same lane replaces that issue's older entry too.
            upto = LANE_ORDER[: LANE_ORDER.index(lane) + 1]
            drop(lambda l, v: not (l in upto and v["issue"] == issue))
            inflight.setdefault(lane, []).append({"pid": pid, "issue": issue, "ts": hms})
            recents.append({
                "epoch": ep, "verb": "dispatch",
                "issue": f"#{issue}", "target": LANE_LABEL[lane],
                # `attempt N/3` is filled in at render time from current
                # labels — the manifest doesn't encode it at dispatch time.
                "detail": "",
            })
            continue
        if rm := REAP_RE.search(rest):
            # The dispatcher logs `reaped stale lock ...` for every reap; bump
            # the housekeeping counter here (one branch, not two) and record
            # the event. `pid=empty` matches when the dispatcher couldn't
            # recover the PID from the lock file — in that case we can't drop
            # a specific inflight entry, but the reap is still worth surfacing.
            reaped_count += 1
            issue, pid = rm.groups()
            if pid != "empty":
                drop(lambda l, v: v["pid"] != pid)
            recents.append({
                "epoch": ep, "verb": "reap",
                "issue": f"#{issue}", "target": "",
                "detail": "stale lock + assignee swept",
            })
            continue
        if dn := DONE_RE.search(rest):
            reaped_count += 1  # a clean exit frees its lock and worktree too
            issue, pid = dn.groups()
            if pid != "empty":
                drop(lambda l, v: v["pid"] != pid)
            recents.append({"epoch": ep, "verb": "done", "issue": f"#{issue}", "target": "", "detail": "worker exited"})
            continue
        if zm := ZOMBIE_RE.search(rest):
            issue, pid, det = zm.groups()
            drop(lambda l, v: v["pid"] != pid)
            recents.append({
                "epoch": ep, "verb": "zombie",
                "issue": f"#{issue}", "target": "", "detail": det.strip(" —"),
            })
            continue
        if am := ALERT_RE.search(rest):
            recents.append({
                "epoch": ep, "verb": "alert",
                "issue": "", "target": "", "detail": am.group(1),
            })

    return {
        "inflight": inflight,
        "recents": recents,
        "last_tick": last_tick,
        "start_hms": start_hms,
        "exited": exited,
        "reaped_count": reaped_count,
    }


def visual_width(s: str) -> int:
    """Approximate east-asian-width-aware cell count."""
    w = 0
    for c in s:
        if unicodedata.category(c) == "Mn" or ord(c) == 0xFE0F:
            continue
        if unicodedata.east_asian_width(c) in ("W", "F"):
            w += 2
        elif ord(c) >= 0x2600:  # most symbol/emoji blocks render wide
            w += 2
        else:
            w += 1
    return w


def vpad(s: str, width: int) -> str:
    """Right-pad s with spaces to occupy `width` visual cells."""
    diff = width - visual_width(s)
    return s + (" " * diff) if diff > 0 else s


def truncate_to(s: str, width: int) -> str:
    """Truncate s to <= width visual cells, adding … if shortened."""
    if visual_width(s) <= width:
        return s
    out = ""
    for c in s:
        if visual_width(out + c) > width - 1:
            return out + "…"
        out += c
    return out


def box_top(label: str, count: int) -> str:
    head = f"┌─ {label:<8} [{count}] "
    fill = "─" * (80 - visual_width(head) - 1)
    return head + fill + "┐"


def box_bot() -> str:
    return "└" + ("─" * 78) + "┘"


def box_line(body: str) -> str:
    body = truncate_to(body, 76)
    return f"│ {vpad(body, 76)} │"


def rebuild_count(item: dict[str, Any] | None) -> int:
    """Read `loop:rebuild-N` from labels; 0 if absent."""
    if not item:
        return 0
    for lab in item["labels"]:
        if m := re.match(r"loop:rebuild-(\d+)", lab):
            return int(m.group(1))
    return 0


def attempt_str(item: dict[str, Any] | None) -> str:
    """`attempt N/3` derived from the issue's current `loop:rebuild-N` label."""
    return f"{min(rebuild_count(item) + 1, 3)}/3"


def rebuild_suffix(item: dict[str, Any]) -> str:
    k = rebuild_count(item)
    return f"↻ {min(k + 1, 3)}/3" if k else ""


# The reason tags live in one file shared with the writers (block-template.md).
REASONS = json.loads(Path(__file__).with_name("drover-reasons.json").read_text(encoding="utf-8"))
REASON_TABLE: list[str] = list(REASONS["tags"])
_TAG_RE = re.compile(r"\[(?:blocked:[a-z-]+|needs-you)\]")


def reason_short(tag: str) -> str:
    """`[blocked:creds]` -> `creds`, `[needs-you]` -> `needs-you`."""
    return tag.strip("[]").removeprefix("blocked:")


def reason_for(body: str) -> str:
    """Short name of the reason tag on the `Reason tag:` line, else the first known
    tag anywhere in the comment; `other` when none parses."""
    for line in body.splitlines():
        if "Reason tag:" in line:
            for t in _TAG_RE.findall(line.split("Reason tag:", 1)[1]):
                if t in REASONS["tags"]:
                    return reason_short(t)
    for t in _TAG_RE.findall(body):
        if t in REASONS["tags"]:
            return reason_short(t)
    return "other"


def render_block_reasons(reasons: list[str], numbers: list[int]) -> list[str]:
    """`Blocked 3 · creds 1 · unclear 1 · needs-you 1`, then one line per tag."""
    if not numbers:
        return ["   (none)"]
    order = [reason_short(t) for t in REASON_TABLE] + ["other"]
    groups: dict[str, list[str]] = {}
    for r, n in zip(reasons, numbers):
        groups.setdefault(r, []).append(f"#{n}")
    keys = sorted(groups, key=lambda k: (-len(groups[k]), order.index(k)))
    out = ["   " + " · ".join([f"Blocked {len(numbers)}"] + [f"{k} {len(groups[k])}" for k in keys])]
    out += [f"   {k:<12} {', '.join(groups[k])}" for k in keys]
    return out


def field_status(node: dict[str, Any]) -> str:
    """Read the `Status` single-select field from a project-item node."""
    for fv in (node.get("fieldValues", {}).get("nodes") or []):
        if fv and fv.get("field", {}).get("name") == "Status":
            return fv.get("name") or "Backlog"
    return "Backlog"


# ───────────────────────────── side-effecting helpers ─────────────────────────────

def drover_config():
    """lib/drover-config.py: the config path and the run slug (the repo name, FR-CFG-1)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("drover_config", Path(__file__).with_name("drover-config.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gh(*args: str, check: bool = True) -> str:
    """Run gh and return stdout. Exits 67 on failure when check=True."""
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, encoding="utf-8", check=False
        )
    except FileNotFoundError:
        print("gh CLI not found on PATH", file=sys.stderr)
        sys.exit(67)
    if proc.returncode != 0:
        if check:
            print(f"gh call failed ({' '.join(args[:3])}…)", file=sys.stderr)
            if proc.stderr.strip():
                print(proc.stderr.strip(), file=sys.stderr)
            sys.exit(67)
        return ""
    return proc.stdout


# ───────────────────────────── GraphQL ─────────────────────────────
# Targeted query — number, title, labels, Status only. ~3 KB vs. 100+ KB for
# `gh project item-list --format json` (which slurps every issue body).
# `repositoryOwner(login:)` is the abstract owner; `... on ProjectV2Owner`
# picks the `projectV2` field which both User and Organization implement.
# `$after` is nullable — first page omits the `-F after=…` arg entirely
# (default null) so we don't have to pass a sentinel value.

ITEMS_QUERY = """
query($owner:String!, $number:Int!, $after:String) {
  repositoryOwner(login:$owner) {
    ... on ProjectV2Owner {
      projectV2(number:$number) {
        items(first:100, after:$after) {
          pageInfo { endCursor hasNextPage }
          nodes {
            content { ... on Issue { number title labels(first:20){nodes{name}} } }
            fieldValues(first:8) {
              nodes { ... on ProjectV2ItemFieldSingleSelectValue {
                name field { ... on ProjectV2SingleSelectField { name } } } }
            }
          }
        }
      }
    }
  }
}
"""

# Hard ceiling on pagination depth. 20 pages × 100 items = 2000 cards, which
# is well past any realistic drover project (the dispatcher's 5000-pt/hr
# GraphQL budget makes a 20-page snapshot a meaningful chunk of quota). If a
# project ever exceeds this, we print a truncation warning rather than loop
# forever on a buggy server response.
MAX_ITEM_PAGES = 20


def paginate_items(
    fetch: Callable[[str | None], dict[str, Any]],
    max_pages: int = MAX_ITEM_PAGES,
) -> tuple[list[dict[str, Any]], bool]:
    """Walk `fetch(after) → graphql_payload` until pagination is exhausted.

    Pure: no I/O of its own — the `fetch` callable does all I/O. Returns
    (all_nodes, hit_cap), where `hit_cap` is True iff we stopped because
    of `max_pages` (i.e., the project is bigger than we surfaced).
    """
    all_nodes: list[dict[str, Any]] = []
    after: str | None = None
    for _ in range(max_pages):
        payload = fetch(after)
        items_section = (
            ((payload.get("data") or {})
                .get("repositoryOwner") or {})
                .get("projectV2") or {}
        ).get("items") or {}
        all_nodes.extend(items_section.get("nodes") or [])
        page_info = items_section.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return all_nodes, False
        after = page_info.get("endCursor")
        if not after:
            return all_nodes, False
    return all_nodes, True


# ───────────────────────────── main ─────────────────────────────


def render_collapsed(label: str, lane_items: list[dict[str, Any]]) -> str:
    """One line of issue numbers, newest first, `… +N more` past 76 cols."""
    out = [box_top(label, len(lane_items))]
    if not lane_items:
        out.append(box_line("(empty)"))
    else:
        nums = [f"#{x['number']}" for x in lane_items]
        tail = "   " + COLLAPSED_TAIL[label]
        accum: list[str] = []
        for i, x in enumerate(nums):
            remaining = len(nums) - i - 1
            candidate = " ".join(accum + [x])
            proposed = candidate + (f" … +{remaining} more" if remaining > 0 else "") + tail
            if visual_width(proposed) > 76:
                break
            accum.append(x)
        remaining = len(nums) - len(accum)
        out.append(box_line(" ".join(accum) + (f" … +{remaining} more" if remaining > 0 else "") + tail))
    out.append(box_bot())
    return "\n".join(out)


def render_kanban(
    by_status: dict[str, list[dict[str, Any]]],
    glyph_for: Callable[[int], str],
    reason_for_number: Callable[[int], str],
) -> str:
    """The kanban boxes in BOARD_ORDER. Pure: lookups come in as callables."""
    boxes: list[str] = []
    for label in BOARD_ORDER:
        lane_items = by_status.get(label, [])
        if label in COLLAPSED_TAIL:
            boxes.append(render_collapsed(label, lane_items))
            continue
        out = [box_top(label, len(lane_items))]
        if not lane_items:
            out.append(box_line("(empty)"))
        for it in lane_items:
            n = it["number"]
            if label == "Blocked":
                out.append(box_line(f"{reason_for_number(n)} #{n}  {it['title']}"))
                continue
            left = f"{glyph_for(n)} #{n}  {it['title']}"
            suffix = rebuild_suffix(it)
            if suffix:
                budget = 76 - visual_width(suffix) - 1
                if visual_width(left) > budget:
                    left = truncate_to(left, budget)
                pad = max(1, 76 - visual_width(left) - visual_width(suffix))
                out.append(box_line(left + (" " * pad) + suffix))
            else:
                out.append(box_line(left))
        out.append(box_bot())
        boxes.append("\n".join(out))
    return "\n".join(boxes)


def main() -> int:
    if len(sys.argv) > 1:
        print("usage: drover status  (one config per repo: .drover/config.json)", file=sys.stderr)
        return 64
    dc = drover_config()
    config_path = dc.config_path()
    if not config_path.is_file():
        print(f"no config: {config_path} (run drover onboard)", file=sys.stderr)
        return 64
    config_slug = dc.slug()

    cfg = json.loads(config_path.read_text())
    project_owner: str = cfg["project"]["owner"]
    project_number: int = int(cfg["project"]["number"])
    runs_dir = ".drover/runs"  # metrics.md FR-MET-3
    run_date = datetime.date.today().isoformat()
    manifest_path = Path(runs_dir) / f"{run_date}-{config_slug}.md"

    # ── fetch project items (paginated) ──
    def fetch_page(after: str | None) -> dict[str, Any]:
        args = [
            "api", "graphql",
            "-f", f"query={ITEMS_QUERY}",
            "-F", f"owner={project_owner}",
            "-F", f"number={project_number}",
        ]
        if after:
            # First page leaves `$after` unset → GraphQL defaults to null.
            args += ["-F", f"after={after}"]
        out = gh(*args)
        payload: dict[str, Any] = json.loads(out)
        if payload.get("errors"):
            print("graphql returned errors:", file=sys.stderr)
            for err in payload["errors"]:
                print(f"  - {err.get('message')}", file=sys.stderr)
            sys.exit(67)
        return payload

    items_raw, hit_cap = paginate_items(fetch_page)
    if hit_cap:
        print(
            f"warning: stopped after {MAX_ITEM_PAGES * 100} items — "
            f"kanban may not show every card",
            file=sys.stderr,
        )

    items: list[dict[str, Any]] = []
    for n in items_raw:
        c = n.get("content") or {}
        if not c.get("number"):
            continue
        items.append({
            "number": c["number"],
            "title": sanitize_title(c.get("title") or ""),
            "labels": [
                l["name"]
                for l in (c.get("labels", {}).get("nodes") or [])
                if l.get("name")
            ],
            "status": field_status(n),
        })

    by_status: dict[str, list[dict[str, Any]]] = {
        s: sorted([i for i in items if i["status"] == s], key=lambda x: -x["number"])
        for s in BOARD_ORDER
    }

    # ── fetch reason-tag comments for Blocked only ──
    # Skip silently on error so a stale token doesn't break the rest of the
    # snapshot. The body is capped at 4000 chars; the reason tag sits in the
    # header and on the `Reason tag:` line of any well-formed block comment.
    reasons: dict[int, str] = {}
    for it in by_status["Blocked"]:
        n = it["number"]
        out = gh("issue", "view", str(n), "--json", "comments", check=False)
        if not out:
            continue
        try:
            comments = json.loads(out).get("comments") or []
        except json.JSONDecodeError:
            continue
        comments.sort(key=lambda c: c.get("createdAt", ""))
        body = (comments[-1].get("body") if comments else "") or ""
        reasons[n] = body[:4000]

    # ── parse manifest ──
    manifest_text = manifest_path.read_text() if manifest_path.is_file() else ""
    now_epoch = int(time.time())
    state = parse_manifest(manifest_text, run_date)
    inflight = state["inflight"]
    recents = state["recents"][-5:][::-1]  # last 5, newest first
    last_tick = state["last_tick"]
    start_hms = state["start_hms"]
    exited = state["exited"]
    reaped_count = state["reaped_count"]

    # ── tiny closures over items / inflight / now_epoch ──
    def item_by_number(n: int) -> dict[str, Any] | None:
        return next((i for i in items if i["number"] == n), None)

    def glyph_for_issue(n: int) -> str:
        for lane, workers in inflight.items():
            if any(v["issue"] == str(n) for v in workers):
                return LANE_GLYPH[lane]
        return "   "

    def worker_dur(hms: str) -> str:
        d = now_epoch - hms_to_epoch(run_date, hms)
        if d < 3600:
            return f"{d // 60}m"
        if d < 86400:
            return f"{d // 3600}h"
        return f"{d // 86400}d"

    def delta_ago(hms: str | None) -> str:
        if not hms:
            return "?"
        d = now_epoch - hms_to_epoch(run_date, hms)
        if d < 90:
            return f"{d}s ago"
        if d < 5400:
            return f"{d // 60}m ago"
        if d < 86400:
            return f"{d // 3600}h {(d % 3600) // 60}m ago"
        return f"{d // 86400}d ago"

    def t_minus(ep: int) -> str:
        d = now_epoch - ep
        if d < 60:
            return "T-1m" if d >= 30 else "T-0m"
        if d < 3600:
            return f"T-{d // 60}m"
        if d < 86400:
            return f"T-{d // 3600}h"
        return f"T-{d // 86400}d"

    def reason_for_issue(n: int) -> str:
        return reason_for(reasons.get(n, ""))

    # ── header ──
    proj = cfg["project"]
    policy = (cfg.get("merge_policy") or {}).get("default")
    human = cfg.get("human_approves_merge") is True or policy == "human" or (
        policy is None and (cfg.get("harness") or cfg.get("host") or "claude") != "claude")
    mode_label = "human-approves" if human else "auto-merge"
    review = (cfg.get("roles") or {}).get("review") or {}
    review_label = f"{review.get('model') or 'sonnet'} (alt {review.get('alt_model') or 'opus'})"
    bot_login = (
        cfg.get("notifications", {}).get("bot_identity")
        or cfg.get("claim", {}).get("assignee_login")
        or "?"
    )
    max_workers = sum(((cfg.get("roles") or {}).get(r) or {}).get("max_workers", 1) for r in ("build", "qa", "review"))

    print(f"drover · {proj['title']} (#{proj['number']})")
    print("─" * 80)
    print(f"config: {config_slug}   labels: qa · bug · feature   base: {cfg.get('base_branch', '?')}")
    print(f"mode:   {mode_label:<22} reviewer: {review_label}")
    print()

    # ── kanban ──
    print(render_kanban(by_status, glyph_for_issue, reason_for_issue))

    # ── workers ──
    print()
    active = sum(len(workers) for workers in inflight.values())
    run_active = bool(last_tick) and not exited

    if not run_active and active == 0:
        print(f"▎Workers  (claim: {bot_login})")
        print("   (no active run — `drover run` to start)")
    else:
        print(f"▎Workers  (claim: {bot_login} · {active}/{max_workers} active)")
        if active == 0:
            print("   (idle)")
        else:
            for lane, v in [(l, v) for l in sorted(inflight, key=LANE_ORDER.index) for v in inflight[l]]:
                glyph = LANE_GLYPH[lane]
                role = LANE_ROLE[lane]
                item = item_by_number(int(v["issue"]))
                extras: list[str] = []
                if item:
                    extras = [
                        l for l in item["labels"]
                        if l.startswith("loop:") and not l.startswith("loop:in-")
                    ]
                extra = (" · " + ", ".join(extras)) if extras else ""
                print(
                    f"   {glyph} {role}  #{v['issue']}  attempt {attempt_str(item)} · "
                    f"{worker_dur(v['ts'])}{extra}"
                )

    # ── block reasons ──
    print()
    print("▎Block reasons")
    nums = [it["number"] for it in by_status["Blocked"]]
    print("\n".join(render_block_reasons([reason_for_issue(n) for n in nums], nums)))

    # ── recent events ──
    print()
    print("▎Recent  (last 5 manifest events)")
    if not recents:
        print("   (no manifest events yet)")
    else:
        for r in recents:
            t = t_minus(r["epoch"])
            # Dispatch detail (`attempt N/3`) is filled in at render time
            # because the manifest doesn't encode the rebuild count at
            # dispatch time — the number shown reflects the issue's *current*
            # label. Live workers in the §Workers block above use the same
            # source, so the two stay consistent with each other.
            detail = r["detail"]
            if r["verb"] == "dispatch" and not detail:
                issue_num = int(r["issue"].lstrip("#"))
                detail = f"attempt {attempt_str(item_by_number(issue_num))}"
            if r["target"]:
                print(
                    f"   {t:<7} {r['verb']:<9} {r['issue']:<4} → "
                    f"{r['target']:<9} {detail}"
                )
            else:
                print(
                    f"   {t:<7} {r['verb']:<9} {r['issue']:<4}   "
                    f"        {detail}"
                )

    # ── health ──
    print()
    print("▎Health")
    if run_active:
        print(
            f"   last tick: {delta_ago(last_tick)}    run started: {delta_ago(start_hms)}"
            f"    workers: {active}/{max_workers}    worktrees cleaned: {reaped_count}"
        )
    elif exited and start_hms:
        print(
            f"   last run: completed {delta_ago(last_tick or start_hms)}    "
            f"workers: 0/{max_workers} idle"
        )
    else:
        print(f"   no run today    workers: 0/{max_workers} idle")

    return 0


if __name__ == "__main__":
    sys.exit(main())
