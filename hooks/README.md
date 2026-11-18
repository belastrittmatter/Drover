# drover guard hooks

Claude Code hook scripts. Python 3 standard library only (no jq, no node). Each reads the
hook JSON on stdin, prints a decision only when it blocks, and exits 0 on its own errors
so a broken guard never stops unrelated work. All of the below are on by default except the
protected-push guard (opt-in).

- `guard-worktree-path.py` — PreToolUse `Bash`: denies `git worktree add|move` whose target resolves outside `<repo>/.drover/worktrees/` and `<repo>/.claude/worktrees/` (honours `cd X &&`, `git -C X`).
- `guard-secrets.py` — PreToolUse `Bash|Read|Grep`: denies reading or piping dotenv files, SSH keys, `.npmrc`, `credentials(.json)`, service-account JSON and `.pem`; `.env.example` stays readable. One plain call to `drover env-check KEY…` (or `drover-env-check.sh KEY…`) is allowed: it prints present / empty / missing per key and never a value (no pipe, chain or redirect may ride along).
- `guard-key-literals.py` — PreToolUse `Edit|MultiEdit|Write|NotebookEdit|Bash`: denies writing a live-looking API key into a file; PostToolUse `Edit|MultiEdit|Write|NotebookEdit`: flags one already on disk. Reports line numbers, never the value.
- `guard-delete-outside.py` — PreToolUse `Bash`: denies `rm`, `rmdir`, `unlink`, `find … -delete` / `-exec rm` and `git clean` whose target resolves outside `$CLAUDE_PROJECT_DIR`, and always `/` or `~` itself (honours `cd X &&`, `git -C X`, `~`, `$HOME`). Inside `/tmp`, `/private/tmp`, `/var/folders` and `$TMPDIR` stays allowed. A path whose value is only known at run time (`$(…)`, a variable set earlier in the same command) is allowed.
- `cleanup-wt.py` — SessionStart (`--auto`): when a base branch tip moved since the last session, removes worktrees and local branches already merged into it. The merge gate also runs it with `--post-merge` after every merge. Never deletes unmerged work (a dirty worktree on a merged branch is wip-committed and its branch kept), never touches bases or the worktree it runs in, and writes a recovery TSV to `<git-common-dir>/cleanup-wt/<timestamp>.tsv` before acting. Restore with `git branch <name> <sha>`. A manual dry run (`python3 "$(drover config home)/hooks/cleanup-wt.py"`, then `--apply`) still works.

## Enable

`drover onboard` does this for you, through the harness entry (`harnesses/claude.json` →
`hooks`): it merges the block below (kept in `settings-snippet.json`) into the untracked
`.claude/settings.local.json`, with `{drover_home}` filled in, backing the file up first and
never adding a command twice. The hooks run from the drover install folder; nothing is
copied into the project. `drover onboard --no-hooks` skips them. By hand, merge this
(with `{drover_home}` replaced by `drover config home`):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"{drover_home}/hooks/guard-worktree-path.py\""
          },
          {
            "type": "command",
            "command": "python3 \"{drover_home}/hooks/guard-delete-outside.py\""
          }
        ]
      },
      {
        "matcher": "Bash|Read|Grep",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"{drover_home}/hooks/guard-secrets.py\""
          }
        ]
      },
      {
        "matcher": "Edit|MultiEdit|Write|NotebookEdit|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"{drover_home}/hooks/guard-key-literals.py\""
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|MultiEdit|Write|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"{drover_home}/hooks/guard-key-literals.py\""
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 \"{drover_home}/hooks/cleanup-wt.py\" --auto --fetch 2>/dev/null || true"
          }
        ]
      }
    ]
  }
}
```

## Opt-in: block pushes to the base branch

`guard-protected-push.py` — PreToolUse `Bash`: denies a `git push` that would update or delete
`main`, `master`, any `base_branch` in `.drover/config.json`, or a name in
`SB_PROTECTED_BRANCHES` (comma-separated). That covers force pushes (`-f`, `--force`,
`--force-with-lease`, `+ref`), explicit refspecs (`feat:main`, `HEAD:refs/heads/main`), `--delete`,
`--all` / `--mirror`, and a bare `git push` while the base is checked out. Force pushes to feature
branches stay allowed: the rebase pass needs them. `SB_ALLOW_PROTECTED_PUSH=1` turns it off for a
session.

It is installed but not wired by default: a brand-new repo often pushes its first commits straight
to `main`. `drover onboard` asks once ("Block direct/force pushes to main? Recommended for
existing apps; skip for brand-new repos"), and `drover onboard --protect-main` wires it. Either
way the entry is `settings-protect-main.json`:

```
{ "matcher": "Bash", "hooks": [ { "type": "command",
  "command": "python3 \"{drover_home}/hooks/guard-protected-push.py\"" } ] }
```

Tests: `tests/test-guard-hooks.sh` (all guards), `tests/test-cleanup-wt.sh`.

## Credit

- `cleanup-wt.py` — ported from the `cleanup-wt` skill in Eric Tech's BookKeepingApp (Node), generalised.
- `guard-worktree-path.py` — ported from `.claude/hooks/guard-worktree-path.js` in Eric
  Tech's BookKeepingApp.
- `guard-secrets.py`, `guard-key-literals.py` — ported from `.agents/hooks/` in Eric
  Tech's ai-builder-starter-kit
  (same author as the upstream pack; the kit has no top-level licence file, and this pack is MIT).
