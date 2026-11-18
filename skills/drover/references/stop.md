# drover stop — full contract

**Where it runs:** interactive orchestrator. Spawns the headless `drover stop` synchronously, reports the summary, exits. No background processes.

## What stop does — the one-line version

Tears down an in-flight `drover run` cleanly: posts a "stopped here" comment on every claimed issue + PR, releases the assignee mutex, kills workers + dispatcher, removes lock files. **Run = resume**, so the next `drover run` picks up where stop left off.

## Intro shown when stop runs

```
drover stop
─────────────────────────────────────────────────────────
Purpose: terminate the in-flight run and wrap up context
         so the next `drover run` resumes cleanly.

Progress: [done] onboard  →  [done] run  →  stop (you are here)
─────────────────────────────────────────────────────────
```

## Preconditions

| Check | Action on fail |
|---|---|
| `.drover/config.json` exists (one per repo) | Exit 64: "no config" hint |
| `gh auth` valid | Continue anyway — comments may fail, kills still work |

Nothing else. Stop is intentionally tolerant — its job is to bring the system to rest, not to enforce policy.

## What stop reads

- `.claude/drover/inflight/<issue-N>` — one file per in-flight worker: `HANDLE=process:<pid>:<start>\nLANE=…\nSTARTED=…\nCOLUMN=…`. Older `PID=…` and single-line PID locks are also read.
- `.claude/drover/runner.lock` — this repo's dispatcher (`HANDLE=process:<pid>:<start>`). A pid whose start time no longer matches is dead and never signalled; an older `PID=` lock is reported, never signalled.

Stop touches only these (runner.md FR-WRK-8): it never searches the process table, so another
repo's runner and workers are left alone. Liveness and signals go through `lib/drover-worker.sh`.

## Per-worker wrap-up (run for each in-flight lock)

1. **Look up branch + PR**
   - `git branch -a --list "*issue-<N>-*"` → latest commit on the issue branch.
   - `gh pr list --search "head:issue-<N>-*"` → PR number, if any.
2. **Post the stop comment** on the issue AND (if it exists) the PR, in the comment format of
   writing-standard.md § 4 — header `[orchestrator] [report] stopped · <lane> lane`, then:
   - Did: worker PID and timestamp.
   - Done: last commit on the branch (a recovery point; inspect the preserved worktree for later edits).
   - Not done: interrupted work may be uncommitted.
   - Next: `drover run`.
3. **Release the assignee mutex** on the issue (`gh issue edit --remove-assignee <bot>`).
4. **Remove descriptive labels** (`loop:in-build`, `loop:in-qa`, `loop:in-review`) — best-effort.
5. **SIGTERM the worker**, sleep 1s, SIGKILL if still alive (`drover worker stop`).

The dispatcher named in `runner.lock` is stopped first (SIGTERM → 1s → SIGKILL), so it
cannot start a new worker during the wrap-up.

## After the per-worker loop

6. **Clear in-flight locks** — remove each wrapped-up lock whose worker is no longer alive; a lock whose worker survived is kept.
7. **Print summary** — workers stopped, dispatchers stopped, resume command.

## What stop does NOT do (deliberate)

- **Does not wait for workers to finish.** `claude -p` workers have no SIGTERM handler that flushes a partial commit. Worktree files remain, but unsaved in-memory work may be lost. Inspect uncommitted edits before resuming; restart recovery keeps dirty worktrees for inspection.
- **Does not touch worktrees** under `.drover/worktrees/` or the legacy `.worktrees/` and `.claude/worktrees/issue-*` locations. A legacy board worktree halts the next run until inspected/moved to the canonical folder; cleanup never force-removes it.
- **Does not touch branches or PRs.** Both persist. State lives on the GitHub Project board — cards stay in whichever column they were in when stopped.
- **Does not modify the config.** A stopped run is not a deactivated config; `.drover/config.json` is untouched.
- **Does not bypass the GitHub assignee mutex.** It releases the mutex, then kills. If the GitHub API is unreachable, release is best-effort and the orphan-scan + reap-on-next-start covers the gap.

## Resume = run (no separate verb)

There is **no `drover resume` verb on purpose**. The board is the state:

- Cards sit in whichever column they were in when stopped.
- Branches + PRs persist.
- The wrap-up comment on each in-flight issue documents what was in progress.
- `drover run` claims the same cards on its next tick and re-runs the lane from scratch (Builder re-implements, Tester re-runs tests, Reviewer re-reviews — there's no per-lane checkpoint inside a worker).

Resume cost: **one lane cycle per previously-in-flight card** (Builder ~5min, Tester ~10min, Reviewer ~3min on a typical card), after any required recovery. Inspect and preserve local edits first; dirty or legacy worktrees need attention before automatic recovery continues.

## When to use stop

- Need to step away and don't want workers consuming gh quota / Claude budget while idle.
- Discovered a bug in the dispatcher / a skill and need to fix it before more cards process.
- About to make a destructive board edit (column rename, project re-permission) that would break in-flight workers.
- Rate limits, billing alerts, or any cross-cutting "stop everything" signal.
- End of a work session — clean shutdown beats letting workers run unattended overnight.

## When NOT to use stop

- A single card is stuck → fix the card; the lane-zombie watchdog auto-recovers, and the rebuild-cap gate moves persistently-failing cards to Blocked.
- The dispatcher's `no progress for 3 ticks` halt already fired → already stopped cleanly.
- You're just curious about status → use `drover status` (read-only, no side effects).

## Orchestrator behavior (when Claude is asked to "stop drover")

Per the cardinal orchestrator/worker rule:

1. Verify `.drover/config.json` exists.
2. Run `drover stop` synchronously (it's fast — seconds, not minutes).
3. Pass through the script's summary to the user.
4. **Do not** retry kills, do not chase down zombies the script missed, do not "while you're at it" clean up worktrees or branches. If the script reported failures, surface them and wait for explicit user direction.

## Failure modes + recovery

| Symptom | Cause | Recovery |
|---|---|---|
| `gh issue comment` fails | gh auth expired or network blip | Comments are best-effort. The kill + lock cleanup still happens. Manually note the stop in the issue later if needed. |
| Assignee release fails (gh 403) | rate limit or auth | The next `drover run` startup runs `reap_finished_locks` which sweeps stale assignees idempotently. No manual action needed. |
| Worker PID survives SIGKILL | extremely rare (kernel-level stuck process) | `ps aux \| grep claude -p` to confirm, then escalate via OS tools. |
| Stop reports "nothing to stop" but `ps` shows live workers | dispatcher and workers were started by a different repo / different inflight dir, or have no lock | Run stop in the right repo; stop never signals a process it has no lock for. |

## Lock file format

```
HANDLE=process:12345:Tue_May_24_18:42:10_2026
LANE=qa
STARTED=2026-05-24T18:42:11Z
COLUMN=QA
```

Parsed line by line, never sourced (`worker_lock` in `lib/drover-worker.sh`). Older `PID=12345` locks and single-line PID files are still read, so an upgrade mid-run is safe.
