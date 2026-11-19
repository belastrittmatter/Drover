# drover: how it works

> **What it does not do:** drover installs skills, scripts and guard hooks,
> and onboard sets up your GitHub Project. It does not grant `gh` access on its own, add CI, or approve merges you
> have not allowed. It is not a CI replacement: workers push branches and your CI still runs.
> It is not a free pass on review: set `human_approves_merge: true` to keep a person on every
> merge. It is not for cards without acceptance criteria: QA grades against them.

drover watches a GitHub Project and runs Build → QA → Review for each card, merging when
the evidence holds. The board is the only state: every agent re-reads it, so a run survives
Ctrl-C, restarts and rate-limit pauses, and `/drover run` resumes from whatever
column each card is in.

| | |
|---|---|
| **What you get** | Merged PRs with evidence: screenshots, test reruns, review findings on the PR |
| **How you start** | `/drover run`, then drag cards into `Ready` |
| **What holds state** | The board. Cards pick up from their column after any stop |
| **Backend** | One runner in the background; headless workers per lane (`harnesses/<harness>.json`) |

## Workflow at a glance

```mermaid
flowchart LR
  collect["/drover-collect<br/>sentry · posthog · issues · PRs · architecture"] --> findings["findings doc"]
  findings --> spec["/to-spec → /to-tickets"]
  spec -->|"1–5 tickets"| impl["/implement-spec"]
  spec -->|"6+ tickets"| backlog["Backlog"]
  backlog -->|"/drover lint, then you"| ready["Ready"]
  ready --> build["Building<br/>drover-build: worktree + draft PR"]
  build --> qa["QA<br/>drover-qa: tests, evidence,<br/>pr-security-review"]
  ready -->|"label qa"| qa
  qa --> review["Review<br/>drover-review: own hypotheses,<br/>prior findings"]
  review --> gate{"merge gate<br/>base + verify_commands"}
  gate -->|green, head unchanged| done["Done (squash-merged)"]
  qa -->|fail| ready
  review -->|bounce| ready
  gate -->|stale or conflicts| ready
  gate -->|refused by GitHub| blocked["Blocked<br/>human"]
  blocked -->|"blockers closed (sweep)"| ready
```

The diagram shows the routes a card can take, not guarantees. A lane that cannot finish writes
the Block template and parks the card for a person; `human_approves_merge: true` replaces the
merge with a hand-off; a card labelled `qa` skips Building.

## Onboarding

`/drover onboard` is a 7-step wizard. Each step opens with a numbered agenda strip and
`N of 7 · <emoji> <Name> — <why>`, then one question with the recommended answer first.

| # | Step | What happens |
|---|---|---|
| 1 | 🔍 Checks | Fixes every must-have with no question — skills, scripts, workflows, guard hooks, settings entries, old folders (super-refine, cleanup-wt, arch-loop), config keys, labels, columns — and lists them under "Fixed for you". Asks only to install a system tool (the exact brew / apt / winget command) or sign in, then re-checks until green. An older drover is upgraded here ("Upgraded for you"). |
| 2 | 🔑 GitHub | Signs in and adds board access (`gh auth refresh -s project,read:project,repo`) only when missing; offers to create the repo. |
| 3 | 🗂️ Board | Ranks your GitHub Projects by matching columns and recommends reusing the best (adds what is missing, keeps cards), or creates one with a name taken from package.json / README. |
| 4 | 🌿 Branch | Finds branches and the deploy source; no staging → "Create staging from main" (Recommended). |
| 5 | 🛡️ Policies | Two questions: safe defaults (merge rule, push guard, migrations), and the permission lines. |
| 6 | 📥 Bug sources | Sentry, PostHog, GitHub issues, past PRs, architecture, plus "➕ Add another source": a link, app, API URL, MCP server or command, pinged read-only before it is saved. |
| 7 | ✅ Review | One compact table, then "Write everything?" — files change once, here. |

Stop at any step: the answers are saved, and the next run shows them and continues. Testing a
live site with no repo is not a board: `/drover-qa <url>`. The full screen-by-screen contract is
`skills/drover/references/onboard.md`; the deterministic half is
`lib/drover-setup.py`.

**Labels.** Every board has the same seven columns. Three labels route a card: `qa` goes Ready →
QA → Review → Done (test what exists), `bug` and `feature` go through Building first, and a card
with no label is built (the classifier adds `feature` or `bug`). There is no Skipped column: a
card dropped on purpose is closed as not planned and moved to Done with a 🤷 comment.

## Setup notes

- Needs Claude Code, `gh` (authenticated for the board's owner), `jq`, bash 3.2+ and Python 3.
- Install from a checkout: `./install.sh` links `drover` into `~/.local/bin`; `drover skills install` links the skills into `~/.claude/skills`. Nothing is copied into a project: `drover onboard` writes `.drover/config.json`, the git excludes and the hooks in the untracked `.claude/settings.local.json`.
- The board is a GitHub Project (v2) with a `Status` field: `Backlog, Ready, Building, QA, Review, Blocked, Done`. `onboard` reuses your best-matching board (adding what is missing, keeping every card) or creates one.
- Three labels route cards: `qa` skips Building (test what exists), `bug` and `feature` are built first; no label is built too.
- `/drover run` starts the runner (`drover run`) in the background and ends the turn; `drover status` shows progress.
- Set `verify_commands` in the config. Without them the merge gate cannot prove the result builds, and says so.
- `onboard` fixes what the board needs without asking (skill links, guard hooks, old in-project copies, config keys) and asks only to install a system tool or sign in. An older drover is upgraded in place.
- `onboard` asks once what the robot may do: auto-merge normal changes up to 400 changed lines (money, auth, destructive schema like DROP/TRUNCATE/RENAME, and bigger PRs wait for you; additive migrations run only on the databases you allow, never live), protect main, and which databases it may migrate (default test + staging). Anything only you may run lands in Blocked tagged 🙋 with the exact command; comment `done` and the next round merges.
- The session that starts runs needs `Bash(drover:*)` in your allowlist; `onboard` offers it as a diff. Headless workers get their permissions from `harnesses/claude.json`.
- Bug sources take any extra source in onboard — a link, app name, API URL, MCP server or command — proven readable (read-only) before it is saved; `/drover-collect` runs it like the built-ins.
- Testing a live site with no repo is not a board: `/drover-qa <url>` runs on its own.
- To enable the usage check, add `printf '%s' "$input" | drover usage record` to your status-line script.
- Cards need acceptance criteria: QA grades against them, and `lint` tells you which are missing.

## Skills

| Skill | Use it for |
| --- | --- |
| `/drover` | Orchestrator. Validates preconditions and starts the runner. Holds no product context, writes no code. |
| `/drover-build` | `Ready` → `QA`. Worktree, smallest safe change, draft PR. |
| `/drover-qa` | `QA` → `Review`. Tests per AC, test-gap check, screenshots/logs/HARs on the PR, or a bounce with a rebuild label. Off-ticket findings file as `source:qa` cards. |
| `/drover-review` | `Review` → `Done`. Re-runs the Tester's tests, adversarial truth-check, merges through the gate or hands to a human. On a re-review it first checks every finding from its last report. Shape problems are filed to `Backlog`, never blocked on. |
| `/drover-collect` | Finding problems in Sentry, PostHog, unboarded issues, recurring PR problems and architecture, each checked by one verifier, then one findings doc for `/to-spec` and `/to-tickets` (1–5 tickets → `/implement-spec`, 6+ → `Backlog`). |
| `/ui-refine-loop` | Polishing one page or component, standalone: Impeccable check → fix rounds ending in a draft PR. The board never runs it. |
| `/git-sync` | Secondary helper: commit per logical change, merge-pull, push without force. |
| `/visual` | Secondary helper: a visual recap or plan. Merged-worktree cleanup is a hook (`hooks/cleanup-wt.py`): the merge gate runs it after each merge and SessionStart runs it when a base moved. |

## The runner

`/drover run` starts `drover run` (`lib/drover-run.sh`) in the background, prints its PID and
manifest path, and ends the turn; `drover run` in a shell is the same runner. It is a shell loop
that re-reads the board every tick and starts headless workers with the command, model flag and
permissions in `harnesses/<harness>.json`. Progress: `drover status`; the log is the manifest in
`.drover/runs/`.

**Stop and resume.** `/drover stop` posts a "stopped mid-flight" comment on every in-flight
issue and PR (lane, last commit, resume hint), releases the assignee mutex, and kills workers and
the runner. Resume with `/drover run`. A card stranded in `Building` by a stopped
run goes back to `Ready`; its branch is kept and the next Builder continues on it.

## How a round runs

**The dependency graph decides what may start.** Every board read with a `Ready` or `Blocked`
card also reads each open issue's `blocked-by:` line or `## Blocked by` section. A Ready card is
claimed only when its blockers have all closed. A `Blocked` card parked on issues that have since
closed comes back to `Ready` on its own, and an owner's `done` on a `[needs-you]` merge request
for the current PR head sends the card back to Review. A dependency line it cannot read
confidently is never treated as free; it is flagged for a human.

**Pre-flight and classifier.** Before a Builder claims a card, one cheap agent checks up to 5
Ready cards at once: already merged, already in progress, overlapping an open PR, unclear or too
big go to Blocked with the reason. A fast classifier adds a missing `feature`/`bug` label and the
risk labels `security`, `migration`, `payments`, `auth`, with one comment saying why, and its
complexity grade picks the Builder's model from `model_tier`.

**Usage pause.** Before each dispatch the runner asks the harness's usage probe. At or over
`usage_pause_pct` (default 95) of the 5-hour or weekly window it starts no new worker, lets the
running ones finish, and resumes when usage drops. It needs the status line to record usage
(Setup notes), works on Pro/Max only, and an unknown reading never halts a run.

**Nothing merges on GitHub's word.** `mergeable: CLEAN` only means the text does not conflict. The
merge gate takes a lock, checks the PR head is still the commit the Reviewer reviewed, merges the
current base into a scratch worktree, runs your `verify_commands`, and only then squash-merges with
`--match-head-commit`. A branch that stopped compiling while it waited goes back to Build for a
rebase pass; a push after review voids the evidence and the card gets a fresh review.

### Which skills each lane loads

Every lane skill is scoped to the diff or the ticket in front of it. Nothing in a lane may ask the
user a question: the run is unattended, and a skill that waits is a skill that hangs.

| Lane | Loads |
|---|---|
| **drover-build** | Routed by the ticket's type label: `bug` → `diagnosing-bugs`+`tdd`, `feature` → `implement`+`tdd`+`codebase-design`, `refactor`/`tech-debt` → `codebase-design`+`tdd`, `docs` → skip `tdd`. Always `ponytail:ponytail` first (inline ladder if absent), `verification-before-completion`, and `code-review` on its own diff. Official docs first for third-party APIs, upgrades, auth/billing. Mechanics (`vitest` / `playwright-best-practices` / `testing-strategy`) by the localisation ladder, never by label. |
| **drover-qa** | `ask-matt` · `tdd` · `diagnosing-bugs` · the same testing skills |
| **drover-review** | `code-review` (Standards + Spec, merge-base as fixed point) · `codebase-design` · `ponytail:ponytail-review` |

Assignment happens when the ticket is written, not at runtime: drover-build routes on the issue's
**type label**, the same `bug` / `feature` / `ux` / `tests` / `docs` / `tech-debt` labels drover-qa
files, plus `refactor` from drover-review. A `Skills:` line in a ticket body replaces its label's row.

**Interactive-only, never in a lane:** `grilling`, `shape`, `clarify` and
`improve-codebase-architecture`. They prompt, wait or open a browser. They belong to
`drover lint` and to you at a keyboard. A worker that wants one is telling you the ticket
needed a human before the loop started.

## The six agentic patterns, mapped

```
  lib/drover-run.sh               the conductor: owns the patterns
  skills/drover-{build,qa,review}  the sheet music: one lane worker each
  the prompt it spawns            "Run drover-build on #N, follow run.md exactly"
```

| Pattern | When |
|---|---|
| **Routing** | `Ready` cards only: a cheap classify agent (haiku; sonnet on `model_tier: high`) picks the model tier per card |
| **Prompt chaining** | Every card: each lane starts from the column the previous lane moved it to |
| **Parallelization** | Always: card A can be in Review while card B builds |
| **Evaluator–optimizer** | QA/Review judge the Builder's work; a fail bounces the card and the next round rebuilds with the comments as context |
| **Orchestrator–workers** | Always: your session never codes; the runner's workers do all product work |
| **Autonomous loop** | The runner's tick loop repeats until the board is drained or a halt gate fires |

Three cards in flight:

```
 RUNNER (background)                    ← orchestrator–workers
   │ read board → pre-flight → classify → claim → start worker
   ▼
 #47: classify → build → qa → review    ┐
 #51:            qa → review            ├ parallelization (cards overlap)
 #52: classify → build ✗ (bounced)      ┘   chain stops; the board keeps the card
                           │
 merge gate: ──[lock]── one merge at a time
```

Merges are serialised by the merge gate's lock alone; reading diffs and re-running suites in
Review run in parallel.

## Safety controls

**Worker storms**, 30 `Ready` cards starting 30 Builders, were the failure that bit early users of
the headless backend. Six gates stand in the way before any spawn:

```
  1  orphan scan ........... workers alive from a crashed run?  → refuse to start
  2  in-flight lockfile .... .claude/drover/inflight/<N>   → skip (survives restart)
  3  assignee claim ........ atomic, BEFORE spawn               → skip (closes the cold-start race)
  4  worker cap ............ roles.<role>.max_workers (default 1) → wait
  5  GraphQL quota <200 .... sleep until reset                  → wait
  6  tick not elapsed? ..... tick_seconds, 120s floor           → wait
```

The 120-second tick holds ProjectsV2 query cost (~103 GraphQL points per tick) to about 3.1k/hr
against a 5k budget; raise `tick_seconds` if you have headroom.

Guard hooks, wired by `drover onboard` unless `--no-hooks`: worktrees only under
`.drover/worktrees/`, no reading of secret files, no API keys written into source, no deleting
outside the project, and merged-worktree cleanup at session start. Blocking direct and force pushes
to the base branch is on by default, and `guard-merge.py` keeps every merge inside the gate. See
`hooks/README.md`.

**Who merges, and which databases the robot touches** (v2.6.0, config `merge_policy` and
`migrations`, enforced inside the merge gate):

```
  PR is …                                      gate        card
  ─────────────────────────────────────────    ────────    ─────────────────────────────
  a normal change, verify green                merge (0)   Done
  money / auth / destructive schema (labels,   exit 7      Blocked 🙋 — you merge it, or
    path globs, added-line keywords), or                   comment `done` to approve
    over auto_max_lines (default 400 changed
    lines: "big PR — please review")
  additive migrations, target env allowed      migrate,    Done
                                               merge (0)
  migrations for live (not allowed), a failed  exit 8      Blocked 🙋 — exact commands;
    migrate command, a `needs-you:` step                   comment `done` → next round merges
```

In plain words: only **destructive** schema changes (DROP / TRUNCATE / RENAME, or a `schema`
label) need a human. Additive migrations (a new table, column or index) follow the database rule:
the robot runs them on the databases you allowed. A live database is always yours.

## Configuration

Minimal config at `.drover/config.json` (one per repo; schema: `skills/drover/references/config-schema.json`):

```json
{
  "project": { "owner": "your-gh-login-or-org", "number": 12 },
  "base_branch": "main",
  "human_approves_merge": false,
  "verify_commands": ["npm ci --silent && npm run typecheck && npm test"],
  "rebuild_cap": 2,
  "notifications": { "bot_identity": "your-bot-login" },
  "evidence": { "mode": "branch", "branch": "drover-evidence" }
}
```

```
  harness               claude (harnesses/<name>.json)
  roles, labels         skill routing: roles.<role>.skills + labels.<label>.<role> + the issue's Skills: line
  columns               Backlog · Ready · Building · QA · Review · Blocked · Done (always all seven)
  human_approves_merge  legacy: true = never auto-merge (prefer merge_policy.default "human")
  merge_policy          default auto|human · auto_max_lines (400) · size_exclude · always_human {money, auth, schema}
  migrations            globs · allowed_envs (test, staging, live) · target_env · commands
  verify_commands       run by the merge gate against the current base before every merge
  rebuild_cap           bounces allowed before a card goes Blocked
  roles.<role>.max_workers  workers per role, default 1
  tick_seconds          headless runner GraphQL budget floor, default 120
  usage_pause_pct       start no new worker at this % of the Claude usage window, default 95
  model_tier            low | medium | high: the Builder's model ladder by card complexity
  refine, collect       optional blocks for ui-refine-loop and drover-collect (collect.custom = extra sources)
```

```
  label           lanes
  ─────────       ─────────────────────────────────────
  feature, bug,   Ready → Building → QA → Review → Done
  none
  qa                     Ready → QA → Review → Done
                         └ hardening code that already exists
```

Every key, with notes: `skills/drover/references/config-schema.json`.

## How workers decide what test to write

Three layers, three questions. None substitutes for another.

```
  tdd          →  how do I write a test worth having?     discipline
  the ladder   →  which layer does the defect live at?    placement
  vitest |        how do I express it in this repo?       mechanics
  playwright
```

**The middle one is what everyone skips.** A Tester finds every bug through a browser. Where you
*observed* a bug says nothing about where it *lives*. Walk down from the browser and stop at the
first rung that still reproduces:

```
  1  call the module directly       unit         Vitest
  2  wire the real collaborators    integration  Vitest
  3  drive a real browser           e2e          Playwright
  4  needs a live third party       NOT a test   file a gap
```

An e2e pinning a pure-logic defect is slower, flakier, and goes red pointing at a page instead of a
function. Every test must be **red for the right reason** (run against unfixed code; a timeout or
missing selector means you pinned the harness) and **refactor-survivable**. `testing-strategy`
informs coverage, not placement. Contract testing stays out of the default set: Pact solves drift
between independently deployed services, which a single-app repo does not have.

## Start with low autonomy

- Run `/drover lint` first and fix what it flags.
- Start with `human_approves_merge: true` and a few cards; read the PR comments each lane leaves.
- Set `verify_commands` before you allow auto-merge, and add `Bash(gh pr merge:*)` to the
  allowlist only when you mean it: it removes the last human gate.
- Label a few cards `qa` on code that already exists before letting Builders loose.
- Run `/drover-collect` in its default dry-run before `--yes`.

Missing, unreadable or stale evidence is a hold for a person, never a pass.

## Limits

- `gh` auth and CI come from you. Onboard reuses or creates the board and its columns and labels; it never signs in or installs a system tool without your OK.
- The usage pause reads only what an interactive status line recorded; headless workers record no usage.
- `verify_commands` are only as good as your test suite. Without them the gate says it proved nothing.
- Lanes call [mattpocock/skills](https://github.com/mattpocock/skills) by name; without that pack a worker fails when it reaches for one.
- The Tester (QA lane) runs the bundled `pr-security-review` on every PR, and a finding fails QA; it reports only high-confidence findings, so it is not a full audit. Cloudflare's `security-audit` (added by onboard) covers the whole repo, and only runs when you ask.
- `bash` 3.2+ (stock macOS is fine), `jq` and Python 3 are required on the machine running the loop.

## Pack layout and tests

```
  skills/<name>/   SKILL.md (agent prompt) · README.md · references/ · scripts/
  workflows/       ui-refine-loop.js
  bin/drover       the entry point: drover <command> runs lib/drover-<command>
  lib/             dispatcher, planner, merge gate, usage check, filers, config
  harnesses/       one file per worker harness (claude)
  hooks/           guard hooks, cleanup-wt, settings snippets
  tests/           bash/python tests, no network: bash tests/test-*.sh
```

The minimal `.claude-plugin/plugin.json` is a leftover;
`install.sh` is the install path. The full lane-to-skill mapping, including the skills kept
because Matt's pack has no equivalent, is in `skills/drover-build/references/decision-policy.md`.
