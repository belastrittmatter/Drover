# drover lane rules — shared by every role

Every lane worker follows these rules with its own role file (`build.md`, `qa.md`, `review.md`). `drover context <role> <issue>` hands a worker both. The runner and orchestrator sections stay in `../run.md`.

## Required GitHub reads can pause a run

Use `drover github-read` for required API evidence. It retries
one failed read at most twice (three total attempts); invalid queries or missing
authority halt immediately. Exit **79** means stop dispatch and pause the current
run. Never substitute an empty board, OPEN issue, full quota, or empty diff.
Check the helper's `--check` before each write, migration, or merge. Preserve
worktrees, claims, card/approval state, and the shared local halt record. Report
the reason locally when GitHub cannot accept a comment. Do not blindly retry a
mutation: a lost response may conceal a successful write. Confirm its remote
outcome once service is available before resuming. After fixing access or the
invalid query, resume explicitly with `drover github-read --resume`: it rechecks the
failed read before clearing the marker and archives the original reason. An uncertain
write has no automatic recovery probe: inspect the remote outcome first, then archive
the halt record explicitly; never rerun a migration or write as a health check.

## Branch + PR model — one branch, one PR per issue

Format: **`issue-<N>-<kebab-title>`** (no lane prefix).

Example: `issue-42-add-chat-streaming`.

There is **exactly one branch per issue** and **exactly one PR per issue**. All three lanes work on the same branch in their own worktrees:

- Builder creates the branch off `config.base_branch`, writes code, commits + pushes, opens a **draft PR**.
- Tester checks out the same branch in a fresh worktree, adds tests, commits to the same branch, pushes.
- Reviewer is the **only lane that merges**. On approval: squash-merges PR into `base_branch`, deletes the branch.
- If `config.merge_policy` says a human merges (`default: "human"`, or a money / auth / schema / size hit), the merge gate exits 7 and the Reviewer parks the card in Blocked for a human click instead.

## PR description template

Format: `writing-standard.md` § 2. Title = a subject in the repo's `commit_style`
(conventional: `feat(chat): stream replies`). The body is marker blocks; each lane rewrites only its own
with `drover pr-body --pr <P> --block <name> --expect-head <sha>
(--body-file | --append-file) <md>`. The script refuses (exit 6) when the PR head moved since you
read it — re-read, then redo the edit. Builder opens the PR with every block filled
(`drover pr-body --skeleton` prints the empty markers).

| Block | Builder | Tester | Reviewer |
|---|---|---|---|
| `status` (alert card) | writes | rewrites on exit | rewrites on exit |
| `problem` (lettered steps under Where, screenshot embedded) | writes from the ticket's Context | — | — |
| `solution` (plain bullets, `Docs:` bullets last) | writes, updates on rebuild | — | — |
| `ac` (checklist + one proof line each) | writes all unchecked | ticks with proof, or leaves unchecked with the reason | unticks one it disproved |
| `history` (Lane · Result · Time · Details) | appends a row | appends a row | appends a row |
| `visual` (Before \| After, UI only) | — | writes | — |
| `risk` (**Low**/**Medium**/**High** + one line, Door, Blast radius) | writes | — | adjusts |
| `redcheck` (only when merged with a failing check) | — | — | writes |

```markdown
<!-- sb:status -->
> [!NOTE]
> In QA · round 1 · 0/2 AC · Closes #<N> · head `<sha>`
<!-- /sb:status -->

<!-- sb:problem -->
## Problem
- **Where:** <page>, `<route>`
  - a. <step>
  - b. <step>
- **Who:** <who sees it>
- **What happened:** <one line>

![<page> · desktop](https://github.com/<OWNER>/<REPO>/raw/<sha>/<path>.png)
<!-- /sb:problem -->

<!-- sb:solution -->
## Solution
- <one change per bullet>
- Docs: <url> — <the one fact it settled>   (or: Docs: none needed — no third-party surface)
<!-- /sb:solution -->

<!-- sb:ac -->
## Acceptance criteria
- [ ] <AC1 text>\
  ↳ not verified yet: QA has not run
- [ ] <AC2 text>\
  ↳ not verified yet: QA has not run
<!-- /sb:ac -->

<!-- sb:history -->
<details><summary>Iteration history</summary>

| Lane | Result | Time | Details |
|---|---|---|---|
| builder | pass | <drover-pr-body.sh --time> | draft · `<sha>` |

</details>
<!-- /sb:history -->

<!-- sb:risk -->
## Risk
**Low** · <one line: what could break and what limits it>
- **Door:** <two-way | one-way> · <how to undo, or why it cannot be undone>
- **Blast radius:** <short label> · <who breaks if this is wrong>
<!-- /sb:risk -->
```

History rows by lane: `builder` · `qa` (`fail v1` / `pass v2`, details = evidence folder) ·
`reviewer` (`bounced` / `merged`, details = finding ids or `squash <sha>`). Time comes
from `drover pr-body --time --config <cfg>` (config `timezone`).

Status card lines: `> [!NOTE]` + `Building` / `In QA` / `In review · round N` ·
`> [!TIP]` + `Merged · <sha>` · `> [!WARNING]` + `Blocked · <reason>` (`Blocked · needs owner`
for a `[needs-you]` hold). Each names the AC
count and the head commit, so evidence is never read against a different one.

No "Not verified" and no "Next" section: an unchecked AC with its one-line reason says what is
not proven. Merged with a failing check (the `[blocked:ci]` CI-budget bypass) → the Reviewer writes the
`redcheck` block first:
`> [!WARNING]` / `> Merged with a failing check: <check> · <why it was safe>`, and names the
check in `risk` too.

## PR review-comment threads — prefix + resolution protocol

Reviewer findings use **line-level review comments** (resolvable threads). Each finding MUST start with the lane that owns the fix — `[builder]`, `[qa]` or `[reviewer]` — then a label (`[blocker]`, `[issue]`, `[suggestion]`, `[nit]`, `[question]`, `[praise]`; writing-standard.md § 4). Read `[QA]` and `[review]` on older PRs as `[qa]` and `[reviewer]`. [PR author notes](pr-author-notes.md) have their own format; read all replies before treating a thread as explanation only.

Examples:

```
src/api/stream.ts:54   [builder] [blocker] uses `new Date()` — replace with `clock.now()`.
e2e/streaming/ttfb.spec.ts:18   [qa] [issue] spec asserts status only — add a TTFB assertion.
```

**Resolution rules (each lane only scans the current branch's PR):**

| Lane exiting | Must resolve | Refusal action |
|---|---|---|
| Builder (Building → QA) | All `[builder]` threads on this PR | Stay in Building, fix, then exit |
| Tester (QA → Review) | All `[qa]` threads on this PR | Stay in QA, fix, then exit |
| Reviewer (approving merge) | All findings and human requests; any conversations GitHub requires resolved | Bounce: `[builder]` open → Ready; `[qa]` open → QA; unresolved human decision → Blocked |

Threads are resolved via `gh api graphql` `resolveReviewThread` mutation when the fix is committed.

## Commenting cadence (issue + PR, every lane)

Every lane writes BOTH on every exit, in the comment format of writing-standard.md § 4:

```
[<role>] [<label>] <status> · <context>
Did: <one line>
Done: <what passed or landed, with sha / file:line / command>
Not done: <what failed or was skipped, and why>   (omit when nothing)
Next: <builder | qa | reviewer | owner | none>
```

- Role = who writes it: `builder` · `qa` · `reviewer` · `collect` · `orchestrator`. Label for a
  lane exit is `report`; `blocker` for a Block comment.
- ≤ 8 lines. Evidence before prose. An unchecked thing is never implied to pass.
- NEVER list changed files — the PR shows them. No restating the issue, no adjectives.
- Machine lines go last and do not count: `Local tests:` (Tester and Builder, the exact command
  the Reviewer reruns), `gh-quota-on-exit:`, `blocked-by:`.
- Run the prose through `humanizer` when installed (else the plain-words rules in
  writing-standard.md).
- The **issue comment** is the same header plus the PR link; the PR comment carries the detail.

Builder exit (PR):

```
[builder] [report] built · round 1
Did: added /api/stream and the client SSE consumer; no schema change, no new deps
Done: AC1 stream opens `streaming.test.ts:12` · typecheck clean · `abc1234`
Not done: AC2 TTFB under load — no load harness here
Next: qa
Local tests: npm test --run streaming
gh-quota-on-exit: graphql=4120/5000 rest=4870/5000
```

Builder exit (issue):

```
[builder] [report] built · PR #87 @ abc1234
Next: qa
```

Tester fail (issue and PR — screenshots mandatory for any UI-touching AC):

```
[qa] [report] failing · v1
Did: ran AC1–AC2 on desktop + mobile against `abc1234`
Done: AC2 CLS 0.04
Not done: AC1 TTFB 1240 ms (want < 800) — fixed looks like first byte under 800 ms
Next: builder
Local tests: npx playwright test e2e/streaming
QA report: https://github.com/<OWNER>/<REPO>/raw/<SHA>/issue-42/v1/REPORT.md

| Viewport | Screenshot |
|---|---|
| Desktop 1920×1080 | ![desktop](https://github.com/<OWNER>/<REPO>/raw/<SHA>/issue-42/v1/desktop.png) |
| Mobile 375×667 | ![mobile](https://github.com/<OWNER>/<REPO>/raw/<SHA>/issue-42/v1/mobile.png) |
```

A pass uses the same table with screenshots of the **working** UI per AC.

Reviewer merge (issue):

```
[reviewer] [report] merged · `ef67890` on staging
Did: path Build, QA fail v1, Build, QA pass v2, Review pass · builder opus, reviewer sonnet
Next: none
```

Block exits use the same header (`[<role>] [blocker] blocked <reason tag>`) and the fields of
`block-template.md`. A card dropped on purpose (out of scope, won't do) is closed as not planned
and moved to Done with a `[<role>] [report] dropped [dropped] <reason>` comment — there is no Skipped
column.

## Worker contract (every lane on claim)

Claim uses a **GitHub Issue assignee mutex** — atomic compare-and-set via `gh issue edit --add-assignee`. Labels are descriptive only; the assignee is the lock.

```
1. ATTEMPT CLAIM (atomic):
   ├─ `gh issue edit <N> --add-assignee drover-bot[bot]`
   │      (if a different assignee is already set → 422; treat as "already claimed")
   ├─ On 422 / conflict → another worker has it; skip this dispatch.
   └─ On success → continue. Apply descriptive label
       (loop:in-build / loop:in-qa / loop:in-review) for UI clarity only.
2. SANITY CHECK: issue body has `## Acceptance Criteria` with ≥1 bullet
   ├─ Missing → write the full Block template (see §4) with `[blocked:unclear]`
   │           release claim (`gh issue edit --remove-assignee`)
   │           move card to Blocked, continue with next card.
   └─ Present → proceed.
3. Do the lane's work (build / QA / review).
4. If this lane opened a PR or pushed changes, refresh [PR author notes](pr-author-notes.md).
   Comment evidence on issue + PR (writing-standard.md § 4) and rewrite your PR body blocks.
5. Move card to next column (or Blocked with the full §4 template; dropped on purpose → closed, Done, `[dropped]` comment).
6. RELEASE CLAIM (`gh issue edit --remove-assignee drover-bot[bot]`) and remove descriptive label.
```

The `drover-bot[bot]` identity is either (a) a GitHub App installed on the repo, or (b) the user's own account on solo projects — onboard step 2 picks which. The assignee mutex is reliable because GitHub serializes assignee writes per issue.

### Root-cause hash (used by the rebuild-cap gate)

To detect "same root cause" without depending on a model classifier, each lane writes a deterministic hash to its failure handoff comment:

```
root-cause-hash: <sha256 first 12 hex chars>
```

The hash inputs (joined with `|`):

1. Lane (`build` / `qa` / `review`).
2. Error class — first matched: `test_assertion`, `test_runtime`, `build_fail`, `lint_fail`, `merge_conflict`, `network`, `auth`, `quota`, `other`.
3. First 3 unique `file:line` frames from the captured stack trace / test output (sorted, normalized — strip absolute paths to repo-relative).

Two consecutive failures on the same card with **identical hash** AND **same lane** count as the same root cause. Hits `rebuild_cap` → Blocked.

Different hash on the same card resets the counter — the bot recognizes that progress has been made even if the card hasn't reached Review yet.

## Worker rate-limit etiquette (read before any burst of gh calls)

Workers share the dispatcher's gh-auth token bucket. The dispatcher's `gh_rate_guard` does NOT protect worker traffic. Every worker MUST follow `rate-limit-etiquette.md` (in this directory):

- Move and read cards with `drover card --config <cfg> move <issue> <Status>` / `status <issue>` — 1 GraphQL point. Every "move card X → Y" in the lifecycles above means this call. Comments, labels and PR reads go over REST (`rate-limit-etiquette.md` → "Price list").

- Run `drover gh-guard check 200` at worker start.
- Run `drover gh-guard check 200` before any burst of gh calls (thread reads, sub-agent spawn, exit verification).
- Sub-agents are capped at 50 gh calls each — prefer `git blame` (local) over `gh api graphql`.
- Final PR handoff comment MUST include `gh-quota-on-exit: graphql=<n>/5000 rest=<n>/5000` (use `drover gh-guard summary`).
- On 403 / secondary-rate-limit: sleep 60s, re-check at threshold 500, then resume.

## Worker self-check (mandatory on exit, every lane)

Before releasing the claim assignee and exiting, every worker MUST verify:

- [ ] Issue comment AND PR comment both written (per "Commenting cadence" above).
- [ ] Card column move's mutation returned success. **Trust the exit code of `drover card move`** — a re-read of the board was the per-worker quota tax (`rate-limit-etiquette.md` §3). If the mutation returned non-zero, preserve its unknown outcome, leave the assignee in place, and pause the run locally. Reconcile the remote card state before any repeat; never blindly retry a write or depend on a successful halt comment.
- [ ] Claim assignee released (`gh issue edit --remove-assignee <bot_identity>`) and the descriptive `loop:in-*` label removed.
- [ ] On failure handoff: `root-cause-hash:` line is present in the PR handoff comment (per "Root-cause hash" above).
- [ ] On Block/Skip exit: the full template from `block-template.md` is populated on BOTH the issue and the PR (if a PR exists); the reason tag is one of `drover-reasons.json` (a dropped card takes `[dropped]`).
- [ ] `gh-quota-on-exit:` line appended to PR handoff comment.

A worker that cannot satisfy this checklist must NOT release its claim. It either fixes the gap and re-checks, or — if the gap itself is structural (e.g., GitHub API refusing to move the card) — it leaves the assignee in place and writes a halt comment so the runner's "no progress for 3 ticks" gate can fire deterministically.

### Known issue — multi-attempt card moves (added 2026-05-22)

During the 2026-05-21 production run, several workers exited cleanly (process terminated, in-flight lock reaped) but **had not moved their card to the next column**. The dispatcher correctly re-dispatched (lane idle + card still in source column = re-fire) — but each retry burned a full lane cycle (~10 min) before the next worker tried again. The #382 Reviewer took **5 attempts × ~10 min = ~50 min** to move a card that the first attempt should have moved.

Suspected causes:
- Worker lost the response to a column move and exited "cleanly" without confirming its outcome. Preserve the unknown outcome and pause; do not claim success or blindly repeat the mutation.
- Worker's drover-build/drover-qa/drover-review skill silently caught the move error and proceeded to assignee-release without surfacing the failure.

**Mitigation for now:** the dispatcher's `reap_finished_locks` + assignee sweep + re-dispatch keep the pipeline rolling, so this is a wall-clock issue, not a correctness issue. A worker that doesn't move the card will eventually have another worker do it.

**Required behavior:** check the shared halt record before a column move, attempt the mutation once, and preserve `move-mutation-result: ok|unknown|skipped` in the handoff. An uncertain outcome pauses the run for read-only reconciliation; it must never trigger blind replay.
