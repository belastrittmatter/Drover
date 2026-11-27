# QA v2 · issue #22 · PR #28 · head cbaecab

Rebuild after review R1 (`worker done` not counted in `worktrees cleaned`) and R3 (FR-MET-2 note placement).

| AC | Proof |
|---|---|
| 1 normal exit logs `worker done` | `tests/test_run_rounds.py` test_handed_off_exit_logs_worker_done + test_report_without_a_move_is_not_a_failed_dispatch · 17 OK |
| 2 killed worker logs `reaped stale lock` | test_two_failed_dispatches_halt + test_live_worker_lock_survives_a_round · 17 OK |
| 3 parsers read both | `tests/test_status_parse.py` test_worker_done_line_frees_the_worker (now asserts reaped_count 1) + REAP_RE tests · 32/32; no other manifest parser in `lib/` |

R1: mutation check, removing `reaped_count += 1` at `lib/drover-status.py:204` turns test_worker_done_line_frees_the_worker red. Restored.
R3: `specs/metrics.md:30-33` closes the 2026-11-17 approval parenthesis before the issue #22 note.
Security pass (pr-security-review): no findings; the diff adds log text and a `\d+`-anchored regex, no new input surface.

Logs: local-tests.log, run-safety.log (2 failures, both pre-existing: test_skill_dispatch.py fails on origin/main 9d1f2d9 too; test-guard-hooks.sh passes 107 cases with DROVER_ROLE unset, it fails only inside a qa worker).

Test gaps (not written):
- Medium: exit 79 inside judge_finished_workers leaves queued workers with no log line (lib/drover-run.sh:708); the halt record covers it.
