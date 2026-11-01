# Sourced (not executed) by every hook and skill entry point, so all of them
# resolve the plugin, the project and the personal-notes remote, branch and
# path with the exact same precedence: git config > environment variable > the
# zero-config default. See ./README.md.

# HOOKS_DIR / PLUGIN_ROOT: where this plugin's own files are - derived from this
# file's own location on disk (${BASH_SOURCE[0]}, which - inside a sourced file -
# is that file's own path, not the sourcing script's), so every script and
# document path below is absolute, whichever project the plugin serves.
HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "${HOOKS_DIR}/.." && pwd)"
# Exported so a module of the package run from here finds the plugin's scripts
# too (see basstler.plugin_root.PluginRoot).
export BASSTLER_PLUGIN_ROOT="${PLUGIN_ROOT}"

# PROJECT_ROOT: the project the session works in - CLAUDE_PROJECT_DIR, which
# Claude Code sets for every hook, else the top of the git repository the caller
# runs in, for a script run by hand. Never derived from this file's location: the
# plugin lives outside the project it serves.
PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
PROJECT_ROOT="$(cd "${PROJECT_ROOT}" && pwd)"
CLAUDE_LOCAL_MD="${PROJECT_ROOT}/CLAUDE.local.md"

# Every caller also does `git` operations (config, fetch, worktree, branch)
# that assume they're running inside the project. Move there explicitly instead
# of trusting the invoking cwd to already be inside it (or inside it at all) -
# git itself would otherwise auto-discover a *different* repo if run from
# inside some other one, or fail outright if run from outside any repo.
cd "${PROJECT_ROOT}"

NOTES_REMOTE="$(git config --get claude.personalNotesRemote || true)"
NOTES_REMOTE="${NOTES_REMOTE:-${CLAUDE_PERSONAL_NOTES_REMOTE:-origin}}"

NOTES_BRANCH="$(git config --get claude.personalNotesBranch || true)"
NOTES_BRANCH="${NOTES_BRANCH:-${CLAUDE_PERSONAL_NOTES_BRANCH:-claude/personal-notes}}"

NOTES_PATH="$(git config --get claude.personalNotesPath || true)"
NOTES_PATH="${NOTES_PATH:-${CLAUDE_PERSONAL_NOTES_PATH:-.claude/personal/notes.md}}"

# NOTES_REMOTE may be either a configured remote's name (e.g. "origin") or a
# raw git URL (e.g. "https://github.com/<you>/<repo>") - `git fetch`/`git
# push` accept both interchangeably, and a URL needs no `git remote add`
# first. Use a URL whenever your own fork isn't the clone's "origin" (for
# example, some session environments name the upstream repo "origin" and your
# fork something else) - the URL form works without depending on that
# session-specific remote name/alias existing at all.

# PERSONAL_SETTINGS_PATH / LOCAL_SETTINGS_RELATIVE_PATH / LOCAL_SETTINGS_JSON /
# PERSONAL_SETTINGS_SYNC_STAMP: the personal Claude Code settings round trip -
# `.claude/personal/settings.local.json` on the personal-notes branch, synced into
# this clone's `.claude/settings.local.json` (the file Claude Code itself reads as
# local settings, and which is gitignored). Fixed convention, never overridden: the
# destination is dictated by Claude Code, and the source is per-contributor already
# by virtue of living on that contributor's own notes branch - same reasoning as
# PLANS_DIR and the pr-progress directory below.
PERSONAL_SETTINGS_PATH=".claude/personal/settings.local.json"
LOCAL_SETTINGS_RELATIVE_PATH=".claude/settings.local.json"
LOCAL_SETTINGS_JSON="${PROJECT_ROOT}/${LOCAL_SETTINGS_RELATIVE_PATH}"
# The stamp records the hash of the settings content last synced into - or saved
# out of - LOCAL_SETTINGS_JSON, which is what makes "has this been edited since?"
# answerable at all: without it, a session start cannot tell a file it wrote itself
# last time from one Claude Code (or a human) has since added rules to.
PERSONAL_SETTINGS_SYNC_STAMP="${PROJECT_ROOT}/.claude/.personal-settings-sync-hash"

# personal_settings_are_locally_modified: returns 0 if this clone's local settings
# exist and differ from what was last synced or saved (so overwriting them would
# lose an edit - typically permission rules Claude Code itself appended after an
# "always allow"), 1 otherwise. Settings that exist but were never synced count as
# modified: nothing recorded them, so nothing may claim them.
personal_settings_are_locally_modified() {
  [ -f "${LOCAL_SETTINGS_JSON}" ] || return 1
  [ -f "${PERSONAL_SETTINGS_SYNC_STAMP}" ] || return 0
  [ "$(git hash-object "${LOCAL_SETTINGS_JSON}")" \
    != "$(cat "${PERSONAL_SETTINGS_SYNC_STAMP}")" ]
}

# record_personal_settings_sync: stamps the local settings' current content as the
# synced baseline, so the next session start may update them in place.
record_personal_settings_sync() {
  mkdir -p "$(dirname "${PERSONAL_SETTINGS_SYNC_STAMP}")"
  git hash-object "${LOCAL_SETTINGS_JSON}" > "${PERSONAL_SETTINGS_SYNC_STAMP}"
}

# current_branch_upstream_remote: prints the remote name the current branch
# tracks (e.g. "abdel-direct" for a branch whose upstream is
# "abdel-direct/some-branch"), or nothing if it has no upstream (detached
# HEAD, or a branch that was never pushed with -u/--set-upstream). Shared by
# fetch_personal_notes_branch below and by create-personal-notes-branch.sh's
# existence check, so both apply the exact same fallback remote. Having no
# upstream is an answer, not an error, so this always returns 0 - otherwise a
# caller under `set -e -o pipefail` (create-personal-notes-branch.sh) exits
# silently with git's 128 instead of going on to create the branch.
current_branch_upstream_remote() {
  local upstream
  upstream="$(git rev-parse --abbrev-ref --symbolic-full-name @{upstream} 2>/dev/null)" || return 0
  printf '%s\n' "${upstream%%/*}"
}

# PERSONAL_NOTES_REFERENCE: where the fetched branch is kept, and the one place
# every reader of the notes branch reads from. Never FETCH_HEAD, which every
# other fetch - by this script, a module, or a concurrent session start -
# overwrites, and never "<remote>/<branch>", which a URL-form remote does not
# create. Equal to basstler.shell_configuration.FetchedBranchReference.
PERSONAL_NOTES_REFERENCE="refs/basstler/personal-notes"

# fetch_personal_notes_branch_from: fetches NOTES_BRANCH from the remote $1 into
# PERSONAL_NOTES_REFERENCE, leaving FETCH_HEAD to whoever else fetches.
fetch_personal_notes_branch_from() {
  git fetch --quiet --no-write-fetch-head "$1" \
    "+refs/heads/${NOTES_BRANCH}:${PERSONAL_NOTES_REFERENCE}" 2>/dev/null
}

# fetch_personal_notes_branch: fetches NOTES_BRANCH from NOTES_REMOTE. If that
# fails (remote unreachable, or the branch just isn't there), falls back once
# to the current branch's own upstream remote (current_branch_upstream_remote
# above) - if it has one, and it differs from NOTES_REMOTE - before giving up.
# This covers the common case of a clone whose checked-out branch already
# tracks a contributor's own fork under some other remote name/URL, without
# requiring NOTES_REMOTE to be configured explicitly for it.
#
# On success: sets ACTIVE_NOTES_REMOTE to whichever remote actually served
# the branch (NOTES_REMOTE or the upstream fallback), keeps the fetched commit
# at PERSONAL_NOTES_REFERENCE, and returns 0.
# On failure: sets ATTEMPTED_NOTES_REMOTES to a human-readable, comma
# separated list of every remote that was tried (for callers that want to
# report it), and returns 1.
#
# Read-only fallback: this never affects where create-personal-notes-branch.sh
# creates the branch, or (by itself) where save-personal-notes.sh pushes an
# edit back to - callers that push should push back to ACTIVE_NOTES_REMOTE,
# i.e. wherever the branch was actually read from, not unconditionally to
# NOTES_REMOTE, so a save always lands on the same remote the notes came from.
fetch_personal_notes_branch() {
  ATTEMPTED_NOTES_REMOTES="${NOTES_REMOTE}"
  if fetch_personal_notes_branch_from "${NOTES_REMOTE}"; then
    ACTIVE_NOTES_REMOTE="${NOTES_REMOTE}"
    return 0
  fi

  local upstream_remote
  upstream_remote="$(current_branch_upstream_remote)"
  if [ -n "${upstream_remote}" ] && [ "${upstream_remote}" != "${NOTES_REMOTE}" ]; then
    ATTEMPTED_NOTES_REMOTES="${ATTEMPTED_NOTES_REMOTES}, ${upstream_remote}"
    if fetch_personal_notes_branch_from "${upstream_remote}"; then
      ACTIVE_NOTES_REMOTE="${upstream_remote}"
      return 0
    fi
  fi

  return 1
}

# default_branch_name: prints the repo's actual default branch name, with no
# network access - resolved from origin's local HEAD ref
# (refs/remotes/origin/HEAD, set by a normal `git clone` or `git remote
# set-head`) when available, otherwise whichever of main/master actually
# exists as a local or origin-tracking branch, otherwise "main". Used by
# pr_progress_path below so a repo whose default branch is neither main nor
# master (e.g. "develop") is still recognized, instead of being silently
# treated as an ordinary per-branch PR-progress branch.
default_branch_name() {
  local remote_head candidate
  remote_head="$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null)"
  if [ -n "${remote_head}" ]; then
    printf '%s\n' "${remote_head#refs/remotes/origin/}"
    return 0
  fi
  for candidate in main master; do
    if git show-ref --verify --quiet "refs/heads/${candidate}" \
        || git show-ref --verify --quiet "refs/remotes/origin/${candidate}"; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  printf 'main\n'
}

# pr_progress_path: prints the deterministic per-branch PR-progress file path
# (.claude/personal/pr-progress/<branch>.md) for the branch $1, or for whichever
# branch is currently checked out when $1 is not given, and returns 0. Returns 1 (prints nothing) if there's no
# sensible "current PR" to track progress for: detached HEAD, the repo's
# default branch (see default_branch_name above), or the personal-notes
# branch itself. The directory is a fixed convention, independent of
# NOTES_PATH - PR progress is inherently plural/keyed, unlike the single
# personal-notes file, so it isn't tied to wherever NOTES_PATH happens to be
# overridden to.
#
# Shared by session-start.sh and save-pr-progress.sh so both agree on exactly
# the same key for exactly the same branch - there is no other place this
# path is computed, so it can never drift between reading and writing it.
PR_PROGRESS_DIRECTORY=".claude/personal/pr-progress"
pr_progress_path() {
  local branch="${1:-}"
  [ -n "${branch}" ] || branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
  case "${branch}" in
    HEAD|"$(default_branch_name)"|"${NOTES_BRANCH}"|"") return 1 ;;
  esac
  printf '%s/%s.md\n' "${PR_PROGRESS_DIRECTORY}" "${branch}"
}

# PERSONAL_GIT_IDENTITY_PATH: where the human contributor's git identity is
# recorded on the notes branch, so a fresh clone can be given one instead of
# inheriting whatever the environment's global git config happens to be. Fixed
# convention, never overridden - same reasoning as the plan paths below.
#
# Stored in git's own config format and read back with `git config --file`
# rather than parsed here: the format already has a parser, and writing it
# with the same tool that reads it means the two can never disagree.
PERSONAL_GIT_IDENTITY_PATH=".claude/personal/git-identity"

# format_git_identity: prints a name and email in the one form every message
# about an identity uses, so the same pair can't be rendered two ways in two
# different reports.
format_git_identity() {
  printf '%s <%s>\n' "$1" "$2"
}

# effective_git_identity: prints "<name><TAB><email>" for the identity a commit
# made here right now would actually carry, and returns 0. Returns 1 (prints
# nothing) if git cannot determine one at all.
#
# Resolved via `git var GIT_AUTHOR_IDENT`, which applies git's real precedence -
# GIT_AUTHOR_NAME/GIT_AUTHOR_EMAIL, then repository-local config, then global.
# `git config --get user.name` deliberately not used: it reports the global
# value even in a clone whose commits are correctly authored from the
# environment, which is the one wrong answer a check about commit authorship
# must never give.
effective_git_identity() {
  local author_identity
  author_identity="$(git var GIT_AUTHOR_IDENT 2>/dev/null)" || return 1
  # GIT_AUTHOR_IDENT is "<name> <<email>> <timestamp> <timezone>"; the trailing
  # two fields are when the commit would be made, not who by.
  printf '%s\n' "${author_identity}" \
    | sed -E 's/^(.*) <(.*)> [0-9]+ [-+][0-9]{4}$/\1\t\2/'
}

# repository_local_git_identity: prints "<name><TAB><email>" for the identity
# configured in this clone's own config, and returns 0. Returns 1 (prints
# nothing) unless both halves are set - half an identity cannot author a commit,
# so it is not an identity.
repository_local_git_identity() {
  local name email
  name="$(git config --local --get user.name || true)"
  email="$(git config --local --get user.email || true)"
  [ -n "${name}" ] && [ -n "${email}" ] || return 1
  printf '%s\t%s\n' "${name}" "${email}"
}

# recorded_git_identity_exists / recorded_git_identity: whether the notes branch
# carries a git identity at all, and what it records. Caller must have already
# fetched NOTES_BRANCH successfully (see fetch_personal_notes_branch) - these
# read PERSONAL_NOTES_REFERENCE rather than fetching again themselves.
#
# Two functions rather than one for the same reason as plan_branch_index_exists
# above: "nothing is recorded yet" and "what is recorded cannot be used" are
# different answers needing different advice, and a single failing lookup
# collapses them into one.
recorded_git_identity_exists() {
  git cat-file -e "${PERSONAL_NOTES_REFERENCE}:${PERSONAL_GIT_IDENTITY_PATH}" 2>/dev/null
}

recorded_git_identity() {
  recorded_git_identity_exists || return 1
  local identity_file name email
  identity_file="$(mktemp)"
  git show "${PERSONAL_NOTES_REFERENCE}:${PERSONAL_GIT_IDENTITY_PATH}" > "${identity_file}"
  name="$(git config --file "${identity_file}" --get user.name || true)"
  email="$(git config --file "${identity_file}" --get user.email || true)"
  rm -f "${identity_file}"
  [ -n "${name}" ] && [ -n "${email}" ] || return 1
  printf '%s\t%s\n' "${name}" "${email}"
}

# PLANS_DIR / PLAN_MANIFEST_FILENAME / PLAN_ROADMAP_FILENAME: the one,
# shared definition of where a plan's files live, so no caller re-derives
# these path fragments itself (session-start.sh and save-plan.sh both used
# to build ".claude/personal/plans/<id>/plan.yaml" inline - two independent
# copies of the same literal that could silently drift apart). Fixed
# convention, never overridden - plan storage is plural/generated data, not
# a per-clone preference like NOTES_PATH.
PLANS_DIR=".claude/personal/plans"
PLAN_MANIFEST_FILENAME="plan.yaml"
PLAN_ROADMAP_FILENAME="roadmap.md"

# plan_directory_path / plan_manifest_path / plan_roadmap_path: the
# deterministic per-plan paths for the given plan id. Shared by
# session-start.sh and save-plan.sh so both agree on exactly the same
# layout - see pr_progress_path above for the same reasoning applied to
# PR-progress files.
plan_directory_path() {
  printf '%s/%s\n' "${PLANS_DIR}" "$1"
}
plan_manifest_path() {
  printf '%s/%s/%s\n' "${PLANS_DIR}" "$1" "${PLAN_MANIFEST_FILENAME}"
}
plan_roadmap_path() {
  printf '%s/%s/%s\n' "${PLANS_DIR}" "$1" "${PLAN_ROADMAP_FILENAME}"
}

# PLAN_BRANCH_INDEX_PATH: the generated reverse index mapping every plan
# item's branch to the plan id that tracks it (see
# skills/plan-dashboard/plan-schema.md for the
# full plan-dashboard schema this feeds).
PLAN_BRANCH_INDEX_PATH="${PLANS_DIR}/_generated/branch-index.tsv"

# DASHBOARD_URL_CACHE_PATH: the generated cache mapping each plan id (plus
# "_index" for the master index) to the Artifact URL its dashboard is
# published at, so /plan-dashboard updates that page instead of minting a
# second one. Named here rather than typed into plan-dashboard/SKILL.md,
# same defined-once reasoning as PLAN_BRANCH_INDEX_PATH above.
DASHBOARD_URL_CACHE_PATH="${PLANS_DIR}/_generated/dashboard-urls.yaml"

# PLAN_HUB_PATH: the hub file - the plan site's address, the registry
# repository holding the complete list of repositories, and that list. Every
# registered repository's notes carry a copy (see basstler.plan_site.hub).
PLAN_HUB_PATH="${PLANS_DIR}/_hub.yaml"

# *_MODULE: every Python entry point in this system - defined once, here, so
# refresh_dashboard.sh, every SKILL.md and every workflow source this file and
# use these variables instead of each carrying its own separately-typed literal.
#
# A *_MODULE is an import path rather than a file path, and is run as
# `"${BASSTLER_PYTHON}" -m "${SOME_MODULE}"`, BASSTLER_PYTHON being the
# package's own interpreter (see resolve_basstler_python below).
#
# BASSTLER_CHECKOUT: the basstler source checkout this plugin sits in, when it
# sits in one - a development clone, or a plugin run straight from a clone of
# its repository. Empty for a plugin installed on its own, whose package comes
# from its repository at the latest release instead (see
# basstler_package_source below).
BASSTLER_CHECKOUT=""
if [ -f "${PLUGIN_ROOT}/../pyproject.toml" ] && [ -d "${PLUGIN_ROOT}/../src/basstler" ]; then
  BASSTLER_CHECKOUT="$(cd "${PLUGIN_ROOT}/.." && pwd)"
  # Exported so every module a caller runs is that checkout's own source,
  # whichever interpreter runs it, and whatever an install elsewhere would
  # resolve to.
  export PYTHONPATH="${BASSTLER_CHECKOUT}/src${PYTHONPATH:+:${PYTHONPATH}}"
fi
# basstler.dashboard.plan_page: renders one plan's dashboard HTML from its manifest
# and live GitHub data - see the module's own docstring.
BUILD_DASHBOARD_MODULE="basstler.dashboard.plan_page"
# basstler.dashboard.index_page: renders the master index page listing every plan.
BUILD_INDEX_MODULE="basstler.dashboard.index_page"
# basstler.plan_site: renders the plan site - the hub, every registered repository's
# index, and every plan's dashboard - from the repositories the hub file lists.
BUILD_SITE_MODULE="basstler.plan_site"
# basstler.plan_site.hub: registers a repository in the hub file.
PLAN_HUB_MODULE="basstler.plan_site.hub"
# basstler.plan.sync_manifest_status: auto-corrects a plan.yaml's item statuses to
# "done" wherever GitHub confirms the item's pull request is merged.
SYNC_MANIFEST_STATUS_MODULE="basstler.plan.sync_manifest_status"
# basstler.plan.check_dependency_readiness: classifies one item's dependencies as
# ready or not-ready to build on - see dependency-readiness.md below.
CHECK_DEPENDENCY_READINESS_MODULE="basstler.plan.check_dependency_readiness"
# basstler.dashboard.refresh_support: the JSON-plumbing helpers
# refresh_dashboard.sh calls between its two module calls.
REFRESH_DASHBOARD_SUPPORT_MODULE="basstler.dashboard.refresh_support"
# basstler.dashboard.record_url: writes one key's published Artifact URL into
# DASHBOARD_URL_CACHE_PATH, resolving that URL from the account's live
# Artifact listing so a URL nobody published cannot be recorded.
RECORD_DASHBOARD_URL_MODULE="basstler.dashboard.record_url"
# basstler.plan.item_bootstrap: opens an item's branch and draft pull request and
# records its manifest entry - invoked from plan-item-kickoff/SKILL.md and
# add-plan-item/SKILL.md.
PLAN_ITEM_BOOTSTRAP_MODULE="basstler.plan.item_bootstrap"
# basstler.plan.item_mode: resolves whether a plan-item skill asks, plans, or
# implements on its own - invoked from plan-item-mode/SKILL.md and from
# plan-item-kickoff/plan-item-resolve via execution-modes.md.
PLAN_ITEM_MODE_MODULE="basstler.plan.item_mode"
# basstler.plan.manifest_tools: reads a plan id out of a manifest and regenerates
# the branch index - the manifest plumbing save-plan.sh calls.
PLAN_MANIFEST_TOOLS_MODULE="basstler.plan.manifest_tools"
# basstler.plan.size_limits: prunes landed items and refuses a plan or progress
# notes over their limits - the gate save-plan.sh and save-pr-progress.sh run.
PLAN_SIZE_LIMITS_MODULE="basstler.plan.size_limits"
# basstler.personal_notes.progress_notes: deletes the progress notes of branches whose pull requests
# have all merged or closed, or that are gone from the remote.
PROGRESS_NOTES_MODULE="basstler.personal_notes.progress_notes"
# basstler.plan.updates_since_support: renders the plan-state delta
# plan-updates-since.sh reports.
PLAN_UPDATES_SINCE_SUPPORT_MODULE="basstler.plan.updates_since_support"
# stack: stacked-PR status tool (status/check/next/register)
# - see its own module docstring and STACK_DIRECTORY/README.md.
STACK_MODULE="basstler.stack"
# maintenance: the deterministic maintenance executor a stacked-PR pass
# runs - board export, fast-forward, restack, promote, run-report.
MAINTENANCE_MODULE="basstler.maintenance"
# basstler.maintenance.default_branch_sync: fast-forwards the default branch every
# session starts from to the upstream this fork tracks - run by session-start.sh.
DEFAULT_BRANCH_SYNC_MODULE="basstler.maintenance.default_branch_sync"
# basstler.plan.check_scope_overlap: reports which of an item's paths a given branch
# already touches - the mechanical half of add-plan-item's scope decision.
CHECK_SCOPE_OVERLAP_MODULE="basstler.plan.check_scope_overlap"
# basstler.upstream_reviews: reports the review threads a fork branch's upstream
# pull request has collected, run by /upstream-reviews in a session or the
# upstream-reviews Action on a runner.
UPSTREAM_REVIEWS_MODULE="basstler.upstream_reviews"
# basstler.upstream_reviews.helper: the same read through a helper session, for a cloud
# session that cannot attach the upstream next to its same-named fork.
UPSTREAM_REVIEWS_HELPER_MODULE="basstler.upstream_reviews.helper"
# basstler.upstream_reviews.action: the same read in the fork's own Actions, started by
# pushing a request to UPSTREAM_REVIEWS_ACTION_BRANCH, which holds the caller
# workflow and nothing of the project's.
UPSTREAM_REVIEWS_ACTION_MODULE="basstler.upstream_reviews.action"
UPSTREAM_REVIEWS_ACTION_BRANCH="basstler/upstream-reviews"
# dependencies: prints the package's requirements an environment does not
# have - what missing_dependencies below calls.
BASSTLER_DEPENDENCIES_MODULE="basstler.setup.dependencies"
# plan-item-modes.toml on the personal-notes branch: the per-user override
# basstler.plan.item_mode layers on top of the package's shipped defaults.
PERSONAL_PLAN_ITEM_MODES_PATH=".claude/personal/plan-item-modes.toml"
# basstler.setup.steps: prints the setup steps no script can do, and performs
# configure-upstream, which names a project's upstream for the stacked-PR
# workflow, and install-upstream-reviews-action.
SETUP_STEPS_MODULE="basstler.setup.steps"
# basstler.setup.personal_notes: the whole personal-notes setup as one command
# that needs no session; /setup-personal-notes asks the questions and runs it.
SETUP_PERSONAL_NOTES_MODULE="basstler.setup.personal_notes"
# The stack settings a project commits, and the personal-notes override
# layered on top of them - either may name the upstream.
PROJECT_STACK_CONFIGURATION_PATH=".claude/basstler/stack.toml"
PERSONAL_STACK_CONFIGURATION_PATH=".claude/personal/stack.toml"

# PLAN_DASHBOARD_DIRECTORY / REFRESH_DASHBOARD_SCRIPT: what the
# plan-dashboard skill keeps outside the package - Claude Code discovers a
# skill and its reference documents by path, and the shell entry point sits
# with the skill it belongs to. Every path from here on is absolute, under
# PLUGIN_ROOT.
PLAN_DASHBOARD_DIRECTORY="${PLUGIN_ROOT}/skills/plan-dashboard"
# refresh_dashboard.sh: orchestrates basstler.plan.sync_manifest_status, the
# conditional push of its correction, then basstler.dashboard.plan_page - the whole
# refresh sequence /plan-dashboard runs for one plan.
REFRESH_DASHBOARD_SCRIPT="${PLAN_DASHBOARD_DIRECTORY}/refresh_dashboard.sh"

# plan-schema.md: the full plan.yaml field reference every plan-* skill
# reads before drafting or interpreting a manifest. On main, next to the
# tooling that enforces it, so every clone has it with no setup - unlike the
# plan *data* it describes, which lives only on the personal-notes branch.
PLAN_SCHEMA_DOCUMENT="${PLAN_DASHBOARD_DIRECTORY}/plan-schema.md"
# MAINTENANCE_BLOCKER_OWNER: the name stack maintenance writes its own
# blockers under, so it replaces and clears its own entries and never one a
# person wrote. Named here once, since the writer and the clearer have to
# agree on it exactly - see basstler.plan.item_bootstrap's block/unblock operations.
MAINTENANCE_BLOCKER_OWNER="stacked-pr-maintenance"
# write-personal-notes-file.sh: generic commit-and-push-one-file-to-the
# personal-notes-branch helper, used by refresh_dashboard.sh (the manifest
# auto-sync correction) and plan-dashboard/SKILL.md (the dashboard-URL
# cache) alike.
WRITE_PERSONAL_NOTES_FILE_SCRIPT="${HOOKS_DIR}/write-personal-notes-file.sh"

# SETUP_PERSONAL_NOTES_DIRECTORY / *_DOCUMENT / STARTER_NOTES_FILE /
# CHECK_SETUP_SCRIPT: the one-time-setup half of this system - the skill a
# person runs first (/setup-personal-notes), the starter notes it offers, the
# shared "is this clone set up yet?" procedure every other skill defers to
# instead of restating it, and the read-only inspection script all of them
# call. Same defined-once reasoning as every path above.
SETUP_PERSONAL_NOTES_DIRECTORY="${PLUGIN_ROOT}/skills/setup-personal-notes"
# check-setup.sh: reports, as TSV, which parts of the setup are already done -
# the single source of truth for that question, so no caller re-implements
# "is the notes branch there?" with its own git plumbing.
CHECK_SETUP_SCRIPT="${HOOKS_DIR}/check-setup.sh"
# INSTALL_GH_STACK_SCRIPT: makes `gh stack` runnable in the session - run by
# session-start.sh, which prints its verdict.
INSTALL_GH_STACK_SCRIPT="${HOOKS_DIR}/install-gh-stack.sh"
# prerequisite-check.md: the shared "run check-setup.sh, run
# /setup-personal-notes if it fails" procedure that plan-create,
# plan-dashboard, plan-item-kickoff and plan-item-resolve each reference in
# one line rather than each spelling it out.
SETUP_PREREQUISITE_DOCUMENT="${SETUP_PERSONAL_NOTES_DIRECTORY}/prerequisite-check.md"
# starter-notes.md: the default content /setup-personal-notes offers to seed a
# brand-new notes file with, so a first session starts from working
# conventions instead of an empty file.
STARTER_NOTES_FILE="${SETUP_PERSONAL_NOTES_DIRECTORY}/starter-notes.md"

# SAVE_PLAN_SCRIPT: same reasoning as the block above, extended to
# save-plan.sh - unlike the other hook scripts in this directory (which are
# always run directly by a human, once, per hooks/README.md's own setup
# instructions), save-plan.sh is invoked from plan-create/SKILL.md's own
# bootstrap step, i.e. a real caller this codebase controls - the same
# duplication risk, just for a hook script instead of a plan-dashboard one.
SAVE_PLAN_SCRIPT="${HOOKS_DIR}/save-plan.sh"

# GITHUB_LIST_PULL_REQUESTS_TOOL / GITHUB_PULL_REQUEST_READ_TOOL: the two
# MCP tools every pr_data.json-gathering procedure in this system calls
# (see pr-data-fetching.md), named once here so every doc references the
# same constant instead of retyping the literal identifier. Documentation
# aliases only, not live substitutions: Claude Code's tool-calling
# mechanism has no notion of a shell-expanded tool name, so an actual call
# always still has to type the literal name below - but a session that has
# sourced this file can read `${GITHUB_LIST_PULL_REQUESTS_TOOL}` in a doc
# and know exactly which tool that refers to, the same way it already does
# for every script path above.
GITHUB_LIST_PULL_REQUESTS_TOOL="mcp__github__list_pull_requests"
GITHUB_PULL_REQUEST_READ_TOOL="mcp__github__pull_request_read"

# plan_id_for_branch: prints the plan id that tracks the given branch, per
# PLAN_BRANCH_INDEX_PATH on PERSONAL_NOTES_REFERENCE, and returns 0. Returns 1 (prints
# nothing) if the index doesn't exist yet, or the branch isn't in it. Caller
# must have already fetched NOTES_BRANCH successfully (see
# fetch_personal_notes_branch) - this reads PERSONAL_NOTES_REFERENCE rather than
# fetching again itself, so session-start.sh and save-plan.sh each fetch
# exactly once per run.
#
# The index is tab-separated values (TSV): one "<branch><TAB><plan-id>" line
# per branch, generated fresh in full by ./save-plan.sh on every run (never
# hand-edited or incrementally patched). TSV rather than a hand-rolled
# YAML-lookalike matched by fixed-string grep: it's an unambiguous, widely
# understood interchange format - a tab can never appear inside a branch
# name or plan id, so a field-based match can't misfire the way a
# substring/prefix match on a YAML-shaped string could - while still
# needing nothing beyond `awk`, which every session-start environment
# already has (see the module docstring: session-start.sh must not gain a
# hard dependency on python3/PyYAML just to check whether the current
# branch belongs to a plan).
plan_id_for_branch() {
  local branch="$1"
  git cat-file -e "${PERSONAL_NOTES_REFERENCE}:${PLAN_BRANCH_INDEX_PATH}" 2>/dev/null || return 1
  git show "${PERSONAL_NOTES_REFERENCE}:${PLAN_BRANCH_INDEX_PATH}" 2>/dev/null \
    | awk -F'\t' -v branch="${branch}" '$1 == branch { print $2; exit }'
}

# item_id_for_branch: the id of the item in plan $1 whose branch is $2, read
# off the fetched manifest with awk alone - dependency-free, like
# plan_id_for_branch above. Prints nothing when no item names the branch.
item_id_for_branch() {
  local plan_id="$1"
  local branch="$2"
  git show "${PERSONAL_NOTES_REFERENCE}:$(plan_manifest_path "${plan_id}")" 2>/dev/null \
    | awk -v branch="${branch}" '
        $1 == "-" && $2 == "id:" { item = $3 }
        $1 == "branch:" && $2 == branch { print item; exit }'
}

# recorded_site_url: the plan site's address from the fetched hub file. Fails
# when the notes branch has no hub file; prints nothing when it names no site.
recorded_site_url() {
  git cat-file -e "${PERSONAL_NOTES_REFERENCE}:${PLAN_HUB_PATH}" 2>/dev/null || return 1
  git show "${PERSONAL_NOTES_REFERENCE}:${PLAN_HUB_PATH}" \
    | awk '$1 == "site_url:" && $2 != "null" { print $2; exit }'
}

# repository_name: this clone's origin as owner/repo, whatever URL form the
# remote takes, or "this repository" for a clone with no origin.
repository_name() {
  local url
  url="$(git remote get-url origin 2>/dev/null || true)"
  if [ -z "${url}" ]; then
    printf 'this repository\n'
    return
  fi
  printf '%s\n' "${url}" | sed -E 's#\.git$##; s#^.*[/:]([^/:]+/[^/]+)$#\1#'
}

# branch_can_hold_plan_item: whether a plan item could ever track the given
# branch. False for a detached HEAD, the repo's default branch, and the
# personal-notes branch: none of the three is per-change work, so telling a
# session "no item tracks this branch" there is noise rather than a prompt to
# record one - work done from them is typically a personal-notes edit that
# never becomes a pull request at all.
#
# Deliberately its own copy of the three cases pr_progress_path excludes,
# rather than a shared helper: the two answer different questions and are
# expected to diverge. A branch whose pull request targets the notes branch
# still wants PR progress tracked, but still never wants a plan item.
branch_can_hold_plan_item() {
  local branch="$1"
  case "${branch}" in
    HEAD|"$(default_branch_name)"|"${NOTES_BRANCH}"|"") return 1 ;;
  esac
  return 0
}

# plan_branch_index_exists / tracked_plan_count: whether any plan is tracked on
# the notes branch at all, and how many distinct ones there are. Same fetched-branch
# precondition as plan_id_for_branch above.
#
# These exist so a caller can tell apart the two situations plan_id_for_branch
# collapses into a single "no": nobody tracks plans here, versus plans exist and
# this branch is in none of them. Only the second is worth a word to a session,
# and only these two functions know which is which, since the index path is
# theirs alone to read.
plan_branch_index_exists() {
  git cat-file -e "${PERSONAL_NOTES_REFERENCE}:${PLAN_BRANCH_INDEX_PATH}" 2>/dev/null
}

tracked_plan_count() {
  plan_branch_index_exists || { printf '0\n'; return 0; }
  git show "${PERSONAL_NOTES_REFERENCE}:${PLAN_BRANCH_INDEX_PATH}" 2>/dev/null \
    | awk -F'\t' 'NF >= 2 { seen[$2] = 1 } END { print length(seen) }'
}

# %% the package's own environment

# BASSTLER_HOME: where the package's environment and anything installed for it
# live - one per user rather than one per clone, so every project the plugin
# serves shares one environment, and a fresh clone of any of them needs no
# install of its own.
BASSTLER_HOME="${BASSTLER_HOME:-${XDG_DATA_HOME:-${HOME}/.local/share}/basstler}"
# BASSTLER_ENVIRONMENT_DIRECTORY: the package's own virtual environment, which
# session-start.sh creates where it is missing and installs the package into.
BASSTLER_ENVIRONMENT_DIRECTORY="${BASSTLER_HOME}/venv"
# BASSTLER_INSTALL_LOCK: a directory held while the package's environment is
# created or installed into, holding the holder's process id, so a caller that
# finds the package missing waits for an install already running - typically a
# session start's - rather than racing it.
BASSTLER_INSTALL_LOCK="${BASSTLER_HOME}/install.lock"
# BASSTLER_INSTALL_MARKER: the process id of whoever holds BASSTLER_INSTALL_LOCK,
# so a check run meanwhile can tell "being installed" from "missing" - see
# basstler_install_in_progress.
BASSTLER_INSTALL_MARKER="${BASSTLER_INSTALL_LOCK}/pid"
# BASSTLER_INSTALLED_SOURCE_FILE: what the environment's package was last
# installed from, so a plugin update - which names a new source - reinstalls it.
BASSTLER_INSTALLED_SOURCE_FILE="${BASSTLER_ENVIRONMENT_DIRECTORY}/basstler-source"
# BASSTLER_PYTHON_REQUIREMENT: the interpreters the package supports - the
# requires-python of the package's pyproject.toml, which a test holds this
# equal to.
BASSTLER_PYTHON_REQUIREMENT=">=3.12"
# BASSTLER_INTERPRETER_CANDIDATES: interpreters satisfying that requirement,
# newest first, tried by name when uv is not there to find or fetch one.
BASSTLER_INTERPRETER_CANDIDATES="python3.14 python3.13 python3.12"
# BASSTLER_UV_DIRECTORY: where uv is installed on a machine with neither uv nor
# any of the candidates - so one whose only interpreter is too old for the
# package still gets an environment, from an interpreter uv fetches.
BASSTLER_UV_DIRECTORY="${BASSTLER_HOME}/uv"
BASSTLER_INSTALLED_UV="${BASSTLER_UV_DIRECTORY}/bin/uv"
# BASSTLER_REPOSITORY_URL: where an installed plugin's package comes from, at
# its latest release.
BASSTLER_REPOSITORY_URL="${BASSTLER_REPOSITORY_URL:-https://github.com/AbdelrhmanBassiouny/basstler}"
# PLUGIN_MANIFEST: the plugin's manifest, which names no version: a release is
# only a tag on its merge commit, so the plugin a session syncs from that merge
# is already the release, with nothing committed after it to miss.
PLUGIN_MANIFEST="${PLUGIN_ROOT}/.claude-plugin/plugin.json"
# BASSTLER_RELEASE_FILE: the latest release tag session-start.sh last read from
# BASSTLER_REPOSITORY_URL - the one the package is installed at, read once per
# session start rather than by every caller.
BASSTLER_RELEASE_FILE="${BASSTLER_HOME}/release"

# %% GitHub's stack extension

# GH_STACK_TOOLS_DIRECTORY: where install-gh-stack.sh puts the `gh` and the
# gh-stack extension it installs, beside the package's environment.
GH_STACK_TOOLS_DIRECTORY="${GH_STACK_TOOLS_DIRECTORY:-${BASSTLER_HOME}/tools}"
# GH_STACK_EXTENSION_DIRECTORY: where install-gh-stack.sh clones and builds the
# extension.
GH_STACK_EXTENSION_DIRECTORY="${GH_STACK_TOOLS_DIRECTORY}/gh-stack"
# GH_STACK_GH_VERSION: the oldest `gh` gh-stack runs under, and the one
# install-gh-stack.sh downloads where the session has none as recent.
GH_STACK_GH_VERSION="${GH_STACK_GH_VERSION:-2.90.0}"
# GH_STACK_SOURCE: the gh-stack extension's repository, built from source where
# `gh extension install` is refused.
GH_STACK_SOURCE="${GH_STACK_SOURCE:-https://github.com/github/gh-stack}"
# GH_RELEASES: where `gh` release archives are downloaded from.
GH_RELEASES="${GH_RELEASES:-https://github.com/cli/cli/releases/download}"

# latest_basstler_release: prints the highest v<major>.<minor>.<patch> tag
# BASSTLER_REPOSITORY_URL has; fails when it cannot be read or has none.
latest_basstler_release() {
  local tags
  tags="$(git ls-remote --tags --refs "${BASSTLER_REPOSITORY_URL}" 'v*' 2>/dev/null)" \
    || return 1
  printf '%s\n' "${tags}" \
    | sed -n 's|.*refs/tags/v\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)$|\1|p' \
    | sort -t . -k 1,1n -k 2,2n -k 3,3n | tail -1 | sed 's/^/v/' | grep .
}

# record_latest_basstler_release: records latest_basstler_release in
# BASSTLER_RELEASE_FILE for a plugin outside a checkout. Keeps the last one
# recorded when the repository cannot be read, so a session offline keeps the
# package it has rather than reinstalling.
record_latest_basstler_release() {
  [ -z "${BASSTLER_CHECKOUT}" ] || return 0
  local release
  release="$(latest_basstler_release)" || return 0
  mkdir -p "$(dirname "${BASSTLER_RELEASE_FILE}")"
  printf '%s\n' "${release}" > "${BASSTLER_RELEASE_FILE}"
}

# basstler_package_source: prints what to install the package from - the
# checkout this plugin sits in, else its repository at the latest release
# recorded, or at its default branch before any was.
basstler_package_source() {
  if [ -n "${BASSTLER_CHECKOUT}" ]; then
    printf '%s\n' "${BASSTLER_CHECKOUT}"
    return
  fi
  local reference=""
  [ -s "${BASSTLER_RELEASE_FILE}" ] && reference="@$(cat "${BASSTLER_RELEASE_FILE}")"
  printf 'basstler @ git+%s%s\n' "${BASSTLER_REPOSITORY_URL}" "${reference}"
}

# basstler_branch_or_tag: prints the basstler branch or tag the package is
# installed at outside a checkout - the latest release recorded, or main before
# any was - for a workflow that has to run the same package as the session.
basstler_branch_or_tag() {
  if [ -s "${BASSTLER_RELEASE_FILE}" ]; then
    cat "${BASSTLER_RELEASE_FILE}"
  else
    printf 'main\n'
  fi
}

# basstler_install_in_progress: succeeds while a running process holds
# BASSTLER_INSTALL_MARKER. A marker whose process has ended - a session start
# that was killed - is not an install in progress.
basstler_install_in_progress() {
  [ -f "${BASSTLER_INSTALL_MARKER}" ] || return 1
  kill -0 "$(cat "${BASSTLER_INSTALL_MARKER}")" 2>/dev/null
}

# wait_for_basstler_install: waits up to $1 seconds (300 by default) for an
# install in progress to finish, then resolves BASSTLER_PYTHON again, since the
# install may have created the environment. Fails if it is still running then.
wait_for_basstler_install() {
  local remaining="${1:-300}"
  while basstler_install_in_progress; do
    [ "${remaining}" -gt 0 ] || return 1
    sleep 1
    remaining=$((remaining - 1))
  done
  resolve_basstler_python
}

# resolve_basstler_python: sets BASSTLER_ENVIRONMENT_INTERPRETER, the package
# environment's interpreter - Scripts/python.exe where the environment has one,
# as on Windows, else bin/python - and BASSTLER_PYTHON, the interpreter every
# caller runs a module of the package with: the environment's once it exists,
# python3 until then. Run on sourcing, and again by session-start.sh once it
# has created the environment.
resolve_basstler_python() {
  BASSTLER_ENVIRONMENT_INTERPRETER="${BASSTLER_ENVIRONMENT_DIRECTORY}/Scripts/python.exe"
  [ -x "${BASSTLER_ENVIRONMENT_INTERPRETER}" ] \
    || BASSTLER_ENVIRONMENT_INTERPRETER="${BASSTLER_ENVIRONMENT_DIRECTORY}/bin/python"
  if [ -x "${BASSTLER_ENVIRONMENT_INTERPRETER}" ]; then
    BASSTLER_PYTHON="${BASSTLER_ENVIRONMENT_INTERPRETER}"
  else
    BASSTLER_PYTHON="python3"
  fi
}
resolve_basstler_python

# installed_package_is_current: whether BASSTLER_PYTHON imports the package and
# it was installed from what basstler_package_source names now.
installed_package_is_current() {
  [ -f "${BASSTLER_INSTALLED_SOURCE_FILE}" ] || return 1
  [ "$(cat "${BASSTLER_INSTALLED_SOURCE_FILE}")" = "$(basstler_package_source)" ] \
    || return 1
  "${BASSTLER_PYTHON}" -c "import basstler" > /dev/null 2>&1
}

# missing_dependencies: prints the requirement specifiers - the package itself,
# then the dependencies it declares - that BASSTLER_PYTHON does not have, space
# separated, and nothing at all when it has every one of them. Returns 1
# without printing when the interpreter is missing or the declaration cannot be
# read, which is a caller's problem to word rather than this function's.
#
# The reading is BASSTLER_DEPENDENCIES_MODULE's rather than a snippet written
# out here: it parses a declaration, and parsing embedded in a shell string is
# code nothing can run on its own or test.
missing_dependencies() {
  command -v "${BASSTLER_PYTHON}" > /dev/null 2>&1 || return 1
  if ! "${BASSTLER_PYTHON}" -c "import basstler" > /dev/null 2>&1; then
    printf 'basstler\n'
    return 0
  fi
  local missing
  # Its failure is reported rather than echoed, since an empty answer is what
  # a caller reads as "nothing to install".
  missing="$("${BASSTLER_PYTHON}" -m "${BASSTLER_DEPENDENCIES_MODULE}")" || return 1
  # Unquoted so the module's one-specifier-per-line output is word split and
  # printed back as one space separated line.
  # shellcheck disable=SC2086
  echo ${missing}
}

# BASSTLER_INSTALL_WAIT_SECONDS: how long to wait for an install another live
# process holds the lock for, before giving up on it.
BASSTLER_INSTALL_WAIT_SECONDS="${BASSTLER_INSTALL_WAIT_SECONDS:-600}"

# acquire_basstler_install_lock: takes BASSTLER_INSTALL_LOCK, waiting while a
# live process holds it and taking it over from one that died holding it.
# Returns 1 when it is still held after BASSTLER_INSTALL_WAIT_SECONDS.
acquire_basstler_install_lock() {
  mkdir -p "${BASSTLER_HOME}"
  local waited=0 holder
  until mkdir "${BASSTLER_INSTALL_LOCK}" 2>/dev/null; do
    holder="$(cat "${BASSTLER_INSTALL_MARKER}" 2>/dev/null || true)"
    if [ -n "${holder}" ] && ! kill -0 "${holder}" 2>/dev/null; then
      rm -rf "${BASSTLER_INSTALL_LOCK}"
      continue
    fi
    [ "${waited}" -lt "${BASSTLER_INSTALL_WAIT_SECONDS}" ] || return 1
    sleep 1
    waited=$((waited + 1))
  done
  printf '%s\n' "$$" > "${BASSTLER_INSTALL_MARKER}"
}

# release_basstler_install_lock: gives BASSTLER_INSTALL_LOCK back.
release_basstler_install_lock() {
  rm -rf "${BASSTLER_INSTALL_LOCK}"
}

# ensure_basstler_package: creates the package's environment where it is
# missing and installs the package where it is missing or out of date, holding
# BASSTLER_INSTALL_LOCK throughout. Sets BASSTLER_PACKAGE_STATE to one of
#   current             - nothing was missing
#   created             - the environment was created and the package installed
#   installed           - BASSTLER_PACKAGE_REQUIREMENTS were installed
#   unchecked           - what the package requires could not be read
#   locked              - another process held the lock past the wait
#   environment-failed  - no environment could be created
#   install-failed      - installing BASSTLER_PACKAGE_REQUIREMENTS failed
# and returns 1 for the last four, when the package cannot be run.
ensure_basstler_package() {
  BASSTLER_PACKAGE_REQUIREMENTS=""
  if ! acquire_basstler_install_lock; then
    BASSTLER_PACKAGE_STATE=locked
    return 1
  fi
  resolve_basstler_python
  local status=0
  install_basstler_package_where_needed || status=$?
  release_basstler_install_lock
  return "${status}"
}

# install_basstler_package_where_needed: ensure_basstler_package's work, run
# while it holds the lock.
install_basstler_package_where_needed() {
  if [ ! -x "${BASSTLER_ENVIRONMENT_INTERPRETER}" ]; then
    BASSTLER_PACKAGE_REQUIREMENTS="$(basstler_package_source)"
    if ! create_basstler_environment; then
      BASSTLER_PACKAGE_STATE=environment-failed
      return 1
    fi
    resolve_basstler_python
    install_basstler_package_or_fail created
    return
  fi
  if ! installed_package_is_current; then
    BASSTLER_PACKAGE_REQUIREMENTS="$(basstler_package_source)"
    install_basstler_package_or_fail installed
    return
  fi
  if ! BASSTLER_PACKAGE_REQUIREMENTS="$(missing_dependencies)"; then
    BASSTLER_PACKAGE_STATE=unchecked
    return 1
  fi
  if [ -z "${BASSTLER_PACKAGE_REQUIREMENTS}" ]; then
    BASSTLER_PACKAGE_STATE=current
    return 0
  fi
  install_basstler_package_or_fail installed
}

# install_basstler_package_or_fail: installs the package into its environment,
# setting BASSTLER_PACKAGE_STATE to $1 when that works and to install-failed
# when it does not.
install_basstler_package_or_fail() {
  local state_when_installed="$1"
  if ! install_basstler_into_environment; then
    BASSTLER_PACKAGE_STATE=install-failed
    return 1
  fi
  BASSTLER_PACKAGE_STATE="${state_when_installed}"
}

# basstler_package_failure_line: prints, on one line, why the package cannot be
# run after ensure_basstler_package failed, and what to do about it.
basstler_package_failure_line() {
  case "${BASSTLER_PACKAGE_STATE}" in
    locked)
      printf 'basstler is still being installed by another process after %ss; wait for it, or remove %s if no install is running\n' \
        "${BASSTLER_INSTALL_WAIT_SECONDS}" "${BASSTLER_INSTALL_LOCK}" ;;
    environment-failed)
      printf 'could not create basstler'"'"'s environment at %s: %s\n' \
        "${BASSTLER_ENVIRONMENT_DIRECTORY}" \
        "$(printf '%s' "${BASSTLER_ENVIRONMENT_OUTPUT}" | tail -1)" ;;
    install-failed)
      printf 'could not install %s: %s - run: %s\n' "${BASSTLER_PACKAGE_REQUIREMENTS}" \
        "$(printf '%s' "${BASSTLER_INSTALL_OUTPUT}" | tail -1)" \
        "${BASSTLER_INSTALL_COMMAND[*]}" ;;
    *)
      printf 'could not read what the basstler package requires for %s - run: pip --python %s install '"'"'%s'"'"'\n' \
        "${BASSTLER_PYTHON}" "${BASSTLER_PYTHON}" "$(basstler_package_source)" ;;
  esac
}

# ready_basstler_python: returns at once when the package is installed and
# current; otherwise completes or waits on its install, and exits the calling
# script with basstler_package_failure_line on stderr when it still cannot run.
ready_basstler_python() {
  installed_package_is_current && return 0
  if ! ensure_basstler_package; then
    basstler_package_failure_line >&2
    exit 1
  fi
}

# require_basstler_python: exits the calling script, saying what to run, unless
# BASSTLER_PYTHON exists and has every requirement of the package. A session
# start installs them; a checkout where that never ran reaches this message
# instead of a traceback.
require_basstler_python() {
  if ! command -v "${BASSTLER_PYTHON}" > /dev/null 2>&1; then
    echo "${BASSTLER_PYTHON} is required to run the basstler package." >&2
    exit 1
  fi
  local missing
  if ! missing="$(missing_dependencies)"; then
    echo "Could not read what the basstler package requires." >&2
    exit 1
  fi
  if [ -n "${missing}" ]; then
    echo "The basstler package's requirements are not installed for ${BASSTLER_PYTHON}: ${missing}" >&2
    echo "Start a new session, or run: pip --python ${BASSTLER_PYTHON} install '$(basstler_package_source)'" >&2
    exit 1
  fi
}

# create_basstler_environment: creates BASSTLER_ENVIRONMENT_DIRECTORY from an
# interpreter satisfying BASSTLER_PYTHON_REQUIREMENT, leaving what the creating
# tool said in BASSTLER_ENVIRONMENT_OUTPUT and returning its exit status.
#
# uv first, since it can fetch a supported interpreter where none is
# installed; then the first of BASSTLER_INTERPRETER_CANDIDATES on PATH; and
# with neither, uv installed into BASSTLER_UV_DIRECTORY for the purpose.
# None of them installs pip into the environment:
# install_basstler_into_environment runs the pip on PATH against it instead.
create_basstler_environment() {
  mkdir -p "${BASSTLER_HOME}"
  local uv
  if uv="$(available_uv)"; then
    create_basstler_environment_with "${uv}"
    return
  fi
  local candidate
  for candidate in ${BASSTLER_INTERPRETER_CANDIDATES}; do
    command -v "${candidate}" > /dev/null 2>&1 || continue
    BASSTLER_ENVIRONMENT_OUTPUT="$("${candidate}" -m venv --without-pip \
      "${BASSTLER_ENVIRONMENT_DIRECTORY}" 2>&1)"
    return
  done
  if ! install_uv; then
    BASSTLER_ENVIRONMENT_OUTPUT="no uv and none of ${BASSTLER_INTERPRETER_CANDIDATES} on PATH, and uv could not be installed: $(printf '%s' "${BASSTLER_ENVIRONMENT_OUTPUT}" | tail -1)"
    return 1
  fi
  create_basstler_environment_with "${BASSTLER_INSTALLED_UV}"
}

# available_uv: prints the uv to create the environment with - the one on PATH,
# else the one install_uv installed earlier - and returns 1 when there is none.
available_uv() {
  command -v uv && return
  [ -x "${BASSTLER_INSTALLED_UV}" ] || return 1
  printf '%s\n' "${BASSTLER_INSTALLED_UV}"
}

# create_basstler_environment_with: creates the environment with the uv at $1,
# which fetches a supported interpreter if none is installed. --no-project, so
# a uv project the caller happens to stand in does not impose its own Python
# requirement, which is not the package's.
create_basstler_environment_with() {
  local uv="$1"
  BASSTLER_ENVIRONMENT_OUTPUT="$("${uv}" venv --no-project \
    --python "${BASSTLER_PYTHON_REQUIREMENT}" \
    "${BASSTLER_ENVIRONMENT_DIRECTORY}" 2>&1)"
}

# install_uv: installs uv into BASSTLER_UV_DIRECTORY with the pip on PATH,
# leaving pip's output in BASSTLER_ENVIRONMENT_OUTPUT. A target directory
# rather than the interpreter's own packages, so an externally managed
# interpreter does not refuse it.
install_uv() {
  BASSTLER_ENVIRONMENT_OUTPUT="$(pip install --target \
    "${BASSTLER_UV_DIRECTORY}" uv 2>&1)"
}

# install_basstler_into_environment: installs the package from
# basstler_package_source into its environment, which brings every declared
# dependency with it, leaving the installer's own output (both streams) in
# BASSTLER_INSTALL_OUTPUT and the command it ran in the array
# BASSTLER_INSTALL_COMMAND, for a caller to report, and returning the
# installer's exit status. On success, records the source it installed from.
#
# A checkout is installed editable, so `import basstler` there is that
# checkout's own source rather than a second copy of it in site-packages. With
# uv where there is one, since pip runs its own code inside the environment's
# interpreter and an older pip breaks on a newer interpreter; otherwise with
# the pip on PATH.
install_basstler_into_environment() {
  local source uv
  source="$(basstler_package_source)"
  local requirement=("${source}")
  [ -n "${BASSTLER_CHECKOUT}" ] && requirement=(--editable "${source}")
  if uv="$(available_uv)"; then
    BASSTLER_INSTALL_COMMAND=("${uv}" pip install --python
      "${BASSTLER_ENVIRONMENT_INTERPRETER}" "${requirement[@]}")
  else
    BASSTLER_INSTALL_COMMAND=(pip --python "${BASSTLER_ENVIRONMENT_INTERPRETER}"
      install "${requirement[@]}")
  fi
  BASSTLER_INSTALL_OUTPUT="$("${BASSTLER_INSTALL_COMMAND[@]}" 2>&1)" || return
  printf '%s\n' "${source}" > "${BASSTLER_INSTALLED_SOURCE_FILE}"
}

# PLAN_STATE_SYNC_STAMP: gitignored file recording the personal-notes commit
# SHA that was PERSONAL_NOTES_REFERENCE the last time this clone read plan state (either
# session-start.sh's own auto-discovery, or ./plan-updates-since.sh). This is
# the "last-seen SHA" the recheck-deltas convention in the personal notes is built
# around: a session that wants to know what changed since it last looked
# diffs from this stamp instead of rereading whole files - see
# ./plan-updates-since.sh, which is also what advances it.
PLAN_STATE_SYNC_STAMP="${PROJECT_ROOT}/.claude/.plan-state-sync-sha"

# record_plan_state_sync_stamp: stamps PERSONAL_NOTES_REFERENCE as the notes-branch commit
# this clone has now read plan state at. Caller must have already fetched
# NOTES_BRANCH successfully (see fetch_personal_notes_branch) - reads
# PERSONAL_NOTES_REFERENCE rather than fetching again itself, same reasoning as
# plan_id_for_branch above.
record_plan_state_sync_stamp() {
  mkdir -p "$(dirname "${PLAN_STATE_SYNC_STAMP}")"
  git rev-parse "${PERSONAL_NOTES_REFERENCE}" > "${PLAN_STATE_SYNC_STAMP}"
}

# last_recorded_plan_state_sha: prints the SHA record_plan_state_sync_stamp
# last recorded, and returns 0. Returns 1 (prints nothing) if nothing has
# been recorded yet - a fresh clone, or one whose session-start.sh predates
# this stamp.
last_recorded_plan_state_sha() {
  [ -f "${PLAN_STATE_SYNC_STAMP}" ] || return 1
  cat "${PLAN_STATE_SYNC_STAMP}"
}
