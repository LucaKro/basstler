---
name: setup-personal-notes
description: One-time setup of the personal-notes branch, its configuration and the plan-dashboard dependencies. Invoke as "/setup-personal-notes". Use on first setup, or when personal notes, PR progress or dashboards are not working.
allowed-tools: Bash, Read, Write, AskUserQuestion, mcp__github__get_me, mcp__github__get_label
---

# Set up personal notes

Gets a clone from "I have a fork of this repo and nothing else" to "personal
notes, PR progress and plan dashboards all work," asking one question per
decision that is genuinely the user's, with a default they can accept as-is.

**Fast path first.** Every step below is conditional on
[`check-setup.sh`](../../hooks/check-setup.sh) reporting that specific thing
missing. If it reports nothing missing, check only the labels the script can't
see (step 5's last part) and stop there. A re-run on an already-set-up clone
must be a near-no-op, not an interrogation.

**Never guess a value that is the user's to choose.** Where their notes live
and what goes in them are personal; ask, with a default. Everything else
(installing a documented dependency, running a read-only script) is mechanical
— just do it.

## 1. Find out what is actually missing

```bash
source "${BASSTLER_PLUGIN_ROOT}/hooks/resolve-personal-notes-config.sh"
bash "${CHECK_SETUP_SCRIPT}" $ARGUMENTS || true
```

`$ARGUMENTS` is whatever this skill was invoked with: `--require-upstream`
when a skill needing the stacked-PR workflow's upstream ran it, nothing
otherwise. Pass the same to every run of the check below.

The script prints one tab-separated `<check>` / `<status>` / `<detail>` row per
check and exits non-zero if any row is `needs-setup` (`|| true` above keeps a
non-zero exit from ending the block early — the rows are the point, not the
status). Read its own header comment for what each check means.

**If it exited 0:** tell the user everything it covers is already set up, list
what was found (the remote, branch and path in play, and that dependencies and
`CLAUDE.local.md` are in place), then check the labels as step 5 describes and
stop. Do not ask
anything else. Do not re-verify by hand what the script just verified.

**An `in-progress` row** means a session start is installing that part right
now. Never install it yourself, and never run a `pip` command for it: wait with
`wait_for_basstler_install` (defined by the script sourced above), then run the
check again.

**Otherwise:** work through the `needs-setup` rows in the order printed —
they're ordered so an earlier fix can be a prerequisite of a later one — using
the steps below. Skip the step for any check already `ok`.

## 2. `tooling_files` / `session_start_hook` — the plugin is incomplete

Not fixable from here: the basstler plugin's own files are missing. Tell them to
reinstall the plugin (claude.ai Customize > Plugins, or `/plugin` in a local
session) and re-run. Stop; every later step depends on those files.

## 3. `claude_local_md_ignored` — CLAUDE.local.md could be committed

The project's `.gitignore` does not ignore `CLAUDE.local.md`, so the notes
could end up in a commit. Offer to add `CLAUDE.local.md`,
`.claude/settings.local.json`, `.claude/.personal-settings-sync-hash` and
`.claude/.plan-state-sync-sha` to it; ask before changing the tracked file.

## 4. Ask what only the user can answer

Steps 4 and 5 cover the `notes_branch`, `notes_file`, `git_identity`,
`dashboard_dependencies` and `claude_local_md` rows, and the pull request
labels `check-setup.sh` cannot see. Everything mechanical about them is one
command that needs no session. This step only collects its arguments; ask
each question only when its row is `needs-setup`.

- **Where the notes live** (`notes_branch`). Is the notes remote the user's
  own fork, or a shared upstream they can't push to? A git remote URL doesn't
  say who owns it. Read the `notes_remote_url` row and get the user's GitHub
  login with `mcp__github__get_me`. If the URL's owner is that login, that
  remote is the answer. Otherwise ask where their notes should live, offering
  `https://github.com/<their-login>/<repo>` as the default, and note that a
  remote name already in the clone works too.
- **Starter notes** (`notes_file`). Offer to seed the new notes from
  [`starter-notes.md`](./starter-notes.md): conventions for pull requests,
  review comments and progress tracking, which they own and can edit. Default
  to yes. An empty file is fine too.
- **Their git identity** (`git_identity`). Ask for the name and email their
  commits should carry. Never take them from this clone's git config, which
  in a fresh session is the agent's own identity.

Then tell them plainly that **the remote choice does not survive a fresh
clone** (Claude Code on the web, any cloud environment). The command records
it in this clone's git config, and that config is gone next session. For
those environments the same value has to be a persistent environment
variable, which no command inside a session can set. Give them the lines to
paste, only the ones that differ from the defaults:

```
CLAUDE_PERSONAL_NOTES_REMOTE=<chosen-remote-or-url>
CLAUDE_PERSONAL_NOTES_BRANCH=<branch, only if not claude/personal-notes>
CLAUDE_PERSONAL_NOTES_PATH=<path, only if not .claude/personal/notes.md>
```

Point them at their environment's docs for where that list lives (for
Claude Code on the web:
<https://code.claude.com/docs/en/claude-code-on-the-web>), and at
[`personal-notes.env.example`](../../hooks/personal-notes.env.example) and
[`configure-personal-notes.sh`](../../hooks/configure-personal-notes.sh).
This part stays the user's to finish, so say so.

The branch and path have working defaults. Mention that both can be
overridden (`claude.personalNotesBranch` / `claude.personalNotesPath`), and
ask only if they want something else.

## 5. Run the setup

```bash
wait_for_basstler_install && "${BASSTLER_PYTHON}" -m "${SETUP_PERSONAL_NOTES_MODULE}" --remote <remote> \
  [--name "<name>" --email "<email>"] [--starter-notes]
```

`wait_for_basstler_install` returns at once unless a session start is still
installing the package this command runs, and waits for it otherwise; if it
fails, the install is still running after five minutes, so report that and
stop.

It refuses a remote GitHub says someone else owns (exit 3) before writing
anything. It records the remote in git config, creates the notes branch if
it's missing, seeds a notes file only if it didn't exist yet, records the
identity, and runs `session-start.sh`, which installs the package's
dependencies and writes `CLAUDE.local.md`. Then it reports which labels this
tooling uses are missing from the repository, and ends by printing
`check-setup.sh`'s report. It exits with that report's status: 0 when
nothing is left, 1 when something is.
Exit 4 means a hook script failed; its own output above says why.

The owner check and the label check need a GitHub token (`GH_TOKEN` or
`GITHUB_TOKEN`) and a notes remote on GitHub; without them the command says
it skipped both and carries on. A session usually has no token, so check the
labels with `mcp__github__get_label` instead, one call per label that
`"${BASSTLER_PYTHON}" -c 'from basstler.workflow_labels import WorkflowLabel; print(*WorkflowLabel)'`
lists. A `404` means missing.

**Labels are visible to everyone who sees the repository, so ask before
creating any.** If the user agrees, run the same command again with
`--create-labels`. Every other step is a no-op the second time. Where there
is no token, `"${BASSTLER_PYTHON}" -m "${SETUP_STEPS_MODULE}"` prints the
`gh label create` commands and the labels page for them. Missing labels don't
block anything else.

The notes it just wrote reach this conversation's context only at the next
session start; everything else is usable right away.

## 6. `stack_upstream` — the repository the project is reviewed on

Only when this row is `needs-setup`: an `info` row means nothing asked for an
upstream, and most projects have none.

The upstream is the repository the project's pull requests are reviewed on,
which is the user's to name. Ask for it as `<owner>/<name>`, offering as the
default the repository the clone's `upstream` remote points at, if it has
one. Then save it to their
personal `stack.toml`, which keeps it out of the project's tracked files:

```bash
"${BASSTLER_PYTHON}" -m "${SETUP_STEPS_MODULE}" configure-upstream --repository <owner>/<name>
```

It keeps every other personal stack setting and replaces an upstream named
before. A project that wants every contributor to share the answer can commit
`upstream_repository` in `.claude/basstler/stack.toml` instead; the check
accepts either.

## 7. `upstream_reviews_action` — the fork's own upstream-reviews Action

Only once an upstream is named (the row says "not checked" otherwise). Unless
the row is `ok`, install it without asking - it is mechanical, and touches
nothing but a branch of its own on the user's fork:

```bash
"${BASSTLER_PYTHON}" -m "${SETUP_STEPS_MODULE}" install-upstream-reviews-action --branch-or-tag "$(basstler_branch_or_tag)"
```

It creates a new branch named `basstler/upstream-reviews` on the user's fork,
with no history in common with the project's branches. The branch holds only a
README and a short workflow file that calls basstler's reusable workflow by
reference, so it works on a fork of any repository. Run again, it updates that
workflow to the basstler branch or tag this session's package was installed at,
which every session start also does once the branch exists.
Then tell
the user the one step left to them, which `"${BASSTLER_PYTHON}" -m basstler.setup.steps` also
prints: a fork starts with Actions off, so they enable them once at
`https://github.com/<fork>/actions`. Until they do, `/upstream-reviews` reads
the upstream from the session instead.

## 8. Confirm, by re-running the check

```bash
bash "${CHECK_SETUP_SCRIPT}" $ARGUMENTS || true
```

Report what the second run says, not what you expect it to say. If a row is
still `needs-setup`, say which and why — a setup that half-worked and was
reported as finished is worse than one that reported the truth.

Then tell them what they can now do, briefly: ask for notes edits in any
session, track a PR's progress, and — if the dependencies installed —
`/plan-create` and `/plan-dashboard`, pointing at
[`example-walkthrough.md`](../plan-dashboard/example-walkthrough.md) for the
worked example.

## What this skill must never do

- **Never invent where someone's notes live.** A wrong remote pushes personal
  notes to a repository they didn't choose. Ask, always, unless the resolved
  remote is already provably their own.
- **Never claim the environment-variable half is done.** No command inside a
  session can persist an environment variable for the next fresh clone.
- **Never touch an existing personal-notes branch's contents.** Every write
  here is either creating a branch that provably doesn't exist yet, or a
  no-op-if-unchanged push of a file the user just asked for.
- **Never create labels in someone's repository unasked.** Labels are visible
  to everyone who sees the repository; report what's missing and offer.
