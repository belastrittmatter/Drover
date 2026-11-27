# QA v1 — #12 drover statusline (PR #27, head 012036a)

| AC | Result | Proof |
|---|---|---|
| 1 two build + one review print `drover: 2 build · 1 review` + oldest card | pass | tests/test-statusline.sh case 2, 3 |
| 2 no state.json or `stopped: true` prints nothing, exit 0 | pass | cases 1, 5 (also outside a git repo, and a garbage file) |
| 3 paused run shows `paused` | pass | case 4 |
| 4 under 100 ms, no network | pass | case 7 (best of 15, script alone), case 8 (no network import) |
| 5 offline test picked up by glob | pass | tests/test-statusline.sh matches `tests/test-*.sh`; run-safety.sh unchanged |

Also checked
- state.json shape matches the writer in lib/drover-run.sh `write_run_state` (paused ISO string or null, cards with issue/lane/phase/since, `stopped` set on exit).
- Malformed shapes (list, string cards, non-dict card, mixed-type `since`) raise inside `line()`; `main()` catches them and prints nothing, exit 0.
- tests/test_harness_permissions.py pass; tests/test_writing_format.py 21/21.

Test gaps (not written, Medium/Low)
- A `since` of mixed type across cards falls to the silent-empty path, not a partial line. Acceptable for a status line.
- Cards in a lane outside build/qa/review count toward the oldest card only, not the lane list.

Security pass (pr-security-review on 9d1f2d9...012036a): no finding kept.
- `git rev-parse` runs with fixed arguments, no shell, no user input.
- The script only reads a local file and prints; no network, no write.
- Dropped as below the exploitability bar: a `phase` name containing terminal escape codes would print to the status line; phase names come from the repo's own workers writing local files.
