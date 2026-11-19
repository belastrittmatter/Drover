# drover specification

Status: Draft · Created: 2026-11-14 · Fork point: super-board `d0b7d5a` ·
Build order: [../PLAN.md](../PLAN.md)

Read this file first, every time. Then read only the area files your task
needs.

| File | Covers | IDs |
|---|---|---|
| [board.md](board.md) | Board, columns, blocking, status | FR-BRD, FR-STS |
| [runner.md](runner.md) | Scheduling, workers, hc, herdr-app | FR-RUN, FR-WRK |
| [roles.md](roles.md) | Builder, QA, reviewer, review memory, second opinion | FR-BLD, FR-QA, FR-REV |
| [gate.md](gate.md) | Merge gate and its exit-code contract | FR-MRG |
| [config.md](config.md) | Project config, skill routing, harnesses | FR-CFG, FR-HRN |
| [conventions.md](conventions.md) | Commit style, markers, emoji, evidence | FR-CNV, FR-EVD |
| [extras.md](extras.md) | Lint and standalone skills | FR-LNT, FR-EXT |
| [metrics.md](metrics.md) | Run metrics through FactoryBench | FR-MET |

## How this spec is written

- **Only what drover changes, plus contracts, gets full requirements.**
  Upstream behaviour that stays as it is gets one line in a "Preserved"
  table in its area file: what must keep working, the upstream source at
  `d0b7d5a`, and the test that covers it. The upstream file is the spec for
  the detail; the line is the regression checklist for PLAN stages 2-6.
- **Contracts** are behaviour other code depends on (exit codes, comment
  markers, columns, reason tags). They get full requirements even when
  inherited, because changing them breaks callers.
- Requirements use EARS phrasing: *The system shall ...* (always), *When
  <trigger>, ...* (event), *While <state>, ...* (state), *If <unwanted
  condition>, then ...* (error), *Where <feature is enabled>, ...* (option).
- IDs are stable. Never renumber; retire an ID by striking it through.
- No open questions in the spec: what needs a measurement or test is a task
  in a PLAN stage; what is decided against or postponed is a row in the
  decisions table below.
- Change the spec first, then the plan, then the code.

## 1. Objective

drover drains a GitHub Project board without supervision: cards a person
moves to Ready come back as merged pull requests with test evidence and a
review trail, or as Blocked cards that say exactly what a human must do.

It is super-board cut down and made harness-independent:

- one headless runner, any coding-agent harness as worker;
- per-project configuration of skills, models and parallelism;
- an optional hc add-on to watch and steer every worker live.

### Users

- **Owner**: one developer who writes issues, sets the config, approves
  human-gated merges, and sometimes watches workers.
- **Worker agents**: headless harness sessions that run one role on one card.

### Out of scope (v1)

- Any state store other than GitHub Projects.
- A web UI of drover's own (GitHub and herdr-app are the UIs).
- Multi-repo boards (one board drives one repo).
- Running without network access to GitHub.

## 2. Decisions

| Topic | Decision | Rejected alternative |
|---|---|---|
| Worker backend | Headless worker command per harness (`claude -p` first); hc is an opt-in add-on with fallback | Upstream `workflow` backend (in-session, not watchable); hc-only |
| Upstream backends | Drop `workflow` and Codex scripts after porting pre-flight, classifier, usage pause, the Blocked sweep (FR-BRD-5), the open-blocker filter (FR-BRD-7) and approval resume (FR-BRD-8) into the runner | Keep three backends |
| State | GitHub Project board; no state abstraction until a second store exists | Labels only; local SQLite |
| Concurrency | `max_workers` per role, default 1 | Fixed 1 per role |
| Structure | `drover` CLI on PATH via `install.sh`, which links `bin/drover` into `~/.local/bin`; skills linked into the harness's user folder (`~/.claude/skills`); hooks in the untracked `.claude/settings.local.json`; project holds one `.drover/config.json` only | Copy scripts into each project; brew tap (later); Claude plugin; several configs per repo with an `active` pointer |
| Skill routing | Per project config (role, label, issue `Skills:`) | Hardcoded `REGISTRY` |
| Commit / PR titles | Match the repo's detected style; conventional commits as fallback | Upstream emoji format |
| QA evidence | Chosen at onboard: orphan branch (default `drover-evidence`) or the PR branch; SHA-pinned raw URLs | One fixed location |
| Reviewer extras | Inline history check; one independent second opinion on risky cards from a different model, no score | Code-grounder + historian subagents with confidence threshold |
| Review memory | Enforced by `drover review-report put` and the gate | Prompt only |
| Test-first | Bugs only, via the builder's reproduction test | Separate test-writing stage for every card |
| Role context | Context packs built by the CLI (roles.md FR-CTX) | Agents load their own context |
| QA crawl mode | Own branch and PR per iteration, through the gate | Direct commits to the active branch |
| Test locking | Deferred; revisit when FR-REV-12 shows builders editing QA tests | Hook + gate exit 10 |
| Hidden checks | Deferred; needs storage that keeps FR-BRD-1 | Local hidden tests per card |
| Cross-PR knowledge | Deferred; upstream's calibration has nothing to reuse (stage 1 audit), so it starts fresh | `.drover/knowledge.md` of recurring findings |
| Stack presets | Deferred | ios / web-ts / python / rust presets at onboard |
| Harnesses | Claude only in v1 (config.md FR-HRN-3) | Codex, OpenCode, Antigravity in v1 |
| Skills | Keep all upstream skills; remove one only for an observed reason | Remove by audit verdict up front |
| Metrics | FactoryBench: one attempt per try at a card, lanes from the run manifest (metrics.md) | One attempt per worker; drover's own metrics store |
| Bot account | Deferred; known issue in metrics.md | Separate GitHub login for drover in v1 |
| Run dashboard | v1: `/drover run` starts the runner, prints PID and log path, and ends; progress via `drover status`. The runner writes `.drover/runs/state.json` each tick (runner.md FR-RUN-17). Deferred: workers report phases (`drover phase <name>`), a Claude Code mod shows a docked pane (runs list, lanes, worker phases, log tail) plus a status-line entry, and a harness-agnostic `drover dash` terminal UI serves herdr/hc (stages 9-10) | In-session wave loop that holds the run in the chat |
| Domain glossary, spec links, when-to-use rule | Not in v1 | Glossary file checked by reviewer; `spec:` card links with progress; README routing rule |

## 3. Domain model

| Entity | Meaning | Key attributes |
|---|---|---|
| Board | The GitHub Project that holds all state | owner, number, columns |
| Column | A card's position | Backlog, Ready, Building, QA, Review, Blocked, Done |
| Card | One issue on the board | number, title, labels, column, assignee (claim) |
| Role | A kind of worker | build, qa, review |
| Worker | One harness session running one role on one card | handle, role, card, harness, backend |
| Branch | The single branch per card | `issue-<n>-<slug>` |
| PR | The single PR per card | draft until review passes |
| Thread | PR review thread owned by a role | prefix `[builder]`, `[qa]`, `[reviewer]` |
| Review report | The reviewer's single PR comment, edited in place | marker, round, reviewed head, findings |
| Finding | One review result | id `R<k>`, owner, class, severity, location, status |
| Evidence | QA artefacts for one card and QA round | issue, version, files, evidence commit |
| Harness | A coding-agent CLI drover can drive | name, worker command, skill dirs, usage probe |
| Config | Per-project settings | `.drover/config.json` |

**Risky card**: labelled `security`, `migration`, `payments` or `auth` (by
the owner or by the classifier, FR-RUN-7), or a diff over
`merge_policy.auto_max_lines`, or a diff the merge policy classes as money,
auth or schema (FR-MRG-3). Used by FR-REV-9.

Card paths by label:

| Label | Path |
|---|---|
| `feature`, `bug`, none | Ready -> Building -> QA -> Review -> Done |
| `qa` | Ready -> QA -> Review -> Done |

## 4. User stories

P1 = useless without it; P2 = needed for daily use; P3 = can ship later.

| ID | P | Story | Independent test |
|---|---|---|---|
| US-1 | P1 | Ready cards come back merged or Blocked with reasons | 3 Ready cards on a throwaway board end in Done or Blocked; no worker or stray worktree left |
| US-2 | P1 | `drover stop` or a crash, then `drover run`, continues | Stop mid-build, run again: same branch and PR, no duplicate PR |
| US-3 | P2 | Different skills, models, verify commands, parallelism per repo | Two repos' build prompts name each repo's skills |
| US-4 | P2 | With hc, attach to a worker, steer it, detach | Attach to the builder mid-run, send one instruction, detach; card finishes |
| US-5 | P2 | A finding "fixed" without a code change never merges | Resolve R1's thread without fixing; next review marks R1 `not fixed` and bounces |
| US-6 | P2 | Commits and PR titles in the repo's own style | Conventional repo gets `feat(scope): ...`; plain repo gets plain subjects |
| US-7 | P3 | Switch a role to another model by config | `roles.review.model: "sonnet"` runs review on Sonnet |
| US-8 | P3 | QA screenshots inline in the PR, stored where the owner chose | Default mode: QA comment shows images, PR diff has none; `pr` mode: images under the configured path in the diff |
| US-9 | P3 | See cost, time, bounces and outcome per card | A card bounced once shows in FactoryBench as one attempt with lanes build, qa, review, build, qa, review and 1 bounce |

## 5. Non-functional requirements

Preserved from upstream:

| ID | Must keep working | Source | Test |
|---|---|---|---|
| NFR-1 | Guard hooks block deleting outside the repo, pushing to protected branches, secret literals, worktrees outside the allowed path | `hooks/guard-*.py` | `test-guard-hooks.sh` |
| NFR-2 | No command deletes a dirty, locked, detached or unregistered worktree, or a branch with unmerged commits | `run.md` stale-worktree scan, `hooks/cleanup-wt.py` | `test-cleanup-wt.sh`, `test_worktree_paths.py` |

New or changed:

- **NFR-3 Idempotence.** Re-running any drover command after a crash shall
  not create a second branch, PR, review report or board item for a card.
- **NFR-4 Observability.** Every dispatch, column move, gate exit and halt
  shall be one line in the run manifest (metrics.md FR-MET-1) with time,
  card, role and reason.
- **NFR-5 Portability.** macOS and Linux with bash 3.2+, Python 3.9+, `gh`,
  `jq`, `git`.
- **NFR-6 GitHub budget.** A round with no work shall cost at most 5 GitHub
  API calls. Stage 0 measures upstream's idle round and may adjust this.
- **NFR-7 Testability.** Every external command (`gh`, remote `git`, harness
  CLIs, `hc`, `herdr-app`) shall be replaceable by a fake on `PATH`.
- **NFR-8 Footprint.** v1 shall have fewer files and fewer lines than the
  fork point (356 regular files, 88,434 lines; `git ls-tree` shows 358
  entries, 2 of them symlinks).

## 6. Boundaries for agents working on drover

**Always**
- Run `bash tests/run-safety.sh` before every commit.
- Update the spec in the same change when behaviour changes.
- Change a marker's writer and parser in one change (FR-CNV-4).

**Ask first**
- Changing a gate exit code, a marker, a reason tag, or the config schema.
- Adding a dependency or a harness.
- Deleting a skill or script the audit did not mark remove.

**Never**
- Commit secrets or tokens.
- Merge, or call `gh pr merge`, outside `drover gate`.
- Remove or weaken a failing test to make a run pass.
- Delete user worktrees or branches (NFR-2).

## 7. Success criteria

- **SC-1** A throwaway board of 10 mixed cards drains with no human action
  except human-gated merges; every Done card's merge is on the base branch.
- **SC-2** No duplicate PRs and no lost cards across 5 forced stops and resumes.
- **SC-3** A finding resolved without a fix never reaches Done (US-5, 10/10).
- **SC-4** Onboarding a new project takes under 10 minutes and adds exactly
  one tracked file (`.drover/config.json`).
- **SC-5** Switching a role to another model takes one config line.
- **SC-6** With hc enabled, any live worker is one command away from
  `drover status` output.
- **SC-7** drover-written text contains no emoji (checked by test).
- **SC-8** Fewer files and lines than the fork point (NFR-8).

## 8. Assumptions

- The owner has `gh` access with project scopes and a harness plan that
  allows headless runs.
- Repos are on GitHub, squash merges are allowed, and branch protection is
  either satisfiable by drover's checks or handled as gate exit 3.
- One runner per board at a time is the normal case.
- hc and herdr-app are optional and may be absent on any machine.

## 9. Traceability

| PLAN stage | Spec |
|---|---|
| 0 baseline | all "Preserved" tables (record which tests pass) |
| 1 audit | extras.md, NFR-8 |
| 2 cut, 3 rename | all "Preserved" tables must still pass; FR-CFG-1 |
| 4 conventions | conventions.md FR-CNV, board.md FR-BRD-6 |
| 5 CLI + harness | config.md (incl. FR-HRN-4) |
| 0 baseline | also metrics.md FR-MET-1 |
| 6 runner | runner.md FR-RUN-2, 5, 7, 8, 13, 14, 15, 16, FR-WRK-1/2/6/8/10; board.md FR-BRD-3, 5, 7, 8; metrics.md FR-MET |
| 7 reviewer | roles.md FR-REV-1, 3, 6, 8-12, FR-BLD-7, FR-CTX; gate.md FR-MRG-4 |
| 8 evidence | conventions.md FR-EVD |
| 9 hc add-on | runner.md FR-WRK-3/4/5/7, board.md FR-STS-2 |
| 10 herdr-app | runner.md FR-WRK-9 |
| 11 skill improvements | extras.md FR-EXT-3 |
