# Configuration, skills and harnesses

Part of the [drover spec](README.md). All of this is new or changed.

## Configuration

- **FR-CFG-1** A project shall need only `.drover/config.json`; drover's
  scripts shall not be copied into the project. (Upstream copies about 30
  scripts into `.claude/bin`.) `drover onboard` shall add `.drover/runs/`
  and `.drover/worktrees/` to `.git/info/exclude`, not to a tracked
  `.gitignore`.
- **FR-CFG-2** The config shall be validated against a published JSON schema
  at every command start; an invalid config stops the command and names the
  failing field.
- **FR-CFG-3** The skills a worker uses shall be the union of
  `roles.<role>.skills`, `labels.<label>.<role>` for each card label, and a
  `Skills:` line in the issue body. Replaces upstream's hardcoded `REGISTRY`
  in `scripts/super-board-skills-ready.py`.
- **FR-CFG-4** Before dispatch, the system shall check that every selected
  skill exists for the worker's harness; if one is missing, then it shall
  halt with the skill name and the folders searched, and claim nothing.
  (Reuses upstream's readiness check, minus `REGISTRY`.)
- **FR-CFG-5** `drover onboard` shall write a starting config with upstream's
  routing as defaults, detected `verify_commands` candidates for the owner
  to confirm, the detected `commit_style`, and the evidence location the
  owner picks (FR-EVD-1).
- **FR-CFG-6** Per-role overrides shall be allowed for `harness`, `model`,
  `max_workers` and, with hc, `template`.

Example:

```json
{
  "harness": "claude",
  "project": { "owner": "me", "number": 3 },
  "base_branch": "main",
  "verify_commands": ["pnpm test", "pnpm build"],
  "commit_style": "conventional",
  "roles": {
    "build":  { "skills": ["tdd"], "model": "opus", "max_workers": 2 },
    "qa":     { "skills": ["playwright-best-practices", "pr-security-review"] },
    "review": { "skills": ["code-review"],
                "second_opinion": "risky", "second_opinion_model": "sonnet" }
  },
  "labels": { "bug": { "build": ["diagnosing-bugs"] } },
  "merge_policy": { "default": "auto", "auto_max_lines": 400 },
  "evidence": { "mode": "branch", "branch": "drover-evidence" },
  "metrics": { "commit_email": "drover@users.noreply.github.com" },
  "hc": { "enabled": false, "view": false }
}
```

### Keys

Start from upstream's `skills/super-board/references/config-schema.json` at
`d0b7d5a`. Every key not listed here keeps its upstream name, meaning and
default (`project`, `base_branch`, `verify_commands`, `model_tier`,
`merge_policy`, `migrations`, `rebuild_cap`, `usage_pause_pct`,
`max_dispatches`, `no_progress_cycles`, `block_rate_alert_pct`, `target`,
`collect`, `refine`, `notifications`, `columns`, `human_approves_merge`,
`timezone`, `description`, `version`, `calibration` until the audit decides).

| Key | Change |
|---|---|
| `worker_backend`, `codex`, `usage_fallback`, `truth_gate`, `truth_threshold` | Removed |
| `host` | Renamed `harness` |
| `max_workers` (global) | Moved to `roles.<role>.max_workers`, default 1 |
| `labels` (upstream: fixed informational list) | Replaced by the skill routing map `labels.<label>.<role>` (FR-CFG-3); card paths stay fixed (README domain model) |
| `paths.runs_dir` | Fixed at `.drover/runs` (metrics.md FR-MET-3); removed from config |
| `roles.<role>.skills`, `.model`, `.harness`, `.template` | New (FR-CFG-3, FR-CFG-6). `model` overrides `model_tier` for that role |
| `roles.review.second_opinion`, `.second_opinion_model` | New (roles.md FR-REV-9, FR-REV-10) |
| `commit_style` | New (conventions.md FR-CNV-1) |
| `evidence` | New (conventions.md FR-EVD-1) |
| `metrics.commit_email` | New (metrics.md FR-MET-11) |
| `hc.enabled`, `hc.view` | New (runner.md FR-WRK-3, FR-WRK-9) |

## Harnesses

- **FR-HRN-1** Each harness shall be described by one file
  `harnesses/<name>.json`: headless worker command, model flag, skill
  folders, hook install method, optional usage probe.
- **FR-HRN-2** Adding a harness shall need a new harness file and no change
  to the runner, gate or roles.
- **FR-HRN-4** The harness file shall declare the permissions a headless
  worker needs (commands, tools, paths), and drover shall pass them at
  start. A worker shall never wait on an approval prompt; any command a
  role's rules tell it to run shall be covered. (Stage 0: upstream's first
  worker command needed approval, so every headless worker quit.)
- **FR-HRN-3** v1 shall ship `claude` only. Codex, OpenCode and
  Antigravity are deferred; each needs its headless command, skill folders,
  hook install and permission enforcement confirmed first (upstream notes
  OpenCode v2.0.22 did not enforce `external_directory: deny`).
