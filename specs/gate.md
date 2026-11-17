# Merge gate

Part of the [drover spec](README.md).

## Preserved from upstream

| ID | Must keep working | Source @ d0b7d5a | Test |
|---|---|---|---|
| FR-MRG-1 | Only the reviewer merges, and only through the gate | `references/run.md` merge protocol | `test-merge-gate.sh` |
| FR-MRG-2 | Gate takes the merge lock, checks the head equals the reviewed SHA, merges current base into a scratch worktree at that SHA, runs `verify_commands`, squash-merges with a head match | `scripts/super-board-merge-gate.sh` | `test-merge-gate.sh` |
| FR-MRG-3 | Gate applies `merge_policy` (auto / human / human for money, auth, schema, size) and `migrations` (allowed envs) itself | `scripts/super-board-merge-gate.sh`, `scripts/super-board-merge-policy.py` | `test-merge-gate.sh` |
| FR-MRG-5 | `run` refuses to start when it would auto-merge to a production `main` without explicit opt-in | `references/run.md` "Production-merge guard" | `test-run-gates.sh` |

## Contracts

- **FR-MRG-4** The gate's exit codes are a stable contract. Changing one is
  an "ask first" change.

  | Exit | Meaning | Card goes to |
  |---|---|---|
  | 0 | merged | Done (after FR-BRD-3) |
  | 2 | branch does not build against current base | rebase pass |
  | 3 | GitHub refused after green verify | Blocked |
  | 4 | merge lock held | stays in Review |
  | 5 | base does not merge cleanly | rebase pass |
  | 6 | head is not the reviewed SHA, **or (new) the latest review report does not name this head, is not `merge-ready`, or has a Blocker row that is `open` or `not fixed`** | stays in Review, reviewed again |
  | 7 | policy: a human merges | Blocked `[needs-you]` |
  | 8 | a human step is required (e.g. migration) | Blocked `[needs-you]` |
  | 9 | `verify_commands` unusable | run halts |

  The bold part of exit 6 is the only change from upstream (FR-REV-6/8).
