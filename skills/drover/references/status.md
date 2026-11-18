# `drover status` — read-only live snapshot

> This is the **only read-only verb** in the `drover` skill. It performs
> no GitHub mutations and is safe to invoke during a headless `drover run`.

---

## Fast path — prefer this

If `drover status` exists:

1. **Run it.**
2. **Print its stdout verbatim, inside a single fenced code block.**
3. **Stop.** Do not write anything before or after the code block.

Skip everything below in this file — the script implements the locked
template defined here.

```bash
drover status                # reads .drover/config.json (one config per repo)
```

### Do NOT, under any circumstances:

- **Do not** summarize, paraphrase, condense, or rewrite the output.
- **Do not** add a `TL;DR`, `Summary`, intro, outro, or any prose around it.
- **Do not** collapse empty lanes onto one line (e.g. don't merge
  `Building [0]`, `QA [0]`, `Review [0]` into one row). Each lane is its
  own multi-line box; preserve every line and every newline as-is.
- **Do not** strip the `▎Workers` / `▎Block reasons` / `▎Recent` / `▎Health`
  section headers, or fold them into the kanban box stack.
- **Do not** re-render the box-drawing characters, "fix" alignment, or
  "improve" the layout in any way.
- **Do not** add markdown links, bold, or other styling inside the code
  block. Issue numbers stay as plain `#NNN`.

The user chose the rich 80-column template deliberately. Compressing it
back into a chat-friendly summary defeats the entire point of having the
script. If the output looks long, that **is** the correct length — print
it anyway and trust the user to scroll. The ≈150× speedup claim assumes
the agent does no rendering work post-script; summarizing forfeits part
of that win and gives the user the wrong layout.

### Background

This file defines a richer status template than the one drover
originally shipped with: a fixed 80-column Kanban plus dedicated
`▎Workers`, `▎Block reasons`, `▎Recent`, and `▎Health` sections.
Model-rendering all of that token-by-token takes ≈3 min per invocation;
the Python script renders the same template in ≈1.3 s (≈150× faster),
so the richer view stays usable in practice. Pure Python 3 stdlib +
`gh` CLI; no `jq`, no bash — runs on macOS, Linux, and Windows alike.

Exit codes the orchestrator should respect:

- `0` — printed snapshot, done.
- `64` — no config-slug arg and ambiguous configs, or invalid slug (path
  traversal); ask the user which / for a valid slug.
- `66` — config not found.
- `67` — `gh` / network failure; surface the stderr line and stop.

The script enforces the same read-only contract as the rest of this file
(no `gh ... edit/create/delete`, no GraphQL mutations). If you want to verify
that for a given invocation: `grep -E 'gh ?\.api\.(issue|pr|project) (edit|create|delete)|mutation {|gh.*(edit|create|delete)' drover status` — should return nothing.

The rest of this document is the format spec the script implements. Read it
only when (a) the script is missing and you need to hand-render, or (b) you're
editing the script and need to confirm the locked template.

---

## Intro shown when status starts

```
drover status
────────────────────────────────────────────────────────────────────────────────
```

---

## The locked template — render EXACTLY this shape

This is a **template, not an example**. Print every section, in this order,
every time — even when counts are zero. Width is fixed at 80 columns. The
sample below is rendered against live data, but the structure is what's
locked: section labels, box layout, lane and reason tags, timestamp format, empty-state
strings. Do **not** improvise.

```
drover · <Project Title> (#<number>)
────────────────────────────────────────────────────────────────────────────────
config: <slug>   labels: qa · bug · feature   base: <base_branch>
mode:   <auto-merge|human-approves>        truth gate: <off|non-trivial (≥N)|always>

┌─ Backlog  [N] ───────────────────────────────────────────────────────────────┐
│ <collapsed line — see §D>                                                    │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ Ready    [N] ───────────────────────────────────────────────────────────────┐
│ <card lines, one per issue — see §C>                                         │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ Building [N] ───────────────────────────────────────────────────────────────┐
│ <card lines>                                                                 │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ QA       [N] ───────────────────────────────────────────────────────────────┐
│ <card lines>                                                                 │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ Review   [N] ───────────────────────────────────────────────────────────────┐
│ <card lines>                                                                 │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ Blocked  [N] ───────────────────────────────────────────────────────────────┐
│ <card lines with reason tag>                                                 │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ Done     [N] ───────────────────────────────────────────────────────────────┐
│ <collapsed line — see §D>                                                    │
└──────────────────────────────────────────────────────────────────────────────┘

▎Workers  (claim: <login> · <active>/<max> active)
   <one line per in-flight worker — see §E>

▎Block reasons
   Blocked <n> · <tag> <count> · <tag> <count> …
   <one line per reason tag — see §F>

▎Recent  (last 5 manifest events)
   <one line per event — see §G>

▎Health
   last tick: <time>    run started: <time>    workers: <a>/<m>    worktrees cleaned: <n>
```

### Sample render (today's live board)

```
drover · My App Board (#1)
────────────────────────────────────────────────────────────────────────────────
config: my-app-drover   labels: qa · bug · feature   base: staging
mode:   auto-merge                 truth gate: non-trivial (≥70)

┌─ Backlog  [3] ───────────────────────────────────────────────────────────────┐
│ #33 #31 #30   (not started, collapsed)                                       │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ Ready    [1] ───────────────────────────────────────────────────────────────┐
│ [B] #26  Strip nav + editor groups from command palette                ↻ 2/3 │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ Building [0] ───────────────────────────────────────────────────────────────┐
│ (empty)                                                                      │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ QA       [2] ───────────────────────────────────────────────────────────────┐
│     #24  Unify status options across Ads and Organic                         │
│ [Q] #29  Organic video detail modal — create + edit-ready              ↻ 1/3 │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ Review   [0] ───────────────────────────────────────────────────────────────┐
│ (empty)                                                                      │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ Blocked  [1] ───────────────────────────────────────────────────────────────┐
│ sequenced #25  Add Controle page                                             │
└──────────────────────────────────────────────────────────────────────────────┘
┌─ Done     [9] ───────────────────────────────────────────────────────────────┐
│ #20 #18 #7 #6 #5 #4 #3 #2 #1   (squash-merged, collapsed)                    │
└──────────────────────────────────────────────────────────────────────────────┘

▎Workers  (claim: LucariusWest · 2/3 active)
   [B] Builder   #26  attempt 2/3 · 6m · loop:rebuild-1
   [Q] Tester    #29  attempt 1/3 · 6m

▎Block reasons
   Blocked 1 · sequenced 1
   sequenced    #25

▎Recent  (last 5 manifest events)
   T-6m    dispatch  #26  → Building  attempt 2/3
   T-6m    dispatch  #29  → QA        attempt 1/3
   T-6m    reap      #29            stale lock + assignee swept
   T-16m   reap      #26            stale lock + assignee swept
   T-22m   dispatch  #29  → Building  attempt 1/3

▎Health
   last tick: 93s ago    run started: 67m ago    workers: 2/3    worktrees cleaned: 5
```

---

## Conventions

### §A — Width contract

- Every line ≤ 80 characters.
- Kanban box frames are **exactly 80 chars wide**: `┌─` + label + `[N]` + `─`-padding to col 79 + `┐`. Closing `└…┘` mirrors the top.
- Card lines inside a box: `│ ` (2 chars) + 76 chars of content (padded with trailing spaces) + ` │`. Truncate the title with `…` if the row would exceed the budget.

### §B — Section order (mandatory, always print)

1. Header line (project + `#number`)
2. Separator (80 × `─`)
3. Config strip (2 lines: `config/labels/base`, `mode/truth-gate`)
4. Kanban — 7 boxes in board order, left to right as on GitHub: Backlog · Ready · Building · QA · Review · Blocked · Done (`BOARD_ORDER` in the script; there is no Skipped column since v3.0.0)
5. `▎Workers`
6. `▎Block reasons`
7. `▎Recent`
8. `▎Health`

**Empty-state strings (locked):**

- Empty Kanban box: `│ (empty)` + padding.
- Empty `▎Workers`: `   (idle)`
- Empty `▎Block reasons`: `   (none)`
- Empty `▎Recent`: `   (no manifest events yet)`
- No active run at all: see §H.

### §C — Card line format

Inside a Kanban box, one line per issue:

```
│ <lane> #<NNN>  <title…>                                     <suffix>      │
```

- **lane** (3 cells + space): the lane tag (`[B]`/`[Q]`/`[R]`) if the issue
  is in-flight (a `dispatch lane=` line in today's manifest with no later reap
  or zombie line), else three spaces. **This tag is the highlight** — no
  separate "active in column X" callout needed. In Blocked, the reason tag's
  short name (§F) takes this place.
- **#NNN**: issue number, left-padded to keep title column aligned.
- **title**: truncated to `…` to fit the remaining budget after the suffix.
- **suffix** (right-justified):
  - In-flight rebuild: `↻ N/3` (from `loop:rebuild-N` label)
  - Otherwise: empty

### §D — Backlog and Done collapsing

Backlog and Done are **always one line** each. Backlog is tagged `(not started, collapsed)`.
Done is **always one line** of issue numbers, newest first, no titles, tagged
`(squash-merged, collapsed)`. Never expand Done card-by-card. If Done has more
than ~12 issues at 80-col width, truncate with `… +N more` at the end.

### §E — Workers section format

One line per in-flight worker, sorted by lane (Builder, Tester, Reviewer):

```
   <lane> <Role-padded-to-8>  #<NNN>  attempt <a>/3 · <Nm> · <extra-labels>
```

Where `<Nm>` is "minutes since the most recent `dispatch lane=…` line for that
issue in the run manifest". `<extra-labels>` lists any other meaningful
`loop:*` labels (e.g. `loop:rebuild-1`), comma-separated, omitted when none.

### §F — Block-reasons section format

Group `Blocked` cards by reason tag: one summary line, then one line per tag,
sorted by count desc (ties in the order of `drover-reasons.json`):

```
   Blocked 3 · creds 1 · unclear 1 · needs-you 1
   creds        #31
   unclear      #28
   needs-you    #25
```

The tag comes from the `Reason tag:` line of the newest comment on each issue
(else the first known tag anywhere in it). The short name drops the brackets and
the `blocked:` prefix. The tags are the ones in `lib/drover-reasons.json`
(see block-template.md) — never improvise. A comment with no known tag counts as
`other`.

### §G — Recent-events section format

Tail the last 5 state-transition lines from the most recent run manifest
(`.drover/runs/<YYYY-MM-DD>-<slug>.md`). Reformat each as:

```
   T-<Nm>    <verb>      #<NNN> → <Target>  <detail>
```

Where:

- `T-<N>m` if event was < 60 minutes ago, `T-<N>h` if < 24h, `T-<N>d` otherwise.
- `<verb>`: `dispatch`, `pass`, `block`, `skip`, `reap`, `alert`, `zombie`, `merge`.
- `<Target>`: target lane/column for the transition (or omit for `reap`).
- Pad columns so verbs align visually.

A "state-transition line" is any manifest line containing `dispatch`,
`reaped`, `→ Done`, `→ Blocked`, `→ QA`, `→ Review`,
`block-rate alert`, or `zombie`. Tick-only lines (`tick — Ready=…`) are
**not** state transitions; skip them.

### §H — No-active-run state

If today's manifest doesn't exist or its last line is `… exiting cleanly`,
the run is not active. Adjust three sections:

- `▎Workers` → `   (no active run — \`drover run\` to start)`
- `▎Recent` → tail the most recent manifest (any date) and prefix each event
  with the date when it's not today: `2026-05-25 T-1d dispatch #20 → Review`
- `▎Health` → `last run: completed <N>{m,h,d} ago    workers: 0/<max> idle`

The Kanban board still renders normally — column counts come from the live
Projects v2 API, not from the manifest.

### §I — Locked lane tags

Plain text only; the snapshot prints no emoji.

| Tag   | Meaning          |
| ----- | ---------------- |
| `[B]` | Builder lane     |
| `[Q]` | Tester (QA) lane |
| `[R]` | Reviewer lane    |

`↻ N/3` marks a rebuild iteration (`attempt N/3`). Block reason tags are §F's
short names from `drover-reasons.json`; `other` is the fallback.

### §J — Timestamp format (locked)

| Where                | Format                                    | Example                 |
| -------------------- | ----------------------------------------- | ----------------------- |
| `▎Recent` events     | `T-<N>m` / `T-<N>h` / `T-<N>d`            | `T-6m`, `T-3h`, `T-1d`  |
| Health "last tick"   | `<N>s ago` if < 90s, else `<N>m ago`      | `93s ago`, `4m ago`     |
| Health "run started" | `<N>m ago` if < 90m, else `<N>h <M>m ago` | `67m ago`, `2h 14m ago` |
| Workers "dispatched" | `<N>m` / `<N>h` (no "ago" suffix)         | `6m`, `2h`              |

Never print absolute clock times (`14:21:45`) in the user-facing output — they
belong in the manifest, not the snapshot.

---

## Behaviors

- Pure-read. Touches GitHub for column counts + assignee scan + run-manifest read. No writes.
- Works whether `drover run` is currently active or not — see §H.
- Multi-project: `drover status bookkeeping-app` operates on that project's config (same lookup rules as §4 of the spec).
- Safe to run during a headless `run`. Does not interfere with worker dispatch.

---

## Implementation hints (authored — NOT in spec)

These notes describe the data flow that `drover-status.py` implements.
They also apply if you need to hand-render via the model-render fallback when
the script is missing.

- **Config resolution:** load `.drover/config.json`; the run slug in manifest names is
  the repo name.
  Header fields come straight from the JSON.
- **Claim assignee resolution:** the in-flight worker scan must match the
  identity recorded in the config under `notifications.bot_identity`. This may
  be a GitHub App bot account (e.g. `drover-bot[bot]`) **or** the user's
  own login when running in user-account mode. If `notifications.bot_identity`
  is absent, fall back to scanning for any assignee that matches the
  configured identity (e.g. `claim.assignee_login`).
- **Project items (column counts + in-flight workers, both from ONE call):**
  the script issues a single targeted `gh api graphql` query (`ITEMS_QUERY`
  near the top of `drover-status.py`) that fetches each item's `number`,
  `title`, `labels{nodes{name}}`, and the `Status` single-select field —
  ≈3 KB total. Group locally by Status for column counts; filter the same
  `labels` array for `loop:in-{builder,tester,reviewer}` to identify
  in-flight workers per lane; `loop:rebuild-N` feeds the `↻ N/3` suffix.
  Deliberately avoids `gh project item-list --format json` (≈100 KB — it
  slurps every issue body) and any separate `gh issue list --assignee` call.
  Do **not** call `gh project item-edit` or any mutation in this verb.
- **Block-reason parsing:** for each issue in `Blocked`, read the
  latest §4 reason-tag comment via `gh issue view <N> --json comments` and
  read its tag as in §F. Cache results during the snapshot — do
  not re-read mid-render.
- **Recent events:** tail the most recent run manifest at
  `.drover/runs/<YYYY-MM-DD>-<slug>.md`. Filter to state-transition
  lines (§G); skip `tick —` lines. Take the last 5 after filtering.
- **Health:**
  - `Last tick` — most recent `tick —` line in today's manifest.
  - `Stale worktrees cleaned` — count `reaped worktree` lines since the most
    recent `drover run started` line in today's manifest.
  - `Run started` — most recent `drover run started` line in today's
    manifest. If absent or followed by `exiting cleanly`, see §H.
  - `Active worker count` — derived from in-flight scan above, capped at
    `roles.<role>.max_workers` summed (each defaults to 1).

---

## Multi-project lookup

Same rules as §4 config discovery:

- **Bare `drover status`** invoked in a project root → use that
  project's config.
- **`drover status <name>`** invoked in an umbrella repo → switch to the
  named sub-project's config.
- **No active config found** → halt with:
  `Run drover onboard first.`

---

## Read-only contract — enforced on both rendering paths

When `drover-status.py` runs, this contract is enforced by inspection:
the script issues no `gh` mutations (see the `grep` verification command in
the Fast-path block above). When the model-render fallback executes instead,
the model MUST confirm before printing the snapshot that no `gh` invocation
it issued belongs to the mutation set below. Either way, if any forbidden
call was made, **halt immediately** and report a contract violation; do not
print the snapshot.

Forbidden in `status`:

- `gh ... edit` (e.g. `gh issue edit`, `gh project item-edit`,
  `gh pr edit`, `gh label edit`, `gh repo edit`)
- `gh ... create` (e.g. `gh issue create`, `gh pr create`,
  `gh project item-create`, `gh label create`, `gh release create`)
- `gh ... delete` (e.g. `gh issue delete`, `gh project item-delete`,
  `gh label delete`, `gh repo delete`)
- `gh issue ... add-label` / `gh issue ... remove-label`
- `gh issue ... add-assignee` / `gh issue ... remove-assignee`
- `gh api graphql` invocations with mutations such as `resolveReviewThread`,
  `addProjectV2ItemById`, `updateProjectV2ItemFieldValue`, `closeIssue`,
  `mergePullRequest`, etc.

Allowed in `status` (read-only):

- `gh project item-list` / `gh project view`
- `gh issue list` / `gh issue view`
- `gh pr list` / `gh pr view`
- `gh api graphql` strictly with `query { ... }` (never `mutation { ... }`)
- Local filesystem reads of the config JSON and run-manifest markdown.

If implementation accidentally calls a mutation, halt and report:
`drover status: contract violation — mutation attempted from a read-only verb.`
