# Stage 1 audit

Fork point `d0b7d5a`. Audited at `b003464` (fork point plus stage 0 docs and
tests; no product code changed since the fork). Spec: [specs/extras.md](../specs/extras.md),
[specs/README.md](../specs/README.md) NFR-8. Plan: [PLAN.md](../PLAN.md) "Stage 1: audit".

## How to read this

- **Verdicts**: `keep` (no change planned), `improve` (keep, change named in
  the reason), `remove` (delete in stage 2, needs sign-off), `remove@6`
  (dropped backend: Codex scripts and the `workflow` backend; deleted in
  stage 6, not stage 2, per PLAN stage 2), `port-then-remove` (a feature in it
  must move into the runner first; deleted in stage 6).
- **Who calls it**: `file:line` of each caller. Callers inside `diagrams/`
  and `RELEASE-NOTES.md` are prose or generated pages and are left out
  unless they are the only caller. Tests are listed separately as `tests:`.
  `run-safety` means `tests/run-safety.sh:12` (globs every
  `tests/test-*.sh` and `tests/test_*.py`).
- `install.sh:229` is the list of scripts copied into `.claude/bin`, and
  `scripts/super-board-setup.py:76-80` is the same list for onboard's `fix`.
  Every script in `scripts/` except `readme-sync.py` is on both lists; that
  caller is not repeated in each row.
- "unverified" means the caller or behaviour was not confirmed by reading
  the code.

## Counts

| | Tracked entries | Regular files | Lines (text) | Binary |
|---|---|---|---|---|
| Fork point `d0b7d5a` | 358 | 356 | 88,434 | 22 |
| Audited `b003464` | 371 | 369 | 91,246 | 22 |

The two non-regular entries are symlinks, `evals/_stubs/bin/git` and
`evals/_stubs/bin/python3`. NFR-8's "356 files" is the regular-file count.
Lines are `git diff --numstat` against the empty tree (binary files count 0).

## 1. Skills

FR-EXT-1/2: every skill is keep or improve. Line counts are SKILL.md / all
Markdown in the skill folder.

| Path | What it does | Who calls it | Verdict | Reason | What the verdict breaks |
|---|---|---|---|---|---|
| `skills/super-board/` (51 / 3,890 lines, 21 files) | Entry skill: onboard, lint, status, run, stop. References hold the runner rules (`run.md` 822 lines), onboarding (`onboard.md`), templates and the writing standard | user (`/super-board`); `scripts/super-board-run.sh:337-339` prompts point workers at `references/run.md` | improve | 3,890 lines of reference text for five verbs. `run.md` and `run-workflow.md` (421) describe two backends; `onboard.md` describes the AGENTS.md step, plugin install, Codex/OpenCode hosts and upstream's upgrade path. 27 upstream-specific references (dated incident notes such as `run.md:794` "2026-05-21 production run", `Eric` as owner). Depends on mattpocock-skills (`lint.md`, `design-preflight.md`) and security-audit (`onboard.md:18-19`) | Stage 6/11 edits only. `calibration.md` and `agents-md-block.md` go in stage 2 (see section 3). `run-workflow.md` is port-then-remove (section 8) |
| `skills/super-build/` (303 / 669, 5 files) | Builder lane: worktree, test-first change, draft PR | `scripts/super-board-run.sh:337`; `workflows/super-board-wave.js` (lane prompts) | improve | Two modes in one skill: board lane and a standalone `claude -p` dispatcher (`scripts/super-build-dispatch.sh`, SKILL.md:112-200) that duplicates the runner's dispatch. Hard-wired to mattpocock-skills (`decision-policy.md`, `worker-preamble.md`) and ponytail. Upstream-specific: `Next: Eric` (SKILL.md:148,154) | none |
| `skills/super-qa/` (722 / 1,893, 11 files) | Tester lane plus standalone crawl mode: Playwright evidence, regression test, bug filing | `scripts/super-board-run.sh:338`; user (`/super-qa`) | improve | Longest lane prompt (SKILL.md 722 + `iteration-preamble.md` 824). Upstream stack assumptions: Supabase env-file fallback and `erictech.ca` default domain (SKILL.md:25), `docs/super-board/runs/` evidence path (SKILL.md:666). Names vitest, playwright, testing-strategy, verification-before-completion, mattpocock-skills. Crawl mode commits to the active branch (FR-EXT-3 changes this) | none |
| `skills/super-review/` (452 / 484, 2 files) | Reviewer lane: hypotheses, review memory, report, merge through the gate | `scripts/super-board-run.sh:339`; user (`/super-review`) | improve | Prompt carries both the lane and standalone review. `Next: Eric` (SKILL.md:150,161,165). Depends on mattpocock-skills:code-review/codebase-design and ponytail:ponytail-review. Stage 7 replaces code-grounder/historian sub-agents (spec decision) | none |
| `skills/super-collect/` (254 / 429, 11 files) | Finds problems (Sentry, PostHog, GitHub, PRs, custom sources), verifies, files Backlog tickets or hands to `/to-spec` | user (`/super-collect`) | improve | Hand-off is built on Matt Pocock's `/to-spec`, `/to-tickets`, `/implement-spec` (`scripts/collect_handoff.py:57,65`); falls back to Backlog when missing, which is the drover-relevant path | none |
| `skills/pr-security-review/` (202 / 226, 3 files) | Security review of one PR diff | `skills/super-qa/SKILL.md:519`; `scripts/super-board-skills-ready.py:30` (QA boundary skill) | keep | Small, standalone, no outside skill. FR-EXT-1 keeps it as a QA skill when configured | none |
| `skills/git-sync/` (96 / 117, 2 files) | Commit per logical change, merge-pull, push | user only (`/git-sync`); named in `skills/super-board/references/agents-md-block.md:17` | keep | Standalone, small, no outside skill | none |
| `skills/visual/` (52 / 350, 7 files) | Renders one self-contained HTML page (recap, plan, skill map) | user only (`/visual`) | keep | Standalone. Its tests read `diagrams/skill-map.json` (see section 3, diagrams row) | none |
| `skills/ui-refine-loop/` (99 / 531, 14 files) | Impeccable check-fix loop on one page, ends in a draft PR | user only (`/ui-refine-loop`) | improve | Requires the Impeccable skill (`references/routing.md`, `scripts/refine-setup.sh`) and Claude's Workflow tool (`SKILL.md:57`, `workflows/ui-refine-loop.js`), so it is Claude-only. The grill names `Eric` (`references/grill.md`) | none |
| `skills/families.json` | Groups skills (you type / board runs) with one-line briefs | `scripts/super-board-readme-sync.py:2,10`; `skills/visual/scripts/visual.py` skillmap (`skills/visual/SKILL.md:35`) | keep | Still read by `visual.py skillmap` after readme-sync goes | none |

## 2. Scripts (`scripts/`, 31 files)

Backend column: **core** = used by the `claude-p` runner or by every
backend; **wf** = `workflow` backend only; **codex** = Codex host only;
**onboard** = install/onboard; **extra** = a standalone skill.

| Path | Lines | Backend | What it does | Who calls it | Verdict | Reason | What the verdict breaks |
|---|---|---|---|---|---|---|---|
| `super-board-run.sh` | 781 | core | Headless `claude-p` runner: tick loop, claim, dispatch, reap, zombie watchdog | user via `skills/super-board/references/run.md:25`; `scripts/super-board-stop.sh:11` (kills it). tests: `test-run-gates.sh`, `test_run_rounds.py`, `test_status_parse.py` and 7 more | improve | Becomes the drover runner (stage 6): worker interface, `max_workers`, FR-RUN-13, FR-BRD-3, plus the ports listed in section 8 | none |
| `super-board-stop.sh` | 250 | core | Graceful stop: comment, release claims, kill workers | `references/stop.md:97`; `references/run-workflow.md:315`. tests: `test-codex-stop.sh`, `test_run_rounds.py` | improve | Route kills through the worker interface (FR-WRK-1); drop Codex branch | none |
| `super-board-card.sh` | 214 | core | Cheap GraphQL board reads and card moves | `super-board-run.sh:126`; `super-board-wave-plan.sh:104`; `workflows/super-board-wave.js:134`; `skills/super-build/SKILL.md:20,55`; `skills/super-qa/SKILL.md:644`; `skills/super-review/SKILL.md:297`; `references/run.md:260,771`. tests: `test-card.sh` | keep | FR-BRD-1 | none |
| `super-board-github-read.py` | 425 | core | Bounded read-only `gh` reads; exit 79 halts | `super-board-run.sh:34`; `super-board-gh-guard.sh:27`; `super-board-card.sh:75`; `super-board-deps.sh:84`; `super-board-preflight.sh:72-80`; `super-board-merge-gate.sh:114`; `super-board-pr-body.sh:77`; `super-board-approval.py:19`; `super-board-collect-state.py:29`; `super-board-skills-ready.py:423`; `super-board-wave-plan.sh:94`; `super-board-lane.sh:148`. tests: `test_github_reads.py` | keep | Shared halt contract (CLAUDE.md "Worker rules") | none |
| `super-board-gh-guard.sh` | 104 | core | Worker-side rate-limit checks (`sb_gh_guard_check`, `sb_gh_quota_merge`) | `super-board-run.sh:278`; `super-board-preflight.sh:66-68`; `super-board-throttle.sh:43`; `references/run.md:259`; `references/rate-limit-etiquette.md:53`. tests: `test_github_reads.py` | keep | FR-RUN-9 | none |
| `super-board-merge-gate.sh` | 372 | core | Merge lock, head pin, verify against current base, squash merge | `references/run.md:471`; `workflows/super-board-wave.js:105,125`; `skills/super-review/SKILL.md` merge protocol. tests: `test-merge-gate.sh` | improve | FR-MRG-1..4 preserved; stage 7 adds the review-report check to exit 6 | none |
| `super-board-merge-policy.py` | 209 | core | Decides who merges (money/auth/schema/size) and which migrations run | `super-board-merge-gate.sh:241,330`. tests: `test-merge-gate.sh` | keep | FR-MRG-3 | none |
| `super-board-approval.py` | 272 | core | Head-pinned human approval: posts the request, verifies a trusted `done` reply | `super-board-merge-gate.sh:249,255`; `super-board-deps.sh:195`. tests: `test_approval.py`, `test-setup.sh` | keep | Removal candidate rejected, see section 3 | none |
| `super-board-pr-body.sh` | 164 | core | Rewrites one `<!-- sb:<block> -->` PR body block | `references/run.md:148,151,215`; `references/writing-standard.md:87-88,168`; `skills/super-build/SKILL.md:272`; `workflows/super-board-wave.js:154`; `super-board-lane.sh:180`. tests: `test-pr-body.sh` | improve | Block markers are a machine-read convention (section 9); stage 4 | none |
| `super-board-preflight.sh` | 171 | core (wf wiring) | Mechanical pre-flight: already merged, open PR, overlap, duplicate fingerprint | `workflows/super-board-wave.js:242`; `references/run.md:262` (Builder pre-flight checklist); `super-board-lane.sh:186`. tests: `test-preflight.sh` | keep | FR-RUN-5/6. The script stays; the batch agent around it is ported (section 8) | none |
| `super-board-deps.sh` | 195 | wf | Dependency graph from `blocked-by:` lines and `## Blocked by` sections | `super-board-wave-plan.sh:127` only. tests: `test-deps.sh` | improve | The `claude-p` runner never calls it, so the Blocked sweep (FR-BRD-5) and dependency filtering exist only in the `workflow` backend. Stage 6 must wire it into the runner | Nothing in stage 2. Missing from PLAN stage 6 port list (section 12) |
| `super-board-wave-plan.sh` | 197 | wf | Plans a wave: free Ready cards, Blocked sweep, stranded, resume (approval), flag | `references/run-workflow.md:181`; `workflows/super-board-wave.js:4,16`. tests: `test-wave-plan.sh` | port-then-remove | Port `sweep` (FR-BRD-5), the open-blocker filter, `resume` and `refreshApproval` (approved 🙋 cards back to Review), `flag`. `stranded` already exists in the runner (`super-board-run.sh:527`) | `test-wave-plan.sh` is the listed test for FR-RUN-3 and FR-BRD-5; it must be rewritten against the runner |
| `super-board-throttle.sh` | 69 | wf | Steps wave width down after a GitHub-limit wave | `references/run-workflow.md:272`. tests: `test-throttle.sh` | remove@6 | Wave width does not exist in the runner; `max_workers` per role replaces it | `config.max_workers` write-back (`config-schema.json:301`) |
| `super-board-usage.sh` | 120 | wf | Claude plan-usage guard (`record` from status line, `check` against `usage_pause_pct`) | `references/run-workflow.md:145,175`; `docs/super-board/README.md:87,128`. tests: `test-usage.sh` | improve | The usage pause is a feature to port (FR-RUN-8): this script becomes Claude's `usage_probe` and the runner calls it before each dispatch | none |
| `super-board-skills-ready.py` | 488 | core | Resolves the skill chain per card and lane; exits 10 if a skill is missing | `super-board-run.sh:328,342`; `workflows/super-board-wave.js:140`; `super-board-setup.py:178`; `super-board-lane.sh:153,174`; `super-board-codex-wave.sh:72`; `skills/super-build/SKILL.md:77`; `skills/super-build/scripts/super-build-dispatch.sh:108`; `skills/super-collect/scripts/collect_handoff.py:43`. tests: `test_skills_readiness.py`, `test_skill_dispatch.py` | improve | Replace hardcoded `REGISTRY` (`:20-42`) with config routing (FR-CFG-3/4) | none |
| `super-board-verification-ready.py` | 45 | core | Checks `verify_commands` are usable; exit 9 | `super-board-run.sh:100,647`; `super-board-merge-gate.sh:146`; `super-board-wave-plan.sh:92`; `super-board-setup.py:321`; `super-board-lane.sh:131`; `super-board-codex-wave.sh:62`. tests: `test_verification_readiness.py` | keep | FR-RUN-1, gate exit 9 | none |
| `super-board-status.py` | 755 | core | Read-only snapshot: columns, live workers, Blocked reasons | `references/status.md:10-21`; `.github/workflows/cross-platform.yml:38,47,61`. tests: `test_status_parse.py` | improve | Parses run.sh log lines and Blocked reason emoji (section 9); FR-BRD-6 and FR-STS-2 | none |
| `super-board-setup.py` | 1,061 | onboard | Onboard's deterministic half: checks, fix, board rank/migrate, branch, upgrade path | `references/onboard.md:47-49`. tests: `test-setup.sh`, `test_upgrade_recovery.py`, `test_skill_dispatch.py` | improve | Becomes `drover onboard` (stage 5). Upstream upgrade/migration paths and `npx skills add` for outside packs (`:82,85`) do not apply to drover | none |
| `super-board-settings.py` | 114 | onboard | Merges permission rules and hook blocks into `.claude/settings.json` | `install.sh:281`; `references/onboard.md:51,325,375-376`. tests: `test-settings.sh` | keep | Needed to write FR-HRN-4 permissions until the harness file passes them at start | none |
| `super-board-env-check.sh` | 57 | onboard | Says whether env keys are set, never their values | `hooks/guard-secrets.py:27`; `references/onboard.md:37,144`; `references/agents-md-block.md:26`. tests: `test-env-check.sh` | keep | The one sanctioned way past `guard-secrets` | none |
| `super-board-project-verifier.py` | 222 | extra (QA) | Inspects a project verifier map, picks affected features | `skills/super-qa/references/project-verifier.md:34,36`. tests: `test_project_verifier.py` | keep | Used by super-qa | none |
| `super-board-collect-state.py` | 184 | extra (collect) | Collector lock and digest receipts | `skills/super-collect/scripts/super-collect-file.sh:106`; `skills/super-collect/references/scheduled-intake.md:61`. tests: `test_collect_windows.py` | keep | Used by super-collect | none |
| `super-qa-file-bug.sh` | 294 | core/extra | Files one QA finding as an issue (dedupe by fingerprint) | `skills/super-collect/scripts/super-collect-file.sh:136,226,255`; `skills/super-qa/references/iteration-preamble.md:627`; `references/run.md:113`. tests: `test-file-bug.sh` | improve | Emoji badges in titles (`:187-188`) conflict with FR-CNV-2/3; header names upstream's "Super Ultimate QA project board" (`:2`) | none |
| `super-review-file-refactor.sh` | 260 | core/extra | Files a refactor ticket from review (dedupe by fingerprint) | `skills/super-review/SKILL.md:114,334`; `skills/super-collect/scripts/super-collect-file.sh:249`. tests: `test-file-refactor.sh` | improve | Emoji title `♻️ [refactor]` (`:192`), FR-CNV-2/3 | none |
| `super-board-host.sh` | 60 | codex | Detects host: claude / codex / opencode | `install.sh:87`; `references/run-workflow.md:24`; `references/onboard.md:370`. tests: `test-host.sh`, `test-hosts-doc.sh` | remove@6 | Replaced by config `harness` (FR-HRN-1/3) | `install.sh:87` Codex link; `docs/hosts.md` |
| `super-board-lane.sh` | 258 | codex | One headless lane on a non-Claude host (`--backend codex`) | `super-board-codex-lane.sh:3`; `super-board-codex-wave.sh:34`. tests: `test-codex-lane.sh`, `test_codex_lane_schema.py` | remove@6 | Codex deferred (FR-HRN-3); the harness file replaces per-host launchers | none in core |
| `super-board-codex-lane.sh` | 3 | codex | Shim to `super-board-lane.sh --backend codex` | `references/run-workflow.md:154,346`. tests: `test-codex-lane.sh` | remove@6 | As above | none in core |
| `super-board-codex-wave.sh` | 279 | codex | Runs a Codex wave with the workflow's result contract | `references/run-workflow.md:33,119,347`. tests: `test-codex-wave-contract.sh`, `test-codex-stop.sh` | remove@6 | As above | none in core |
| `super-board-calibration.py` | 216 | wf | Eligibility/brief adapter for an externally installed calibration skill after a run | `references/calibration.md:61` only. `super-board-run.sh:779-780` reads the config key and logs "calibration skipped". tests: `test_calibration.py` | remove | See section 3 | `calibration` config key; `references/run.md:53-56`, `references/run-workflow.md:239,301-302`, `SKILL.md:39`, `README.md:71` text |
| `super-board-agents-md.py` | 343 | onboard | Writes and checks a managed `<!-- super-board:begin -->` block in a target's AGENTS.md | `install.sh:314`; `references/onboard.md:50,138,279,377,404,504`. tests: `test-agents-md.sh` | remove | See section 3 | onboard step 5; `install.sh:313-316` |
| `super-board-readme-sync.py` | 159 | dev | Regenerates README skill tables from SKILL.md + `families.json` | `.claude/settings.json:9` (PostToolUse hook); `hooks/pre-commit-readme.sh:16-17`. Not in `install.sh:229`. tests: `test-readme-sync.sh` | remove | See section 3 | README tables stop regenerating (README is rewritten in stage 12) |

## 3. Removal candidates (PLAN stage 1 list)

| Candidate | Callers (grep, excluding itself) | Decision | Reason |
|---|---|---|---|
| `diagrams/` (72 files, 46,023 lines, 20 binary) | `.github/workflows/pages.yml:4-53`; `CLAUDE.md:99-101`; `tests/test_pages_version.py:4,20,73`; `tests/test_visual_details.py:20`, `tests/test_visual_families.py:20`, `tests/test_visual_main_steps.py:11` (read `diagrams/skill-map.json`); `tests/test_visual_source_base.py:12,30` (string only, no file read) | **confirm remove**, except `diagrams/skill-map.json` moves to `tests/fixtures/` | Public marketing pages for upstream's GitHub Pages (`erictechpro.github.io`). Half the repo's lines. The three visual tests use `skill-map.json` as a real input; move it, do not delete it |
| `docs/assets/` (3 files) | `README.md:3,27`; `diagrams/_shared/maps.json:9`, `diagrams/index.html:231` (removed with diagrams) | **confirm remove** | Upstream logo and screenshots; README is rewritten in stage 12 |
| `.github/workflows/pages.yml` | none outside itself; publishes `diagrams/` | **confirm remove** | Nothing to publish once diagrams go |
| `.github/workflows/release.yml`, `.github/scripts/publish-release.sh` | `release.yml:35` calls `publish-release.sh`; `release.yml:18` reuses `cross-platform.yml`; `tests/test-release.sh` | **confirm remove** | Upstream's tag-to-release flow. drover v1 is installed from the repo (spec: "`drover` CLI on PATH"); no release channel is specified |
| `RELEASE-NOTES.md` (1,097 lines) | `README.md:192,210`; `RELEASING.md:8`; `docs/blocked-column-audit.html:701` | **confirm remove** | Upstream history; git log and the fork point keep it |
| `RELEASING.md` | `CLAUDE.md:97`; `README.md:211`; `.github/workflows/release.yml:4`; `.github/scripts/publish-release.sh:14` | **confirm remove** | Goes with release.yml |
| `get.sh` | `README.md:41,78`; `CLAUDE.md:6`; `install.sh:26` (`SUPER_BOARD_QUIET_NEXT`); `references/onboard.md:6,42,458`; `skills/super-collect/SKILL.md:165`; `skills/super-collect/scripts/collect_handoff.py:65` (message text); `.claude-plugin/plugin.json:4`. tests: `test-get.sh`, `test-install.sh:265` | **confirm remove** | `curl` installer from `EricTechPro/super-board/main`. FR-CFG-1 replaces copy-in installs. Its other job, `npx skills add` for mattpocock-skills and security-audit (`get.sh:176,192`), is duplicated in `super-board-setup.py:82,85` |
| `evals/` (54 entries, 1,374 lines) plus `hooks/dev/gate-skill-evals.py` | `.claude-plugin/plugin.json:21`; `.gitignore:40`; `.claude/settings.json:20` (Stop hook runs `gate-skill-evals.py`); `hooks/README.md:104-128`; `RELEASING.md:28`; `docs/super-board/README.md:337`; `skills/super-collect/README.md:53`; `tests/fixtures/collect/fixture_http.py:5` (comment). tests: `test-guard-hooks.sh:142-181` (gate-skill-evals cases), `test-install.sh:193` (asserts it is not installed) | **confirm remove** | Paid `claude plugin eval` cases; not in `run-safety`. Stage 11 checks skill changes with one real card instead. The Stop hook blocks the end of a turn in this repo whenever a skill changes |
| calibration (`scripts/super-board-calibration.py`, `references/calibration.md`) | see section 2 row; config key `calibration` (`config-schema.json:44`) | **confirm remove** | It only prepares a brief for a calibration skill that is not shipped. The `claude-p` runner never runs it (`super-board-run.sh:779-780` logs "skipped"). Spec "Cross-PR knowledge" asked for this audit: nothing here to build on. Drop the `calibration` key from the config (config.md "until the audit decides") |
| approval (`scripts/super-board-approval.py`) | `super-board-merge-gate.sh:249,255`; `super-board-deps.sh:195`. tests: `test_approval.py` (245 lines), `test-setup.sh:34` | **reject, keep** | It is how the owner "approves human-gated merges" (spec section 1 Users): gate exit 7 merges only after a trusted `done` reply pinned to the head. Removing it leaves exit-7 cards Blocked until the owner merges by hand |
| readme-sync (`scripts/super-board-readme-sync.py`) | `.claude/settings.json:9`; `hooks/pre-commit-readme.sh:16-17`; `CLAUDE.md:8`; `README.md:129,158`. tests: `test-readme-sync.sh` | **confirm remove** | Dev-only, not installed. Its tables list upstream skill names; stage 12 rewrites the README by hand |
| agents-md (`scripts/super-board-agents-md.py`, `references/agents-md-block.md`) | see section 2 row; `references/writing-standard.md:396`; `scripts/super-board-setup.py:78` | **confirm remove** | Writes a managed block into the target's AGENTS.md. SC-4 allows onboarding to add exactly one tracked file (`.drover/config.json`) |
| `hooks/pre-commit-readme.sh` | none (installed by hand: its own `:8`); `tests/test-readme-sync.sh:18,89-93` | **confirm remove** | Goes with readme-sync |
| also found: `docs/blocked-column-audit.html` (812 lines) | none | **remove** | Upstream audit page; no link to it anywhere |
| also found: `.claude/settings.json` (27 lines) | Claude Code loads it for this repo | **remove** | Its only two hooks run `readme-sync.py` and `gate-skill-evals.py`, both removed |

## 4. Hooks

| Path | What it does | Who calls it | Verdict | Reason | What the verdict breaks |
|---|---|---|---|---|---|
| `hooks/guard-worktree-path.py` | Blocks `git worktree add/move` outside `.claude/worktrees/` | `hooks/settings-snippet.json:9`. tests: `test-guard-hooks.sh`, `test_worktree_paths.py` | improve | NFR-1; path becomes `.drover/worktrees/` (stage 3) | none |
| `hooks/guard-delete-outside.py` | Blocks deletes outside the project | `hooks/settings-snippet.json:13`. tests: `test-guard-hooks.sh` | keep | NFR-1 | none |
| `hooks/guard-secrets.py` | Blocks reading/piping secret files | `hooks/settings-snippet.json:22`. tests: `test-guard-hooks.sh` | keep | NFR-1 | none |
| `hooks/guard-key-literals.py` | Blocks writing live-looking keys | `hooks/settings-snippet.json:31,42`. tests: `test-guard-hooks.sh` | keep | NFR-1 | none |
| `hooks/guard-protected-push.py` | Blocks direct/force push to protected branch (opt-in) | `hooks/settings-protect-main.json:9`; `install.sh:248`. tests: `test-guard-hooks.sh` | keep | NFR-1 | none |
| `hooks/cleanup-wt.py` | Removes merged worktrees/branches (session start, post-merge) | `hooks/settings-snippet.json:52`; `scripts/super-board-merge-gate.sh:356-357`. tests: `test-cleanup-wt.sh`, `test_worktree_paths.py` | keep | NFR-2 | none |
| `hooks/settings-snippet.json` | Hook wiring merged into target settings | `install.sh:299`; `scripts/super-board-setup.py:206`; `skills/visual/scripts/visual.py:677`. tests: `test-install.sh` | improve | Moves into the Claude harness file (FR-HRN-1 "hook install method") | none |
| `hooks/settings-protect-main.json` | Push-guard wiring | `install.sh:300,306`; `references/onboard.md:376` | keep | | none |
| `hooks/README.md` | Hook docs | `README.md` table (`:153-159`) | improve | Drop the gate-skill-evals section (`:104-128`) and upstream port notes (`:134-137`) | none |
| `hooks/pre-commit-readme.sh` | See section 3 | | remove | | |
| `hooks/dev/gate-skill-evals.py` | See section 3 (evals) | | remove | | |

## 5. Workflows

| Path | What it does | Who calls it | Verdict | Reason | What the verdict breaks |
|---|---|---|---|---|---|
| `workflows/super-board-wave.js` | `workflow` backend wave: classify, pre-flight batch, build, QA, review per card | `references/run-workflow.md:252` (Workflow tool `scriptPath`); `install.sh:236`; `scripts/super-board-setup.py:81`. tests: `test-wave-preflight.sh`, `test-wave-review-memory.sh`, `test-workflow-halt.sh`, `test_codex_lane_schema.py`, `test_run_allowlist.py`, `test-codex-wave-contract.sh` | port-then-remove | Port the pre-flight batch (`:206-284`, FR-RUN-5), the classifier (`:290-306`, FR-RUN-7) and the model ladders (`:169-184`) into the runner | `test-wave-preflight.sh` is the listed test for FR-RUN-5; rewrite against the runner |
| `workflows/ui-refine-loop.js` | ui-refine-loop's check-fix workflow | `skills/ui-refine-loop/SKILL.md:57`; `install.sh:236`; `scripts/super-board-setup.py:81`. tests: `test-ui-refine-loop-workflow.sh` | keep | ui-refine-loop is kept and needs it | none |

## 6. Skill-bundled scripts

| Path | What it does | Who calls it | Verdict | Reason |
|---|---|---|---|---|
| `skills/super-build/scripts/super-build-dispatch.sh` (191) | Standalone `/super-build`: one issue to one `claude -p` worker; success = a `[chore] loop: close #N` commit | `skills/super-build/SKILL.md:112,116`. tests: `test_skill_dispatch.py`, `test_worktree_paths.py`, `test_writing_format.py` | improve | Duplicates the runner's dispatch and greps commit subjects (section 9). Decide in stage 6 whether standalone `/super-build` goes through `drover run` |
| `skills/super-collect/scripts/collect_common.py` (107) | Shared helpers | imported by `collect_custom.py:43`, `collect_posthog.py:43`, `collect_prs.py:23`, `collect_sentry.py:36` | keep | |
| `skills/super-collect/scripts/collect_custom.py` (347) | Custom sources | `skills/super-collect/SKILL.md:68,73`; `references/onboard.md:52,339`. tests: `test_collect_custom.py` | keep | |
| `skills/super-collect/scripts/collect_handoff.py` (167) | Routes findings to Matt Pocock's chain or Backlog | `skills/super-collect/SKILL.md:161,173,182`. tests: `test_collect_handoff.py` | improve | Loads `super-board-skills-ready.py` (`:37-49`), which loses `REGISTRY` in stage 5 |
| `skills/super-collect/scripts/collect_posthog.py` (340) | PostHog source | `skills/super-collect/SKILL.md:64,82,95,98,143`; `references/onboard.md:349`. tests: `test_collect_fetchers.py`, `test_collect_baseline.py` | keep | |
| `skills/super-collect/scripts/collect_prs.py` (126) | Merged-PR source; reads review reports | `skills/super-collect/SKILL.md:66`. tests: same | keep | Parses `<!-- super-review:report -->` (section 9) |
| `skills/super-collect/scripts/collect_sentry.py` (229) | Sentry source | `skills/super-collect/SKILL.md:63,142`; `references/onboard.md:348`. tests: same | keep | |
| `skills/super-collect/scripts/super-collect-file.sh` (272) | Files one item into the holding column | `skills/super-collect/SKILL.md:187,200,203`. tests: `test-collect-file.sh`, `test-collect-baseline.sh`, `test_collect_intake_safety.py` | keep | |
| `skills/super-qa/scripts/runtime_compare.py` (370), `runtime_browser.mjs` (52) | Optional baseline-vs-change runtime scenarios | `skills/super-qa/references/runtime-verification.md:10,49,113`; `examples/runtime-demo/demo.py:67,69`. tests: `test_runtime_compare.py` | keep | |
| `skills/ui-refine-loop/scripts/refine-setup.sh` (224), `shoot.mjs` (238), `pr-shots.sh` (86) | Setup, screenshots, PR table | `skills/ui-refine-loop/SKILL.md:39-84`; `workflows/ui-refine-loop.js:22-47`. tests: `test-ui-refine-loop-setup.sh`, `test-ui-refine-loop-workflow.sh` | keep | |
| `skills/visual/scripts/visual.py` (905) | Detect, facts, render, check, skillmap | `skills/visual/SKILL.md:14-37`. tests: `test_visual_*.py` | keep | |

## 7. CI and installers

| Path | What it does | Who calls it | Verdict | Reason | What the verdict breaks |
|---|---|---|---|---|---|
| `.github/workflows/cross-platform.yml` | Runs `tests/run-safety.sh` on Linux/macOS (`:87`), status smoke on Windows (`:38-61`) | GitHub on push/PR; `release.yml:18` | improve | Keep as the CI. Drop `workflow_call` (only release.yml used it) and the Windows smoke (NFR-5 is macOS and Linux) | none |
| `.github/workflows/pages.yml` | see section 3 | | remove | | |
| `.github/workflows/release.yml` | see section 3 | | remove | | |
| `.github/scripts/publish-release.sh` | see section 3 | | remove | | |
| `install.sh` (344) | Copies the pack into `.agents/`, links host folders, merges hooks, refreshes AGENTS.md block | user; `get.sh` (unverified line); `references/onboard.md:6,42`. tests: `test-install.sh` | improve | FR-CFG-1: drover installs once on PATH, nothing copied into the project. Stage 5 replaces it | none in stage 2 except dropping `:313-316` (agents-md) |
| `get.sh` (205) | see section 3 | | remove | | |

## 8. Features to port before the `workflow` and Codex backends go (stage 6)

| Feature | Source | Runner today | Spec |
|---|---|---|---|
| Pre-flight batch (one cheap agent per batch of 5 Ready cards, verdict schema) | `workflows/super-board-wave.js:206-284`; `references/run-workflow.md:227-229`; script `super-board-preflight.sh` | Not run by `super-board-run.sh`. The Builder is told to run pre-flight inline (`references/run.md:250-262`) | FR-RUN-5/6 |
| Classifier (kind + complexity -> model tier) | `workflows/super-board-wave.js:290-306`, ladders `:169-184` | none | FR-RUN-7 (adds risk labels) |
| Usage pause | `references/run-workflow.md:145,175`; `scripts/super-board-usage.sh` | none | FR-RUN-8 |
| Blocked sweep (`blocked-by:` cleared -> Ready) | `super-board-wave-plan.sh` `sweep`; `super-board-deps.sh` | none | FR-BRD-5 (Preserved, source `run-workflow.md`) |
| Open-blocker filter (Ready card with open blockers is not dispatched) | `super-board-wave-plan.sh:8-11` | none: `top_card_in_column()` (`super-board-run.sh:202`) takes the top card | not stated; follows from FR-BRD-4/5 |
| Approved 🙋 card back to Review (`resume`, `refreshApproval`) | `super-board-wave-plan.sh`; `references/run.md:696-712` | none | owner approval (spec section 1); gate exit 7 |
| Review memory wiring (`prior_report`) | `workflows/super-board-wave.js:110-114` | prompt-only via `references/run.md` Reviewer step 3b | FR-REV (stage 7) |

## 9. Machine-read conventions

Every place code (or a prompt a worker must follow) reads a convention that
another file writes. Stage 4 changes writer and parser together (FR-CNV-4).

| Convention | Writer | Parser | Notes |
|---|---|---|---|
| `<!-- super-review:report -->` reviewer report marker | reviewer per `skills/super-review/SKILL.md:145,340`; `references/run.md:432`; `references/writing-standard.md:373-377` | code: `skills/super-collect/scripts/collect_prs.py:25`. prompts: `skills/super-review/SKILL.md:194-199` and `references/run.md:400` (`jq contains`); `workflows/super-board-wave.js:114`; `scripts/super-board-lane.sh:191` | Stage 7 adds the gate as a parser (FR-MRG-4 exit 6) |
| `<!-- sb:<block> -->` / `<!-- /sb:<block> -->` PR body blocks (status, problem, solution, ac, history, visual, risk) | `scripts/super-board-pr-body.sh:53,56` (skeleton); templates `references/run.md:165-210`, `references/writing-standard.md:90-98` | `scripts/super-board-pr-body.sh:105,108` | tests: `test-pr-body.sh` |
| `<!-- super-qa-meta ... -->` with a `fingerprint: <key>` line | `scripts/super-qa-file-bug.sh:223,231` | `scripts/super-qa-file-bug.sh:172` (dedupe); `scripts/super-board-preflight.sh:95` (`scan("fingerprint:")`) | |
| `<!-- super-review-fingerprint: <key> -->` | `scripts/super-review-file-refactor.sh:83` | `scripts/super-review-file-refactor.sh:89` | |
| `<!-- super-collect-fingerprint: <key> -->`, `<!-- super-collect-source: -->` | `skills/super-collect/scripts/super-collect-file.sh:242-243` | `skills/super-collect/scripts/super-collect-file.sh:205` (body token match) | |
| `<!-- super-board:begin vX -->` / `<!-- super-board:end -->` in target AGENTS.md | `scripts/super-board-agents-md.py:149-150` | `scripts/super-board-agents-md.py:49-50`; `install.sh:313` | Removed with agents-md |
| `<!-- skills:start -->` / `<!-- skills:end -->` in README | `scripts/super-board-readme-sync.py:31` | same | Removed with readme-sync |
| `blocked-by: <n, m>` or `blocked-by: -` (last line of a Block comment) | lanes per `references/block-template.md:44,54-66` | `scripts/super-board-deps.sh:140,158` | Runner does not read it today (section 8) |
| `## Blocked by` issue-body section | ticket writers per `references/ticket-format.md` | `scripts/super-board-deps.sh:100-104` | |
| `Reason tag: <emoji>` line, 🙋 = needs you | lanes per `references/block-template.md:29,119` | `scripts/super-board-approval.py:26`; `scripts/super-board-deps.sh:147` (both match `Reason tag:[^\n]*🙋`) | FR-BRD-6 replaces emoji with `[needs-you]` etc. |
| Blocked reason emoji vocabulary | `references/block-template.md:79-92` (12 emoji: 🔐 💳 🔑 ❓ 🛡 🧑 🤷 📦 🎨 👯 ⏳ 🙋) | `scripts/super-board-status.py:291-305` (5 emoji: 🛡 🔐 💳 ❓ ⚙, first one found anywhere in the newest comment); `references/status.md:326` | **Already out of step**: ⚙ is not in the writer's table, and 8 writer emoji (incl. 🙋) fall to "other" |
| `approval-request: ` line | `scripts/super-board-approval.py:23` (`--request`) | `scripts/super-board-approval.py:26` | |
| `needs-you: <command>` line in PR body | builder per `references/block-template.md:92`; echoed by `scripts/super-board-merge-gate.sh:264,317` | `scripts/super-board-merge-policy.py:199` | |
| `human-gate: <category> — <why>` gate output | `scripts/super-board-merge-gate.sh:258` | reviewer prompt `references/run.md:489` | |
| Gate exit codes 0-9 | `scripts/super-board-merge-gate.sh` | `references/run.md:471-500`; `workflows/super-board-wave.js:105,125-126` | Contract FR-MRG-4 |
| Run manifest lines (`[HH:MM:SS] ...`: `dispatch lane=`, `reaped stale lock`, `💀 zombie ... worker on #N`, `tick —`, `super-board run started`, `exiting cleanly`, `⚠ block-rate alert:`) | `scripts/super-board-run.sh:366,398,483,485,595,680,707,711,718` | `scripts/super-board-status.py:81-92,149-156` | Zombie and alert parsers match text after the emoji, so dropping the emoji keeps them working; stage 6 moves the file (FR-MET-1/3) |
| Lock file `PID=` / `LANE=` / `STARTED=` | `scripts/super-board-run.sh:360` | `scripts/super-board-run.sh:255` (`issue_locked`), `:464` (`reap_finished_locks`); `scripts/super-board-stop.sh` (sources it) | Stage 6 adds `HANDLE=` |
| `[role] [report] <emoji> <status>` comment prefix | `scripts/super-board-run.sh:555`; `scripts/super-board-stop.sh:103`; prompts `skills/super-build/SKILL.md:138,148,154`, `references/run.md:320,349-350` | no code parser. Agents read `[builder] [report]` (`references/run.md:396`). Shape pinned by `tests/test_writing_format.py` | |
| Review thread prefixes `[builder]` `[qa]` `[reviewer]` | reviewer per `references/run.md:229` | agents: `references/run.md:331`, `skills/super-qa/SKILL.md:712` | |
| Commit subject `<emoji> [type] scope: subject` | agents per `references/writing-standard.md`; `scripts/super-qa-file-bug.sh:187-188`; `scripts/super-review-file-refactor.sh:192` (issue titles) | `skills/super-build/scripts/super-build-dispatch.sh:157-159,175` (finds `[chore] loop: close #N` / `[wip]` commits); `tests/test_writing_format.py` | FR-CNV-2 changes the style; the dispatch grep breaks with it |
| `WIP-PARTIAL:` / `HUMAN GATE TRIPPED:` worker log lines | `skills/super-build/references/worker-preamble.md:74` and onward | `skills/super-build/scripts/super-build-dispatch.sh:163,170` | |
| `Skills:` line in issue body | owner | `scripts/super-board-skills-ready.py` `override()` (`:62`) | FR-CFG-3 keeps it |
| Labels `loop:rebuild-N`, `loop:in-*` | lanes per `references/run.md` | `scripts/super-board-status.py:276,683` | |
| `gh-quota-on-exit: graphql=<n>/5000 rest=<n>/5000` | workers per `references/run.md:776`, `references/writing-standard.md:352` | none in code; `tests/test_writing_format.py:167` allows it | |
| Pre-flight / lane result JSON schemas | agents | `workflows/super-board-wave.js:217-232` (pre-flight), `STAGE_SCHEMA` (pinned by `test_codex_lane_schema.py`) | Must be redefined in the runner (stage 6) |

## 10. Outside dependencies

From `REGISTRY` (`scripts/super-board-skills-ready.py:20-42`) and skill files.

| Skill | Pack | Where named | Required? |
|---|---|---|---|
| `diagnosing-bugs`, `tdd`, `implement`, `codebase-design`, `code-review`, `resolving-merge-conflicts`, `domain-modeling`, `ask-matt` | mattpocock-skills (`npx skills add mattpocock/skills`) | `skills-ready.py:21-38`; `skills/super-build/references/decision-policy.md`, `worker-preamble.md`; `skills/super-qa/SKILL.md`, `references/iteration-preamble.md`; `skills/super-review/SKILL.md`; `references/design-preflight.md`, `references/lint.md` | build rows and lane boundaries: required (exit 10 if missing). `resolving-merge-conflicts`, `domain-modeling`, `ask-matt`: conditional |
| `to-spec`, `to-tickets`, `implement-spec`, `improve-codebase-architecture` | mattpocock-skills | `skills-ready.py:37-38`; `skills/super-collect/SKILL.md`, `scripts/collect_handoff.py:57`; `references/lint.md`, `references/run.md`; `skills/families.json` | optional (super-collect falls back to Backlog) |
| `ponytail:ponytail`, `ponytail:ponytail-review` | ponytail | `skills-ready.py:28,36,39-41` (fallbacks to in-repo policy text); `skills/super-build/SKILL.md`; `skills/super-review/SKILL.md`; `workflows/super-board-wave.js` | build boundary required with fallback; review optional |
| `verification-before-completion` | unverified pack (`skills/super-build/references/decision-policy.md:8` says the superpowers and gsd packs are no longer dependencies) | `skills-ready.py:28-29`; super-build, super-qa files | required in build and qa |
| `testing-strategy` | unverified pack | `skills-ready.py:30,33`; super-build, super-qa files | required in qa, conditional in build |
| `vitest`, `playwright-best-practices` | unverified pack | `skills-ready.py:33,35`; super-qa, super-build files | conditional |
| `security-audit` | Cloudflare (`npx skills add cloudflare/security-audit-skill`) | `skills-ready.py:36`; `get.sh:183-200`; `scripts/super-board-setup.py:85,193-197`; `references/onboard.md:18-19`; `skills/pr-security-review/`, `skills/super-qa/` READMEs | optional; no lane runs it |
| Impeccable | impeccable | `skills/ui-refine-loop/` (all references), `workflows/ui-refine-loop.js:83-98`; `config-schema.json` | required by ui-refine-loop; rubric fallback in `references/rubric.md` |
| `grilling`, `shape`, `clarify` | mattpocock-skills | `skills-ready.py:46` (`INTERACTIVE`: refused for workers) | prohibited for workers |
| Claude Workflow tool | Claude Code | `workflows/*.js`; `references/run-workflow.md`; `skills/ui-refine-loop/SKILL.md:57` | `workflow` backend (dropped) and ui-refine-loop |

Installers: `get.sh:176,192` and `scripts/super-board-setup.py:82,85` run
`npx -y skills@latest add ...` for mattpocock-skills and security-audit.
Under FR-CFG-3 these names become config defaults, not hardcoded requirements.

## 11. Approval-prone commands

Commands the runner prompt or a role's skill/reference tells a headless
worker to run that may stop on a permission prompt. Current allowlist source:
`references/run-workflow.md:330-350`, written by onboard into
`.claude/settings.json`. The `claude-p` runner starts workers with
`nohup claude -p "$prompt"` (`scripts/super-board-run.sh:352`): no
`--allowedTools`, no `--permission-mode`, so only that settings file applies.

| # | Command (as instructed) | Told by | Why it may prompt |
|---|---|---|---|
| 1 | `.claude/bin/super-board-skills-ready.py --root ... --lane ... --hydrate` **with the card JSON as stdin** | `scripts/super-board-run.sh:342` (first operation of every worker) | Pipe or here-string to feed stdin; bare path not covered by the rule `Bash(python3 .claude/bin/super-board-skills-ready.py:*)`. This is the stage 0 failure (docs/baseline.md) |
| 2 | Same, first operation | `workflows/super-board-wave.js:140`; `scripts/super-board-lane.sh:174`; `skills/super-build/SKILL.md:77`; `skills/super-build/scripts/super-build-dispatch.sh:116` | same |
| 3 | `source .claude/bin/super-board-gh-guard.sh; sb_gh_guard_check 200` | `references/run.md:259`; `references/rate-limit-etiquette.md:53`; CLAUDE.md "Worker rules" | `source` and compound `;`; no allow rule |
| 4 | `.claude/bin/super-board-card.sh ... items \| jq ...` | `skills/super-build/SKILL.md:55-56` | pipe into `jq` |
| 5 | `.claude/bin/super-board-card.sh --config <cfg> move <N> <Status>` | `references/run.md:771`; super-build/qa/review SKILL.md | covered by `Bash(.claude/bin/super-board-card.sh:*)` only in the bare form |
| 6 | `bash .claude/bin/super-board-preflight.sh --repo ... --issues ...` | `references/run.md:262`; `workflows/super-board-wave.js:242` | covered (`run-workflow.md:339`) |
| 7 | `bash .claude/bin/super-board-pr-body.sh --pr <P> --block <name> --expect-head <sha>` | `references/run.md:148`; `references/writing-standard.md:87`; `skills/super-build/SKILL.md:272` | covered only with the `bash` prefix |
| 8 | `HEAD=$(gh pr view <N> --json headRefOid -q .headRefOid)` then `bash .claude/bin/super-board-merge-gate.sh --config ... --pr <N> --expect-head "$HEAD"` | `references/run.md:470-471`; `skills/super-review/SKILL.md:349-352` | command substitution; merge gate is not on the base list (`tests/test_run_allowlist.py:20-22`, offered separately at onboard) |
| 9 | `python3 .claude/bin/super-board-github-read.py --kind ... -- <gh args>` | `references/run.md:9`; CLAUDE.md "Worker rules" | covered |
| 10 | `.claude/bin/super-qa-file-bug.sh ...` / `ISSUE_N=$(scripts/super-qa-file-bug.sh ...)` | `skills/super-qa/references/iteration-preamble.md:627`; `references/run.md:113` | command substitution; no allow rule for `super-qa-file-bug.sh` |
| 11 | `scripts/super-review-file-refactor.sh ...` | `skills/super-review/SKILL.md:114,334` | no allow rule; relative `scripts/` path does not exist in a target project |
| 12 | `gh issue edit <N> --add-assignee / --remove-assignee / --add-label / --remove-label` | `references/run.md:616,623,630,785`; `skills/super-build/SKILL.md:102,137,147,153,160` | gh write; covered by `Bash(gh issue edit:*)` |
| 13 | `gh issue comment N --body "..."` (multi-line bodies with `\n`, backticks) | `skills/super-build/SKILL.md:107,138,148,154,161` | gh write; covered, but quoted multi-line bodies may be split by the matcher (unverified) |
| 14 | `gh label create ... 2>/dev/null \|\| true` | `skills/super-build/SKILL.md:87-89` | `\|\|` compound; no `gh label` rule |
| 15 | `gh pr create --draft ... --body-file <f>`, `gh pr create ... --label "$([[ ... ]] && echo ...)"` | `skills/super-build/SKILL.md:136,146`; `skills/super-qa/references/iteration-preamble.md:312-315` | gh write; the QA form has command substitution |
| 16 | `gh pr ready <PR>`, `gh pr edit` | `references/run.md:457`; `skills/super-review/SKILL.md:349` | covered |
| 17 | `gh api -X PATCH repos/.../issues/comments/<id> -F body=@<file>` | `references/run.md:429`; `skills/super-review/SKILL.md:203`; `references/rate-limit-etiquette.md:36-43` (labels, close via `gh api`) | gh write via `gh api`; covered by `Bash(gh api:*)` |
| 18 | `gh api graphql -f query='mutation{resolveReviewThread...}'` | `skills/super-build/SKILL.md:268`; `skills/super-qa/SKILL.md:712`; `references/run.md:246` | gh write; covered by `Bash(gh api:*)` |
| 19 | `gh issue close <N> --reason "not planned"` | `references/block-template.md:75` | no `gh issue close` rule |
| 20 | `gh issue create` (via the filers) | `skills/super-qa/SKILL.md:192`; `iteration-preamble.md:595` | run inside the filer scripts; the scripts themselves need rules (rows 10-11) |
| 21 | `git add <evidence> && git commit && git push`; `git push -u origin HEAD` | `skills/super-qa/SKILL.md:666`; `skills/super-qa/references/iteration-preamble.md:311` | `&&` compound; push is covered by `Bash(git push:*)` |
| 22 | `git diff --name-only $(git merge-base HEAD origin/<base>)...HEAD` | `skills/super-qa/SKILL.md:485` | command substitution; no `git diff`/`git merge-base` rule |
| 23 | `gh project list --owner $(gh repo view --json owner -q .owner.login) --format json` | `skills/super-build/SKILL.md:32`; `skills/super-qa/SKILL.md:57-59` | command substitution; no `gh project list` / `gh repo view` rule |
| 24 | `git worktree add`, `git checkout`, `git fetch`, `git pull`, `git rebase`/`merge` for rebase passes | `references/run.md` Builder steps; `references/run.md:685` | `worktree`, `checkout`, `fetch`, `pull` covered; `git merge`/`rebase`/`diff`/`log`/`status` not listed |
| 25 | Project test runners, `npx playwright test ...`, `npm run ...` | `skills/super-qa/SKILL.md:113,362,384`; `skills/super-review/SKILL.md:171` | project-specific; run-workflow.md:351 leaves them to the owner |
| 26 | Writes under `.claude/` (worktrees under `.claude/worktrees/`, evidence, run state) | `references/run.md`; `hooks/guard-worktree-path.py` | `.claude/` is a protected path in headless mode (`references/run-workflow.md:357-360`); drover moves these to `.drover/` (FR-CFG-1) |

`tests/test_run_allowlist.py:13-19` checks only `run-workflow.md`,
`super-board-wave.js` and the three role SKILL.md files, and only for
`.claude/bin` scripts. It does not read `scripts/super-board-run.sh` or
`references/run.md`, which is why row 1 was never caught.

## 12. Tests (65 suites in `run-safety`)

All are run by `tests/run-safety.sh:12` and by
`.github/workflows/cross-platform.yml:87`. Verdict follows what they test.

| Suite | Tests | Verdict | Note |
|---|---|---|---|
| `test-agents-md.sh` | agents-md.py | remove | with agents-md |
| `test-card.sh` | card.sh | keep | FR-BRD-1 |
| `test-cleanup-wt.sh` | cleanup-wt.py | keep | NFR-2 |
| `test-codex-lane.sh` | lane.sh / codex-lane.sh | remove@6 | |
| `test-codex-stop.sh` | stop.sh Codex path | remove@6 | stop.sh keeps `test_run_rounds.py` coverage |
| `test-codex-wave-contract.sh` | codex-wave.sh | remove@6 | |
| `test-collect-baseline.sh` | super-collect-file.sh | keep | |
| `test-collect-custom.sh` | collect_custom.py | keep | |
| `test-collect-fetchers.sh` | collect fetchers | keep | |
| `test-collect-file.sh` | super-collect-file.sh | keep | |
| `test-deps.sh` | deps.sh | keep | |
| `test-env-check.sh` | env-check.sh | keep | |
| `test-file-bug.sh` | super-qa-file-bug.sh | improve | stage 4 titles |
| `test-file-refactor.sh` | super-review-file-refactor.sh | improve | stage 4 titles |
| `test-get.sh` | get.sh | remove | with get.sh |
| `test-guard-hooks.sh` | guard hooks + gate-skill-evals | improve | drop `:142-181` (gate-skill-evals cases) |
| `test-host.sh` | host.sh | remove@6 | |
| `test-hosts-doc.sh` | docs/hosts.md vs host.sh | remove@6 | |
| `test-install.sh` | install.sh | improve | replaced with drover install tests in stage 5 |
| `test-merge-gate.sh` | merge gate | keep | FR-MRG-1..3 |
| `test-pr-body.sh` | pr-body.sh | keep | |
| `test-preflight.sh` | preflight.sh | keep | FR-RUN-5/6 |
| `test-readme-sync.sh` | readme-sync + pre-commit hook | remove | with readme-sync |
| `test-release.sh` | publish-release.sh | remove | with release.yml |
| `test-run-gates.sh` | run.sh gates | keep | FR-RUN-1/4, FR-MRG-5 |
| `test-safety-runner.sh` | run-safety.sh | keep | |
| `test-settings.sh` | settings.py | keep | |
| `test-setup.sh` | setup.py | improve | stage 5 |
| `test-throttle.sh` | throttle.sh | remove@6 | |
| `test-ui-refine-loop-setup.sh` | refine-setup.sh | keep | |
| `test-ui-refine-loop-workflow.sh` | ui-refine-loop.js | keep | |
| `test-usage.sh` | usage.sh | keep | becomes the probe test |
| `test-wave-plan.sh` | wave-plan.sh | port-then-remove | listed test for FR-RUN-3, FR-BRD-5 |
| `test-wave-preflight.sh` | wave.js pre-flight | port-then-remove | listed test for FR-RUN-5 |
| `test-wave-review-memory.sh` | wave.js review memory | port-then-remove | stage 7 |
| `test-workflow-halt.sh` | wave.js halt | port-then-remove | halt-on-79 in the runner |
| `test_approval.py` | approval.py | keep | |
| `test_calibration.py` | calibration.py | remove | with calibration |
| `test_codex_lane_schema.py` | Codex schema = STAGE_SCHEMA | remove@6 | |
| `test_collect_baseline.py` | collect fetchers | keep | |
| `test_collect_custom.py` | collect_custom.py | keep | |
| `test_collect_fetchers.py` | collect fetchers | keep | |
| `test_collect_handoff.py` | collect_handoff.py | keep | |
| `test_collect_intake_safety.py` | collect intake | keep | |
| `test_collect_windows.py` | collect-state.py on Windows shim | improve | NFR-5 does not include Windows; keep while collect-state has the msvcrt path |
| `test_github_reads.py` | github-read.py, gh-guard | keep | FR-RUN-9 |
| `test_pages_version.py` | diagrams pages + pages.yml | remove | with diagrams |
| `test_project_verifier.py` | project-verifier.py | keep | |
| `test_run_allowlist.py` | allowlist coverage | improve | extend to the runner prompt and `run.md` (section 11) or replace with FR-HRN-4 check |
| `test_run_rounds.py` | runner rounds (stage 0) | keep | |
| `test_runtime_compare.py` | runtime_compare.py | keep | |
| `test_skill_dispatch.py` | setup/launcher boundaries | improve | references codex-lane; stage 6 |
| `test_skills_readiness.py` | skills-ready.py | improve | REGISTRY -> config (stage 5) |
| `test_status_parse.py` | status.py | improve | stage 4 reason tags |
| `test_upgrade_recovery.py` | setup.py upgrade path | improve | upstream-version upgrade; revisit in stage 5 |
| `test_verification_readiness.py` | verification-ready.py | improve | references codex-lane; stage 6 |
| `test_visual_chrome_cleanup.py` | visual.py | keep | |
| `test_visual_details.py` | visual.py on `diagrams/skill-map.json` | improve | read the moved fixture |
| `test_visual_families.py` | same | improve | read the moved fixture |
| `test_visual_interactions.py` | visual template | keep | |
| `test_visual_lanes.py` | visual.py render | keep | |
| `test_visual_main_steps.py` | same as details | improve | read the moved fixture |
| `test_visual_source_base.py` | visual.py `--source-base` | keep | no file dependency on diagrams |
| `test_worktree_paths.py` | prompt -> guard -> git -> cleanup | improve | `.drover/worktrees/` in stage 3 |
| `test_writing_format.py` | commit/ticket/comment shapes | improve | stage 4 rewrites the formats |
| `tests/fixtures/` (23 files) | fixtures | keep | `fake_codex.py` and `wave-*.json` go with their stage 6 suites |

## 13. Top-level docs and folders

| Path | What it is | Who calls it | Verdict | Reason |
|---|---|---|---|---|
| `README.md` | Upstream README | GitHub | improve | Stage 12 rewrite. Links to removed files: `:3,27` (docs/assets), `:41,78` (get.sh), `:47-48` (upstream marketplace), `:71` (calibration), `:192,210-211` (release docs) |
| `CLAUDE.md` | Agent notes for this repo | Claude Code | improve | Stage 2 drops `:6` (get.sh), `:8` (readme-sync), `:97` (RELEASING), `:99-101` (diagrams); stage 12 rewrite |
| `LICENSE` | MIT | | keep | PLAN stage 3 |
| `VERSION` | Pack version | `install.sh:90`; `get.sh:158`; `.github/scripts/publish-release.sh:10`; `pages.yml:53` | keep | `install.sh` still reads it |
| `skills/super-board/VERSION` | Installed version marker | `scripts/super-board-agents-md.py:48`; `scripts/super-board-setup.py:296,301` | keep | upgrade detection until stage 5 |
| `PLAN.md`, `specs/` | drover plan and spec | | keep | |
| `docs/baseline.md`, `docs/baseline.txt` | Stage 0 results | | keep | |
| `docs/audit.md` | This file | | keep | |
| `docs/hosts.md` | Codex/OpenCode host notes | `README.md:66`; `config-schema.json:38`. tests: `test-hosts-doc.sh` | remove@6 | Codex/OpenCode deferred |
| `docs/super-board/README.md` | Long-form pack docs | `README.md:35-36`; `skills/super-board/README.md:31` | improve | stage 12 |
| `docs/super-board/routing.md` | Which tool for which idea | `README.md:99`; `skills/super-board/SKILL.md:20`; `skills/super-collect/SKILL.md:22`, `README.md:5` | keep | linked from kept skills |
| `docs/blocked-column-audit.html` | Upstream audit page | none | remove | section 3 |
| `docs/assets/` | Logo, screenshots | section 3 | remove | |
| `diagrams/` | Pages site | section 3 | remove | `skill-map.json` moves to `tests/fixtures/` |
| `evals/` | Paid behavioural evals | section 3 | remove | |
| `RELEASE-NOTES.md`, `RELEASING.md` | Release docs | section 3 | remove | |
| `.claude-plugin/plugin.json`, `marketplace.json` | Plugin manifests | Claude Code plugin install (`README.md:47-48`) | improve | Stage 3 rename; drop `experimental.evals` (`plugin.json:21`) in stage 2 |
| `.claude/settings.json` | This repo's dev hooks | Claude Code | remove | section 3 |
| `.gitignore` | Ignores | git | improve | drop `evals/results/` (`:40`); stage 3 paths |

## 14. Remove list for sign-off

Deleted in stage 2. All paths exist at the fork point.

| Path | Files | Lines | Binary |
|---|---|---|---|
| `diagrams/` except `diagrams/skill-map.json` (moved to `tests/fixtures/`) | 71 | 40,104 | 20 |
| `docs/assets/` | 3 | 20 | 2 |
| `evals/` | 54 (incl. 2 symlinks) | 1,374 | |
| `hooks/dev/gate-skill-evals.py` | 1 | 210 | |
| `hooks/pre-commit-readme.sh` | 1 | 23 | |
| `scripts/super-board-readme-sync.py` | 1 | 159 | |
| `scripts/super-board-agents-md.py` | 1 | 343 | |
| `skills/super-board/references/agents-md-block.md` | 1 | 40 | |
| `scripts/super-board-calibration.py` | 1 | 216 | |
| `skills/super-board/references/calibration.md` | 1 | 101 | |
| `get.sh` | 1 | 205 | |
| `RELEASE-NOTES.md` | 1 | 1,097 | |
| `RELEASING.md` | 1 | 102 | |
| `.github/workflows/pages.yml` | 1 | 69 | |
| `.github/workflows/release.yml` | 1 | 35 | |
| `.github/scripts/publish-release.sh` | 1 | 22 | |
| `.claude/settings.json` | 1 | 27 | |
| `tests/test-agents-md.sh` | 1 | 82 | |
| `tests/test-get.sh` | 1 | 122 | |
| `tests/test-readme-sync.sh` | 1 | 96 | |
| `tests/test-release.sh` | 1 | 69 | |
| `tests/test_calibration.py` | 1 | 214 | |
| `tests/test_pages_version.py` | 1 | 88 | |
| **Total** | **149 entries (147 regular files + 2 symlinks)** | **45,630** | **22** |

Against the fork point: 358 entries / 356 regular files / 88,434 lines
before; about 209 entries / 209 regular files / 42,804 lines after, before
the caller edits stage 2 makes. `run-safety` goes from 65 to 59 suites.

Not on this list, deleted in stage 6 (`remove@6` and `port-then-remove`):
`scripts/super-board-{codex-lane,codex-wave,lane,host,wave-plan,throttle}.sh`,
`workflows/super-board-wave.js`, `references/run-workflow.md`,
`docs/hosts.md`, 11 suites and their fixtures: 25 files, 2,965 lines.

Rejected: `scripts/super-board-approval.py` (keep, section 3).

Signed off 2026-11-17, except `docs/blocked-column-audit.html`, which the
owner keeps: the total is 148 entries (146 regular files), 44,818 lines.

## 15. Findings that contradict or are missing from the spec

1. **The port list is incomplete.** specs/README.md "Upstream backends" and
   PLAN stage 6 port only pre-flight, classifier and usage pause. The
   `claude-p` runner also lacks the Blocked sweep (FR-BRD-5, whose listed
   source is `run-workflow.md` and test is `test-wave-plan.sh`), the
   open-blocker filter, and moving approved 🙋 cards back to Review. All
   three live only in `super-board-wave-plan.sh` + `super-board-deps.sh`.
   Dropping the `workflow` backend without them breaks FR-BRD-5 and leaves
   approved cards Blocked.
2. **FR-RUN-5 is not "the same behaviour" in both backends.** runner.md says
   FR-RUN-5 "lives in the `workflow` backend today; stage 6 moves them into
   the runner. Behaviour stays the same." In the `claude-p` runner the
   Builder runs pre-flight itself per `references/run.md:250-262`, one card
   at a time, after the claim. The batch-before-claim behaviour exists only
   in `workflows/super-board-wave.js:206-284`.
3. **Reason-tag writer and parser already disagree.** `status.py:291-297`
   knows 5 emoji, one of them (⚙) not in `block-template.md:79-92`; 8 of the
   writer's 12 emoji, including 🙋, show as "other". FR-STS-1 "Blocked
   grouped by reason" is only partly true at the fork point. FR-BRD-6 fixes
   it; the Preserved row should not claim it works today.
4. **FR-HRN-4 coverage test misses the runner.** `test_run_allowlist.py`
   does not read `scripts/super-board-run.sh` or `references/run.md`, and
   the runner's first instruction (`run.sh:342`) needs stdin plus a bare
   `.claude/bin/...py` path that no allow rule matches (section 11).
5. **NFR-8's 356 is regular files only.** `git ls-tree` at `d0b7d5a` has 358
   entries; the two extra are symlinks under `evals/` (removed here).
6. **config.md keeps `calibration` "until the audit decides".** This audit
   removes it (section 3); config.md's key list needs the update in stage 2.
7. **specs/README.md decisions say "Cross-PR knowledge: deferred; audit
   upstream's calibration feature first".** The feature is an adapter for an
   outside skill that is not shipped and never runs on the `claude-p`
   backend; there is nothing in it to reuse for `.drover/knowledge.md`.
8. **`super-build-dispatch.sh` reads commit subjects** (`[chore] loop:
   close #N`, `:157-159,175`). FR-CNV-2 (repo commit style) breaks it unless
   stage 4 changes both, or stage 6 retires standalone dispatch.
