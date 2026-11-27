# Runner and workers

Part of the [drover spec](README.md).

## Preserved from upstream

| ID | Must keep working | Source @ d0b7d5a | Test |
|---|---|---|---|
| FR-RUN-1 | `run` checks preconditions before any dispatch and changes nothing if one fails: config, board and columns, `gh` scopes, clean tree on base, usable `verify_commands`, no leftover workers, database reach | `references/run.md` "Preconditions" | `test-run-gates.sh`, `test_verification_readiness.py` |
| FR-RUN-3 | Dispatch order Review, then QA, then Build | `references/run.md` "Dispatch allocation" | `test_run_ports.py` (`test_preflight_verdicts`) |
| FR-RUN-4 | A card is claimed by setting the issue assignee before a worker starts; a failed claim skips the card | `scripts/super-board-run.sh` `try_claim_assignee` | `test-run-gates.sh` |
| FR-RUN-5 | Pre-flight before any build claim: one cheap agent per batch of up to 5 Ready cards; verdict proceed / hold (done, in progress, unclear, too big) / sequence; no verdict means not built | `references/run.md` "Builder pre-flight", `scripts/super-board-preflight.sh` | `test-preflight.sh`, `test_run_ports.py` |
| FR-RUN-6 | Two overlapping Ready cards without open PRs: lower number builds, the other is sequenced `blocked-by:` it | `references/run.md` "Builder pre-flight" | `test-preflight.sh` |
| FR-RUN-9 | GraphQL quota below 200 waits for reset | `scripts/super-board-gh-guard.sh` | `test_github_reads.py` |
| FR-RUN-10 | `stop` comments "stopped mid-flight" on in-flight issues and PRs, releases claims, stops workers, keeps branches, PRs and worktrees | `references/stop.md`, `scripts/super-board-stop.sh` | none (add in stage 0) |
| FR-RUN-11 | `run` after a stop continues existing branches and PRs | `references/run.md` builder step 2 | none (add in stage 0) |
| FR-RUN-12 | After `rebuild_cap` (default 2) bounces a card goes to Blocked `[blocked:rebuild-cap]` | `references/run.md` reviewer step 7 | none |

FR-RUN-5's batch before any claim existed only in upstream's `workflow` backend
(`workflows/super-board-wave.js:206-284`). Upstream's `claude-p` runner
had the builder run pre-flight itself, one card at a time, after the claim
(`references/run.md` "Builder pre-flight"). Stage 6 moved the batch into
the runner (`lib/drover-run.sh` `preflight_ok`), with the `workflow` backend
as the reference, and deleted that backend.
A Ready card with an open PR from its own `issue-<N>-` branch came back for a
rebuild (a QA or review bounce) and skips pre-flight: an agent once held such a
card as "in progress" against its own PR.

## New or changed

- **FR-RUN-2 (changed)** The system shall not run more than
  `roles.<role>.max_workers` workers per role (default 1). Upstream's
  `claude-p` runner allows exactly one per lane. Builders run in parallel
  only on cards the same pre-flight cleared together, so overlapping cards
  are already sequenced (FR-RUN-6); an overlap pre-flight missed surfaces
  at the gate as exit 5 and a rebase pass.
- **FR-RUN-7 (changed)** A fast model shall classify each Ready card before
  it is claimed: feature or bug when untyped, complexity (which picks the
  model tier), and whether it needs any of the risk labels `security`,
  `migration`, `payments`, `auth`. It shall add missing risk labels and say
  why in one comment; it shall never remove a label or add `qa`.
  (Upstream: type and complexity only, `workflow` backend only.)
- **FR-RUN-14 (new)** `/drover run` in a harness session shall start
  `drover run` in the background, print its PID and manifest path, and end
  the turn. Progress is read with `drover status`.
- **FR-RUN-15 (new)** When the classifier (FR-RUN-7) adds a risk label, it
  shall post one comment `[orchestrator] [classify] added label <label>`
  with `Did: classified as <type>, complexity <tier>` and `Why: <one line>`;
  no comment when it adds nothing.
- **FR-RUN-16 (new)** The run manifest gains these additive line types
  (FR-MET-2 lines unchanged): `preflight batch #a #b: proceed #a, hold #b
  (<reason>)`, `usage pause: <n>% >= <pct>%, no new workers`, `usage resume:
  <n>%`, `unblocked #<n>: blocker #<m> closed, back to <column>`, `approval
  resume #<n>: owner approved head <sha7>, back to Review`.
- **FR-RUN-8 (changed)** While the harness's usage probe reports plan usage at
  or above `usage_pause_pct` (default 95), the system shall start no new
  worker, let running workers finish, and resume when usage drops. If the
  harness has no usage probe, then it shall say so once per run and
  continue. (Upstream: Claude-only, `workflow` backend only.)

- **FR-RUN-13 (new)** A worker that exits without moving its card or
  posting its role's report shall count as a failed dispatch. If two
  dispatches in a row fail on the same card, then the system shall halt the
  run and print the last 20 lines of the latest worker log. (Upstream
  re-dispatches every tick until the runaway gate stops the run after 6.)
- **FR-RUN-17 (new)** At the end of every tick the runner shall write
  `.drover/runs/state.json` (temp file + rename, no GitHub call): `repo`,
  `pid`, `tick`, `updated`, `paused` (the usage reset time, or null) and
  one `cards` entry per in-flight worker whose card is still in its lane
  (`issue`, `lane`, `worker` handle, `since`, `phase`, `phases`). On exit it
  shall drop `pid`, set `"stopped": true` and keep the last cards.
  Tested by `test-run-state.sh`.
- **FR-RUN-18 (new)** `drover phase <name>` (name `^[a-z][a-z0-9-]{0,31}$`,
  anything else exits 64 and writes nothing) shall append `{"name", "at"}`
  to `.drover/runs/phases/<DROVER_ISSUE>.jsonl` in the main checkout and
  never fail the worker otherwise. `state.json` shall show the worker's
  entries since its `since` as `phases` and the last name as `phase`.
  Tested by `test-phase.sh`.

### Worker interface

- **FR-WRK-1** All worker control shall go through one interface:
  `start(role, card, prompt) -> handle`, `alive(handle)`, `stop(handle)`.
  No other code may start, signal or detect worker processes. A worker's
  lock shall only be removed when `alive(handle)` is false; a live
  worker's lock is never treated as stale.
- **FR-WRK-2** The default `process` backend shall run the harness's
  headless worker command and log output to one file per worker.
- **FR-WRK-6** The board column shall decide whether a worker finished its
  card; a worker's report or exit is a signal to check the board, not proof.
- **FR-WRK-8** `stop` shall only touch workers this runner started.
- **FR-WRK-10 (changed)** When a worker's card leaves the worker's lane
  while the worker still runs, the system shall let it run for one more
  tick before stopping it, so it can finish its handoff. (Upstream's
  zombie watchdog kills it at once: `scripts/super-board-run.sh:375-412`.)

### hc add-on

- **FR-WRK-3** Where `hc.enabled` is true or `--hc` is given, the system
  shall start each worker as an hc terminal named `drover-<role>-<issue>`,
  labelled `drover.issue` and `drover.role`.
- **FR-WRK-4** If hc is enabled but `hc` is missing or its server is down,
  then the system shall log one line and use the `process` backend.
- **FR-WRK-5** If an hc launch reports its first message as not sent, then
  the system shall read the terminal, fail that dispatch, and not resend.
- **FR-WRK-7** Where hc is enabled and the builder terminal for a bounced card
  is still live, the system shall send the findings to it instead of
  starting a new builder.

### herdr-app view

- **FR-WRK-9** Where `hc.view` is true, the system shall show live workers in
  one herdr-app space per repo. If herdr-app or its window is absent, then
  it shall skip this silently.

## Edge cases

- Card closed on GitHub while a worker runs: the result is discarded; the
  card stays as GitHub shows it.
- Two runners on one board: the assignee claim stops double work; the
  second runner's precondition check refuses while the first's workers live.
- Harness hits its usage limit mid-worker: the worker fails, the claim is
  released, the next round retries.
- hc server restarts mid-run: lost workers count as dead; the board decides
  what is redone.
