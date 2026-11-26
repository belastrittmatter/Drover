# /drover-review

The Reviewer: decides whether a PR is safe to merge, and merges it through the gate when it is.

## What It Does

- **Forms its own view first.** It reads the ACs and the raw diff and writes 2-4 hypotheses before it reads the builder's summary, then checks the builder's claims like any other lead.
- **Remembers the last review.** On a re-review it loads the previous report, marks every finding `fixed`, `not fixed` or `no longer applies` with file:line proof, and bounces again on anything unfixed. A resolved thread is not proof.
- **Classes every finding**: Gap, Bug, Verification miss, Scope drift, or Over-engineering (from `ponytail:ponytail-review`; Should fix at most, never blocks alone). Checks that held go on the `Done:` line, gaps on `Not done:`.
- **Re-runs the Tester's tests**, checks the history of the changed lines (`git log`, `git blame`), and on a bug card checks the builder's reproduction test fails at base.
- **Runs on a model other than the builder's** (`roles.review.model`, `roles.review.alt_model`), and the report names both.
- **Merges through `drover-merge-gate.sh`**, pinned to the head it reviewed. A push after review voids the evidence.
- **Files shape problems** in the diff to `Backlog` as refactor cards; it never blocks a card on them.

## When To Use It

- Inside a drover run (the Review lane). This is the normal path.
- Standalone, as `/drover-review`, on a branch or PR: you get a readiness verdict and findings routed to the lane that owns each fix.

## Report format

```
<!-- drover-review:report head=<reviewed sha> -->
[reviewer] [report] <merge-ready | bounced | blocked | human-gated | unverified | merged> · round <N>
Did · Done · Not done · Next · Models
| ID | Owner | Class | Severity | Where | Finding | Status |
```

One report per PR, written only with `drover review-report put`, which refuses a report that
drops or fakes a fix of an earlier finding, and edited in place each round. The gate merges only
on a `merge-ready` report for the head it merges. Findings keep stable ids (`R1`, `R2`, …) so the next review can check them. Format: `skills/drover/references/writing-standard.md` § 4.

## Install

Part of the drover pack: `drover skills install` links it into `~/.claude/skills`. Set `human_approves_merge: true` in the config to keep a person on every merge. Lane details: `skills/drover/references/run.md` → Reviewer.
