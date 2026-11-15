# Lint and standalone skills

Part of the [drover spec](README.md).

## Preserved from upstream

| ID | Must keep working | Source @ d0b7d5a | Test |
|---|---|---|---|
| FR-LNT-1 | `lint` checks active issues against the 19-criterion table (incl. observable outcome, Given/When/Then, too big) and writes the pre-flight checklist `run` requires | `skills/super-board/references/lint.md` | none |

## New or changed

- **FR-EXT-1** v1 shall keep all nine upstream skills, renamed: `drover`,
  `drover-build`, `drover-qa` (including crawl mode), `drover-review`,
  `drover-collect`, `pr-security-review`, `git-sync`, `visual`,
  `ui-refine-loop`. The extras shall work standalone and shall not be
  required by the core loop, except `pr-security-review` when configured as
  a QA skill (FR-QA-3).
- **FR-EXT-2** The audit (PLAN stage 1) shall record keep or improve for
  each skill. A skill is removed only for an observed reason (broken, unused
  in real runs, duplicated by another skill), and removal is an "ask first"
  change.
- **FR-EXT-3 (changed)** QA crawl mode shall work on its own branch and open
  a PR at the end of each iteration, merged through the gate like any card.
  (Upstream commits each iteration directly to the active branch.)
