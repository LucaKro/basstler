#!/bin/bash
# The wording of session-start.sh's summary lines, defined once.
#
# Sourced by ./session-start.sh, which prints these, and called directly by the
# tests that assert on them - so a reworded message changes both sides at once
# instead of drifting apart from a second copy typed into an assertion.
#
# One function per outcome rather than one template string per outcome: the
# arguments are then named and positional in the same place the sentence is
# written, and a caller that passes the wrong number fails loudly here rather
# than rendering a half-substituted line.
#
# Deliberately holds no logic. Deciding *which* message applies is
# session-start.sh's business; this file only says how each one reads.

# %% the plan line

# plan_line_not_applicable: for a branch no plan item could ever track - the
# default branch, the notes branch, a detached HEAD.
plan_line_not_applicable() {
  printf 'not applicable (this branch never holds a plan item)'
}

# plan_line_no_plans_tracked: plans are not in use on the notes branch at all.
plan_line_no_plans_tracked() {
  local notes_branch="$1"
  printf "no plans tracked on '%s' yet" "${notes_branch}"
}

# plan_line_no_item_tracks_branch: plans are in use, and none holds an item for
# this branch. Even-handed on purpose: belonging to no plan is an ordinary
# state for most branches and must not read as a reprimand.
plan_line_no_item_tracks_branch() {
  local branch="$1"
  local tracked_plan_count="$2"
  printf "no item tracks branch '%s' (%s plan(s) tracked) - if this session's work belongs to one of them, add its item before starting; if it belongs to none, there is nothing to do" \
    "${branch}" "${tracked_plan_count}"
}

# plan_line_manifest_missing: the index names a plan whose manifest is not on
# the notes branch, so the two have drifted apart.
plan_line_manifest_missing() {
  local plan_id="$1"
  local manifest_path="$2"
  local notes_branch="$3"
  printf "'%s' tracks this branch, but %s is missing on '%s'" \
    "${plan_id}" "${manifest_path}" "${notes_branch}"
}

# plan_line_tracked: the branch is a tracked item of a plan that resolved.
plan_line_tracked() {
  local plan_id="$1"
  local tracking_issue="$2"
  printf "'%s' (tracking issue: %s)" "${plan_id}" "${tracking_issue}"
}

# %% the hub line

# hub_line_not_registered: the notes branch has no hub file.
hub_line_not_registered() {
  local notes_branch="$1"
  printf "none on '%s' - /setup-personal-notes registers this repository on your plan site" \
    "${notes_branch}"
}

# hub_line_registered: the plan site, and where this session's work sits on it.
hub_line_registered() {
  local site_url="$1"
  local position="$2"
  printf '%s - this session: %s' "${site_url:-not published yet}" "${position}"
}

# %% the git identity line

# git_identity_line_not_recorded: the notes branch carries no identity at all,
# so there is nothing to write into this clone.
git_identity_line_not_recorded() {
  local notes_branch="$1"
  local identity_path="$2"
  printf "not recorded on '%s' (%s) - run ./save-git-identity.sh to record one" \
    "${notes_branch}" "${identity_path}"
}

# git_identity_line_incomplete: an identity is recorded but only half of it,
# and half an identity cannot author a commit.
git_identity_line_incomplete() {
  local identity_path="$1"
  local notes_branch="$2"
  printf "%s on '%s' needs both user.name and user.email - nothing written" \
    "${identity_path}" "${notes_branch}"
}

# git_identity_line_already_set: this clone has an identity of its own, which
# the hook only ever fills a gap around rather than overriding.
git_identity_line_already_set() {
  local identity="$1"
  printf 'already set in this clone: %s - left unchanged' "${identity}"
}

# git_identity_line_written: the recorded identity was written into this
# clone's repository-local config.
git_identity_line_written() {
  local notes_branch="$1"
  local identity_path="$2"
  local identity="$3"
  printf "set from '%s' (%s): %s" "${notes_branch}" "${identity_path}" "${identity}"
}

# %% the default branch line

# default_branch_line_not_synced: the module that catches the default branch
# up could not run at all, so there is no outcome to report rather than a
# clean one. Every outcome it can reach is worded by the module itself.
default_branch_line_not_synced() {
  local module="$1"
  printf 'not synced - %s did not run' "${module}"
}

# %% the setup line

# setup_line_not_checked: check-setup.sh is not in this plugin, so there is
# no verdict to report rather than a passing one.
setup_line_not_checked() {
  local check_setup_script="$1"
  printf 'not checked - %s is missing' "${check_setup_script}"
}

# setup_line_ok: every check passed.
setup_line_ok() {
  printf 'ok'
}

# setup_line_needs_setup: the heading above the indented needs-setup rows,
# which check-setup.sh itself words.
setup_line_needs_setup() {
  local needs_setup_count="$1"
  printf '%s check(s) need setup - run /setup-personal-notes:' "${needs_setup_count}"
}

# %% the dependencies line

# dependencies_line_not_checked: nothing could be looked up - the interpreter or
# the package metadata is missing. Which of the two is check-setup.sh's row to
# word, and the setup line carries it, so this one does not say it twice.
dependencies_line_not_checked() {
  printf 'not checked - the setup line below says why'
}

# dependencies_line_install_running: another process held the install lock
# for longer than a session start waits.
dependencies_line_install_running() {
  local lock="$1"
  printf 'not checked - another process is still installing (lock %s)' "${lock}"
}

# dependencies_line_already_installed: the package's environment already had
# every requirement, so nothing was installed.
dependencies_line_already_installed() {
  local environment_directory="$1"
  printf 'already installed in %s' "${environment_directory}"
}

# dependencies_line_installed: what was missing or out of date, now installed
# by installing the package into its environment.
dependencies_line_installed() {
  local installed="$1"
  local environment_directory="$2"
  printf 'installed %s into %s' "${installed}" "${environment_directory}"
}

# dependencies_line_install_failed: the install did not work. Reported rather
# than fatal, and carrying what the installer itself said, because a hook that
# dies here takes everything after it down with it - the whole point is that
# the rest of the run continues.
dependencies_line_install_failed() {
  local missing="$1"
  local environment_directory="$2"
  local reason="$3"
  local install_command="$4"
  printf 'could not install %s into %s - %s - run: %s' \
    "${missing}" "${environment_directory}" "${reason}" "${install_command}"
}

# dependencies_line_environment_created: the package's environment did not
# exist, and now does, with the package installed into it.
dependencies_line_environment_created() {
  local environment_directory="$1"
  local source_tree="$2"
  printf 'created %s and installed %s into it' "${environment_directory}" "${source_tree}"
}

# dependencies_line_environment_not_created: no environment could be made -
# no supported interpreter, or nothing to fetch one with. Callers fall back to
# python3 until a later start manages it.
dependencies_line_environment_not_created() {
  local environment_directory="$1"
  local reason="$2"
  local python_requirement="$3"
  printf 'could not create %s - %s - run: uv venv --no-project --python "%s" %s' \
    "${environment_directory}" "${reason}" "${python_requirement}" \
    "${environment_directory}"
}

# %% the upstream-reviews Action line

# upstream_reviews_action_line_not_read: the fork's branches could not be read,
# so whether it has the Action is unknown.
upstream_reviews_action_line_not_read() {
  local remote="$1"
  printf "not checked - could not read the fork's branches on '%s'" "${remote}"
}

# upstream_reviews_action_line_not_installed: the fork has no Action branch.
# Installing one is setup's decision, so a session start never does.
upstream_reviews_action_line_not_installed() {
  printf 'not installed on the fork - /setup-personal-notes installs it once an upstream is named'
}

# upstream_reviews_action_line_current: the Action already runs this release.
upstream_reviews_action_line_current() {
  local release="$1"
  printf 'runs %s, the release this session runs' "${release}"
}

# upstream_reviews_action_line_updated: the Action ran another release, and
# now runs this session's.
upstream_reviews_action_line_updated() {
  local previous_release="$1"
  local release="$2"
  printf 'brought from %s to %s, the release this session runs' \
    "${previous_release}" "${release}"
}

# upstream_reviews_action_line_update_failed: the Action runs another release,
# and bringing it to this session's failed; the run carries on.
upstream_reviews_action_line_update_failed() {
  local previous_release="$1"
  local release="$2"
  local install_command="$3"
  printf 'runs %s, not %s, and could not be updated - run: %s' \
    "${previous_release}" "${release}" "${install_command}"
}

# %% the gh stack line

# gh_stack_line_ready: `gh` already runs the gh-stack extension.
gh_stack_line_ready() {
  local gh_version="$1"
  printf 'ready (gh %s)' "${gh_version}"
}

# gh_stack_line_installed: install-gh-stack.sh installed what was missing.
gh_stack_line_installed() {
  local gh_version="$1"
  printf 'installed (gh %s, gh stack)' "${gh_version}"
}

# gh_stack_line_installed_off_path: installed, but no later command sees it.
gh_stack_line_installed_off_path() {
  local gh_version="$1"
  local gh_directory="$2"
  printf 'installed (gh %s, gh stack) - put %s first on PATH to use it' \
    "${gh_version}" "${gh_directory}"
}

# gh_stack_line_unavailable: `gh stack` does not run, and why.
gh_stack_line_unavailable() {
  local reason="$1"
  printf 'unavailable - %s' "${reason}"
}
