# Run metrics

Part of the [drover spec](README.md).

drover does not store metrics itself. It reports to FactoryBench
(`belastrittmatter/factory-bench`), which records cost, tokens, time, tool
use, human effort and PR outcomes, and compares configs. Two inputs:

- **Events** (`factorybench emit`, contract in FactoryBench's
  `spec/events.schema.json`): one **attempt** per try at a card, covering
  every build, QA and review dispatch and every bounce in between.
- **Run manifest and worker logs** (local files, read by
  `factorybench dashboard --runs .drover/runs`): the lanes, dispatches,
  outcomes and column moves inside each attempt. FactoryBench assigns each
  dispatch and its model calls to the attempt by time and counts bounces
  and re-dispatches from the lane order.

## Preserved from upstream

| ID | Must keep working | Source @ d0b7d5a | Test |
|---|---|---|---|
| FR-MET-1 | Run manifest `<date>-<slug>.md` with lines `[hh:mm:ss] dispatch lane=<role> issue=#<n> pid=<p> ...`, a later line naming the same `pid=` with the outcome, and `card moved to '<column>'`; one log per worker under `<date>-<slug>-workers/worker-<role>-<n>.log` starting `=== dispatch lane=<role> issue=#<n> at <iso time> ===` | `scripts/super-board-run.sh` (`RUN_MANIFEST`, `WORKER_LOG_DIR`, `log "dispatch ..."`) | `test_status_parse.py` (dispatch line) |

## Contracts

- **FR-MET-2** The manifest and worker-log formats in FR-MET-1 are parsed by
  `drover status` and by FactoryBench. Changing them is an "ask first"
  change. `pid=` shall always be a number unique within the run (the
  process id for the `process` backend, a run counter for hc).
  (Approved 2026-11-17: the zombie, clean-exit and block-rate lines lost
  their leading emoji in stage 4; both parsers match the words after it.
  Issue #22: a worker that handed off logs `worker done #<n> lane=<role>
  (pid=<p>)`; `reaped stale lock` is kept for one that died without a handoff.)

## New or changed

- **FR-MET-3 (changed)** The manifest and worker logs shall be written to
  `.drover/runs/`, which is git-ignored. They are also drover's log
  (NFR-4). (Upstream writes them into the repo under
  `docs/super-board/runs/`.)
- **FR-MET-4** Where `factorybench` is on `PATH` and `FACTORYBENCH_URL` is
  set, the system shall send attempt events. Otherwise it shall send
  nothing and log nothing about it. Reporting shall never block, slow or
  fail a run (`factorybench emit` always exits 0 and spools).
- **FR-MET-5** Attempt lifecycle per card:

  | When | Event |
  |---|---|
  | First claim of a card with no open attempt | `attempt.started`: `work_item`, labels, base commit, `factory_config_version`; no `role` |
  | Builder opens the PR | `pr.linked` |
  | QA result, review result, each gate exit | `gate` named `qa`, `review`, `merge`, `passed`, one-line `reason`; none when review hands the card to Blocked (the `human.waiting` below says why) |
| QA or review sends the card back to an earlier lane (changed) | `send_back`: `from` the lane, `to` `build` or `qa`, `kind` `fix-attempt`, `reason` `bounced to <column>` |
  | Card moves to Blocked | `human.waiting`, `reason` = the reason tag |
  | Card leaves Blocked | `human.resumed` |
  | `drover stop` with the card in flight | `human.waiting`, `reason` = `stopped`; the next claim sends `human.resumed` on the same attempt |
  | Card reaches Done (FR-BRD-3) | `attempt.finished` `completed` |
  | Work thrown away: PR closed unmerged, card closed not planned, or moved back to Backlog | `attempt.finished` `failed` with reason; the next claim starts a new attempt |

- **FR-MET-6** The open attempt id per card shall be kept in
  `.drover/runs/attempts.json`. If the file is lost, the next claim starts a
  new attempt.
- **FR-MET-7** Each worker process shall run with Claude Code's OTLP
  variables and `factorybench.attempt=<attempt id>,factorybench.phase=<phase>,factorybench.step=<lane>`
  (build `implement`, qa `verify`, review `review`; step is the lane name, so
  FactoryBench draws one row per lane with its time and cost) (changed).
  drover shall send no `usage` events for Claude.
- **FR-MET-8** `factory_config_version` shall be
  `drover@<drover commit>+claude-code@<version>+cfg:<first 8 hex of sha256 of .drover/config.json>`.
- **FR-MET-9** Event keys shall be fixed per call site (`started`, `pr`,
  `gate:<name>:<round>`, `send_back:<k>`, `waiting:<k>`, `resumed:<k>`, `finished`) so
  retries are duplicates, not new events.
- **FR-MET-10** Events shall contain no prompts, code, diffs or secrets, and
  drover shall not set `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_TOOL_DETAILS` or
  `OTEL_LOG_RAW_API_BODIES`.
- **FR-MET-11** Where `metrics.commit_email` is set, workers shall commit
  with that author and committer email.
- **FR-MET-12** `drover run` shall call `factorybench emit --flush` once at
  start.

## Known issues (not fixed in v1)

- drover acts under the owner's GitHub login, so FactoryBench cannot tell
  the owner's reviews and comments on agent PRs from drover's. Human-effort
  metrics based on the login are unreliable until drover runs under a
  separate bot account (decisions table: deferred).
- Pre-flight and classifier calls span several cards, so they belong to no
  attempt and their tokens are not counted.
- GitHub API calls per round (NFR-6) are not a FactoryBench metric; drover
  counts them in the manifest.
