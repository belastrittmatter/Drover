# Board and status

Part of the [drover spec](README.md).

## Preserved from upstream

| ID | Must keep working | Source @ d0b7d5a | Test |
|---|---|---|---|
| FR-BRD-1 | The GitHub Project is the only source of truth for card state | `skills/super-board/SKILL.md`, `references/run.md` | `test-card.sh` |
| FR-BRD-4 | Every move to Blocked posts the block comment: reason tag, why, unblock checklist, owner, `blocked-by:` line | `references/block-template.md` | `test-file-bug.sh` (partial) |
| FR-BRD-5 | When an issue named in `blocked-by:` closes, the next round returns the card to its column | `references/run-workflow.md` round-start sweep, `scripts/super-board-wave-plan.sh`, `scripts/super-board-deps.sh`; in drover: `lib/drover-run.sh` `refresh_deps` / `card_free` / `sweep_blocked` | `test_run_ports.py`, `test-deps.sh`, `test_approval.py` |
| FR-BRD-7 | A Ready card whose `blocked-by:` names an open issue is not claimed | `scripts/super-board-wave-plan.sh`, `scripts/super-board-deps.sh`; in drover: `lib/drover-run.sh` `refresh_deps` / `card_free` / `sweep_blocked` | `test_run_ports.py`, `test-deps.sh`, `test_approval.py` |
| FR-BRD-8 | When the owner approves a Blocked `[needs-you]` merge (`done` on the current PR head), the next round moves the card back to Review; if the head changed while Blocked, it asks for a new approval instead | `scripts/super-board-wave-plan.sh` `resume` / `refreshApproval`, `scripts/super-board-approval.py`; in drover: `lib/drover-run.sh` `refresh_deps` / `card_free` / `sweep_blocked` | `test_run_ports.py`, `test-deps.sh`, `test_approval.py` |
| FR-STS-1 | `status` is read-only and shows column counts, live workers, Blocked grouped by reason tag (FR-BRD-6; at `d0b7d5a` only 4 of 12 reason emoji were grouped) | `references/status.md`, `scripts/super-board-status.py` | `test_status_parse.py` |

## Contracts

- **FR-BRD-2** The system shall use exactly the columns Backlog, Ready,
  Building, QA, Review, Blocked, Done.

## New or changed

- **FR-BRD-3 (changed, stage 6)** A card shall reach Done only after its
  merge commit is confirmed on the base branch, or after a not-planned
  close with a comment. If a card's issue is closed without either, then
  the system shall move it to Blocked `[needs-you]`. (Upstream moves any
  closed issue to Done without a merge check:
  `scripts/super-board-run.sh:216-225`. Stage 0 tests cover only the
  open-card side.)
- **FR-BRD-6 (changed)** Reason tags shall be plain text from one fixed list
  defined in a single file read by both writers and the status parser:
  `[blocked:creds]`, `[blocked:quota]`, `[blocked:access]`,
  `[blocked:unclear]`, `[blocked:unsafe]`, `[blocked:review]`,
  `[blocked:design]`, `[blocked:duplicate]`, `[blocked:sequenced]`,
  `[blocked:too-big]`, `[blocked:ci]`, `[blocked:schema]`,
  `[blocked:rebuild-cap]`, `[needs-you]`. `drover status` groups Blocked
  cards by these tags. They replace upstream's 12 reason emoji
  (`block-template.md`, `status.md` section I); upstream's out-of-scope and
  wrong-place reasons become `[dropped]`: the card is closed as not planned
  and moves to Done, as upstream already does for dropped cards.
- **FR-STS-2 (new)** Where hc is enabled, `drover status` shall show each
  live worker's hc terminal name so the owner can attach to it.
