# Roles: builder, QA, reviewer

Part of the [drover spec](README.md). The merge itself is in [gate.md](gate.md).

## Preserved from upstream

Source for all rows: `skills/super-board/references/run.md` "Lane
lifecycles", plus the role skill named.

| ID | Must keep working | Source @ d0b7d5a | Test |
|---|---|---|---|
| FR-BLD-1 | Builder works in its own worktree on `issue-<n>-<slug>`, creating it from base or continuing it | `run.md` builder 1-2 | `test_worktree_paths.py` |
| FR-BLD-2 | Builder reads issue, all comments and the project's agent instructions before changing code | `run.md` builder 3 | none |
| FR-BLD-3 | Builder writes a failing test per testable criterion before the change (TDD) | `super-build/SKILL.md`, worker preamble | none |
| FR-BLD-4 | Over `auto_max_lines` (default 400, lockfiles etc. excluded): push, propose a split, Blocked `[blocked:too-big]` | `run.md` builder 4 | none |
| FR-BLD-5 | One draft PR per card with the PR template; `[builder] [report]` comment | `run.md` builder 6-7, `scripts/super-board-pr-body.sh` | `test-pr-body.sh` |
| FR-BLD-6 | Rebuild fixes every unresolved `[builder]` thread and every open or `not fixed` builder finding | `run.md` builder (rebuild) | none |
| FR-QA-1 | One observable check per criterion, expected vs actual recorded | `run.md` tester 4 | none |
| FR-QA-2 | Test-gap ledger: every criterion mapped to unit / component / e2e tests or `none`; tests that assert nothing count as `none`; on auth, money, data-loss or security changes a subagent that sees only criteria and diff does it | `super-qa/SKILL.md` "Test-gap check" | none |
| FR-QA-4 | Pass moves to Review; fail moves to Ready with `loop:rebuild-<k>` | `run.md` tester 6-7 | none |
| FR-QA-5 | A `qa` card: QA creates the branch; defects it cannot cover become new `bug` cards | `run.md` "`qa` cards", `scripts/super-qa-file-bug.sh` | `test-file-bug.sh` |
| FR-QA-6 | QA records the exact test command for the reviewer | `run.md` reviewer 5 (`Local tests:` line) | none |
| FR-REV-2 | Reviewer reruns QA's test command; red bounces to QA | `run.md` reviewer 5 | none |
| FR-REV-4 | Findings classed Gap / Bug / Verification miss / Scope drift / Over-engineering, severity Blocker / Should fix / Nit; Over-engineering never blocks or bounces alone | `super-review/SKILL.md` | `test-wave-review-memory.sh` (partial) |
| FR-REV-7 | A prior finding `not fixed` bounces to its owner without a fresh pass | `run.md` reviewer 3b | `test-wave-review-memory.sh` |

## Contracts

- **FR-REV-5** The system shall keep exactly one review report comment per
  PR, starting with the marker `<!-- drover:review-report -->` (upstream:
  `<!-- super-review:report -->`), edited in place each round. Finding ids
  are stable across rounds, new findings take the next free id, rows are
  never deleted. Statuses: `open`, `fixed`, `not fixed`, `no longer applies`.
  Header states: `merge-ready`, `bounced`, `blocked`, `human-gated`, `merged`.
- Thread prefixes `[builder]`, `[qa]`, `[reviewer]` route findings: an open
  `[builder]` thread sends the card to Ready, `[qa]` to QA.

## New or changed

### Builder

- **FR-BLD-7 (new, stage 7)** For a `bug` card, the builder's first commit
  shall be a reproduction test. The reviewer shall run that test at the
  base commit; if it passes there, then the reviewer shall record a
  Verification miss for the builder and bounce the card.

### QA

- **FR-QA-3 (changed)** Where `pr-security-review` is among the configured QA
  skills, QA shall run it on the PR diff; each kept finding fails QA as a
  `[builder]` thread. (Upstream: always on.)

### Reviewer

- **FR-REV-1 (changed)** The reviewer shall record its own hypotheses before it
  receives the builder's and QA's reports; FR-CTX-3 enforces the order.
  (Upstream: same rule, prompt only.)
- **FR-REV-3 (changed)** The reviewer shall check the history of changed
  lines itself (`git log`, `git blame`: reverts, earlier attempts, linked
  decisions). Replaces upstream's historian subagent.
- **FR-REV-6 (new)** The reviewer shall write the report only through
  `drover review-report put`, which shall:
  1. load the previous report from the PR, if any;
  2. reject the new report unless every previous finding id is present
     with status `fixed`, `not fixed` or `no longer applies`, and each
     `fixed` row cites a `file:line` at the reviewed head in a file that
     changed since the previous report's reviewed head (a fix with no
     change to the cited file is rejected);
  3. reject statuses, classes or severities outside the fixed lists;
  4. record the reviewed head SHA.
- **FR-REV-8 (new)** See [gate.md](gate.md) FR-MRG-4 exit 6: the gate checks
  the latest report before merging.
- **FR-REV-9 (new)** Where `roles.review.second_opinion` is `risky` and the
  card is risky (see README domain model), or where it is `always`, the
  system shall run one independent second reviewer that receives only the
  acceptance criteria and the diff and returns findings with class,
  severity and `file:line`. No confidence score. Replaces upstream's
  `truth_gate`, `truth_threshold` and code-grounder subagent.
- **FR-REV-10 (new)** The second reviewer shall run on
  `second_opinion_model`, which shall differ from the main reviewer's
  model; the report shall name both models.
- **FR-REV-11 (new)** If the second reviewer reports a Blocker the main
  reviewer did not, then it shall be added to the report and handled like
  any Blocker.
- **FR-REV-12 (new)** The review report shall list every change to a test
  file made by a commit outside the QA role since QA's last pass, with the
  commit and file. This is visibility only; it blocks nothing.

### Context packs (stage 7)

Upstream gives each worker a one-line prompt and lets it load its own
context, about 1,900 lines for the reviewer, most of it other roles' steps.

- **FR-CTX-1** `drover context <role> <issue>` shall build each worker's
  context deterministically and pass it as the worker's first input. No role
  shall be told to load the whole pipeline document.
- **FR-CTX-2** Packs shall contain exactly:

  | Content | build | qa | review | second opinion |
  |---|---|---|---|---|
  | That role's rules file | yes | yes | yes | yes |
  | Shared rules: markers, `commit_style` + 3 repo examples, block format | yes | yes | yes | no |
  | Project instructions file and configured skills | yes | yes | yes | no |
  | Issue and acceptance criteria | yes | yes | yes | yes |
  | Diff against base | own branch | yes | yes | yes |
  | Unresolved threads | `[builder]` | `[qa]` | all | no |
  | Builder report | no | yes | after FR-CTX-3 | never |
  | QA report and test command | no | no | after FR-CTX-3 | never |
  | Previous review report | own `open` / `not fixed` rows | own `open` / `not fixed` rows | full | never |

- **FR-CTX-3** The reviewer's first pack shall hold only criteria, diff and
  the previous report. `drover context review --claims` shall refuse until
  the reviewer has recorded hypotheses for the current head in the review
  report (section "Hypotheses"), so the order is stored on GitHub
  (FR-BRD-1) and visible.
- **FR-CTX-4** Tests shall assert each role's pack contents, including that
  a second-opinion pack never contains builder, QA or review reports.

## Edge cases

- PR pushed to after review passed: the gate exits 6; the card is reviewed
  again.
- Review report edited by a human: `put` validates against the edited
  version; a removed row is a validation error the reviewer reports.
