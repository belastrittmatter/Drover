# /drover

Drives a GitHub Project board on its own: drag a card into `Ready`, and it comes back merged with evidence on the PR.

## What It Does

- **Follows your dependency graph.** A `Ready` card is claimed only when its `## Blocked by` issues have all closed. A `Blocked` card whose blockers closed is swept back to `Ready`; a dependency line it cannot read is flagged, never guessed.
- **Checks before it builds.** A cheap pre-flight agent holds duplicates, unclear and too-big cards; a fast classifier adds missing type and risk labels and picks the model tier.
- **Runs each card through three lanes**: drover-build (Building), drover-qa (QA), drover-review (Review). A failed lane bounces the card back with comments the next round reads.
- **Merges only through the merge gate**: a lock, the current base merged in, your `verify_commands`, then a squash-merge pinned to the reviewed commit.
- **Holds no state of its own.** The board is the state, so Ctrl-C, restarts and rate-limit pauses lose nothing. Stranded `Building` cards return to `Ready`.
- **Pauses near your Claude usage limit** when the usage check is recording (see the pack README).
- **Never writes product code.** Your session only starts the runner in the background; its headless workers do the work. Progress: `/drover status`.

## When To Use It

- You have a GitHub Project of cards with acceptance criteria and want them built, tested and reviewed unattended.
- You want to harden code that already exists: label those cards `qa` and they skip Building (QA and Review only).

## Verbs

| Command | Does |
|---|---|
| `/drover onboard` | One-time setup; checks the board's columns, writes the config |
| `/drover lint` | Flags vague or missing ACs and unreadable `Blocked by` lines before a run |
| `/drover status` | Read-only 80-column board snapshot (~1.3s, pure Python) |
| `/drover run` | Starts the runner in the background; also the resume command |
| `/drover stop` | Posts "stopped mid-flight" notes, releases claims, stops workers |

## Install

Part of the drover pack: `drover skills install` links it into `~/.claude/skills`. Needs `gh` (authenticated), `jq`, bash 3.2+, a GitHub Project (v2) with a `Status` field, and the [mattpocock/skills](https://github.com/mattpocock/skills) pack. Ships `pr-security-review` (run by the Tester in QA on every PR); onboard also adds Cloudflare's on-demand [`security-audit`](https://github.com/cloudflare/security-audit-skill). Full reference: [`docs/drover/README.md`](../../docs/drover/README.md).
