# drover run — full contract

**Scope:** the runner (`drover run`), the orchestrator that starts it, and the runner's own agents (pre-flight, classifier). The lane rules are in `rules/` (see "Lane lifecycles").

**Where the runner runs:** in the background. `/drover run` starts `drover run` as a background process, prints its PID and manifest path, and ends the turn; `drover run` in a shell is the same runner. The runner script (lib/drover-run.sh) is a pure shell while-loop that dispatches workers per lane with the command, model flag and permissions in `harnesses/<harness>.json`, and never holds session state. Each worker is its own short-lived headless context — it starts from `drover context <role> <issue>`, not this file. Progress: `drover status`.

## Orchestrator delegation contract (NON-NEGOTIABLE)

**Drover is an autonomous trader. The interactive Claude session that invokes `drover run` is an orchestrator, NOT a worker.** Its only jobs are:

1. Verify preconditions (see table below).
2. Start the runner in the background: `mkdir -p .drover/runs && nohup drover run >>.drover/runs/runner.log 2>&1 &`.
3. Report the runner PID and the manifest path (`.drover/runs/<date>-<repo>.md`) back to the user.
4. End the turn. `--headless` is accepted and ignored for one release (it is the only mode now); say so in one line.

The orchestrator MUST NOT:

- Build, test, review, or fix issues itself. All product work is delegated to the runner's headless workers via `dispatch_lane`.
- Patch the dispatcher script or skill files unless the user explicitly asked for that change. Drift fixes belong in a follow-up issue, not a side-edit during a run dispatch.
- Wait for workers to finish. Workers run in their own contexts and write evidence back to the issue + PR. The orchestrator's output to the user is the dispatch confirmation, not the run result.
- Hold context for multi-card progress. Workers own the state machine via the GitHub Project board + assignee mutex + inflight lock files.

If a problem surfaces during the run (bug in the dispatcher, missing skill, stuck card), the orchestrator should: (a) capture the symptom, (b) tell the user what they observed, (c) wait for explicit approval before touching anything. **Do not silently expand the task into "fix the dispatcher while you're at it" — that's the orchestrator becoming a worker.**

When the user asks for diagnostics or a fix, prefer a focused subagent over doing the work in the orchestrator session, so the orchestrator's context stays small and the work stays inspectable in its own log.

## Intro shown when run starts

```
drover run
─────────────────────────────────────────────────────────
Purpose: the autonomous loop. Drains your GitHub Project board.

Progress: [done] onboard  →  [done] lint  →  run (you are here)
─────────────────────────────────────────────────────────
```

## Preconditions (verified before any worker dispatches)

| Check | Action on fail |
|---|---|
| Usable verification commands | Run `drover verification-ready --config <config>`. Exit 9 halts setup before dispatch: configure the owning project's checks, then restart explicitly. |
| Active config exists | Halt: "Run `drover onboard` first." |
| Project + required columns exist | Halt: "Project / columns missing. Run `drover onboard` to repair." |
| `gh auth` valid with required scopes | Halt: "Re-auth: `gh auth refresh -s project,read:project,repo`." |
| `pre-flight.md` all items `[✓]` | Halt: "Pre-flight incomplete — fix these: [list]." |
| No issues missing ACs in active columns | Halt: "N issues need clarification. Run `/drover lint`." |
| Clean git working tree on base branch | Halt: "Working tree dirty. Stash or commit before running." |
| Legacy worktrees | If `.worktrees/issue-*` or a pre-rename `.claude/worktrees/issue-*` exists, halt before dispatch. Stop its worker, inspect/preserve edits, then `git worktree move` it to the matching `.drover/worktrees/` path. Never delete it as part of an upgrade. |
| Stale worktree scan | Scan only `.drover/worktrees/issue-<N>` and `issue-<N>-build/qa/review`. If its named branch is gone, use `git worktree remove` without force. Keep detached, dirty, locked, unregistered, or unrelated folders; log failures for inspection. A missing board label alone is not permission to delete work. |
| Production-merge guard | If `base_branch == "main"` AND `human_approves_merge == false` AND `merge_policy.default != "human"` AND `merge_policy.allow_auto_on_production != true` AND production-detection signals fire (see onboard → base branch), halt with: `Refusing to start: would auto-merge to production main. Set merge_policy.default: "human", switch base_branch to staging, or re-run drover onboard to opt in explicitly.` |
| Leftover-worker check (added 2026-05-22 after #381 worker storm) | No lock in `.claude/drover/inflight/` may name a live worker, and `.claude/drover/runner.lock` may not name a live runner (`drover worker alive`). Otherwise halt with: `${N} drover workers already running ... Stop them first: drover stop`. The dispatcher must never run while leftover workers exist — they will collide on assignee claims and produce duplicate PRs. |
| Database reach | Run every `migrations.checks[env]` for an env in `allowed_envs` (read-only, e.g. `supabase migration list --db-url …`). On failure halt with its error: "Migrate URL for <env> does not connect — fix it before a wave builds work the gate cannot land." |
| GraphQL rate-limit guard | Before each tick, `drover gh-guard check 200` (lib/drover-gh-guard.sh — it reads GraphQL's own `rateLimit`, because `gh api rate_limit` misreports the GraphQL bucket). If GraphQL remaining < 200, sleep until reset. Prevents the runner from dying mid-loop when the user has burned quota in another tool. |

## Lanes and label routing

One board shape (v3.0.0): Backlog · Ready · Building · QA · Review · Blocked · Done. A card's
label picks its path — three labels, no per-board variant:

| Label | Path | Why |
|---|---|---|
| `feature` | Ready → Building → QA → Review → Done | new behaviour: build, test, review |
| `bug` | Ready → Building → QA → Review → Done | a fix: same lanes |
| `qa` | Ready → QA → Review → Done | test what already exists; skips Building |
| none | as `feature` | the classifier adds `feature` or `bug`; it never adds `qa` |

The runner reads the label of the top Ready card (`card_lane`): `qa` goes straight to the
Tester, anything else to the Builder.

```
Builder:   Ready → Building → QA
           worker   = drover-build skill
           worktree = .drover/worktrees/issue-<N>-build/
           branch   = issue-<N>-<slug>  (created here, persists across lanes)

Tester:    QA → Review            (a `qa` card: Ready → QA → Review)
           worker   = drover-qa skill (issue-scoped mode)
           worktree = .drover/worktrees/issue-<N>-qa/
           branch   = same issue-<N>-<slug>  (a `qa` card: the Tester creates it)

Reviewer:  Review → Done
           worker   = drover-review skill
           worktree = .drover/worktrees/issue-<N>-review/
           branch   = same issue-<N>-<slug>  (squash-merged on approval)
```

**`qa` cards.** The Tester moves the card Ready → QA itself, creates `issue-<N>-<slug>` from the
base branch, and tests what is already there against the ACs. Tests it writes are committed to
that branch; a PR opens only when there is something to merge (tests, evidence). A failure it
cannot fix with tests alone is filed as a `bug` card (`drover-qa-file-bug.sh`) and named in the
handoff — the `qa` card still moves on to Review with the findings. The Reviewer reviews the QA
report and any test diff.

A live site with no repo is not a board. `/drover-qa <url>` tests it on its own.

## Dispatch allocation model — one worker per lane, not per backlog

`drover run` allocates headless Claude capacity by **lane**, not by how many cards are stacked in one column.

- **Max concurrency:** 3 workers total — at most one Builder, one Tester, and one Reviewer at the same time.
- **Never dispatch multiple workers from the same column just because that column has a backlog.** If all cards are in `Ready`, the runner dispatches exactly one worker for the top card — a Builder, or a Tester when the card is labelled `qa` — until it exits.
- **Mixed-column example:** if `Ready`, `QA`, and `Review` each contain cards and all lanes are idle, the runner dispatches three workers: one Builder from `Ready`, one Tester from `QA`, and one Reviewer from `Review`.
- **Downstream-first priority:** when multiple lanes are available, dispatch Review before QA before Ready/Build, so finished work gets closed before new build work starts.
- **Lane idle gate:** a lane is eligible only when its prior worker process has exited. The runner must track lane PIDs or an equivalent lane lease; GitHub issue assignees are per-card mutexes, not lane-capacity controls.

This means a board with 30 cards in `Ready` and zero elsewhere does **not** start 3 Builder sessions. It starts one Builder. As that Builder moves its card to `QA` and exits, the next tick can start one new Builder for the next `Ready` card and one Tester for the first `QA` card, because those are different lanes.

## Lane lifecycles (per card)

Each lane's rules live in `rules/<role>.md` (`build`, `qa`, `review`), the rules every lane shares in `rules/shared.md`. A worker gets them, with its card's context, from `drover context <role> <issue>` (specs/roles.md FR-CTX). The two runner agents below stay here.

### Builder pre-flight (before ANY card goes Ready → Building)

Two agents must never build the same thing, and nothing already on the base branch gets built
twice. So before a Builder starts, a **fresh, cheap sub-agent** (no code, no worktree) checks the
card. The runner does it before any build claim: one short agent (role `preflight`, model
`sonnet`, `haiku` on a `low` tier) per batch of up to 5 Ready build cards, which returns one
JSON verdict per card. The agent runs with the harness's read-only `agent` profile (read tools,
the pre-flight script, GitHub reads through drover github-read, gh issue and pr view or list,
git log, show and diff, each also behind a `permissions.allow_commands` wrapper word;
no edits, no GitHub writes) and JSON output, and is stopped after 120 s. The runner posts the Block
template, then moves held and sequenced cards to Blocked, and logs one "preflight batch"
manifest line per batch. Builders the same batch cleared may run side by side
(`roles.build.max_workers`); a clearance lapses when the card leaves Ready or after 3 ticks,
and a later build is checked again.

```
[ ] drover gh-guard check 200
[ ] build the in-flight list: drover card --config <cfg> items (one call) → [{number,title,status}] for
    Building / QA / Review / Ready cards; save it to a temp file
[ ] drover preflight --repo <owner/name> --issues <N[,M…]> \
         --inflight <file> [--files <paths you expect to touch>]
[ ] judge each card: the script's verdict, then its `candidates` (near-misses) semantically
[ ] act on the outcome below, then return one verdict per card
```

The script asks four questions and the agent adds a fifth; the agent owns the judgement:

| # | Question | Mechanical signal (script) | Agent adds |
|---|---|---|---|
| 1 | Already done? | merged PR closes `#N`; merged PR / closed issue with the same `fingerprint:` or title | a merged PR that delivers the same behaviour under other words |
| 2 | Already in progress? | open PR on another branch closes `#N` or has the same title; a card in Building/QA/Review (or a lower-numbered Ready peer) with the same title | same feature, different words — not merely the same files |
| 3 | File overlap? | open PR touches files the card names; a lower-numbered card in the same `--issues` batch names the same files | expected files from reading the issue (`--files`); the same overlap with a Ready peer in another batch |
| 4 | Unclear? | no `## Acceptance Criteria` bullets | AC that contradicts the current code (cite `file:line`) |
| 5 | Too big? | — | likely over ~400 changed lines (`merge_policy.auto_max_lines`; lockfiles, generated, snapshots, migration SQL excluded) or spans many areas (UI + API + DB + jobs …) — lint.md criterion 15 |

**Two Ready cards that overlap each other, neither with an open PR, go one at a time.** The
lower-numbered card proceeds; only the other is sequenced, with `blocked-by: #<lower>`, and the
Blocked sweep frees it once the lower one closes. Sequencing each against the other leaves
both with nothing to wait on and neither built — the same deadlock comes back every round.

The agent may turn a `proceed` into a `hold` on evidence. It may turn a mechanical `hold` into
`proceed` only by naming why the match is a different feature. Its own branch's PR
(`issue-<N>-*`) is the card coming back, never its own duplicate.

**Outcomes — never drop a card silently:**

| Verdict | Action | Block template (`block-template.md`) |
|---|---|---|
| `proceed` | nothing; the Builder starts | — |
| `hold` — done | move card to Blocked | `[blocked:duplicate]` · names the merged PR / closed issue · Owner: owner (close as duplicate) · `blocked-by: -` |
| `hold` — in progress | move card to Blocked | `[blocked:duplicate]` · names the open PR / card · `blocked-by: <the issue that PR or card closes>` — the sweep returns it when that closes, and pre-flight then finds it done |
| `hold` — unclear | move card to Blocked | `[blocked:unclear]` · the missing AC or the contradiction with `file:line` · `blocked-by: -` |
| `hold` — too big | move card to Blocked | `[blocked:too-big]` · "too big — likely over <cap> changed lines / spans <areas>" · suggests splitting with `/to-tickets` into vertical slices, each under the cap · `blocked-by: -` |
| `sequence` — `blockedBy` non-empty | move card to Blocked | `[blocked:sequenced]` · the overlapping PR and files · `blocked-by: <blockedBy>` — the Blocked sweep frees it |
| `sequence` — `blockedBy` empty | proceed (card stays Ready, return column `Ready`), comment `expected conflict with PR #<P> on <files> — the merge gate will rebase` | — |
| `halted` | script exit 79: required evidence unavailable; leave the card untouched, stop this run, resume explicitly after recovery | — |

A card the pre-flight agent returns no verdict for (no answer, bad JSON, a timeout) is treated
as `skipped` (a verdict, not a column) and never built unchecked. It is asked again two ticks
later; after 3 such answers it goes to Blocked `[blocked:unclear]`. `qa` cards are not pre-flighted: nothing is built.

### Classifier (before any Ready claim)

A fast agent (role `classify`, model `haiku`, `sonnet` on a `high` tier, the same read-only
profile and 120 s limit) reads each Ready card once per run and returns `kind`, `complexity` and
`risk`. With no usable answer the card waits and is asked again on the next tick, at most twice;
then it is claimed unclassified. The runner then:

- adds `feature` or `bug` when the card has no type label (any routing label counts: `qa`, `bug`,
  `feature` and every key of `labels` in `lib/drover-routing.json` or the config, e.g. `ux`,
  `refactor`, `tech-debt`, `tests`, `docs`); it never overrides one, never adds `qa`;
- adds each missing risk label of `security`, `migration`, `payments`, `auth`;
- never removes a label;
- when it added a label, posts one comment `[orchestrator] [classify] added label <labels>` with
  `Did: classified as <type>, complexity <tier>` and `Why: <one line>`; no comment otherwise;
- picks the Builder's (or a `qa` card's Tester's) model from `model_tier` and the complexity:
  `low` haiku / sonnet / opus, `medium` (default) sonnet / opus / harness default, `high` opus.
  A role's `model` in the config wins. The runner records the model each build worker starts on
  (`.drover/runs/models/<issue>`). The Reviewer runs on `roles.review.model` (default `sonnet`), or
  on `roles.review.alt_model` (default `opus`) when that equals the card's builder model, so the
  reviewer of a PR never runs on the model that built it (roles.md FR-REV-13); `roles.review.harness`
  moves it to another model family. Its prompt carries the `Models:` line for the report.

## Per-tick logic (~30s cadence)

```
1. Refresh project items + column counts via gh api
2. Re-validate preconditions (auth, pre-flight, columns)
3. Downstream-first dispatch by lane capacity:
   ├─ Review has cards + Reviewer idle → dispatch top of Review
   ├─ QA has cards + Tester idle       → dispatch top of QA
   └─ Ready has cards → top card labelled `qa` + Tester idle → Tester;
                         otherwise Builder idle → Builder

   Allocation rule: at most ONE worker per lane at a time.
   Max concurrency = 3 total workers (Builder + Tester + Reviewer),
   but never 3 Builders from a Ready backlog. GitHub issue assignees prevent two workers claiming the same card;
   the runner still must track lane idleness separately to prevent multiple
   same-lane workers.
4. Wait for any lane to finish OR 30s timeout
5. Re-read project state (lane may have moved a card)
6. Loop until done conditions met
```

## Anti-zombie addendum (added 2026-05-22 after #381 worker storm)

Worker-side assignee claim alone is **not sufficient** — `claude -p` cold-start takes 10–30s, during which the dispatcher's next tick can fire another worker for the same card. The 2026-05-21 first-run produced **7 racing workers** (3 on #381, 4 on #382) before the dispatcher died from rate limit.

The dispatcher MUST also:

1. **Claim BEFORE spawning the worker** — `try_claim_assignee` runs in the dispatcher and only proceeds to start the worker (`drover worker start`) if it wins the assignee write. Closes the cold-start race.
2. **Write a local in-flight lock** — `.claude/drover/inflight/<issue-N>` holds `HANDLE=process:<pid>:<start>` (`<start>` from `ps -o lstart=`, so a recycled pid reads as dead and is never signalled), `LANE=`, `STARTED=` and `COLUMN=` (the card's column at dispatch), written atomically. Older `PID=<pid>` locks still read: they block a run start but are never signalled. `top_card_in_column` skips any issue with a lock even if the assignee write hasn't propagated yet.
3. **Cap workers per lane** — at most `roles.<role>.max_workers` (default 1) live locks per lane; downstream first (Review, QA, then Ready).
4. **Reap stale locks each tick** — `reap_finished_locks` removes a lock only when its worker is no longer alive. A worker that exited with its card still in its dispatch column (or in Building, where no lane looks; the card then goes back to Ready) and with no `[<role>] [report]` or `[<role>] [blocker]` comment on the issue since it started is a failed dispatch; two in a row on one card halt the run with exit 76 and print the last 20 lines of its worker log. Other lanes' workers keep running with their locks.
5. **Leftover-worker check on startup** — refuse to start while any lock names a live worker.
6. **One board read per tick** — `drover card items` once per tick, not per column lookup (~1 GraphQL point per 100 cards).

The locks (assignee, in-flight file) are defense in depth: either alone has a race window; together they make a duplicate dispatch effectively impossible.

## Usage pause

Before each dispatch the runner runs the harness's `usage_probe` (`harnesses/<harness>.json`;
Claude: `drover usage check --threshold <usage_pause_pct>`). At or above `usage_pause_pct`
(default 95) it starts no new worker, lets running workers finish, and logs
`usage pause: <n>% >= <pct>%, no new workers`; when usage drops it logs `usage resume: <n>%` and
dispatches again. Paused rounds do not count toward the runaway halt. Every change between
paused, running and no reading is logged.

**When the reading goes blind.** A pause holds while the probe has no reading (snapshot deleted,
unreadable) until the reset time of the window that paused the run, or until a fresh reading
below the threshold arrives: usage cannot fall inside a window. A run that was not paused and
gets no reading keeps dispatching: the guard is blind, and Claude Code's own limit at 100% is the
backstop. A harness with no probe is said once per run and never pauses.

Claude's reading is the `rate_limits` block Claude Code pipes to the status line. It reaches
the probe only if the owner's status-line command records it; add one line after it reads stdin:
`printf '%s' "$input" | drover usage record`. Pro/Max only (API-key sessions have no
`rate_limits`); it refreshes on the owner's interactive turns, not on headless workers.

## Halt gates

| Gate | Action |
|---|---|
| `config.rebuild_cap` reached on same card + same root-cause hash | Move card to Blocked with full §4 template (`[blocked:rebuild-cap]`), continue run |
| No card progresses for 3 ticks AND no lane is idle | Halt, dump state |
| Auth expires mid-run | Halt, ping user with refresh instruction |
| Pre-flight check fails on re-validation | Halt with the specific missing item |
| Merge conflict, or a stale branch the merge gate rejects | Move card to **`Ready`** with the `loop:rebase` label — NOT Blocked. See "The rebase pass" below |
| User-defined time/budget window reached | Graceful halt: finish in-flight workers, no new dispatches |
| Destructive action would be required (prod deploy, db drop, secret rotation) | Halt, never proceed; move card to Blocked with `[blocked:unsafe]` |
| Block-rate alert: Blocked count > `config.block_rate_alert_pct` of initial Ready | Post the breakdown in the run report, continue run |

### The Blocked sweep and approval resume

Every fresh board read with a card in `Ready` or `Blocked` also reads the dependency graph
(`drover-deps.sh`: the last `blocked-by:` line of a Block comment, else the issue body's
`## Blocked by` section, plus the approval check of `drover-approval.py`). The runner then,
before it dispatches anything that tick:

- **unblocks** a `Blocked` card whose `blocked-by:` issues have all closed: moves it to `Ready`,
  comments `[orchestrator] [report] moved back to Ready · blocker closed`, and logs
  `unblocked #<n>: blocker #<m> closed, back to Ready`. It is claimed on the next tick.
  A card with `blocked-by: -` waits for a person and is never swept.
- **resumes** a `[needs-you]` `Blocked` card whose newest pinned request has a later `done`
  from a human with verified write, maintain or admin permission, for the current
  PR head: moves it to **Review** and logs `approval resume #<n>: owner approved head <sha7>,
  back to Review`. `needs-you:done` is a display label only. The Reviewer re-runs the gate,
  which independently checks the request, current policy/steps and head, verifies
  the build, and runs allowed migrations. New code or a newer request needs fresh
  human approval; a label or old bare comment cannot bypass that hold.
- **refreshes** a card whose PR head changed while it was `Blocked`: moves it to **Review** with a
  comment that the old approval does not cover the new head, and removes a stale
  `needs-you:done` display label. The Reviewer reviews the new code
  and lets the gate produce a new pinned request, then returns it to **Blocked** for a fresh
  human reply. This is not an approved resume.
- **holds** a `Ready` card whose blockers are still open, whose `## Blocked by` line cannot be
  parsed, or which the graph does not know: it is not claimed (and costs no per-card GitHub read
  when the graph lists it). An unreadable line, on a Ready or a Blocked card, gets one comment
  per run asking for `- #N` bullets or `- None.`. Never guess: a card treated as free on an
  unreadable line gets built against a base that does not have what it needs.

`Building` cards with no claim and no live worker go back to `Ready` too
(`reclaim_stranded_building`, at start and after every fresh board read): nothing selects from
Building. A leftover build worktree is removed only when it is clean; the branch is kept.

**Why the sweep exists.** `Blocked` used to be terminal. A card parked naming the issue it waited
for, that issue later merged, and nothing ever read the note back — the run's own done-condition
("only Blocked/Done cards remain") meant a closing blocker could not wake anything. Five
cards sat that way on a real board until a human opened it and noticed.

## Done conditions

The loop exits cleanly when:
- All active-pipeline columns are empty; OR
- Only Blocked/Done cards remain (Backlog is not part of a run); OR
- A halt gate fires.

## Run manifest

```
.drover/runs/<YYYY-MM-DD>-<slug>.md
```

Worker logs: `.drover/runs/<YYYY-MM-DD>-<slug>-workers/worker-<role>-<N>.log`. `.drover/runs/` is git-excluded.

Records: config used, columns, target, per-card history (claim → completion → next column with evidence links), halt gates, final counts, per-lane wall-clock, resume command.

## Notification cadence

- **Start** — project, initial column counts, link to live status: `drover status`.
- **Per card completion** — `Tester → #41 passed all checks → Review`.
- **Per Block** — short headline + reason tag (`#19 blocked [blocked:creds] missing OPENAI_API_KEY`) + link to the full §4 template comment on the issue.
- **Every 10 dispatches** — brief column-count snapshot.
- **Block-rate alert** — when Blocked count exceeds `config.block_rate_alert_pct` of initial Ready: ping with breakdown ("Blocked 11 of 30 Ready · creds 6 · quota 3 · unclear 2 — see project board"). One-shot per run (does not re-fire).
- **Final** — end column counts, total moved to Done, blockers (with reason-tag breakdown), total wall-clock.

## Known issue — lane-zombie workers (added 2026-05-24, auto-remediated)

**Symptom seen on a live board's first run:** a Tester worker successfully moved #81 QA → Review and the Reviewer subsequently merged it to Done — but the original Tester's `claude -p` process kept running for 30+ minutes afterward. The dispatcher saw the live lane PID as "still busy," so the QA lane never re-dispatched. By the time it was noticed, the QA column had grown 1 → 2 → 3 cards with no Tester picking them up.

Different from the multi-attempt-move issue above: there the worker exited without moving the card; here the worker moved the card but didn't exit.

**Auto-remediation (now implemented in `drover-run.sh` as anti-zombie control #7):**

On every tick that reads the board fresh, `sweep_lane_zombies` checks every live worker lock (a pending grace tick forces that read even when every lane is at its cap). A worker whose card's current column is NOT in its lane's expected source set (nor the column it was dispatched from) gets one more tick to finish its handoff; if the card is still out of the lane on the next tick, the worker is stopped:

| Lane    | Expected source columns | Anything else means → |
|---------|-------------------------|-----------------------|
| build   | Ready, Building         | zombie                |
| qa      | QA                      | zombie                |
| review  | Review                  | zombie                |

On first sight: log "<lane> worker on #<N> (pid=<P>) left its lane (now '<col>'); one more tick to finish its handoff" (not "card moved to": FR-MET-1 readers take that as the outcome line). A card back in its lane drops the mark. A tick later: `SIGTERM` + 1s + `SIGKILL` through `drover worker stop`, remove the inflight lock once the worker is dead, idempotently sweep the assignee, log `zombie <lane> worker on #<N> (pid=<P>) — card moved to '<col>'; killing`. Uses the cached project items only — zero extra API calls per tick.

Trade-off: if someone (human or external tool) manually moves a card out of its source column while a worker is legitimately mid-work, the watchdog will kill that worker. Acceptable — manual board edits during an active run are an anti-pattern, and the worker would fail on its own column-move anyway.
