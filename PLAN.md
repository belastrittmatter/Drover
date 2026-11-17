# drover: implementation plan

Requirements live in [specs/](specs/README.md); this file is the build order.
Each stage names the spec files its agent should read.

Fork of super-board. A GitHub Project board moves cards
Ready -> Building -> QA -> Review -> Done; headless workers build, test,
review and merge through a gate. Leaner, harness-independent, configurable
per project, with an optional hc add-on to watch and steer every worker.

Each stage ends with `bash tests/run-safety.sh` green and is its own commit.

## Decisions

See [specs/README.md](specs/README.md) section 2. Not repeated here.

## Target layout

```
drover/
  bin/drover                 # entry point: onboard|lint|status|run|stop|skills|evidence
  lib/                       # runner, card, gate, preflight, status, worker, harness
  hooks/                     # guard hooks (installed per harness)
  skills/                    # drover, drover-build, drover-qa, drover-review,
                             # + the five extras (specs/extras.md FR-EXT-1)
  harnesses/<name>.json      # worker command, skill dirs, hook install, usage probe
  templates/hc/              # hc templates per role (add-on)
  presets/                   # starting configs written by onboard (optional, later)
  tests/
```

Project side: `.drover/config.json` only (worktrees stay under the
harness-neutral `.drover/worktrees/`).

Config shape: [specs/config.md](specs/config.md).

## Stage 0: baseline (done)

Result in `docs/baseline.md`: upstream suite green (64 suites), new tests
for FR-WRK-1, stop, resume and the open-card side of Done, idle round 3
GitHub calls (NFR-6 holds). The reference run on `claude-p` failed because
its headless workers' first command needed approval, so each quit within
seconds and the card was re-dispatched until the runaway gate stopped the
run. Locks were reaped only after workers exited; FR-WRK-1 stays as a
preventive rule. Follow-ups: FR-HRN-4 (stage 5), FR-RUN-13, FR-WRK-10,
FR-BRD-3 (stage 6).

## Stage 1: audit (1-1.5 days)

Deliverable: `docs/audit.md`, one row per skill, script, hook, workflow and
test suite: what it does, who calls it, verdict (keep / improve / remove),
reason, and what the verdict breaks.

- Skills: all 9 are kept (specs/extras.md FR-EXT-1). Verdict per skill is
  keep or improve: is its prompt longer than its job needs, does it depend
  on outside skill packs. Removal only for an observed reason, and you
  approve it.
- Scripts: all 31; mark which belong to the dropped backends and which
  features must be ported (pre-flight wave, classifier, usage guard).
- Machine-read conventions: every place a script greps an emoji, a
  `[role] [report]` prefix or a marker comment, so stage 4 changes producer
  and parser together.
- Outside dependencies: every skill named in `REGISTRY` and the skill files
  (mattpocock-skills, ponytail, vitest, playwright, security-audit).
- Known removal candidates to confirm or reject: `diagrams/`, `docs/assets/`,
  pages/release CI, `RELEASE-NOTES.md`, `RELEASING.md`, `get.sh`, `evals/`,
  calibration, approval, readme-sync, agents-md, `pre-commit-readme.sh`.

Spec: specs/extras.md, specs/README.md NFR-8.

Exit: every file has a verdict; you sign off on the remove list.

## Stage 2: cut (0.5-1 day)

- Delete what the audit marked remove, plus its tests.
- Do not delete the `workflow` / Codex backends yet (stage 6 ports them first).
- Grep each deleted path; fix every caller in scripts, skills, `install.sh`,
  `CLAUDE.md`, plugin manifests.

Spec: every "Preserved" table in specs/ must still pass.

Exit: no reference to a deleted file; tests green.

## Stage 3: rename to drover (0.5 day)

- `super-board-*` -> drover names; skills `super-build|qa|review|collect` ->
  `drover-build|qa|review|collect`; `super-board` skill -> `drover`.
- Branches `issue-N-slug` keep their shape; worktrees move to
  `.drover/worktrees/`.
- Plugin manifests: name, owner, homepage; drop the upstream branding and
  community links.
- `LICENSE`: keep the original copyright line (MIT requires it), add yours.

Spec: every "Preserved" table in specs/ must still pass; specs/config.md FR-CFG-1.

Exit: `grep -rniE 'super-?board|super-(build|qa|review)|erictech|skool' .`
hits only LICENSE; tests green.

## Stage 4: conventions (1 day)

- Commit style detection at onboard: read the last ~50 subjects on the base
  branch, classify (conventional / bracket-tag / plain imperative /
  other), write `commit_style` to config. Workers get the style and 3 real
  example subjects from the repo in their prompt.
- `writing-standard.md`: rewrite around `commit_style`; replace the emoji
  regex in `tests/test_writing_format.py` with per-style checks.
- Blocked reasons: the plain tags in specs/board.md FR-BRD-6 in `block-template.md` and `status.md`; update the grouping
  parser in the status script.
- Strip emoji from every drover-written comment, log line and PR body;
  drop the footer boilerplate in the PR body script. Manifest lines keep
  their format (specs/metrics.md FR-MET-2).

Spec: specs/conventions.md, specs/board.md FR-BRD-6.

Exit: no emoji in `lib/`, `skills/`, `hooks/` output; parser tests cover the
new tags; tests green.

## Stage 5: CLI core + harness layer (2 days)

- `bin/drover` entry point; scripts move to `lib/`. Install = put `bin` on
  PATH (brew tap or `install.sh` that only links the CLI).
- `harnesses/claude.json` (worker command, skill dirs, hook install,
  usage probe). Other harnesses are later entries; a missing entry halts
  with a clear message.
- `drover skills install` copies or links skills into the harness's folders
  (`.claude/skills` or plugin).
- `drover onboard` writes `.drover/config.json` only, asking for the
  evidence location (branch name, or PR branch and path); hooks are
  installed through the harness entry.
- Skill routing from config: role + label + per-issue `Skills:` merged,
  each checked to exist for the harness before dispatch (reuse the
  readiness check from `super-board-skills-ready.py`, minus `REGISTRY`).
  Onboard writes upstream's current routing as the starting config.
- Headless permissions in `harnesses/claude.json`, passed at worker start;
  test that every command in the role rules is covered (FR-HRN-4).

- `config-schema.json` per specs/config.md "Keys"; validation at every
  command start (FR-CFG-2).

Spec: specs/config.md.

Exit: a fresh repo onboards with only `.drover/config.json` added; tests green.

## Stage 6: runner (2.5 days)

- `lib/worker.sh start|alive|stop` with the `process` backend; route every
  `nohup claude -p`, `kill -0` and `pgrep` site in the runner and stop
  scripts through it. Lock files store `HANDLE=` (read old `PID=` too).
- `max_workers` per role: replace the single `BUILD_PID`/`QA_PID`/
  `REVIEW_PID` slots with per-role sets. Downstream-first priority stays.
- Port from the workflow backend, then delete it and the Codex scripts:
  - pre-flight wave: one cheap agent per batch of up to 5 Ready cards,
    before any builder claims;
  - classifier: fast model picks feature/bug and complexity (model tier),
    and adds missing risk labels with a one-line reason (FR-RUN-7);
  - usage pause: per harness (`usage_probe` in the harness entry; Claude's
    status-line `rate_limits` today), checked before each dispatch.
- Failed-dispatch halt (FR-RUN-13), one-tick grace before stopping a
  worker whose card left its lane (FR-WRK-10), closed-without-merge goes to
  Blocked not Done (FR-BRD-3); each with a test.
- Run manifest and worker logs move to git-ignored `.drover/runs/`, format
  unchanged (FR-MET-1/3).
- FactoryBench attempt per card in the runner, OTLP env per worker
  (specs/metrics.md FR-MET-4 to 12): no-op without `factorybench` and
  `FACTORYBENCH_URL`; tests with a fake `factorybench`.

Spec: specs/runner.md, specs/metrics.md.

Exit: the stage-0 card runs again and shows up in FactoryBench as one
attempt with its lanes, next to the upstream baseline; two Ready cards with disjoint files
build in parallel with `max_workers: 2`; tests green.

## Stage 7: reviewer rework + context packs (2 days)

- Fold the history check (`git log`/`git blame` on changed hunks, reverted
  attempts) into the main reviewer steps.
- Replace adversarial mode with `second_opinion: off | risky | always`.
  Risky = risk label (owner or classifier), diff over `auto_max_lines`,
  or a diff the merge policy classes as money/auth/schema. The second reviewer gets ACs + diff only, returns
  file:line findings with class and severity. A Blocker it finds that the
  main reviewer missed bounces or blocks the card. No score.
- `second_opinion_model`: must differ from the reviewer's model; the
  report names both.
- Remove `truth_gate`, `truth_threshold` and the verbatim subagent brief.
- Enforce review memory in code (specs/roles.md FR-REV-5, 6, 8; specs/gate.md exit 6): `drover review-report
  get|put` loads the previous report, rejects a new one that drops a prior
  finding id, uses an unknown status, or marks `fixed` without a `file:line`
  in a file that changed since the last reviewed head;
  records the reviewed head SHA. The gate refuses (exit 6) unless the latest
  report names the head being merged, says `merge-ready`, and has no open
  Blocker.
- Bug cards: reviewer reruns the builder's reproduction test at the base
  commit and bounces if it passes there (FR-BLD-7).
- Review report lists test-file changes by non-QA commits since QA's last
  pass, visibility only (FR-REV-12).
- Context packs (FR-CTX): `drover context <role> <issue>`, per-role rules
  files split out of upstream's `run.md`, `--claims` gated on recorded
  hypotheses, tests asserting each pack's contents. Adds about 1 day.

Spec: specs/README.md, specs/roles.md, specs/gate.md.

Exit: a risky test PR gets a second opinion from a different model; a clean
small PR does not; a bug card whose reproduction test passes on base is
bounced; tests green.

## Stage 8: evidence (0.5 day)

- `drover evidence push <issue> <version> <files...>`: commits to the
  location in `evidence` (orphan branch, default `drover-evidence`, or the
  PR branch under `path`), pushes, prints the SHA-pinned raw URLs for the
  comment.
- QA skill always goes through it.
- `drover evidence prune --older-than <days>` (later, when the branch grows).

Spec: specs/conventions.md FR-EVD.

Exit: in `branch` mode a UI card's QA comment shows inline screenshots and
the PR diff has no images; in `pr` mode the images are under the configured
path.

## Stage 9: hc add-on (1-2 days)

- Toggle: `hc.enabled` in config or `drover run --hc`. If `hc` is missing
  or its server is down, log one line and use `process`.
- `templates/hc/drover-{build,qa,review}.md`, installed to
  `~/.config/herdr-core/templates/`; a role may name its own template
  (e.g. `drover-build-ios`).
- `worker.sh` hc backend: `hc run --bg --name drover-<role>-<issue>`,
  labels `drover.issue` / `drover.role`; check `first_message.sent`, never
  resend blindly; done signal via `hc send <runner>`; the board column stays
  the source of truth for completion.
- Bounce to a live builder: send review findings to its terminal instead of
  starting a cold worker.
- `stop`: `hc interrupt`, grace, `hc kill`, `hc rm`, only `drover.*`
  terminals this runner started.
- Tests with a fake `hc` on PATH.

Spec: specs/runner.md FR-WRK-3/4/5/7, specs/board.md FR-STS-2.

Exit: one card end to end with `--hc`; attach and steer mid-build; stop
leaves no `drover.*` terminals.

## Stage 10: herdr-app view (0.5 day)

- `hc.view: true` or `--view`: one `herdr-app` space per repo, a tab per
  live worker. Skipped when herdr-app or its window is absent.

Spec: specs/runner.md FR-WRK-9.

## Stage 11: skill improvements (ongoing)

- Apply the audit's "improve" verdicts: shorten the QA, review and build
  prompts, remove upstream-specific references, decouple from outside skill
  packs where the config now names skills.
- QA crawl mode: own branch, PR per iteration, merged through the gate
  (FR-EXT-3).
- Each change is checked by one real card for the affected role.

Spec: specs/extras.md.

## Stage 12: docs (0.5 day)

- README: what it does, install, config reference, harnesses, `--hc`.
- `CLAUDE.md` / `AGENTS.md` for agents working on drover itself.

## Rough total

About 12.5-15.5 working days for stages 0-10; stage 11 continues with use.

## Later

Deferred items are rows in the decisions table in
[specs/README.md](specs/README.md).
