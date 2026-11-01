# basstler

Workflow tooling for Claude Code sessions: personal notes that follow you across sessions,
per-pull-request progress, multi-PR plans with live dashboards, maintenance of stacked pull
requests kept as GitHub's native stacks (`gh stack`), and an upstream review reader.

The name is the German word for someone who builds things themselves, and shares its
first letters with the surname of the person who wrote it, Bassiouny.

Setting it up: [`docs/SETUP.md`](docs/SETUP.md).

## What is in this repository

| Path | What it is |
| --- | --- |
| `plugin/` | The Claude Code plugin: its manifest, the skills, and the bash hooks the skills and the session start run. |
| `.claude-plugin/marketplace.json` | The marketplace that lists the plugin, so this repository is what you add it from. |
| `src/basstler/` | The Python package every hook and skill runs, as `python -m basstler.<module>`. |
| `tests/` | The test suite, run by `pytest tests`. |
| `docs/` | The setup guide and the stacked-PR workflow. |

The plugin carries no Python, and the package carries no hooks: a session start installs the
package at the plugin's own version, so the two are always one release.

## Releasing

Every push to `main` that changes anything is a release, made by `release.yml`. An install
takes the package from the latest release tag, so a change merged without one would reach
some installs and not others: a plugin's hooks would call a package that predates them.
Every change counts, not only those to the package, since a list of shipped paths would go
stale.

- **A release is only a tag on the merge commit.** No file names the version - the build
  reads it from the tag (`hatch-vcs`), and `plugin/.claude-plugin/plugin.json` names none -
  so nothing is pushed to `main` after a merge. A plugin synced from the merge is already
  the release; a version commit pushed seconds after the merge is what a synced plugin used
  to miss, leaving it installing the release before.
- **The workflow increments the latest release's patch,** or its minor or major part when a
  pull request merged since is labelled `release:minor` or `release:major`. It then
  publishes a GitHub release with generated notes.
- **A session start installs the highest `v<major>.<minor>.<patch>` tag** of this
  repository. One that starts between a merge and its tag gets the release before, until
  its next start.

## The package's own environment

**Every session start gives `basstler` an environment of its own, without asking:**
`~/.local/share/basstler/venv` - `$BASSTLER_HOME/venv` when `BASSTLER_HOME` is set - from an
interpreter the package supports, with the package installed into it. One per user rather
than one per clone, so a fresh clone of any project the plugin serves needs no install of
its own.

What it installs from depends on where the plugin is:

- **A plugin installed on its own** installs `basstler @ git+https://github.com/AbdelrhmanBassiouny/basstler@<tag>`,
  the tag being the latest release, read once per session start; offline, it keeps the one
  it last read.
- **A plugin run from a clone of this repository** installs that clone editable, so
  `import basstler` is the clone's own source.

The environment comes from `uv` where it is on the path, since `uv` can fetch a supported
interpreter where none is installed, and otherwise from the newest supported `python3.1x`
on the path. A machine with neither - one whose only interpreter is too old - gets `uv`
installed into `$BASSTLER_HOME/uv` with the `pip` it has, and `uv` fetches the interpreter.
`uv` also does the installing wherever there is one, since `pip` runs inside the
environment's own interpreter and an older `pip` breaks on a newer one.

Three things bound it:

- **Only for someone who has already set the tooling up.** The hook stops before this
  point when it cannot reach your personal-notes branch, so a project that has never run
  `/setup-personal-notes` installs nothing at all.
- **Only what is missing or out of date.** The usual start finds the environment installed
  from the source the plugin names, looks the requirements up and runs no installer. A
  plugin update names a new source, and the environment is installed into again.
- **Never fatal.** No `pip` to install `uv` with, or no network: the summary's
  `dependencies:` line says so, naming the command to run, and the rest of the run carries
  on.

An Actions runner reaches no session hook, so a workflow that runs a module installs the
package itself - `tests/test_package_contract.py` finds every such workflow and checks that
it does.

## Running it

Every entry point is run as a module, by the package's own interpreter:

```bash
~/.local/share/basstler/venv/bin/python -m basstler.stack configuration
~/.local/share/basstler/venv/bin/python -m basstler.maintenance run-report --json
~/.local/share/basstler/venv/bin/python -m basstler.dashboard.plan_page --help
```

The hooks and the skills that call them all use `"${BASSTLER_PYTHON}" -m "${SOME_MODULE}"`,
with the interpreter and each module named once in
`plugin/hooks/resolve-personal-notes-config.sh`. A session finds that file through
`BASSTLER_PLUGIN_ROOT`, which the plugin's session start exports.

## Developing it

```bash
uv venv --python ">=3.12" .venv && uv pip install --python .venv/bin/python --editable . pytest pytest-xdist
PATH="$PWD/.venv/bin:$PATH" pytest tests -n auto
```

The hook tests run `python3`, so the environment has to be first on the path. Python is formatted
by `python scripts/format_docstrings.py <files>` (black, then docformatter where black keeps
its result), also run as a pre-commit hook. To try a
change in a real session, start Claude Code with `--plugin-dir plugin`: the plugin then
sits in this clone, and installs the clone editable.

## Licence

basstler is a personal project by Abdelrhman Bassiouny (<bido.bassuny@gmail.com>), developed
independently of any employer or institution. It is licensed under the
[Apache License 2.0](LICENSE); see [NOTICE](NOTICE) for attribution.

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
