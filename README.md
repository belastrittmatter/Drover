<div align="center">

<h1>drover</h1>

<p><strong>Add tasks, walk away, get merged PRs with proof.</strong></p>

<p>Stop babysitting coding agents: cards you drop in Ready come back as tested, reviewed PRs.</p>

<p>9 skills (5 you type, 4 the board runs) · 9 commands · 6 guard hooks</p>

<p>
<img alt="Version" src="https://img.shields.io/badge/version-3.3.0-1f883d?style=flat-square">
<img alt="Host" src="https://img.shields.io/badge/Claude%20Code-skills-d97757?style=flat-square">
<img alt="License" src="https://img.shields.io/badge/license-MIT-0969da?style=flat-square">
</p>

<p><a href="#install">Install</a> · <a href="#quick-start">Quick start</a> · <a href="#how-it-works">How it works</a> · <a href="#skills">Skills</a> · <a href="#commands">Commands</a> · <a href="#guards--hooks">Guards</a></p>

</div>

## Install

Before you start:

- [ ] Claude Code, and `gh` with board access: `gh auth refresh -s project,read:project,repo`
- [ ] Dynamic workflows on in `/config` ([backends](docs/drover/README.md#backends))
- [ ] `verify_commands` set in your config, so the merge gate can prove the build ([setup notes](docs/drover/README.md#setup-notes))

In your project folder:

```bash
./install.sh /path/to/your-project   # from a checkout of this repo
```

Or as a Claude Code plugin:

```
/plugin marketplace add belastrittmatter/drover
/plugin install drover@drover
```

> [!NOTE]
> The plugin ships the skills only. Run `/drover:drover onboard` and its 🔍 Checks step adds the guard hooks, scripts and workflows. Needs Claude Code, `gh`, `jq`, bash 3.2+ and Python 3.9+; the installer checks.

### Security

Two security skills come with every install, and they stay separate:

| Skill | Runs | Covers |
|---|---|---|
| [`pr-security-review`](skills/pr-security-review/README.md) | **On every PR**, inside the QA lane. Each finding fails QA and goes back to Build. | The PR's diff against its base: high-confidence, exploitable bugs only. From Anthropic's [claude-code-security-review](https://github.com/anthropics/claude-code-security-review). |
| `security-audit` | **Only when you ask** (`/security-audit`). The board never runs it. | The whole repository, in depth. From Cloudflare's [security-audit-skill](https://github.com/cloudflare/security-audit-skill), fetched at install with `npx skills`. |

`--no-helper-skills` skips the `security-audit` download; `/drover onboard` adds it later.
Claude Code's built-in `/security-review` is left as it is.

Running from Codex or OpenCode? See [supported hosts](docs/hosts.md) for tested versions, sign-in and gaps.

## Quick start

### 1. Install

```bash
./install.sh /path/to/your-project   # from a checkout of this repo
```

### 2. Onboard

```
/drover onboard
```

Checks · GitHub · Board · Branch · Policies · Bug sources · Review. <kbd>Enter</kbd> takes the recommended answer.

### 3. Run

```
/drover run my-app
```

Add cards to `Ready`. The board drains on its own.

## How it works

**Which path?** Clear idea, foggy idea or bugs: [the routing rule](docs/drover/routing.md) says what to run, and when 1–5 tickets go to `/implement-spec` instead of the board.

## Skills

**9 skills (5 you type, 4 the board runs).** Uses [Matt Pocock skills](https://github.com/mattpocock/skills), ponytail, impeccable, humanizer, [Anthropic's security review](https://github.com/anthropics/claude-code-security-review) and [Cloudflare's security-audit](https://github.com/cloudflare/security-audit-skill).

<!-- skills:start -->
**You type — 5 skills**

| Skill | What it does |
|---|---|
| [`/drover`](skills/drover/README.md) | Orchestrator: plans waves, runs Build → QA → Review, merges when green. |
| [`/drover-collect`](skills/drover-collect/README.md) | Finds problems, verifies them, writes a findings doc for /to-spec and /to-tickets. |
| [`/ui-refine-loop`](skills/ui-refine-loop/README.md) | Critique → refine loop that polishes one page or component. |
| [`/visual`](skills/visual/README.md) | One HTML page: branch recap, plan, or codebase map with diagrams. |
| [`/git-sync`](skills/git-sync/README.md) | Commit per logical change, merge-pull, push without force, report. |

**The board runs — 4 skills**

| Skill | What it does |
|---|---|
| [`/drover-build`](skills/drover-build/README.md) | Builder lane: worktree, smallest safe change, tests, draft PR. |
| [`/drover-qa`](skills/drover-qa/README.md) | Tester lane: evidence, test-gap check, screenshots, or bounce to Build. |
| [`/drover-review`](skills/drover-review/README.md) | Reviewer lane: own hypotheses, remembers prior findings, merge gate. |
| [`/pr-security-review`](skills/pr-security-review/README.md) | Security pass on every PR diff; drover-qa runs it, findings fail QA. |
<!-- skills:end -->

Each name links to that skill's README. Adding a skill? Write its `SKILL.md` and `README.md`, and add a line to `skills/families.json` and to this table.

## Commands

```
/drover onboard          # 7-step setup, resumes
/drover lint             # vague ACs?
/drover status           # read-only snapshot
/drover run <slug>       # drain the board
/drover stop             # stop workers
/drover-collect [source]       # find problems → findings doc → /to-spec
/ui-refine-loop <route>       # polish one page
/visual [recap|plan|path]     # one HTML page
/git-sync                     # commit, pull, push
```

Flags: `run --low|--high` · `run --codex[=<model>]` · `drover-collect --since 30d`

## Guards & hooks

Run automatically once installed. Python stdlib, JSON in, JSON out.

| | |
| --- | --- |
| [guard-worktree-path](hooks/guard-worktree-path.py) | Blocks `git worktree add` outside `.drover/worktrees/` (and the harness's `.claude/worktrees/`) |
| [guard-secrets](hooks/guard-secrets.py) | Blocks reading or piping dotenv files, SSH keys and credential files; key names via `drover-env-check.sh` |
| [guard-key-literals](hooks/guard-key-literals.py) | Blocks a live-looking API key written into a file; flags one already there |
| [guard-delete-outside](hooks/guard-delete-outside.py) | Blocks `rm`, `find -delete` and `git clean` aimed outside the project, `~` or `/` |
| [guard-protected-push](hooks/guard-protected-push.py) | Opt-in (`onboard` asks, or `install.sh --protect-main`): blocks direct and force pushes to main/master/base |
| [cleanup-wt](hooks/cleanup-wt.py) | Removes merged worktrees and branches after each merge and at session start, with a recovery file |
| [merge gate](scripts/drover-merge-gate.sh) | Merges only after the current base plus your non-empty project `verify_commands` pass (missing checks halt setup), pinned to the reviewed commit; applies `merge_policy` and runs allowed DB migrations |

## Models

A cheap router grades each card easy, medium or hard first; the run flag picks the model for each grade. With `--codex`, every lane runs on Codex GPT models instead of Claude.

| Command | Router | Easy card | Medium card | Hard card |
| --- | --- | --- | --- | --- |
| `/drover run` | Haiku 4.5 | Sonnet 5.5 | Opus 5.5 | session model |
| `/drover run --low` | Haiku 4.5 | Haiku 4.5 | Sonnet 5.5 | Opus 5.5 |
| `/drover run --high` | Sonnet 5.5 | Opus 5.5 | Opus 5.5 | Opus 5.5 |
| `/drover run --codex` | gpt-6-luna | gpt-6.1-sol | gpt-6.1-sol | gpt-6-astra |
| `/drover run --codex --low` | gpt-6-luna | gpt-6-luna | gpt-6.1-sol | gpt-6.1-sol |
| `/drover run --codex --high` | gpt-6-luna | gpt-6-astra | gpt-6-astra | gpt-6-astra |

`/drover run --codex=<model>` pins one model for every card and skips the router.

Run inside Codex? plain `/drover run` switches to the Codex ladder by itself. Codex has no
`/drover` command: it finds skills in `.agents/skills/` (symlinks followed), so link
`.claude/skills/drover` there and type `$drover run`.

## Writing standard

Commits, tickets, PR bodies and comments all follow [one writing standard](skills/drover/references/writing-standard.md), in the pack and in every project it is installed into.

<details>
<summary><strong>Upgrading from 2.x</strong></summary>

1. Re-run the install line.
2. Run `/drover onboard`; step 1 upgrades for you and keeps a backup.
3. Try `board-migrate --dry-run` on a copy of your board first.

</details>

## Credits

MIT © Eric Tech (upstream) and belastrittmatter ([LICENSE](LICENSE)). Built on [obra/superpowers](https://github.com/obra/superpowers), [mattpocock/skills](https://github.com/mattpocock/skills), [BuilderIO/skills](https://github.com/BuilderIO/skills) and [tt-a1i/archify](https://github.com/tt-a1i/archify). `pr-security-review` is ported from [anthropics/claude-code-security-review](https://github.com/anthropics/claude-code-security-review) (MIT © 2025 Anthropic, [notice](skills/pr-security-review/LICENSE)); `security-audit` is [cloudflare/security-audit-skill](https://github.com/cloudflare/security-audit-skill) (MIT © 2025-2026 Cloudflare, Inc.), installed from its own repo, not bundled.

### Worker skill availability

Setup and dispatch resolve real, nonempty `SKILL.md` files for the actual
Claude or Codex host. Missing required skills halt before claims or workers;
lockfiles and arbitrary plugin caches do not count. The shared registry in
`scripts/drover-skills-ready.py` owns lane dependencies, overrides and the
two declared ponytail fallbacks. The QA lane requires the bundled
`pr-security-review`; Cloudflare's `security-audit` is an optional QA entry,
so a missing copy never halts a run. Setup's full helper union may be red while a
selected card is runnable. Read-only checks never install; explicit onboard
repair rechecks files after installation.

The CLI accepts `--home <fixture-home>` and `--catalog <active-skills.json>`.
Launchers accept the same inventory through `DROVER_SKILLS_HOME` and
`DROVER_SKILLS_CATALOG`; neither changes the actual host. Catalog shape:
`{"host":"codex","skills":[{"name":"vendor:skill","path":"/exact/SKILL.md",
"enabled":true,"scope":"project","project_root":"/exact/project"}]}`.
User entries use `scope:"user"`. Restart explicitly after repairing missing
skills; readiness certifies availability, not their content or external tools.
