# QA report · issue #10 · v1

Head under test: `eb5c0fb` (builder) + QA test commit. Non-visual change (runner shell), so there are no screenshots.

## AC ledger

| AC | Unit (`tests/test-run-state.sh`) | Whole runner (`tests/test_run_rounds.py`) |
|---|---|---|
| 1 in-flight card has lane, worker, since | case 1 | `test_run_state_json_after_a_run`: real run, card #7 `build`, `process:<pid>:…`, ISO `since` |
| 2 card that left its lane is dropped | case 2 (Building → QA, and a reaped lock) | n/a: lane exit needs a worker moving the card |
| 3 no partial file (tmp + rename) | case 3 (no `*.tmp.*` left, both writes go through `write_lock`) | no `state.json.tmp.*` left after the run |
| 4 exit sets `"stopped": true`, keeps cards | case 5 (the function) + grep on `halt_cleanup` | real exit through `halt_cleanup`: `stopped`, no `pid`, last card kept |
| 5 offline test in run-safety | `tests/test-*.sh` glob picks it up | `tests/test_*.py` glob picks it up |

## Mutation checks (not committed)

- Dropped `write_run_state stopped` from `halt_cleanup` → `test_run_state_json_after_a_run` red (`None is not True`).
- Dropped the lane filter in `write_run_state` → `test-run-state.sh` red (`FAIL: left lane`).

## Commands

```
bash tests/test-run-state.sh                                              PASS: run state
python3 tests/test_run_rounds.py RunnerRounds.test_run_state_json_after_a_run   OK
bash tests/run-safety.sh                                                  51 ok, 2 FAIL
```

## Known red, not from this PR

- `tests/test-guard-hooks.sh`: fails only inside a worker (`DROVER_ROLE` set). With
  `env -u DROVER_ROLE -u DROVER_RUN -u DROVER_ISSUE`: `PASS (107 cases)`.
- `tests/test_skill_dispatch.py` `test_legacy_halt_stops_owned_worker_preserves_unrelated_and_evidence`:
  2 failures, the same on base `origin/main` (`ab2d05d`) in a clean worktree.

## Security

pr-security-review: 0 findings. All values reach jq via `--arg`/`--argjson`; the issue
number is digits-only (`issue_locks`); the path is fixed under `.drover/runs/`.

## Test gaps (not written)

- Medium: under `set -e` a failed `write_run_state` in the tick loop (disk full, unwritable
  `.drover/runs/`) stops the runner; only the exit call has `|| true`. Witness: `chmod a-w .drover/runs` mid-run.
- Low: `stopped` mode on a corrupt `state.json` writes an empty file (jq fails, `write_lock` still runs).
