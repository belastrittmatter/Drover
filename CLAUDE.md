# drover — agent-facing notes

This repo ships nine skills under `skills/`: five you type (`drover`, `drover-collect`,
`ui-refine-loop`, `visual`, `git-sync`) and four the board runs (the lane workers `drover-build`, `drover-qa`,
`drover-review`, plus `pr-security-review`, which drover-qa runs on every PR). Cloudflare's
`security-audit` is not shipped: onboard fetches it with `npx skills`, and no lane runs it. Architecture findings come from `/drover-collect architecture`.
Worktree cleanup (`hooks/cleanup-wt.py`) is a hook, not a skill.

## The cardinal rule

**`drover` is an autonomous trader. The interactive Claude session that invokes `/drover run` is an *orchestrator*, NOT a worker.** Its only jobs are:

1. Verify preconditions (clean git, no orphan workers, GraphQL quota, etc.).
2. Dispatch: start the runner in the background (`mkdir -p .drover/runs && nohup drover run >>.drover/runs/runner.log 2>&1 &`). Its
   headless workers do all product work; they start with the command, model flag and
   permissions in `harnesses/<harness>.json`. `--headless` is accepted and ignored for one
   release (it is the only mode now).
3. Report back to the user: the runner PID and the manifest path, then end the turn. Progress is
   `drover status`.

The orchestrator MUST NOT:

- Build, test, review, or fix issues itself. All product work is delegated to the runner's headless workers.
- Patch the dispatcher script or skill files mid-run, even if it sees a problem. Capture the symptom and tell the user; wait for explicit approval.
- Wait on the runner or its workers, or relay their work. They write their evidence back to the GitHub issue + PR. The orchestrator's user-facing output is the dispatch confirmation, not the run result.
- Hold context for multi-card progress. State lives on the GitHub Project board + the inflight lockfiles, not in the orchestrator's session.

If a problem surfaces during the run, the orchestrator's reply is: "I saw X. Want me to dig in or stop the runner?" — not "I went ahead and fixed it."

## Worker rules

Workers (`drover-build`, `drover-qa`, `drover-review`) share the dispatcher's `gh` token bucket. They MUST:

- Required GitHub reads use `drover github-read`; exit 79 stops the run.
- Check its `--check` before GitHub writes, migrations, merges, and new dispatch; preserve work when halted.
- Run `drover gh-guard check 200` at worker start and before any burst of `gh` calls.
- Prefer local `git blame` / `git log` over `gh api graphql` for any sub-agent that doesn't need fresh state.
- Cap sub-agents at 50 gh calls each. If a sub-agent runs out, it returns `confidence: insufficient_data` rather than burning the shared quota.
- Append `gh-quota-on-exit: graphql=<n>/5000 rest=<n>/5000` to the PR handoff comment.

See `skills/drover/references/rate-limit-etiquette.md` for the full discipline.

## Installation contract

`install.sh` links `bin/drover` into `~/.local/bin` and copies nothing into a project
(specs/config.md FR-CFG-7). `drover <command>` runs `lib/drover-<command>.sh|py` from the
install folder; upgrading is `git pull` there. `drover skills install` links the nine skills
into the harness's user skill folder (`~/.claude/skills`).

A project holds one tracked file, `.drover/config.json` (FR-CFG-1), validated against
`skills/drover/references/config-schema.json` at the start of every `drover` command.
`drover onboard` (`lib/drover-setup.py onboard`) is its only writer: it also adds
`.drover/runs/`, `.drover/worktrees/`, `.claude/settings.local.json*` and `.claude/drover/`
to `.git/info/exclude`, and merges the guard hooks from the harness entry
(`harnesses/claude.json` → `hooks`) into the untracked `.claude/settings.local.json`, where
they run from the install folder. `drover setup fix` finishes an upgrade from an older
copying install with no question (backup in `.claude/drover/backup/`).

```
bin/drover                 the entry point
lib/                       runner, card, gate, preflight, status, setup, config, ...
harnesses/claude.json      worker command, model flag, skill folders, hooks, usage probe, permissions
hooks/                     guard hooks + cleanup-wt.py, wired per harness
skills/<all nine>/
workflows/                 ui-refine-loop.js (the standalone ui-refine-loop skill)
```

## Board shape (v3.0.0)

Columns are always Backlog · Ready · Building · QA · Review · Blocked · Done — no Skipped, no
per-board `variant`. Labels `qa` · `bug` · `feature` route cards: `qa` skips Building, everything
else (and no label) is built. Change routing in `lib/drover-run.sh` (`card_lane`) and
`run.md` → "Lanes and label routing" together.

Skills call the dispatcher scripts as `drover <command>`; a headless worker's permissions
(`harnesses/claude.json`) must cover every command a role's rules name (`tests/test_harness_permissions.py`).

## Writing

Commits, PR bodies, tickets and comments in this repo follow
`skills/drover/references/writing-standard.md`, the same standard drover writes into
every project it is installed in.

| Thing | Format |
|---|---|
| Commit | this repo's `commit_style` is conventional: `<type>(<scope>): subject` + short bullets (feat · fix · chore · refactor · test · perf · docs · ci · ui · security · revert · wip); no emoji, no attribution lines |
| PR body | marker blocks, one owner each: status · problem · solution · ac · history · visual · risk |
| Ticket | Problem · Context · Fix · Acceptance Criteria · Risk · Blocked by (+ Evidence for bugs) |
| Comment | `[role] [label] status` · Did · Done · Not done · Next · ≤ 8 lines |
| PR author notes | Purpose · What changed · Why it matters, in native file/inline review comments; follow `skills/drover/references/pr-author-notes.md` |

- NEVER rewrite a whole PR body: `lib/drover-pr-body.sh` rewrites one block.
- ALWAYS change a format in `writing-standard.md` first, then the templates and `tests/test_writing_format.py`.

## Release checks

Run `bash tests/run-safety.sh` for the offline suite. CI (`.github/workflows/cross-platform.yml`)
runs the same checks on Linux/macOS on pushes to main/dev and on pull requests.
