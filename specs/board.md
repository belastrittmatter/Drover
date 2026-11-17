# Board and status

Part of the [drover spec](README.md).

## Preserved from upstream

| ID | Must keep working | Source @ d0b7d5a | Test |
|---|---|---|---|
| FR-BRD-1 | The GitHub Project is the only source of truth for card state | `skills/super-board/SKILL.md`, `references/run.md` | `test-card.sh` |
| FR-BRD-4 | Every move to Blocked posts the block comment: reason tag, why, unblock checklist, owner, `blocked-by:` line | `references/block-template.md` | `test-file-bug.sh` (partial) |
| FR-BRD-5 | When an issue named in `blocked-by:` closes, the next round returns the card to its column | `references/run-workflow.md` round-start sweep | `test-wave-plan.sh` |
| FR-STS-1 | `status` is read-only and shows column counts, live workers, Blocked grouped by reason | `references/status.md`, `scripts/super-board-status.py` | `test_status_parse.py` |

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
  `[blocked:duplicate]`, `[blocked:unclear]`, `[blocked:too-big]`,
  `[blocked:sequenced]`, `[blocked:ci]`, `[blocked:creds]`,
  `[blocked:schema]`, `[blocked:rebuild-cap]`, `[needs-you]`. They replace
  upstream's emoji vocabulary (`block-template.md`, `status.md` section I).
- **FR-STS-2 (new)** Where hc is enabled, `drover status` shall show each
  live worker's hc terminal name so the owner can attach to it.
