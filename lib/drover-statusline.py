#!/usr/bin/env python3
"""drover statusline: one line from .drover/runs/state.json (specs/runner.md FR-RUN-19).

  drover: paused · 2 build · 1 review · #14 tests

Lane counts in board order, then the oldest card with its phase. Prints nothing and
exits 0 when there is no state file, it is unreadable, or the runner stopped. Local
file read only: no network, no config validation (bin/drover skips it for this command).
"""
import json
import os
import subprocess

LANES = ("build", "qa", "review")


def state_path():
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, check=False).stdout.strip()
    return os.path.join(os.path.dirname(common), ".drover", "runs", "state.json") if common else None


def line(state):
    if state.get("stopped"):
        return ""
    cards = state.get("cards") or []
    parts = ["paused"] if state.get("paused") else []
    for lane in LANES:
        n = sum(1 for c in cards if c.get("lane") == lane)
        if n:
            parts.append(f"{n} {lane}")
    if cards:
        oldest = min(cards, key=lambda c: c.get("since") or "")
        phase = oldest.get("phase")
        parts.append(f"#{oldest.get('issue')}" + (f" {phase}" if phase else ""))
    return "drover: " + (" · ".join(parts) or "idle")


def main():
    try:
        with open(state_path()) as f:
            state = json.load(f)
        out = line(state)
    except (OSError, TypeError, ValueError, AttributeError):
        out = ""
    if out:
        print(out)


if __name__ == "__main__":
    main()
