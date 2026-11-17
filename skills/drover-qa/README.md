# /drover-qa

The Tester: proves a card's acceptance criteria with tests and evidence, or sends it back to Build with a precise failure.

## What It Does

- **One observable test per AC**, written at the layer the defect lives (unit or integration in Vitest, e2e in Playwright only when the browser is what broke).
- **Test-gap check after build**: maps every AC to tests, hunts edge cases with exact values, names weak tests. High gaps are written red-first; Medium/Low are listed and never block.
- **Captures evidence**: screenshots at desktop, tablet and mobile, logs and HARs, committed to the branch and embedded inline in the PR comment.
- **Runs `pr-security-review` on every PR diff.** Each finding fails QA and goes back to Build, like any failing check. For a whole-repo audit it points you at `security-audit`.
- **Files what it finds** off-ticket as `source:qa` cards instead of fixing them silently.

## When To Use It

- Inside a drover run (the QA lane). This is the normal path.
- Standalone, as `/drover-qa`, to bug-bash an app: a browser crawl from `docs/drover-qa/queue.md` that grows a Playwright suite and files fix-ready issues.

## Standalone flags

| Command | Does |
|---|---|
| `/drover-qa` | 10 iterations |
| `/drover-qa 5` | 5 iterations |
| `/drover-qa --resume` | Continue from the last iteration |
| `/drover-qa --continue-on-error` | Don't halt on one failed iteration |

## Install

Part of the drover pack: `./install.sh /path/to/project`. Run it against staging, not production. Lane details: `skills/drover/references/run.md` → Tester.
