# QA v1 · issue #22 · PR #28 · head c4677e6

| AC | Proof |
|---|---|
| 1 normal exit logs `worker done` | `tests/test_run_rounds.py` test_handed_off_exit_logs_worker_done (card moved) + test_report_without_a_move_is_not_a_failed_dispatch (report posted, card stays) |
| 2 killed worker logs `reaped stale lock` | test_two_failed_dispatches_halt (same run) + test_live_worker_lock_survives_a_round (killed, reaped at next start) |
| 3 parsers read both | `tests/test_status_parse.py` test_worker_done_line_frees_the_worker + REAP_RE tests; in-repo FactoryBench uses metrics events, not manifest text; FR-MET-1 needs a later `pid=` line, which `worker done` carries |

Mutation checks: startup reap logging `worker done` → AC2 assertion red; reported exit logging stale lock → AC1 assertion red. Both restored.

Logs: local-tests.log (rc=0), run-safety.log (2 failures, both pre-existing: test_skill_dispatch.py fails on base 9d1f2d9; test-guard-hooks.sh passes with DROVER_ROLE unset).

Test gaps (not written):
- Medium: `drover status` "worktrees cleaned: N" now counts only stale reaps (lib/drover-status.py:736).
- Medium: exit 79 inside judge_finished_workers leaves queued workers with no log line (lib/drover-run.sh:708).
