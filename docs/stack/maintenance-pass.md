# The maintenance pass: why its rules hold

`plugin/skills/stacked-pr-maintenance/SKILL.md` carries only the steps a pass takes. This page is
the reasoning behind them, for whoever changes the skill or the executor. The workflow itself is in
[README.md](README.md).

## What GitHub records, and what the pass reads from it

- A fork pull request's base branch is its parent in the stack.
- A fork pull request stays a draft until its author has reviewed it; taking it out of draft is the
  only signal that it may be promoted. Nothing else grants approval.
- A branch is under upstream review while an upstream pull request opened from it is open. The
  board export reads that from the upstream, and the pass mirrors it in the `in-review` label
  and a link to the upstream pull request in the fork pull request's description, adding and
  removing both; a session never writes either itself. An upstream the export cannot
  read stops the pass rather than being guessed from the label.
- A branch whose checks failed is not promoted; the export records the verdict over each head's
  checks.
- A branch that is an ancestor of the upstream base has landed.

## Why the pass never touches code or reviews

Every branch in the stack belongs to somebody else's session. A conflict resolved by the pass is a
change to that branch its owner never saw, so a conflict is reported to its owner and left exactly
where it is. Review threads are the owner's work for the same reason.

A pull request activity subscription delivers review comments and threads, not just CI, and turns on
the per-event handler that makes a session investigate, plan and reply - which is how a maintenance
run turns into review work. So CI is learned by polling, and a review event that reaches the pass
ends its turn.

Claude Code's Workflow tool - not a GitHub Actions workflow - fans work out to subagents; for a
short sequence of git and API calls that only multiplies the chance of two agents pushing the same
branch. Nothing in a pass needs it: the executor fast-forwards with plain git and rebases with
`gh stack`.

## Why reparenting comes first

A child left on a landed base cannot reach the upstream base, and is closed outright the moment
that base branch is deleted. The inflated diff it shows is a symptom, not the problem. Retargeting
it is what lets the fast-forward finish it.

`reparents` decides by git ancestry, so it also covers a base whose own pull request was *closed*
rather than merged - one absent from the board entirely.

### Base changes stay with the session

The executor never changes a base itself and leaves `reparents` to the session, after `prepare`. That followed from a
session's `GH_TOKEN` being refused a base change with a 403. On 2026-10-26 the same
`PATCH /repos/{owner}/{repo}/pulls/{number}` with `{"base": ...}` and
`Content-Type: application/json` succeeded both ways, so the refusal no longer holds and moving
the reparent into the executor is open work. Until it lands, the session makes the change with the
MCP `update_pull_request` tool.

### The 422: native stacks

Changing the base of a pull request in a GitHub stack fails with
`422 - Cannot change the base branch because the pull request is part of a stack`. That is GitHub
protecting the stack's structure, and dissolving the stack clears it. A pull request is a member
when its REST JSON, fetched with `X-GitHub-Api-Version: 2026-03-10`, carries a non-null `stack`
object. The stacks endpoints have no MCP tool, so they need curl.

Unstacking has no selective form: it drops every open, draft and closed member and keeps merged
ones, and there is no undo - hence recording the list first. Closing the child and opening a
replacement would lose its number, labels and review thread for a base change that is available.
Fast-forwarding the landed base instead moves the merge-base so the diff looks right, but the child
still targets a branch about to disappear, and when that base is a stack's trunk it desynchronises
the stack's recorded `base.sha` from its real head. The API is a preview, so the pass never
improvises around an answer it does not recognise.

## Why a pass costs the session so little

A session pays for every tool call and every byte a command prints, and the stack grows without
bound. So the pass is shaped around what needs judgement - writing an upstream summary - and
everything else is an executor command with a bounded output:

- **`basstler.sh`** runs every module. A skill that started before the session start's install
  finished waits for it (the install holds a lock with its process id), one whose install died is
  completed, and one that still cannot run gets one line saying what to do - never a traceback that
  sends the session reading hook scripts.
- **`prepare`** exports the board and prints, as one JSON document, the reparents and what each
  summary is written from: the fork pull request's title and a capped description, its commit
  subjects and its largest changed files. That is what an upstream reviewer's first paragraph needs;
  a raw diff is tens of kilobytes per branch and adds nothing a summary should say. A branch is
  listed only until its link is recorded - the `promotion-link-sent` label already marks that, so
  nothing has to remember summaries between passes, and an unchanged branch is never summarized
  twice.
- **`run-report`** exports its own board, so a reparent made after `prepare` is already in it, and
  prints a summary-first report: the status, one line of counts, then each section capped at a few
  entries and every conflict at a couple of files, the rest counted. The full report goes to a file
  beside the board, every conflict is already in its comment, and every create-link is already in
  its pull request's description, so the summary repeats none of them. Tests hold the summary under
  7 KiB for boards of 150 to 600 pull requests.
- **CI** is read when the board is exported: each pull request's head, at the start of the pass.
  Right after a push every check is still queued, so polling then learns nothing; by the next pass
  they have finished. The pass therefore never waits on CI - what it pushes is reported by the next
  one - and a branch whose checks failed is withheld from promotion. `checks` prints the same
  verdicts on their own.

Re-deriving the summary, re-reading the full report, or running the individual commands as well
does the same work twice and can disagree with what actually ran. `--json` exists for tooling; a
session never needs it.

`integration-failed` means rebasing onto the parent failed without conflicting on anything -
`gh stack` not runnable, a branch checked out in another worktree, a rebase already in progress.
The branch is not what needs fixing, so its owner is deliberately not told and it is never
labelled.

A `skipped` pull request has a head the credential cannot push to (`head-on-another-fork`) or one
whose repository is gone (`head-repository-deleted`). Its base is still on the fork, so the session still
reparents it when that base lands.

## Labels replace

GitHub's label write takes the pull request's entire new label set. Computing that set from the
intended change alone silently strips the rest, so `basstler.stack labels` computes it from the
labels the pull request carries now.

## Force-pushes

A stack is kept current the way GitHub's own stacks are: every layer is rebased onto the one below
it and force-pushed, a branch under upstream review included. Each push carries a lease of its own,
so it is refused rather than forced over a push the pass did not see - the branch's owner pushed
in the meantime - and the branch is reported `push-rejected`. See
[Restacking with `gh stack`](README.md#restacking-with-gh-stack).

## Why a stack is a single line

GitHub's stacks are a line of pull requests, each based on the branch of the one below it, so a
plan item that rests on two unlanded items at once, or a pull request stacked on one that already
carries another, is refused when it is placed rather than stacked on an arbitrary one of them. A
parent with two children on the fork is still restacked: the second child is a line of its own,
based on the parent's branch.

## Reporting a conflict

No session subscribes to a pull request's activity, so the `🔴 MAINTENANCE - NEEDS RESOLUTION:`
comment sits on GitHub until the owner reads it; it has to stand alone. The `needs-resolution` label
withholds the branch from promotion until it is cleared.
