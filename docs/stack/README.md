# Stacked-PR workflow (fork staging → upstream review)

High-velocity, review-constrained workflow. This fork (`origin`) holds the **full stack** of
in-flight branches; **the upstream** is the slow review queue. You promote approved branches
to the upstream as their parents land. Claude does the mechanical restacking so the stack never rots
and you keep coding.

## The rationale (why)

The reviewers are the constraint. Throughput dies from big PRs and unbounded work in review,
so: keep each PR small and single-concern, and make stack maintenance free. (Stacked diffs +
trunk-based small batches - Graphite/Sapling, DORA/*Accelerate*, Reinertsen, Theory of
Constraints.)

## GitHub is the source of truth

You never hand-edit a ledger. The stack is read from **GitHub itself** plus git:

| What | Where it lives | You set it by |
|---|---|---|
| dependency **tree** (parent) | each fork PR's **base branch** (`base = parent`) | retargeting the PR base on GitHub - from a session, only via the GitHub MCP `update_pull_request` tool (see the maintenance skill) |
| `draft` ↔ `ready` | the fork PR's **draft toggle** | un-drafting when you approve it |
| `in-review` | an **open upstream PR** whose head is the fork branch | opening the upstream PR - a maintenance pass mirrors it in the **`in-review`** label and an upstream link in the fork PR's description, adding and removing both |
| `merged` | branch is an ancestor of `<upstream_remote>/<upstream_base>` | nothing - GitHub marks the PR merged itself once its head is contained in its base |
| `merge` vs `rebase` | the **`rebase`** label; default `merge` | labelling on GitHub |
| upstream create-link built | the **`promotion-link-sent`** marker | nothing - a maintenance pass sets it when it puts a create-link in the PR description, and clears it once the upstream PR is open |
| CI | the **checks** on the fork PR's head | nothing - `status` shows their verdict, and a branch whose checks failed is not promoted |
| conflict/CI-red reported | the **`needs-resolution`** label | nothing - a maintenance pass sets it when it reports a restack conflict to the branch's owning session, and clears it once the branch stops conflicting |

## Files

Every module named below lives in the `basstler` package, `src/basstler/`, and runs
from the package's own environment; `stack.toml` sits beside them. This document stays here,
next to the workflow it describes, because it is read rather than run.

- **`stack.toml`** - the shipped defaults: label names and the upstream base branch. It names
  nobody's repository. The project names its own upstream in its committed
  `.claude/basstler/stack.toml` - `upstream_repository = "<owner>/<name>"`, the one repository
  that is the same for every contributor - which outranks the shipped values. The fork is
  *whichever remote is not the upstream*, matched by the repository each URL points at rather
  than by what the remote is called, so `origin` may be either one. A
  `.claude/personal/stack.toml` on the personal-notes branch layers your own overrides on top of
  both (see `Configuration.load` in `stack/settings.py`), including a `fork_repository` to pick between
  remotes when more than one could be the fork.
- **`board.json`** - the fork-PR snapshot (`number`, `head`, `base`, `draft`, `labels`, `check_state`,
  `session`) that `basstler.stack` reads. Written from GitHub as scratch by whatever refreshes it,
  to `basstler/board.json` under the repository's common git directory
  (`git rev-parse --git-common-dir`): one file every worktree of the repository shares, which no
  checkout can commit and no install of the package holds. The maintenance pass's full report sits
  beside it as `basstler/maintenance-report.json`.
- **`basstler.stack`** - read-only status tool (never mutates branches). Reads `board.json` + git:
  - `~/.local/share/basstler/venv/bin/python -m basstler.stack status` - the whole stack, with ahead/behind drift per parent.
  - `~/.local/share/basstler/venv/bin/python -m basstler.stack check` - would each branch integrate cleanly onto its parent
    *now* (fast, non-mutating `git merge-tree` probe)?
  - `~/.local/share/basstler/venv/bin/python -m basstler.stack next` - every branch ready to submit upstream next: approved,
    parent landed, not withheld. **This is your "what goes upstream next" answer.**
  - `~/.local/share/basstler/venv/bin/python -m basstler.stack next --porcelain` - machine-readable `next`: one
    `name<TAB>pr` line per branch to promote (or nothing).
  - `~/.local/share/basstler/venv/bin/python -m basstler.stack restack-plan` - the bottom-up restack plan as JSON (one
    `{branch, parent, strategy}` per not-yet-`merged` branch, in-review ones included so they
    pick up a moved parent via a conflict-free `merge`).
  - `~/.local/share/basstler/venv/bin/python -m basstler.stack configuration` - every resolved setting as `key<TAB>value`
    lines, keyed by `Configuration`'s own field names: the labels, the upstream base, which
    remote is the fork and which is the upstream, and `upstream_location`, what the upstream is
    fetched from: its remote's name, or its URL when the clone has no remote for it. Either way
    its base is kept at `refs/remotes/<upstream_remote>/<upstream_base>`, so no step needs a
    remote added. Answerable from git alone, so it runs before `board.json`
    exists. It takes `--fork`/`--upstream` for a caller that already knows the answer, and exits
    `4` rather than guessing when nothing does. This is the one surface shell tooling reads
    configuration through - parsing `stack.toml` directly would miss the personal override.
  - `~/.local/share/basstler/venv/bin/python -m basstler.stack labels --current <label> --add <label> --remove <label>` - the
    **complete** label set to write back, one per line. GitHub's label write replaces the whole set,
    so computing it from the intended change alone silently strips the rest; this is what the
    maintenance skill passes to every label write rather than working it out itself.
  - `~/.local/share/basstler/venv/bin/python -m basstler.stack check-move --action push --source B --destination B
    --destination-remote <remote>` - exits `0` when the move is safe and `5` with its reasons on
    stderr when it is not: wrong branch checked out, a push naming different branches on each
    side, a destination that is not the fork, or a push that would make a child an ancestor of its
    own parent (which GitHub reads as a merged pull request).
  - `~/.local/share/basstler/venv/bin/python -m basstler.stack promotion-link --branch B --title T --body ...` - the upstream
    compare-and-create URL, encoded and within the length limit, warning on stderr when the body had
    to be shortened.
  - `~/.local/share/basstler/venv/bin/python -m basstler.stack reparents` - one `branch<TAB>pr<TAB>current base<TAB>target base`
    line per open PR whose base has already landed, including a base whose own PR was *closed* and
    which is therefore absent from `board.json`.
  - `~/.local/share/basstler/venv/bin/python -m basstler.stack landed` - one `name<TAB>pr` line per open fork PR whose branch
    is already in the upstream base. Reporting only: fast-forwarding the fork's copy of the
    upstream base is what actually closes them.
- **`basstler.maintenance`** - the executor: the half of a pass that moves commits, where `basstler.stack`
  only derives and prints. `board --write`, `prepare` (the reparents and what each upstream
  summary is written from, as JSON), `fast-forward`, `restack`, `promote`, `checks` (the verdict
  over every open branch's checks) and `run-report` (the whole pass, ending in a size-bounded
  finish summary with the full report in a file, or `--json` for tooling); see [Running a maintenance pass](#running-a-maintenance-pass). It is the
  command line onto modules named for what they do, so nothing has to be hunted for inside one
  long file: `maintenance/constants.py` (every value edited by hand),
  `maintenance/git_commands.py`, `maintenance/board.py`, `maintenance/description.py`,
  `maintenance/fast_forward.py`, `maintenance/restack_steps.py` and
  `maintenance/restack_procedure.py` (the steps, and the order that is the procedure),
  `maintenance/promotion.py`, `maintenance/promotion_context.py`,
  `maintenance/check_overview.py`, `maintenance/report.py`, `maintenance/finish_summary.py` and
  `maintenance/commands.py`, with the git runner and the GitHub client they call coming
  from `basstler_domain_model`. A skill runs it through `plugin/hooks/basstler.sh`, which waits on
  or completes the package install first.
- **`skills/stacked-pr-maintenance/SKILL.md`** - the maintenance instructions, invocable
  as `/stacked-pr-maintenance` from any session, and run first by `/plan-item-kickoff` and
  `/plan-item-resolve`. It takes `fork=` / `upstream=` arguments, falls back to
  `configuration`, and asks when neither answers. It carries only the steps; why each rule
  holds is in [maintenance-pass.md](maintenance-pass.md).

## The state machine (your approval gate)

`draft` → **`ready`** → `in-review` → `merged`, all derived from GitHub:

- `draft → ready` is **your gate**: self-review the fork PR and **un-draft it** on GitHub to
  approve. `basstler.stack next` only ever promotes a `ready` (un-drafted) branch - nothing reaches
  the upstream without your sign-off.
- `ready → in-review`: when you open its upstream PR. The next maintenance pass adds the
  **`in-review`** label to the fork PR, and removes it if the upstream PR is closed unmerged.
- `in-review → merged`: automatic once the branch lands in `<upstream_remote>/<upstream_base>` (git ancestry).

## The loop you run

1. Code at full speed on top of your stack tip; open each PR with **`base` = its parent branch**.
2. **Self-review the bottom fork PR.** If good, **un-draft it** on GitHub. ← the gate.
3. `~/.local/share/basstler/venv/bin/python -m basstler.stack next` → it names every approved, unblocked branch whose
   checks have not failed. Open its upstream PR; the next maintenance pass labels the fork PR
   **`in-review`** and links the upstream PR in its description.
4. When the upstream merges it: nothing to edit - it becomes `merged` automatically. Run a maintenance
   pass to cascade the new base up the stack; `status`/`check` confirm it's clean again.

## Running a maintenance pass

Everything in step 4 - reparenting a pull request whose base has landed, fast-forwarding the
fork's copy of the upstream base, restacking whatever the move left behind, and building the
promotion links for whatever is now ready - is one skill. From any session:

```text
/stacked-pr-maintenance
```

With no arguments it resolves the fork and the upstream from your checkout, and asks once if it
cannot; the answer is saved to `.claude/personal/stack.toml` on your personal-notes branch, so it
never asks twice. Pass them explicitly to skip resolution entirely:

```text
/stacked-pr-maintenance fork=<owner/repo> upstream=<owner/repo>
```

Nothing runs it on a schedule: `/plan-item-kickoff` and `/plan-item-resolve` run it before
they start on an item, so a stack is brought up to date when someone works on it.

### One restack merge per branch

A `merge`-strategy branch is restacked by merging its parent's moved tip in, so a pull request
that waits through many passes would collect one merge per pass. Instead, each restack drops the
merges it made earlier at the branch's tip that only brought in what the branch is about to hold
anyway, and merges the parent's current tip in once - so the branch carries a single restack merge
however long it waits. A restack marks its merges with a `Restacked-parent:` trailer naming the
parent and a `Restacked-parent-commit:` trailer naming the parent commit it merged in, and only
merges carrying both, with that commit being the one actually merged in, are ever dropped: a merge
somebody made by hand is kept, even one reusing an earlier restack merge's message. A marked merge is
dropped only when the parent and the branch's own commits together hold every change it brought
in - which still holds after the branch is reparented onto a base that lacks its former parent's
commits - and its content is exactly what merging its two sides produces: a merge carrying a
conflict resolution, one bringing in another branch, and one with work committed on top of it are
all kept. Replacing merges rewrites the branch, so it is pushed under a lease and never over a push
the pass did not see. The replacing merge goes onto the deepest commit beneath the dropped merges
it merges cleanly onto, trying shallower ones in turn; when none merges cleanly, the parent is
merged onto the published tip as before.

## Rules of hygiene

- **One branch ⇄ one session.** Never point two live sessions at the same branch (force-push
  races).
- **The branch is the durable state** - commit + push often; cloud containers are ephemeral.
- **Restack only after the parent has landed/updated.** Restacking onto a still-conflicting,
  unmerged parent is premature - land the parent first.
- **Refresh `board.json` before acting.** It's a snapshot; a pass exports a fresh one from
  GitHub.
- **CI is the validator.** A suite the cloud container cannot run (missing system
  dependencies, hardware, services) is never run locally - poll a PR's CI with the GitHub MCP and treat its
  red/green as the oracle (no session subscribes to a PR's activity - CI is learned by polling; see [maintenance-pass.md](maintenance-pass.md)). A
  maintenance pass never fixes a failing check: it reports the branch to its owner and moves on.
  Never disable a leak/CI check to go green.
