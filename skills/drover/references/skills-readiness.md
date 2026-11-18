# Skill readiness before dispatch

Run `drover skills-ready --root <project> --host
<claude|codex> --cards <cards.json>` (`--config` defaults to the repo's `.drover/config.json`) before any claims or dispatch writes. The cards
array carries number, status, labels and literal issue body; hydrate absent
bodies read-only with the guarded GitHub reader (`--hydrate` does this).
For a worker boundary add `--lane build|qa|review`. Before using a custom helper
the card did not route, add `--require <exact-name>`; repeat when the execution host changes.

Load `resolved_path` files from the JSON report. Use only its declared fallback
IDs and source instructions. Exit 10 names missing required skills: return
`status=halted`, detail beginning `skills-readiness:`, preserve card state,
claims, worktrees and approval evidence, repair files and restart explicitly.
Exit 64 means malformed input/catalog; halt and repair it. GitHub read exit 79
keeps its existing halt policy. Verification-readiness remains exit 9.

The config owns the routing (specs/config.md FR-CFG-3): a worker's skills are
`roles.<role>.skills`, plus `labels.<label>.<role>` for each card label, plus the
issue's `Skills:` line outside code fences (Build, or the first lane of a `qa` card),
merged. A config without `roles` or `labels` gets that half from
`lib/drover-routing.json`, the routing onboard writes. Name aliases and the
fallbacks for `ponytail:*` stay in the script. Every selected skill must exist in
the harness's skill folders (`harnesses/<name>.json` → `skill_dirs`); a missing one
halts with its name and the folders searched (FR-CFG-4), before any claim.
Setup uses `--profile setup` for the nine core skills and every routed skill;
a selected chain can be runnable while the setup union is incomplete.
The headless runner checks the card itself and hands the worker the resolved
paths in its prompt, so a headless worker runs no readiness command.

Claude sees project/user `.claude/skills` and enabled, scoped plugin records.
Codex sees `.agents/skills` from the current directory through its owning Git
root, user/active Codex roots, and
authoritative catalog entries. Its core `.claude` direct reads are supported;
helper files in `.claude` alone are not Codex availability. Symlinks are valid
when their target is a readable, nonempty regular `SKILL.md`. A lockfile,
cache directory or another host's install is not evidence.

`--home` isolates home discovery; `--catalog` supplies the enabled active
inventory. Launchers accept `DROVER_SKILLS_HOME` and
`DROVER_SKILLS_CATALOG`. Catalog entries name exact paths, host and
user/project scope, with project_root for project entries. They grant no
fallback or mutation authority. Read-only checks and dispatch install nothing.
Explicit onboard `fix` retains helper repair and re-resolves files afterward.

Codex native `[[skills.config]]` disabled paths also apply after symlink
resolution. Unsupported activation syntax requires an authoritative active
catalog; only its exact enabled paths may resolve until native syntax is
understood. Discovery and activation follow [Codex skill documentation](https://learn.chatgpt.com/docs/build-skills).

Project `.codex/config.toml` activation depends on trust and configuration
layers. When a scoped project config contains skill activation, provide the
host's effective active catalog; the resolver uses only its exact enabled
paths rather than guessing trust or layer precedence. Ordinary project
configs with no activation leave discovery unchanged. See [Codex project
configuration](https://learn.chatgpt.com/docs/config-file/config-basic).
