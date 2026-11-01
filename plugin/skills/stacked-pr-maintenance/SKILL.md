---
name: stacked-pr-maintenance
description: Run one maintenance pass over a fork's stacked pull requests: reparent, restack with GitHub's gh stack, and promote approved branches upstream. Invoke as "/stacked-pr-maintenance [fork=<owner/repo>] [upstream=<owner/repo>]". Use when asked to restack or promote the stack, and as the first step of plan-item-kickoff and plan-item-resolve.
allowed-tools: Bash, Read, Write, AskUserQuestion, mcp__github__update_pull_request
---

# Stacked-PR maintenance

A pass is four commands and one piece of writing. Everything mechanical - the fast-forward,
the restack, conflict comments, labels, links, CI - is done by the executor; you only write the
upstream summaries. Why each rule holds is in `docs/stack/maintenance-pass.md`; you do not need it.

Run every command through `"${BASSTLER_PLUGIN_ROOT}/hooks/basstler.sh"`. It waits for or finishes
the package install by itself. If it exits 1 with one line on stderr, relay that line and stop;
never read the hook scripts to debug it.

The restack rebases each stack with `gh stack` and force-pushes it with a lease. The session-start
summary's `gh stack:` line says whether it runs here; if it says `unavailable`, relay that line and
stop before step 3.

Every output is small (a few KB at most) and final. Read each once. Never re-run a command to see
its output again, never pipe it through `grep`, `sed`, `jq` or `head`, and never re-derive what it
says.

## Rules

- Change no files in the project and write no code, not even to resolve a conflict.
- Do not review, answer or act on review comments, and never poll or debug CI. If a review or
  comment event reaches you, end the turn. Never call `subscribe_pr_activity`, enter plan mode,
  or fan out to subagents.
- Never read a branch's diff or source. The summary context below is all you get.
- Never open the upstream pull request, write `in-review`, or label anything yourself.

## 1. Repositories

```bash
"${BASSTLER_PLUGIN_ROOT}/hooks/basstler.sh" stack configuration [--fork <owner/repo>] [--upstream <owner/repo>]
```

Pass `fork=` / `upstream=` arguments straight through. Never inspect or guess at remotes yourself.

| exit | what you do |
|---|---|
| 0 | go on - the pass reaches the upstream through `upstream_location`, a remote's name or a URL, so never add a remote for it |
| 4 | ask which repository is the fork (options from `git remote -v`), write `fork_repository = "<owner/repo>"` to a scratchpad file, save it with `"${BASSTLER_PLUGIN_ROOT}/hooks/write-personal-notes-file.sh" --source <file> --destination .claude/personal/stack.toml --message "record which repository is my fork"`, re-run |
| 11 | ask for the upstream, save it with `"${BASSTLER_PLUGIN_ROOT}/hooks/basstler.sh" setup.steps configure-upstream --repository <owner/repo>`, re-run |
| other | stop and report |

## 2. Prepare

```bash
"${BASSTLER_PLUGIN_ROOT}/hooks/basstler.sh" maintenance prepare
```

It prints one JSON document: `reparents` (bases to retarget) and `to_summarize` (one entry per
branch about to get its upstream link, each with its fork title, description, commit subjects and
largest changed files). A branch is listed once in its life: after this pass records its link, it
is never listed again.

The export reads the upstream's open pull requests through GitHub's API. In a Claude Code cloud
session that read is refused (403) until the upstream is attached: call `add_repo` with its owner
and name and `access: "push"`, the only level that lets the session's API calls reach a repository.
The pass only reads the upstream and never pushes to it; skip the clone the result suggests, and
re-run. If the attach is refused or blocked, stop and tell the user the pass needs the upstream's
open pull requests and approving the attach unblocks it. Never run the rest of the pass without
them: which branches are under upstream review decides which are promoted and labelled.

### Reparents (only if any)

For each entry, set the pull request's base to `target_base` with `update_pull_request`.

A `422 ... part of a stack` means a native GitHub stack. Use curl with `GH_TOKEN` and the header
`X-GitHub-Api-Version: 2026-03-10`:

1. `GET /repos/{owner}/{repo}/stacks`; record the stack's pull requests, bottom to top.
2. `POST /repos/{owner}/{repo}/stacks/{number}/unstack`.
3. `update_pull_request` each orphaned child's base. Never close and reopen it instead.
4. Once step 3 has run, `POST /repos/{owner}/{repo}/stacks` with `{"pull_requests": [...]}` - the
   recorded list minus landed and closed members - then `GET` it and confirm every member.

If any call fails or answers something not described here, stop on that stack and report it.
Never fast-forward the landed base to get around a 422.

### Summaries (only if `to_summarize` is not empty)

From the `to_summarize` entries alone, write one JSON file in your scratchpad, keyed by
`pull_request_number`:

```json
{"<number>": {"title": "<title in the upstream's own convention>", "points": ["<point>", "..."]}}
```

Two to four points, each a short sentence for an upstream reviewer who has not seen the fork pull
request. Write an entry for every listed number; both fields are optional.

## 3. Run the pass

```bash
"${BASSTLER_PLUGIN_ROOT}/hooks/basstler.sh" maintenance run-report [--summaries <the file>]
```

It exports a fresh board, fast-forwards, restacks, comments on and labels conflicts, records the
upstream links, and reports CI: each pull request's checks are read from its head when the pass
starts, so what the previous pass pushed is reported now and what this pass pushes is reported by
the next. Its summary is capped (at most a few KB); the whole report is in the file its last line
names - do not read it.

The first line is the status:

| status | what you do |
|---|---|
| `success`, `branch-needs-attention` | relay the summary |
| `not-fast-forward` | relay it; the fork's base is behind the upstream |

## Finish

Relay the summary as printed, then add only what this session did: every reparent step 2 could not
complete (number, stuck base, target base, the native-stack step that stopped you) and anything
you stopped on.

## Resuming a partial run

`run-report` is the whole pass and safe to re-run. The single steps exist for a person, not for a
pass: `board --write`, then `fast-forward`, `restack`, `promote`, or `checks` for the CI overview.
Push a stack's branches by hand only with `gh stack push --remote <fork-remote>`, which pushes every
layer with a lease of its own.
