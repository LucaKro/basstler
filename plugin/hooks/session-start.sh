#!/bin/bash
set -euo pipefail

# SessionStart hook. Writes CLAUDE.local.md at the project root from the
# personal-notes branch (default claude/personal-notes on origin; overridable by
# git config or environment, see ./README.md): the notes, this branch's PR
# progress, and this branch's plan item with the plan's standing rules. Also
# syncs personal settings and git identity, keeps the package environment
# installed, stamps the plan state SHA for plan-updates-since.sh, fast-forwards
# the default branch to the upstream (see basstler.maintenance.default_branch_sync),
# brings the fork's upstream-reviews Action to this session's release, makes
# `gh stack` runnable, and prints a summary of what it did. Never fails the session on a setup gap.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/resolve-personal-notes-config.sh"
source "${SCRIPT_DIR}/session-start-messages.sh"

# Tell later commands where the plugin is (CLAUDE_ENV_FILE is SessionStart-only).
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  printf 'export BASSTLER_PLUGIN_ROOT=%q\n' "${PLUGIN_ROOT}" >> "${CLAUDE_ENV_FILE}"
fi

fetch_personal_notes_branch || exit 0

# Reads below use PERSONAL_NOTES_REFERENCE, which later fetches leave alone.
record_plan_state_sync_stamp

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
# Safe inside <!-- --> headers; lookups still use CURRENT_BRANCH.
CURRENT_BRANCH_FOR_COMMENT="${CURRENT_BRANCH//-->/}"

OUTPUT_FILE="$(mktemp)"
trap 'rm -f "${OUTPUT_FILE}"' EXIT
WROTE_ANYTHING=0

# SUMMARY_* lines are printed at the end; the rest are set on every path below.
SUMMARY_NOTES="not found"
SUMMARY_PROGRESS="not applicable (no current PR on this branch)"

# Installs the package environment only where something is missing; never
# fatal. Runs before the plan section and the setup check, which rely on it.
# Holds the install lock, so a skill started while this runs waits for it.
ensure_basstler_environment() {
  record_latest_basstler_release
  ensure_basstler_package || true
  case "${BASSTLER_PACKAGE_STATE}" in
    current)
      SUMMARY_DEPENDENCIES="$(dependencies_line_already_installed \
        "${BASSTLER_ENVIRONMENT_DIRECTORY}")" ;;
    created)
      SUMMARY_DEPENDENCIES="$(dependencies_line_environment_created \
        "${BASSTLER_ENVIRONMENT_DIRECTORY}" "${BASSTLER_PACKAGE_REQUIREMENTS}")" ;;
    installed)
      SUMMARY_DEPENDENCIES="$(dependencies_line_installed \
        "${BASSTLER_PACKAGE_REQUIREMENTS}" "${BASSTLER_ENVIRONMENT_DIRECTORY}")" ;;
    environment-failed)
      SUMMARY_DEPENDENCIES="$(dependencies_line_environment_not_created \
        "${BASSTLER_ENVIRONMENT_DIRECTORY}" \
        "$(printf '%s' "${BASSTLER_ENVIRONMENT_OUTPUT}" | tail -1)" \
        "${BASSTLER_PYTHON_REQUIREMENT}")" ;;
    install-failed)
      SUMMARY_DEPENDENCIES="$(dependencies_line_install_failed \
        "${BASSTLER_PACKAGE_REQUIREMENTS}" "${BASSTLER_ENVIRONMENT_DIRECTORY}" \
        "$(printf '%s' "${BASSTLER_INSTALL_OUTPUT}" | tail -1)" \
        "${BASSTLER_INSTALL_COMMAND[*]}")" ;;
    locked)
      SUMMARY_DEPENDENCIES="$(dependencies_line_install_running \
        "${BASSTLER_INSTALL_LOCK}")" ;;
    *)
      SUMMARY_DEPENDENCIES="$(dependencies_line_not_checked)" ;;
  esac
}

ensure_basstler_environment

if git cat-file -e "${PERSONAL_NOTES_REFERENCE}:${NOTES_PATH}" 2>/dev/null; then
  cat <<HEADER >> "${OUTPUT_FILE}"
<!--
Personal notes, synced from '${NOTES_BRANCH}' (${NOTES_PATH}) on remote
'${ACTIVE_NOTES_REMOTE}' by session-start.sh.
To edit: change the notes between the markers below, then run
  "\${BASSTLER_PLUGIN_ROOT}/hooks/save-personal-notes.sh"
to push the change back. This header and the markers are regenerated every
session from git config/environment/default (plus a same-branch-upstream
fallback) - editing them has no effect; only content between the markers is
ever saved.
-->
<!-- BEGIN-PERSONAL-NOTES -->
HEADER
  git show "${PERSONAL_NOTES_REFERENCE}:${NOTES_PATH}" >> "${OUTPUT_FILE}"
  echo "<!-- END-PERSONAL-NOTES -->" >> "${OUTPUT_FILE}"
  WROTE_ANYTHING=1
  SUMMARY_NOTES="loaded from '${NOTES_BRANCH}' (${NOTES_PATH}) on '${ACTIVE_NOTES_REMOTE}'"
fi

PROGRESS_PATH="$(pr_progress_path || true)"
if [ -n "${PROGRESS_PATH}" ]; then
  [ "${WROTE_ANYTHING}" = "1" ] && printf '\n' >> "${OUTPUT_FILE}"
  cat <<PROGRESS_HEADER >> "${OUTPUT_FILE}"
<!--
PR progress for branch '${CURRENT_BRANCH_FOR_COMMENT}', from '${NOTES_BRANCH}'
(${PROGRESS_PATH}) on '${ACTIVE_NOTES_REMOTE}'. Holds only the plan, what is
done and what is next - history belongs in the commits and on the pull
request; save-pr-progress.sh refuses notes over the limit in
${PLAN_SIZE_LIMITS_MODULE}. Edit between the markers, then run
  "\${BASSTLER_PLUGIN_ROOT}/hooks/save-pr-progress.sh"
-->
<!-- BEGIN-PR-PROGRESS -->
PROGRESS_HEADER
  if git cat-file -e "${PERSONAL_NOTES_REFERENCE}:${PROGRESS_PATH}" 2>/dev/null; then
    git show "${PERSONAL_NOTES_REFERENCE}:${PROGRESS_PATH}" >> "${OUTPUT_FILE}"
    SUMMARY_PROGRESS="loaded for branch '${CURRENT_BRANCH}' (${PROGRESS_PATH})"
  else
    cat <<'SCAFFOLD' >> "${OUTPUT_FILE}"
No progress recorded yet for this branch. Initialize it now as three short
lists - Plan, Done, Next - and keep it current as you work.
SCAFFOLD
    SUMMARY_PROGRESS="no saved progress yet for branch '${CURRENT_BRANCH}' - scaffold written"
  fi
  echo "<!-- END-PR-PROGRESS -->" >> "${OUTPUT_FILE}"
  WROTE_ANYTHING=1
fi

PLAN_ID="$(plan_id_for_branch "${CURRENT_BRANCH}" || true)"

# No plan item: say which case (untrackable branch, no item here, no plans).
if [ -z "${PLAN_ID}" ]; then
  if ! branch_can_hold_plan_item "${CURRENT_BRANCH}"; then
    SUMMARY_PLAN="$(plan_line_not_applicable)"
  elif plan_branch_index_exists; then
    SUMMARY_PLAN="$(plan_line_no_item_tracks_branch "${CURRENT_BRANCH}" "$(tracked_plan_count)")"
  else
    SUMMARY_PLAN="$(plan_line_no_plans_tracked "${NOTES_BRANCH}")"
  fi
else
  PLAN_MANIFEST_PATH="$(plan_manifest_path "${PLAN_ID}")"
  PLAN_ROADMAP_PATH="$(plan_roadmap_path "${PLAN_ID}")"
  # Replaced below unless the indexed manifest is missing.
  SUMMARY_PLAN="$(plan_line_manifest_missing "${PLAN_ID}" "${PLAN_MANIFEST_PATH}" "${NOTES_BRANCH}")"
  if git cat-file -e "${PERSONAL_NOTES_REFERENCE}:${PLAN_MANIFEST_PATH}" 2>/dev/null; then
    [ "${WROTE_ANYTHING}" = "1" ] && printf '\n' >> "${OUTPUT_FILE}"
    # `|| true`: a plan without tracking_issue must not kill the hook.
    TRACKING_ISSUE="$(git show "${PERSONAL_NOTES_REFERENCE}:${PLAN_MANIFEST_PATH}" 2>/dev/null \
      | grep -oE '^tracking_issue:[[:space:]]*[0-9]+' | head -1 \
      | grep -oE '[0-9]+$' || true)"
    if [ -n "${TRACKING_ISSUE}" ]; then
      TRACKING_ISSUE_NOTE="Structural changes (a wave, a deferral, a split, a reprioritization) are
the user's call: ask first, then edit the manifest and comment on tracking
issue #${TRACKING_ISSUE}. While working an item of this plan, read that issue's
comments before each push - an issue cannot be subscribed to."
    else
      TRACKING_ISSUE_NOTE="This plan has no tracking_issue set, so there is no coordination
mailbox for structural changes yet - edit the manifest directly as usual."
    fi
    # Load only this branch's item; the rest costs context every turn.
    MANIFEST_COPY="$(mktemp)"
    git show "${PERSONAL_NOTES_REFERENCE}:${PLAN_MANIFEST_PATH}" > "${MANIFEST_COPY}"
    if ! PLAN_ITEM="$("${BASSTLER_PYTHON}" -m "${PLAN_MANIFEST_TOOLS_MODULE}" item \
        "${MANIFEST_COPY}" --branch "${CURRENT_BRANCH}" 2>&1)"; then
      PLAN_ITEM="(could not read this branch's item: ${PLAN_ITEM})"
    fi
    rm -f "${MANIFEST_COPY}"
    cat <<PLAN_HEADER >> "${OUTPUT_FILE}"
<!--
This branch's item in plan '${PLAN_ID}', read from '${NOTES_BRANCH}'
(${PLAN_MANIFEST_PATH}) on remote '${ACTIVE_NOTES_REMOTE}' by
session-start.sh, followed by the plan's standing rules (${PLAN_ROADMAP_PATH}).
Read-only: editing it here changes nothing. To read another item, run
  "\${BASSTLER_PYTHON}" -m ${PLAN_MANIFEST_TOOLS_MODULE} item <manifest-file> --id <item-id>
on a copy from 'git show ${PERSONAL_NOTES_REFERENCE}:${PLAN_MANIFEST_PATH}' after fetching
'${NOTES_BRANCH}' - never read the whole manifest when one item will do.
To change the plan, record the item with basstler.plan.item_bootstrap's 'record', or
fetch both files fresh, edit them, and run
  "\${BASSTLER_PLUGIN_ROOT}/hooks/save-plan.sh" ${PLAN_ID} --manifest <file> --roadmap <file>
which drops landed items, removes a plan with none left, and refuses a plan
over the limits in ${PLAN_SIZE_LIMITS_MODULE}. Then run
/plan-dashboard ${PLAN_ID}.

${TRACKING_ISSUE_NOTE}
-->
<!-- BEGIN-PLAN-ITEM: ${PLAN_ID} -->
${PLAN_ITEM}
<!-- END-PLAN-ITEM -->
PLAN_HEADER
    if git cat-file -e "${PERSONAL_NOTES_REFERENCE}:${PLAN_ROADMAP_PATH}" 2>/dev/null; then
      printf '\n' >> "${OUTPUT_FILE}"
      git show "${PERSONAL_NOTES_REFERENCE}:${PLAN_ROADMAP_PATH}" >> "${OUTPUT_FILE}"
    fi
    WROTE_ANYTHING=1
    SUMMARY_PLAN="$(plan_line_tracked "${PLAN_ID}" "${TRACKING_ISSUE:-none}")"
  fi
fi

# This session's page on the plan site, from the hub file.
SUMMARY_HUB="$(hub_line_not_registered "${NOTES_BRANCH}")"
if SITE_URL="$(recorded_site_url)"; then
  HUB_POSITION="${SITE_URL:+${SITE_URL%/}/}$(repository_name)/"
  if [ -n "${PLAN_ID}" ]; then
    HUB_POSITION="${HUB_POSITION}${PLAN_ID}/"
    ITEM_ID="$(item_id_for_branch "${PLAN_ID}" "${CURRENT_BRANCH}")"
    [ -n "${ITEM_ID}" ] && HUB_POSITION="${HUB_POSITION}#item-${ITEM_ID}"
  fi
  SUMMARY_HUB="$(hub_line_registered "${SITE_URL}" "${HUB_POSITION}")"
fi

if [ "${WROTE_ANYTHING}" = "1" ]; then
  mv "${OUTPUT_FILE}" "${CLAUDE_LOCAL_MD}"
else
  rm -f "${OUTPUT_FILE}"
fi

# Git identity from the notes branch, only where the clone has no local one.
if ! recorded_git_identity_exists; then
  SUMMARY_GIT_IDENTITY="$(git_identity_line_not_recorded \
    "${NOTES_BRANCH}" "${PERSONAL_GIT_IDENTITY_PATH}")"
elif ! RECORDED_GIT_IDENTITY="$(recorded_git_identity)"; then
  SUMMARY_GIT_IDENTITY="$(git_identity_line_incomplete \
    "${PERSONAL_GIT_IDENTITY_PATH}" "${NOTES_BRANCH}")"
elif LOCAL_GIT_IDENTITY="$(repository_local_git_identity)"; then
  IFS=$'\t' read -r LOCAL_NAME LOCAL_EMAIL <<< "${LOCAL_GIT_IDENTITY}"
  SUMMARY_GIT_IDENTITY="$(git_identity_line_already_set \
    "$(format_git_identity "${LOCAL_NAME}" "${LOCAL_EMAIL}")")"
else
  IFS=$'\t' read -r RECORDED_NAME RECORDED_EMAIL <<< "${RECORDED_GIT_IDENTITY}"
  git config --local user.name "${RECORDED_NAME}"
  git config --local user.email "${RECORDED_EMAIL}"
  SUMMARY_GIT_IDENTITY="$(git_identity_line_written "${NOTES_BRANCH}" \
    "${PERSONAL_GIT_IDENTITY_PATH}" \
    "$(format_git_identity "${RECORDED_NAME}" "${RECORDED_EMAIL}")")"
fi

# Personal settings, copied to .claude/settings.local.json unless edited locally.
SUMMARY_SETTINGS="none on '${NOTES_BRANCH}' (${PERSONAL_SETTINGS_PATH})"
if git cat-file -e "${PERSONAL_NOTES_REFERENCE}:${PERSONAL_SETTINGS_PATH}" 2>/dev/null; then
  if personal_settings_are_locally_modified; then
    SUMMARY_SETTINGS="kept local edits to ${LOCAL_SETTINGS_RELATIVE_PATH} - run save-personal-settings.sh to push them"
  else
    mkdir -p "$(dirname "${LOCAL_SETTINGS_JSON}")"
    git show "${PERSONAL_NOTES_REFERENCE}:${PERSONAL_SETTINGS_PATH}" > "${LOCAL_SETTINGS_JSON}"
    record_personal_settings_sync
    SUMMARY_SETTINGS="synced to ${LOCAL_SETTINGS_RELATIVE_PATH}"
  fi
fi

# Default branch, caught up with the upstream this fork tracks: a stale base is
# invisible from inside the clone. Fast-forward only, never fatal; the
# checked-out branch moves along only when it has no commits of its own.
SUMMARY_DEFAULT_BRANCH="$(default_branch_line_not_synced "${DEFAULT_BRANCH_SYNC_MODULE}")"
if DEFAULT_BRANCH_REPORT="$("${BASSTLER_PYTHON}" -m "${DEFAULT_BRANCH_SYNC_MODULE}" \
    2>/dev/null)"; then
  SUMMARY_DEFAULT_BRANCH="${DEFAULT_BRANCH_REPORT}"
fi

# The fork's upstream-reviews Action, kept at the release this session runs:
# the run and the session replaying it must run the same package, so every
# release would otherwise leave it stale. Only a branch already there is
# touched - installing one is setup's decision. Never fatal.
SUMMARY_UPSTREAM_REVIEWS_ACTION="$(upstream_reviews_action_line_not_read "${NOTES_REMOTE}")"
SESSION_RELEASE="$(basstler_branch_or_tag)"
if ACTION_STATE="$("${BASSTLER_PYTHON}" -m "${UPSTREAM_REVIEWS_ACTION_MODULE}" \
    --remote "${NOTES_REMOTE}" status --branch-or-tag "${SESSION_RELEASE}" 2>/dev/null)"; then
  case "${ACTION_STATE}" in
    missing*)
      SUMMARY_UPSTREAM_REVIEWS_ACTION="$(upstream_reviews_action_line_not_installed)" ;;
    current*)
      SUMMARY_UPSTREAM_REVIEWS_ACTION="$(upstream_reviews_action_line_current \
        "${SESSION_RELEASE}")" ;;
    stale*)
      if "${BASSTLER_PYTHON}" -m "${UPSTREAM_REVIEWS_ACTION_MODULE}" \
          --remote "${NOTES_REMOTE}" install --branch-or-tag "${SESSION_RELEASE}" \
          > /dev/null 2>&1; then
        SUMMARY_UPSTREAM_REVIEWS_ACTION="$(upstream_reviews_action_line_updated \
          "${ACTION_STATE#stale }" "${SESSION_RELEASE}")"
      else
        SUMMARY_UPSTREAM_REVIEWS_ACTION="$(upstream_reviews_action_line_update_failed \
          "${ACTION_STATE#stale }" "${SESSION_RELEASE}" \
          "${BASSTLER_PYTHON} -m ${SETUP_STEPS_MODULE} install-upstream-reviews-action --branch-or-tag ${SESSION_RELEASE}")"
      fi ;;
  esac
fi

# gh stack, which the maintenance pass and the plan item skills rebase and push
# stacks with. Installed only in a cloud session; never fatal.
SUMMARY_GH_STACK="$(bash "${INSTALL_GH_STACK_SCRIPT}" 2>/dev/null)" || true

# Setup verdict from check-setup.sh, run last so it sees what this run wrote.
SUMMARY_SETUP="$(setup_line_not_checked "${CHECK_SETUP_SCRIPT}")"
if [ -f "${CHECK_SETUP_SCRIPT}" ]; then
  NEEDS_SETUP_ROWS="$(bash "${CHECK_SETUP_SCRIPT}" 2>/dev/null \
    | awk -F'\t' '$2 == "needs-setup" { printf "    %s: %s\n", $1, $3 }' || true)"
  if [ -z "${NEEDS_SETUP_ROWS}" ]; then
    SUMMARY_SETUP="$(setup_line_ok)"
  else
    SUMMARY_SETUP="$(printf '%s\n%s' \
      "$(setup_line_needs_setup "$(printf '%s\n' "${NEEDS_SETUP_ROWS}" | wc -l | tr -d ' ')")" \
      "${NEEDS_SETUP_ROWS}")"
  fi
fi

cat <<SUMMARY
session-start.sh summary:
  personal notes:  ${SUMMARY_NOTES}
  local settings:  ${SUMMARY_SETTINGS}
  PR progress:     ${SUMMARY_PROGRESS}
  plan:            ${SUMMARY_PLAN}
  plan site:       ${SUMMARY_HUB}
  default branch:  ${SUMMARY_DEFAULT_BRANCH}
  git identity:    ${SUMMARY_GIT_IDENTITY}
  dependencies:    ${SUMMARY_DEPENDENCIES}
  reviews Action:  ${SUMMARY_UPSTREAM_REVIEWS_ACTION}
  gh stack:        ${SUMMARY_GH_STACK}
  setup:           ${SUMMARY_SETUP}
  plan state SHA:  $(last_recorded_plan_state_sha) (run plan-updates-since.sh <plan-id> to recheck from here later)
SUMMARY
