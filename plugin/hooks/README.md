# Personal Claude Code notes hook

A `SessionStart` hook that writes your own workflow preferences into `CLAUDE.local.md` — which
Claude Code already loads as project memory, and which is gitignored — from a branch on a remote
you own. Your notes (e.g. "always open my PRs as drafts") follow you across sessions and are never
committed to a shared branch.

> **Setting this up for the first time?** [`SETUP.md`](../../docs/SETUP.md) is the whole thing in
> three steps, including the ones only you can do — labels on your fork, Claude's access to it,
> and the variables a fresh-clone environment needs. This page is the reference behind it.
>
> **Rather see it in action than read about it?**
> [`example-walkthrough.md`](../skills/plan-dashboard/example-walkthrough.md) is a short worked
> example of the whole thing in use, from a plan-mode idea to a published dashboard.

Three kinds of content, stored the same way and never merged anywhere:

| Section in `CLAUDE.local.md` | Holds | Stored on the notes branch at | Saved by |
| --- | --- | --- | --- |
| Personal notes | Your standing preferences | `.claude/personal/notes.md` | [`save-personal-notes.sh`](./save-personal-notes.sh) |
| PR progress | Plan, progress and next steps for the current branch's PR | `.claude/personal/pr-progress/<branch>.md` | [`save-pr-progress.sh`](./save-pr-progress.sh) |
| Plan | A multi-PR initiative's manifest and roadmap | `.claude/personal/plans/<plan-id>/` | [`save-plan.sh`](./save-plan.sh) |

The PR-progress section appears on any branch that isn't the default branch, a detached `HEAD`, or
the notes branch itself, as an empty scaffold even before anything has been saved.

Your local Claude Code settings ride along on the same branch too, as a whole file rather than a
section — see [Syncing your local Claude Code settings](#syncing-your-local-claude-code-settings).
Every run prints a summary of what it found and wrote, so a session never has to infer it.

## Quick start

1. Run `/setup-personal-notes` in any Claude Code session on this repo.
2. Answer the one question it asks — which remote your notes live on — or accept the default.
3. Done. Every session from now on writes `CLAUDE.local.md` automatically.

It is safe to re-run: on a clone that's already set up it reports what it found and asks nothing.
You don't have to run it first either — `/plan-create`, `/plan-dashboard`, `/plan-item-kickoff`,
`/plan-item-resolve` and `/add-plan-item` each run it for you if something is missing, without
stopping to ask.

To do the same without a session, one command performs every step the skill would:

```bash
~/.local/share/basstler/venv/bin/python -m basstler.setup.personal_notes \
  --remote <your-fork-remote-or-url> --name "Your Name" --email "you@example.com" \
  [--starter-notes] [--create-labels]
```

It refuses a remote GitHub says someone else owns, checks the labels this tooling uses when
`GH_TOKEN` or `GITHUB_TOKEN` is set, and ends with `check-setup.sh`'s report. The steps it runs
are the hook scripts here, which also work one at a time:

```bash
"${BASSTLER_PLUGIN_ROOT}/hooks/create-personal-notes-branch.sh"           # create the branch
"${BASSTLER_PLUGIN_ROOT}/hooks/save-git-identity.sh" --name "Your Name" \
  --email "you@example.com"                                                   # commit as yourself
"${BASSTLER_PLUGIN_ROOT}/hooks/check-setup.sh"                            # inspect, change nothing
"${BASSTLER_PLUGIN_ROOT}/hooks/session-start.sh" && cat CLAUDE.local.md   # verify
```

`check-setup.sh` prints one row per check and exits non-zero if anything still needs doing.

Every session start also gives the `basstler` package its own environment,
`~/.local/share/basstler/venv` (under `$BASSTLER_HOME` when set), with the package and its
dependencies installed into it wherever any of that is missing or the plugin names a newer
release, for a project whose personal-notes branch resolves — reported on its `dependencies:`
line, never fatal, and described in the repository's `README.md`.

Every session start prints its own summary, so none of the three things below has to be
remembered. Its `setup:` line runs `check-setup.sh` and names any check that still needs setup;
its `plan:` line distinguishes *no plans are tracked here* from *plans exist and no item tracks
this branch* — the second being the cue to add the item before starting work, not after; its
`git identity:` line names whose commits this clone will author. All three lines appear only
once a personal-notes branch exists, so a clone that uses none of this stays silent.

## Editing your notes

- **Ask Claude** — *"add \<X\> to my personal notes"*, *"edit my personal notes"*. Nothing else to
  explain: the header the hook writes into `CLAUDE.local.md` is always in context and names the
  branch, the path, and the script that pushes changes back.
- **By hand** — edit `CLAUDE.local.md` between that section's `BEGIN-`/`END-` markers, then run the
  save script from the table above.

Only content between the markers is ever saved. Headers and markers are regenerated every session,
so editing them has no effect.

## Git identity

A fresh clone inherits whatever global git config its environment provides. In an agent session
that is the agent's own identity, so commits are attributed to it *by default* — not by anyone
forgetting. Record yours once:

```bash
"${BASSTLER_PLUGIN_ROOT}/hooks/save-git-identity.sh" --name "Your Name" --email "you@example.com"
```

It is stored at `.claude/personal/git-identity` on the notes branch, and every session start writes
it into that clone's **repository-local** git config. A clone that already has one keeps it, and
global config is never touched. Both arguments are required — the script will not read them from the
clone's current config, since that is the thing that may be wrong.

`check-setup.sh`'s `git_identity` row compares what a commit here would *actually* be authored as
against what is recorded. It resolves that with `git var GIT_AUTHOR_IDENT` rather than
`git config --get user.name`, because `GIT_AUTHOR_*`/`GIT_COMMITTER_*` outrank every config file
when set — so the config value can say one thing while every commit says another.

Setting those four variables in your environment does the same job with no hook at all, and applies
from the session's first command rather than from when the hook runs. See
[`personal-notes.env.example`](./personal-notes.env.example). Recording the identity on the notes
branch is what covers environments where you can't set variables.

Two things no local hook can reach: **commits made outside a session** (the GitHub merge button, a
scheduled job) carry whatever identity that context has — set your GitHub account's commit email for
those — and **global config stays wrong**, since only this repository is covered.

## Syncing your local Claude Code settings

The same branch can carry your personal `.claude/settings.local.json` — the file Claude Code reads
as this project's local settings (permission rules, environment variables, anything else in its
settings schema). Store it on the notes branch at `.claude/personal/settings.local.json` and each
session start copies it into the project root, so permissions you'd otherwise re-grant in every
fresh clone follow you around. It should be gitignored; a committed `.claude/settings.json`
stays the shared, team-wide one.

It gets no header or markers — strict JSON has nowhere to put them — so the file is copied whole.

**Your local edits are never overwritten.** Claude Code writes to that same file whenever you grant
a permission with "don't ask again", so the hook writes only when the file is missing or still
identical to what it last synced (tracked in the gitignored `.claude/.personal-settings-sync-hash`).
Otherwise it keeps your version and says so in its summary. To push your edits up and let syncing
resume:

```bash
"${BASSTLER_PLUGIN_ROOT}/hooks/save-personal-settings.sh"
```

Or ask Claude — *"save my Claude settings"* — the same way as your notes.

## Configuration

Three independent settings, each resolved as **git config**, then **environment variable**, then
**default**:

| Setting | git config | Environment variable | Default |
| --- | --- | --- | --- |
| Remote | `claude.personalNotesRemote` | `CLAUDE_PERSONAL_NOTES_REMOTE` | `origin` |
| Branch | `claude.personalNotesBranch` | `CLAUDE_PERSONAL_NOTES_BRANCH` | `claude/personal-notes` |
| Path | `claude.personalNotesPath` | `CLAUDE_PERSONAL_NOTES_PATH` | `.claude/personal/notes.md` |

Override only what you need:

- **Remote** — when your notes don't live on this clone's `origin` (commonly: `origin` is the shared
  upstream and your fork is a differently-named remote, or isn't a remote at all). Takes a remote
  name (`myfork`) or a raw URL (`https://github.com/<you>/<repo>`); the URL form needs no
  `git remote add` first, so it works in a clone that has never heard of your fork.
- **Branch** — when several people share one remote and you don't want to collide on the default.
- **Path** — rarely needed.

### Where to put them

- **A persistent local clone** → `git config <key> <value>`, once per clone. Never committed.
- **A fresh clone every session** (cloud/web) → git config won't survive, so use the environment:
  - Your environment has a **persistent environment-variable list**: paste the variables in, per
    [`personal-notes.env.example`](./personal-notes.env.example). Nothing else to configure.
  - Your environment has a **setup script**: export the variables there, then call
    [`configure-personal-notes.sh`](./configure-personal-notes.sh) — it seeds the fresh clone's git
    config from them, and is a no-op if none are set.

For Claude Code on the web, see <https://code.claude.com/docs/en/claude-code-on-the-web> for where
either of those lives. [`src/basstler/setup/steps.py`](../../src/basstler/setup/steps.py), run as
`~/.local/share/basstler/venv/bin/python -m basstler.setup.steps`, prints the exact variable lines your
clone needs — only the settings you have moved off their defaults — alongside the other two steps
no script can perform for you.

### Fallback: your branch's own upstream

If the resolved remote doesn't have the notes branch, the hook tries one more: the remote your
currently checked-out branch already tracks. That covers the common case of a fork added under some
name other than `origin`, with no configuration at all.

Reads use the fallback; `create-personal-notes-branch.sh` never creates there. `save-personal-notes.sh`
writes back to whichever remote actually served the notes, and the header always names it.

## Starting from a fresh base

Sessions are cut from your fork's default branch. When that branch has drifted behind the upstream
it is forked from, every session in that clone plans, reviews and implements against a stale base —
and nothing looks wrong from inside the clone, because the clone is perfectly consistent with
itself. By the time a rebase surfaces the drift, the work is already written against the old base.

So every `session-start.sh` run brings that branch level, via
[`src/basstler/maintenance/default_branch_sync.py`](../../src/basstler/maintenance/default_branch_sync.py): it fetches the
upstream's base branch, fast-forwards this clone's copy of it, and pushes the result to your fork,
whose default branch is what the *next* session gets cloned from. The summary's `default branch`
line says what it did. A checked-out branch with no commits of its own — a session branch cut from
the stale base — is fast-forwarded onto the new one too; one with commits of its own gets an
indented row saying how far behind it now is, and merging or rebasing it is left to you.

Which repository is the upstream comes from the same resolution the stacked-PR tooling runs on: the
project's committed `.claude/basstler/stack.toml` names it, and a personal
`.claude/personal/stack.toml` on the notes branch can override it. A project that names no upstream
— one that is not a fork, like basstler itself — is caught up with its own remote instead: the
clone's default branch and a checked-out branch with no commits of its own move to that remote's
default branch, and nothing is pushed, since the remote is already what the next clone starts from.
Only a clone with no such remote gets a `not synced` line.

Run it by hand to catch a long-running session's clone up mid-session:

```bash
~/.local/share/basstler/venv/bin/python -m basstler.maintenance.default_branch_sync
```

### The fork's upstream-reviews Action

A fork that has the `basstler/upstream-reviews` branch (installed by `/setup-personal-notes`) runs
the Action at one basstler release, which has to be the release the session replaying its result
runs. Every session start therefore brings an Action at another release to this session's, pushing
only to that branch; the summary's `reviews Action` line says what it did. A fork without the
branch is left without it.

### `gh stack`

The stacked-PR maintenance pass and the plan item skills keep stacks with GitHub's `gh stack`
extension, so every session start runs
[`install-gh-stack.sh`](install-gh-stack.sh) and prints its verdict on the summary's `gh stack`
line. In a cloud session (`CLAUDE_CODE_REMOTE=true`) it installs what is missing: `gh` at
`GH_STACK_GH_VERSION` or newer, and the extension built from source with Go, both under
`$BASSTLER_HOME/tools`, with the `PATH` change written to `CLAUDE_ENV_FILE` for every later
command. Anywhere else it only reports what to install - `gh extension install github/gh-stack` -
unless `GH_STACK_INSTALL=1` asks it to install there too. It never fails the session start.

## Plan dashboards (multi-PR initiatives)

When one PR's progress note isn't enough — a stacked refactor, a multi-wave programme, anything
you'd otherwise write up as a one-off master-roadmap doc — a **plan** is a `plan.yaml` (waves,
tracks, and items with branch, PR number, status and dependencies) plus a sibling `roadmap.md` for
the narrative that doesn't belong in structured data.

- Worked example → [`example-walkthrough.md`](../skills/plan-dashboard/example-walkthrough.md).
- Field reference → [`plan-schema.md`](../skills/plan-dashboard/plan-schema.md).
- Create one → `/plan-create <plan-id>`.
- Publish or refresh → `/plan-dashboard [<plan-id>]`; no argument publishes the master index of
  every plan. It cross-checks every item against live GitHub PR/CI/review state, so a manifest
  can't silently go stale the way a hand-maintained roadmap doc could.
- Start or unblock one item → `/plan-item-kickoff <plan-id> <item-id>`,
  `/plan-item-resolve <plan-id> <item-id>`. Kickoff opens the item's branch and draft PR and
  marks it `in progress` as soon as its plan is approved — via
  [`src/basstler/plan/item_bootstrap/__main__.py`](../../src/basstler/plan/item_bootstrap/__main__.py), which you can also
  run by hand as `~/.local/share/basstler/venv/bin/python -m basstler.plan.item_bootstrap` — so the
  manifest never says `not started` while the work is underway.
- Decide where a new piece of work goes → `/add-plan-item <description>`. It runs the shared scope
  check in [`scope-decision.md`](../skills/add-plan-item/scope-decision.md) — the rule all four plan
  skills defer to for "is this new work, or a change to work already in flight?"
- Choose whether either skill implements on its own, plans first, or asks → `/plan-item-mode
  <auto|plan|ask> [kickoff|resolve|both]`, or the
  [`src/basstler/plan/item_mode.py`](../../src/basstler/plan/item_mode.py) `resolve|set` it calls, which you
  can also run by hand as `~/.local/share/basstler/venv/bin/python -m basstler.plan.item_mode`. Defaults in
  [`plan-item-modes.toml`](../../src/basstler/plan/plan-item-modes.toml) are `auto` for both; `set` pins a per-user
  override at `.claude/personal/plan-item-modes.toml` on the notes branch. What each mode obliges
  the skill to do, and when `auto` still stops to ask →
  [`execution-modes.md`](../skills/plan-dashboard/execution-modes.md).
- Recheck one for updates, without rereading it →
  [`plan-updates-since.sh`](./plan-updates-since.sh) `<plan-id> [--since <sha>]`. Every
  `session-start.sh` run stamps the notes-branch commit it just fetched (gitignored, at
  `.claude/.plan-state-sync-sha`), so this can diff the plan's directory from that stamp and print
  only the tracking-issue comments newer than it. Needs no Claude Code session: it prefers the `gh`
  CLI when installed, otherwise `GH_TOKEN`/`GITHUB_TOKEN` with `curl`.

**Auto-discovery.** If your branch is an item in some plan, that plan's `plan.yaml` and `roadmap.md`
are pulled into `CLAUDE.local.md` too, via a generated branch-to-plan index that `save-plan.sh`
regenerates from every manifest on each save, so it can't drift.

**Labels the dashboard reads**, all applied by this repo's convention rather than by GitHub itself:

- `merged` — the changes landed but GitHub's merge API never recorded it (branch pushed directly,
  PR then closed by hand). Treated exactly like a real merge.
- `bug` — marks the item with a `bug` chip in the dashboard's "What to do next" sidebar, in
  whichever action group it already belongs to, and is what the sidebar's "Bug fixes only" filter
  keeps. Being a bug fix is a property of the work, not a next action of its own, so it never
  moves an item into a group or out of one.
- `in-review` — the fork PR has an open upstream PR; a maintenance pass adds and removes it,
  together with the link to that upstream PR in the fork PR's description.

Any other label is preserved but not interpreted.

## Safety

- Does nothing until you create the notes branch: `git fetch` finds nothing, so `CLAUDE.local.md` is
  never written.
- Never merges and never checks anything out — the hook and `plan-updates-since.sh` only read the
  branch off `refs/basstler/personal-notes`, the ref every fetch of it is kept at.
- Catching the default branch up is fast-forward only, in every step: a default branch carrying
  commits the upstream does not is reported and left alone, and nothing is ever force-pushed. Your
  checked-out branch is never merged or rebased for you: it is fast-forwarded only when it has no
  commits of its own and no uncommitted change is in the way, and otherwise only measured against
  the default branch.
- A base that cannot be synced never stops a session: no upstream named, unresolvable remotes, an
  unreachable upstream, a diverged or uncommitted-over base and a refused push are each reported.
- Never touches your current branch or working tree: every script that writes works in a scratch
  worktree.
- `create-personal-notes-branch.sh` refuses to run if the branch already exists anywhere it can see,
  so it can't overwrite notes you already have. The save scripts are no-ops when nothing changed.
- Each save script extracts only its own markers, so neither the sync headers nor another section's
  content can leak into what it pushes.
- PR progress and plans can't be merged into a PR by construction: they're only ever written to the
  notes branch, never to a file tracked on your branch.
- Synced settings never silently replace local ones: `.claude/settings.local.json` is written only
  when it's missing or unchanged since the last sync, so "don't ask again" grants survive until you
  run `save-personal-settings.sh` yourself.
- The git identity sync only ever fills a gap: it writes repository-local config, and only when the
  clone has none of its own. Global config is never written.
- `CLAUDE.local.md`, `.claude/settings.local.json` and the recheck stamp
  `.claude/.plan-state-sync-sha` are all gitignored.
- Always operates on this repo's project root, resolved from the scripts' own location on disk —
  not the caller's cwd, which a `SessionStart` hook can't rely on.
- Coexists with your own `SessionStart` hooks: Claude Code concatenates hook arrays across settings
  layers rather than overriding them.
