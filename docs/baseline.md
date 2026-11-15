# Stage 0 baseline

Fork point `d0b7d5a`. Raw suite output: [baseline.txt](baseline.txt)
(`bash tests/run-safety.sh`: 64 suites, all pass, exit 0).

## Why the reference run reaped workers' locks

**Short answer: the reaped workers had already exited. The runner did not
delete a live worker's lock as stale.** The reference run (one card, three
runner starts, `claude-p` backend, 120 s tick) shows 12 lock removals. Each
worker's own harness session log gives the time of its last event. Comparing
that time with when its lock was removed:

| # | Lane | Dispatched (UTC) | Session last event | Lock removed | Removed by | Worker alive? |
|---|---|---|---|---|---|---|
| 1 | build | 15:07:14 | 15:07:30 | 15:09:17 | reap | no, gone 107 s |
| 2 | build | 15:09:31 | 15:09:45 | 15:11:34 | reap | no |
| 3 | build | 15:11:46 | 15:12:02 | 15:13:51 | reap | no |
| 4 | build | 15:14:03 | 15:14:18 | 15:16:06 | reap | no |
| 5 | build | 15:16:19 | 15:16:38 | 15:18:22 | reap | no |
| 6 | build | 15:18:34 | 15:18:48 | 15:22:08 (next start) | reap | no |
| 7 | build | 15:22:30 | 15:26:38 (final report) | 15:26:38 | zombie watchdog | yes, done but not exited |
| 8 | qa | 15:26:50 | 15:29:40 | 15:30:59 | reap | no |
| 9 | build | 15:31:10 | 15:34:08 | 15:38:39 (next start) | reap | no |
| 10 | review | 15:41:08 | 15:44:47 | 15:45:18 | reap | no |
| 11 | qa | 15:45:33 | 15:48:58 | 15:49:40 | reap | no |
| 12 | review | 15:49:52 | 15:51:54 (mid tool call) | 15:51:55 | zombie watchdog | yes, still working |

One more removal is not logged: a QA worker (15:38:57 to 15:40:59) had its
dead lock removed by `issue_locked()` when the review lane picked the same
card. Locks are one file per issue.

The "2-4 minutes" is the tick, not the worker's lifetime. Locks are checked
only at the start of a round, one tick (120 s) after the previous round, so
a worker that exits after 15 s is reaped about 2 minutes after dispatch.

### Code paths

- `scripts/super-board-run.sh:478` (`reap_finished_locks()`) and
  `scripts/super-board-run.sh:261` (`issue_locked()`) remove a lock only when
  `kill -0 $PID` fails. `$PID` is the worker itself: `nohup claude -p ... &`
  at `scripts/super-board-run.sh:352` gives `$!` = the `claude` process.
  `claude` on the reference machine is a native binary (a Mach-O, not a
  shell wrapper), so the PID does not belong to a launcher that exits early.
  The runner logged `b_idle=0` / `q_idle=0` for the workers that ran for
  minutes, which confirms `kill -0` saw them while they were alive.
- `scripts/super-board-run.sh:375-412` (`check_lane_zombie()`) is the only
  code that removes a live worker's lock. It does this by design: when the
  board shows the card has left the lane's source column, it kills the worker
  (`:399-401`) and then removes the lock (`:405`). Because it kills first,
  FR-WRK-1 still holds: the lock goes only after the worker is stopped. Row 7
  had written its final report. Row 12 had moved its card to Blocked and was
  still running a tool call when it was killed, so any writes it had left
  after the move (such as the block comment) were lost.

### What actually stopped the run

1. **Rows 1-6: workers could not run their first command.** The prompt
   (`scripts/super-board-run.sh:342`) tells the worker to pipe card JSON into
   `super-board-skills-ready.py`. The headless worker's permission rules held
   that piped command for approval, and nobody could approve it. So each
   worker stopped within 14-19 s without touching the board. The card stayed
   in Ready, the reap released the claim (correctly), and the next round
   dispatched the same card again. After 6 rounds with nothing reaching Done,
   the runaway gate stopped the run (`scripts/super-board-run.sh:761`). The
   later starts get past this step (exit 0), which suggests the permission
   setting was changed between starts.
2. **Rows 8-12: the lanes ran normally.** QA failed the card back to Ready,
   then review bounced it to QA twice, and finally moved it to Blocked for a
   human decision. Each worker exited when its lane finished, and its lock
   was reaped as it should be. The second start ended on the runaway gate
   while a rebuild worker (row 9) was still running. The runner does not kill
   workers on that exit, so row 9 finished after the runner had stopped.

### Regression test and fix

`tests/test_run_rounds.py::test_live_worker_lock_survives_a_round` runs two
whole runner rounds with fake `gh`, `claude` and `pgrep` on `PATH`. A live
fake worker keeps its lock, its claim and its only dispatch. Once it exits,
the next round reaps it. The test **passes on the untouched runner**. As a
check, making `reap_finished_locks()` ignore liveness (line 478) makes it fail
(`live worker lost its lock`). Since there is no live-lock bug to fix,
`scripts/super-board-run.sh` is unchanged.

### Related findings, not changed in stage 0

- **FR-BRD-3 gap:** `top_card_in_column()` moves *any* closed issue in an
  active column to Done (`scripts/super-board-run.sh:216-225`). It does not
  check for a merge commit or a deliberate not-planned close. The new
  `test_done_only_after_merge` covers the open-card side: an open card whose
  reviewer exits without merging never reaches Done. The merge side is
  covered by `test-merge-gate.sh`, where an unconfirmed merge halts (exit 79)
  and never exits 0.
- **Re-dispatch on a no-op exit:** a worker that exits without moving its
  card gets the same card back on the next tick, until the runaway gate
  stops the run (rows 1-6).
