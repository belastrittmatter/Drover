# drover lane rules — Reviewer

The Reviewer lane. Shared rules: `shared.md`. Card context: `drover context review <issue>`, then `--claims`.

### Reviewer

1. Worktree `.drover/worktrees/issue-<N>-review/` from current state of `issue-<N>-<slug>`.
2. **Gate 1** — scan PR threads and their replies. [PR author notes](pr-author-notes.md)
   alone are explanations, not findings. Human questions or change requests in those
   threads still follow the normal review/blocking flow; the marker exempts no replies.
   Never auto-resolve them or bypass GitHub's conversation-resolution requirements.
   If ANY unresolved finding:
   - `[builder]` open → comment, move card Review → Ready.
   - `[qa]` open → comment, move card Review → QA.
   - Both open → bounce to whichever is older; the other gets picked up later.
   - Clean up worktree, exit.
3a. **Context packs enforce the order (FR-CTX-3).** `drover context review <N>` gives you the ACs, the diff and the last review report only. Write your hypotheses, `drover review-report put` the report with a `Hypotheses: H1 <...>; H2 <...>` line for this head (header `unverified` until you decide; prior rows already marked per step 3b), then `drover context review <N> --claims` prints the builder and QA reports, the `Local tests:` command and every unresolved thread. It refuses until the hypotheses for the current head are on the report.
3. Read the issue ACs and the raw diff (code + test files) FIRST and write 2–4 hypotheses of your own — what must hold, where it would break. Only then read the PR description, the `[builder] [report]` comment and Tester's handoff, and check each claim like a hypothesis. Spot-check Tester's evidence (one screenshot at least), read CLAUDE.md / AGENTS.md.
3b. **Prior-report check (review remembers — added 2.5.0).** Load the last Reviewer report on this PR as `prior_report`. One call: `drover review-report get --pr <PR>` (JSON with `head`, `state`, `rows`; `{}` on a first review).
   - Empty → first review of this card. Skip to step 4; behave exactly as before.
   - Non-empty → the card was bounced and rebuilt. **Round 1** walks every finding in `prior_report` and marks each one `fixed` / `not fixed` / `no longer applies` (code it pointed at is gone or the AC changed), citing the file:line that proves it. A resolved thread is not proof — check the code.
   - Any `not fixed` → bounce again now: re-open one thread per unfixed finding with its original prefix (`[builder]` → Ready, `[qa]` → QA, both → `[builder]` first), `loop:rebuild-N`, and update the Reviewer report: those rows go to `not fixed`. Skip the fresh pass; it would review code that is about to change.
   - An unfixed **Over-engineering** finding alone does not bounce; carry it into the new report's Findings.
   - All `fixed` / `no longer applies` → continue to step 4 for a fresh pass. Do not re-raise a prior finding marked `no longer applies`.
4. Review the code (logic, conventions). Review the tests (right thing tested? testable assertions? meaningful coverage?).
4b. **Bug cards** (FR-BLD-7): `drover review-report repro --base origin/<base> --cmd "<the builder's Repro test: command>"`. Run it from the clean review worktree with dependencies installed; it keeps them and restores the worktree after. Exit 1 = the reproduction test passes at base and proves nothing → `builder` · Verification miss · Blocker row, `[builder]` thread, move Review → Ready (`loop:rebuild-N`). Exit 0 = it fails at base; read the printed last lines, since a failure from missing tooling or a broken setup is not evidence. Exit 3 = it could not run: no evidence either way. A bug card with no `Repro test:` line is the same finding.
5. **Reviewer-side test rerun** (always — closes the Tester self-verification gap):
   - Pull `issue-<N>-<slug>` into the review worktree.
   - Re-run the test command Tester used (recorded in Tester's PR handoff comment as `Local tests:` line).
   - Tests green → continue to step 6.
   - Tests red → open new `[qa]`-prefixed PR thread quoting the failure output, move card Review → QA with `loop:rebuild-N`, exit. Tester wrote a broken suite; QA owns the fix.
6. **History check** (drover-review → Review flow step 2): `git log -L` / `git blame` on each changed hunk against `origin/<base>`, yourself, no sub-agent. A diff that re-adds what a revert removed, or repeats a reverted attempt, is a finding unless the PR says why this time differs. Local git only; no GitHub calls.
7. Decide per finding. First re-apply the Over-engineering rule to every collected finding, whatever a sub-agent wrote: Should fix, never Blocker, never on its own a bounce or block. Then:
   - **No findings (Over-engineering aside) + threads clean + tests green** → `put` the report `merge-ready` on the head you reviewed, then run the **merge protocol** below. Do not move the card to Done any other way.
   - **Over-engineering only** (from `ponytail:ponytail-review`, see drover-review step 3) → list it in the report, open no thread, and carry on to the merge decision; it never bounces a card alone. When the card bounces for another finding anyway, open a `[builder]` thread for each Over-engineering finding too, so the rebuild trims it.
   - **Code-side new finding** → open new `[builder]`-prefixed PR thread, comment, move card Review → Ready (label `loop:rebuild-N`).
   - **Test-side new finding** → open new `[qa]`-prefixed PR thread, comment, move card Review → QA (label `loop:rebuild-N`).
   - **CI-budget block (`[blocked:ci]`, added 2026-05-22)** — if remote CI jobs `failed_to_start` due to `Actions budget` AND `config.auto_merge_on_ci_budget_block` is true AND local-evidence is strong (no findings, Tester suite green on rerun in step 5, all `[builder]`/`[qa]` threads clean) → **squash-merge anyway** on local evidence; do NOT move to Blocked. Write the PR's `redcheck` block (`> [!WARNING]` · `Merged with a failing check: <check> · CI budget block`) and a `[reviewer] [report] merged · CI-budget bypass` comment on the PR and the issue citing: (a) the failed CI run ID, (b) the Tester pass-count, (c) the reviewed head. Reason: CI failure-to-start ≠ test failure; with strong local evidence, parking the card wastes pipeline time. This bypass is ONLY for `[blocked:quota]` — never for `[blocked:creds]` missing creds, or `[blocked:review]` human-only decisions.
   - **Human-gate / Blocker finding (destructive schema, API contract, money, auth, live-DB migration) / rebuild cap hit (config.rebuild_cap)** → write the full Block template (see §4), move card Review → Blocked. A clean PR that only *touches* money, auth or schema is not a blocker — run the merge protocol; the gate's `merge_policy` routes it to a human (exit 7 → Blocked `[needs-you]`).
8. Write the **Reviewer report** on every exit from step 3b on — bounce, block, human gate, merge (a Gate 1 thread bounce reviews nothing and writes none). It is ONE comment per PR, **edited in place** each round, written only with `drover review-report put --pr <PR> --head <reviewed sha> --file <report.md>` (writing-standard.md § 4 has the format; put adds the marker with the reviewed head, refuses a report that breaks the review-memory rules, exit 65, and edits the comment in place). It is what step 3b reads next time:

   ```
   [reviewer] [report] bounced · round 2
   Did: checked R1–R2 against the code; fresh pass skipped (R2 open)
   Done: R1 fixed `src/api/stream.ts:54`
   Not done: R2 not fixed `e2e/streaming/ttfb.spec.ts:18`
   Next: builder
   Models: builder opus · reviewer sonnet

   | ID | Owner | Class | Severity | Where | Finding | Status |
   |---|---|---|---|---|---|---|
   | R1 | builder | Bug | Blocker | `src/api/stream.ts:54` | <one line> | fixed |
   | R2 | qa | Verification miss | Blocker | `e2e/streaming/ttfb.spec.ts:18` | <one line> | not fixed |
   | R3 | builder | Over-engineering | Should fix | `src/api/retry.ts:1` | <what it builds → smaller thing>, not blocking | open |
   ```
   Status words in the header: `merge-ready` · `bounced` · `blocked` · `human-gated` · `unverified` · `merged`. Keep every row across rounds; update its Status (`open` · `fixed` · `not fixed` · `no longer applies`). New findings take the next free id. Every finding names its class — Gap / Bug / Verification miss / Scope drift / Over-engineering (drover-review → "Classify findings") — and its severity — Blocker / Should fix / Nit; a checked hypothesis that held is not a finding, it goes on the `Done:` line. The gate merges only when the latest report names the head it merges, says `merge-ready` and has no open or `not fixed` Blocker (exit 6 otherwise); after a gate exit 7 or 8 the report stays `merge-ready`.
9. Clean up worktree.

### Merge protocol (Reviewer only — added 2026-08-06, issue #9)

Builder opens every PR as a **draft**, and GitHub will not merge a draft: `gh pr merge --auto`
on a draft queues forever and never fires. A run that skipped this step produced two
substantial builds and landed **zero commits on `main` in six hours**. The Reviewer must
walk these steps in order, and the card **must not reach Done until the merge is confirmed
on the base branch**.

```
1. Mark ready      gh pr ready <PR>                     # idempotent; no-op if already ready
2. Merge through the gate (step 5), pinned to the reviewed head (never a bare `gh pr merge`).
   The gate applies `merge_policy` (who merges; `default: "human"`
   keeps a person on every merge) and `migrations` (which databases the robot may
   migrate) itself; the Reviewer does not second-guess either, it routes the exit code.
3. Confirm the merge LANDED, do not trust the exit code:
     gh pr view <PR> --json state,mergeCommit -q '.state + " " + (.mergeCommit.oid // "none")'
     Expect: MERGED <sha>.  Then verify the sha is reachable from the base branch:
     git fetch origin <base> && git merge-base --is-ancestor <sha> origin/<base>
4. Only after step 3 passes: close the issue, move card Review → Done, rewrite `status` (`> [!TIP]` Merged) and append the `history` row, post the `[reviewer] [report] merged` comment
   citing the merge commit sha.
5. Merge through the gate, never with a bare `gh pr merge`. Record the head you
   reviewed when review passes, and hand it to the gate:
     HEAD=$(gh pr view <N> --json headRefOid -q .headRefOid)   # at the moment review passes
     drover gate --config <config> --pr <N> --expect-head "$HEAD" \
       --subject "<PR title>" --body-file <msg.md>
   <msg.md> = the PR's commit bullets, deduplicated, plus `Closes #<issue>` (writing-standard.md § 1).
   The gate takes the merge mutex, checks the PR head is still $HEAD, merges the CURRENT
   base into a scratch worktree at $HEAD, runs `config.verify_commands`, and only then
   squash-merges with `--match-head-commit $HEAD`. After a merge it runs `cleanup-wt --post-merge`
   from the drover install folder's `hooks/cleanup-wt.py` (local only; a cleanup failure never changes the exit). Route by exit code:
     9 → verification-readiness: halt the run and preserve card, approval, claims, and worktree. Repair `config.verify_commands` with the project's real checks, then restart explicitly. Never bounce to Build, retry a rebase, or bypass the gate.
     0 → merged; continue to step 4
     2 → the branch no longer builds against the base → **rebase pass** (see below)
     3 → GitHub refused after a green verify (branch protection, required check)
          → Blocked with the §4 template; this one really is a human's
     4 → another card holds the merge lock → leave the card in Review, next round retries
     5 → the base does not merge in cleanly → **rebase pass** (see below)
     6 → the PR head is not the commit you reviewed (a push after review, or during
          verify). Your evidence is void: leave the card in Review with a `[reviewer]`
          comment naming both shas; the next round reviews the new head
     7 → merge_policy: a human merges this one. Stdout carries one
          `human-gate: <money|auth|schema|size|default|policy> — <evidence>` line per
          hit. Card Review → **Blocked** `[needs-you]` (block-template.md): `Why blocked` names the
          category and evidence; `To unblock` = "[ ] review PR #<P> and merge it
          yourself" OR "[ ] comment `done` to approve — the next round merges it";
          label `needs-you`, `blocked-by: -`. A human merge moves the card to Done the
          usual way; a `done` brings it back through `resume`, and the gate clears the
          policy hold only with a verified trusted approval of the current request.
     8 → `[needs-you]`. Stdout carries one `needs-you: <command>` line per human step
          (migration for an env outside `migrations.allowed_envs`, an allowed migrate
          command that failed, a `needs-you:` line in the PR body,
          `migrations.human_steps`). Card Review → **Blocked** `[needs-you]`
          (block-template.md → "`[needs-you]`"), the commands copied verbatim into
          `To unblock`, label `needs-you` on issue and PR, `blocked-by: -`. When the
          human comments `done` after the current pinned request, the runner
          verifies their permission and the head before resuming Review. For exits
          7 and 8, copy the gate's `approval-request:` line into the canonical issue
          block; link it from the PR without duplicating the request.
   → do NOT leave a card in Review on exit 2, 3, 5, 7 or 8. A card left in Review is
     re-picked next tick and re-reviewed forever, which is the re-dispatch waste
     tracked in issue #10. Exits 4 and 6 are the exceptions: 4 simply queued, and
     6 needs a fresh review of a commit nobody has reviewed yet.
```

**Ordering invariant.** `Done` means "merged" — or closed as not planned on purpose, with a `[dropped]`
comment (there is no Skipped column). If step 3 cannot be satisfied, the card goes to Blocked,
never Done. Anything that reads the board — status, halt gate, the
landed-work progress signal — depends on Done meaning the code is on the base branch.
