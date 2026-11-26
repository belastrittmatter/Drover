# issue-11 QA v1 — PR #25 @ 9a516aa

| AC | Result | Proof |
|---|---|---|
| 1 `drover phase tests` records for the worker's issue | pass | tests/test-phase.sh:17-20; live call from the linked worktree wrote .drover/runs/phases/11.jsonl in the main checkout (live-phases-11.jsonl) |
| 2 state.json shows `phase` and ordered `phases` | pass | tests/test-phase.sh:45-58 (write_run_state); mutant dropping `.at >= $since` goes red |
| 3 bad names exit 64, write nothing | pass | tests/test-phase.sh:23-29; newline and trailing-space names exit 64 (name-edge-cases.log) |
| 4 test_harness_permissions.py passes | pass | tests.log |
| 5 offline test picked up by glob | pass | tests/run-safety.sh:12 `tests/test-*.sh`; run-safety.log |

run-safety: all suites pass except tests/test_skill_dispatch.py (2 failures in test_legacy_halt_stops_owned_worker_preserves_unrelated_and_evidence), identical on unchanged main.
pr-security-review: 0 findings.
Test gaps (not written, medium/low): runner does not re-check the name pattern when it reads the log (a hand-written line passes any string to state.json); submodule checkout writes to the parent repo's runs dir.
