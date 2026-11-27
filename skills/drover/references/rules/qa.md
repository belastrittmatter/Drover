# drover lane rules — Tester (QA)

The Tester lane. Shared rules: `shared.md`. Card context: `drover context qa <issue>`.

### Tester (first pass — repo-backed)

1. Pull latest of base; checkout `issue-<N>-<slug>` into worktree `.drover/worktrees/issue-<N>-qa/`.
   A `qa` card has no branch yet: move it Ready → QA, then create the branch from the base.
2. `target.url` set → health-check it first. Unhealthy → Block.
3. Read issue + PR + Builder's handoff comment.
4. Build issue-scoped test plan: one observable test per AC.
4a. Existing project verifier → follow `../../drover-qa/references/project-verifier.md`
for affected feature selection, actual host readiness and new run-owned proof.
Record uncovered/stale optional coverage alongside ordinary issue AC checks.
4b. **Test-gap check** (drover-qa → "Test-gap check (after build)"): map every AC to unit / component / e2e tests, hunt edge cases, write the High gaps red-first. A High gap that needs app code changed → Fail (step 7) with the gap list. Medium/Low go in the handoff under `Test gaps (not written)`.
4c. **Security pass** (drover-qa → "Security pass (every PR)"): run `pr-security-review` on the PR's diff against its base. Each kept finding is a failing check → Fail (step 7) with a `[builder]` thread on its file:line. Skill missing → Block.
5. Run the tests. Write evidence (screenshots, logs, HARs, `REPORT.md`) to a scratch folder outside the worktree, never into the repo. For UI/visual ACs, capture screenshots at the standard viewports (1920×1080 desktop, 1024×768 tablet, 375×667 mobile). Commit and push the test files to the issue branch first, then publish the evidence BEFORE writing the comment: `drover evidence push <N> v<V> <files...>` (see "Evidence push" below). It prints the SHA-pinned URL of each file; never `git add` an evidence file yourself.
6. **Pass** → tick the `ac` block with proof lines, write `visual` for UI, append a `history` row, rewrite `status` → `[qa] [report] pass` PR comment with results, ending with the machine lines `QA report: <REPORT.md URL>` and `QA head: <the head sha you passed>` (the Reviewer lists test edits made after it, FR-REV-12) **+ inline screenshot embeds** (see "Screenshot embed format" below) → issue comment with the SAME inline screenshot embeds → move card QA → Review. Clean up worktree.
7. **Fail** → leave failing ACs unchecked with the reason, append a `history` row, rewrite `status` → `[qa] [report] failing` PR comment with per-AC expected/actual + repro file:line + "what fixed should look like", ending with `QA report: <REPORT.md URL>` **+ inline screenshot embeds of the broken state** → issue comment with the same inline screenshots (showing what's wrong) → increment rebuild counter → move card QA → Ready (label `loop:rebuild-N`). Clean up worktree.

#### Evidence push

`drover evidence push <N> v<V> <files...>` commits the files to the location in the config's `evidence` (specs/conventions.md FR-EVD): mode `branch` puts them on the orphan evidence branch under `issue-<N>/v<V>/`, so the PR diff holds no QA file; mode `pr` puts them on the PR branch under `<evidence.path>/issue-<N>/v<V>/`. It pushes and prints one URL per file, in argument order, pinned to the commit that holds them. It does not touch your worktree or branch; in `pr` mode run `git pull --ff-only` before you push anything else to the PR branch. Exit 1 (push failed after retries) → Block with the printed error. Exit 1 also covers a push refused for any other reason (auth, branch protection, a hook): it stops at once with git's message. Exit 65 → fix the input it names: an image over 5 MB is downscaled to at most 1920 px wide first; an `evidence.branch` equal to the base branch, `main` or `master` is refused, so Block with that message for the owner to change the config.

#### Screenshot embed format (mandatory on every QA exit — added 2026-05-22)

Inline screenshots in the GitHub comment using raw-URL markdown so they render directly on the issue/PR page without anyone having to clone the repo. Paste the URLs `drover evidence push` printed; never build one by hand or from a branch name:

```markdown
| Viewport | Screenshot |
|---|---|
| Desktop 1920×1080 | ![desktop](https://github.com/<OWNER>/<REPO>/raw/<SHA>/issue-<N>/v<V>/desktop.png) |
| Tablet 1024×768  | ![tablet](https://github.com/<OWNER>/<REPO>/raw/<SHA>/issue-<N>/v<V>/tablet.png) |
| Mobile 375×667   | ![mobile](https://github.com/<OWNER>/<REPO>/raw/<SHA>/issue-<N>/v<V>/mobile.png) |
```

- `<V>` — the QA version (`v1`, `v2`, ...). Push every file of one round in one call, so they share one `<SHA>`.
- File names — keep them stable and descriptive (`desktop.png`, `tablet.png`, `mobile.png`, or `before-fix-desktop.png` / `after-fix-desktop.png` for rebuild-pass cases).

For non-visual ACs (API tests, migration SQL, etc.), skip the screenshot block. Test output (logs, `REPORT.md`) still goes through `drover evidence push`, and the comment's `QA report:` line links the report.

### Tester (rebuild — when Reviewer bounced for `[qa]` thread fixes)

1. Worktree as above.
2. Read PR review threads; filter `[qa]` prefix.
3. For each unresolved `[qa]` thread: apply fix to test files, resolve thread.
4. Re-run full test suite for the ticket.
5. Commit + push the test fixes. Publish the round's evidence with `drover evidence push <N> v<V+1> <files...>`.
6. Verify ALL `[qa]` threads are resolved.
7. Update `ac`, `history`, `status`; post `[qa] [report]` PR + issue comments. Move QA → Review. Clean up worktree.
