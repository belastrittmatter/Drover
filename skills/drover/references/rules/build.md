# drover lane rules — Builder

The Builder lane. Shared rules: `shared.md`. Card context: `drover context build <issue>`.

### Builder (first pass)

1. Create worktree `.drover/worktrees/issue-<N>-build/` off `config.base_branch`.
2. Create branch `issue-<N>-<slug>` from `config.base_branch` — unless one already exists
   (the card came back from Building after a stopped run): then check it out, keep its
   commits and open PR, and continue from where it stopped.
3. Read issue body + ALL comments + PROJECT.md.
3b. Touches a third-party API/SDK, an upgrade, or auth/billing? Read the current official docs
    for the installed version first (drover-build → "Docs before outside-tool code").
4. Implement smallest safe change covering ACs, under the PR size cap
   (`merge_policy.auto_max_lines`, default 400 changed lines). Growing past it mid-build → stop,
   push what you have, propose the split (vertical slices via `/to-tickets`) in a PR comment and
   move the card to Blocked `[blocked:too-big]` — never push on past the cap (drover-build → "Keep each PR small").
5. Commit + push (always). Commits follow writing-standard.md § 1: the subject in the repo's
   `commit_style` (your prompt gives the style and three real subjects) + short bullets. The last
   commit of a finished build ends its body with `Drover: done #<N>`; partial work with
   `Drover: partial #<N>`. On a `bug` card the FIRST commit is the reproduction test alone, red
   before the fix (roles.md FR-BLD-7).
6. Open draft PR linked to the issue with the PR description template (title in `commit_style`, every block filled).
7. Follow [PR author notes](pr-author-notes.md), then post the `[builder] [report]` PR comment (see "Commenting cadence"). On a `bug` card it ends with the machine line `Repro test: <command that runs the reproduction test>`.
8. Post a short status comment on the issue with the PR URL.
9. Clean up worktree. Keep branch + PR open.
10. Move card Building → QA.

### Builder (rebuild)

1. Worktree as above.
2. Read PR review threads on this branch's PR; filter `[builder]` prefix.
3. For each unresolved `[builder]` thread: read file:line + suggested fix, apply, resolve thread via graphql mutation.
4. Address any new failure feedback from Tester's latest `[qa] [report] failing` comment.
   (A Reviewer bounce reaches you as those same `[builder]` threads, listed in the latest review report (`drover review-report get --pr <PR>`). A finding the Reviewer re-opened as `not fixed` was resolved without a fix last time — fix the code, not just the thread.)
5. Commit + push to same branch.
6. Verify ALL `[builder]` threads are resolved. If not, return to step 3.
7. Refresh [PR author notes](pr-author-notes.md), rewrite `status`, `solution`, `history` blocks; post `[builder] [report]` PR + issue comments. Move Building → QA. Clean up worktree.

### The rebase pass

A merge conflict is not a human decision. It is a build task, and it belongs in the lane that is
allowed to push.

**Why this changed.** On 2026-08-20 a PR reached Review with 264 tests green and a 94/100 truth
gate, and could not merge: eleven hunks across five files, every one of them two tickets adding
members to the same interface on purpose. The Reviewer refused — correctly, because **the Reviewer
never pushes to the branch it is judging** — and moved the card to `Blocked`. Two further cards
were waiting on that one and parked behind it. The resolution, when a human finally did it, was
"keep both sides" in twelve of the thirteen hunks and one block of setup code moved into a
function. Three cards lost a round to a mechanical edit.

**The rule.** On merge-gate exit 2 or 5:

```
[ ] label the issue `loop:rebase`
[ ] move the card Review → Ready  (NOT Blocked)
[ ] comment on the PR: which files conflicted, or which verify command failed, and its output
```

The Builder picks it up as an ordinary pass, routes `mattpocock-skills:resolving-merge-conflicts`,
and pushes. Its finish line is a **green verification**, not an empty `git diff --diff-filter=U`:
when a shared interface grows on both sides, the files git merged cleanly are where most of the
work is. In the run above, thirteen conflict hunks were followed by ten further files that merged
without a marker and still failed to typecheck.

`Blocked` is still correct when the rebase itself cannot be done: the two sides genuinely disagree
about behaviour, or resolving requires a product decision. That is a `[blocked:unclear]` or `[blocked:review]` block with the
usual template — and, per §4, a `blocked-by:` line.
