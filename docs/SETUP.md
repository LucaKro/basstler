# First-time setup

Five steps, once per person and project. They give you personal notes that follow you
across sessions, per-pull-request progress tracking, and multi-PR plan dashboards.

Details of everything below: [`hooks/README.md`](../plugin/hooks/README.md).

## 0. Add the plugin

basstler is a Claude Code plugin, and this repository is its marketplace.

- **Cloud sessions (claude.ai):** add it under Customize > Plugins, from the marketplace
  `AbdelrhmanBassiouny/basstler` - or have an organization Owner install it for members.
  Plugins a repository names in its own settings do not load in cloud sessions.
- **Local sessions and CI:** `/plugin marketplace add AbdelrhmanBassiouny/basstler`, then
  `/plugin install basstler@basstler` - or enable it for everyone working on a project in
  its committed `.claude/settings.json` (`extraKnownMarketplaces` and `enabledPlugins`).

The plugin's session start installs the package at the plugin's own version into
`~/.local/share/basstler/venv` (`$BASSTLER_HOME/venv` when set) - nothing to install by hand.

## 1. Run the setup

In any Claude Code session on the project:

```
/setup-personal-notes
```

It creates your notes branch, records the git identity your commits are authored
with, installs what the dashboards need, and asks only about the decisions that
are genuinely yours. Re-running it on a set-up project changes nothing.

Without a session, the same setup is one command, once the package's environment exists:

```bash
~/.local/share/basstler/venv/bin/python -m basstler.setup.personal_notes \
  --remote <your-fork-remote-or-url> --name "Your Name" --email "you@example.com"
```

`--help` lists the rest: seeding the notes from the starter template, and creating the
labels your fork lacks.

## 2. Do the steps no script can do

```bash
~/.local/share/basstler/venv/bin/python -m basstler.setup.steps
```

The session start created that environment; see the repository's [`README.md`](../README.md).

It prints these three, already filled in for your fork:

| Where | What |
| --- | --- |
| Your fork | Create the `merged`, `bug` and `in-review` labels. |
| Your Claude settings | Give Claude access to the fork on GitHub. |
| Your Claude environment | Paste the `CLAUDE_PERSONAL_NOTES_*` variables — only if you moved a setting off its default, and only for environments that clone fresh each session. |

## 3. For the stacked-PR workflow: name the project's upstream

Only if the project uses `/stacked-pr-maintenance` or `/upstream-reviews`. Both skills
ask for it themselves the first time they need it, so there is nothing to do ahead of
time. To name it yourself, save the repository the project's pull requests are reviewed
on to `.claude/personal/stack.toml` on your personal-notes branch, so the project commits
nothing of basstler's:

```bash
~/.local/share/basstler/venv/bin/python -m basstler.setup.steps configure-upstream --repository <owner>/<name>
```

A project that wants every contributor to share the answer commits
`upstream_repository = "<owner>/<name>"` in `.claude/basstler/stack.toml` instead.

Stacks are GitHub's own stacked pull requests, kept with the `gh stack` extension. A cloud
session installs it at its start; on your own machine install `gh` 2.90 or newer and run
`gh extension install github/gh-stack` once. Every session start's `gh stack` line says whether
it runs.
`check-setup.sh --require-upstream` reports whether either names one.

`/upstream-reviews` reads the upstream in your fork's own Actions, from a branch of
basstler's that shares no history with the project - so neither the project nor your
fork's default branch carries a basstler file. `/setup-personal-notes` installs it once
an upstream is named; to do it yourself, or to bring it to a new release:

```bash
~/.local/share/basstler/venv/bin/python -m basstler.setup.steps install-upstream-reviews-action --branch-or-tag <a basstler release tag, or main>
```

That writes `basstler/upstream-reviews` on your fork: a workflow calling basstler's
reusable one, and a README. Each read pushes a request there, and the run commits what
it read back to the same branch. The one step left to you: GitHub starts every fork with
Actions off, so enable them once at `https://github.com/<your fork>/actions`.

Until then, or wherever the Action cannot answer, the skill reads the upstream from the
session. In a Claude Code cloud session it attaches the upstream and reads it over REST;
that needs the upstream to be a repository your GitHub account can read. A session
working in your fork cannot attach the same-named upstream beside it, so there the skill
starts a short-lived helper session with only the upstream, reads its result and
archives it, all in the same turn. Anywhere else, such as Claude Code in your own
terminal, it reads GraphQL as whoever `gh` is logged in as (`gh auth login`, or
`GH_TOKEN`). Every way needs only read access to the upstream, never push access.

A project that prefers a dispatch-only workflow on its default branch may still copy
[`examples/upstream-reviews.yml`](examples/upstream-reviews.yml) to its
`.github/workflows/upstream-reviews.yml`; that caller reads the committed
`.claude/basstler/stack.toml`. It calls this repository's reusable workflow and installs
the package, both from `main`; to pin a release instead, change both references to its
tag.

## 4. Check it worked

```bash
"${BASSTLER_PLUGIN_ROOT}/hooks/check-setup.sh"
```

Every row `ok` and you are done. Anything left says what it needs. Every session
start prints the same verdict, so you never have to remember to check.
