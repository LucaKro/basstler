#!/bin/bash
set -uo pipefail

# Makes `gh stack` (GitHub's stacked pull requests, the github/gh-stack extension)
# runnable in this session, and prints one line saying how that went. Called by
# ./session-start.sh; the stacked-PR maintenance pass and the plan item skills
# rebase and push stacks with it.
#
# A cloud session starts from a fresh container with no `gh`, and `gh extension
# install github/gh-stack` is refused there, so this installs both itself:
#   - `gh` ${GH_STACK_GH_VERSION}, the release archive, into ${GH_STACK_TOOLS_DIRECTORY}
#   - gh-stack, cloned and built with Go, then installed as a local extension
# and appends the PATH change to ${CLAUDE_ENV_FILE}, which Claude Code sources for
# every command the session runs afterwards.
#
# Installs only in a cloud session (CLAUDE_CODE_REMOTE=true) or when asked to with
# GH_STACK_INSTALL=1: on someone's own machine, downloading a second `gh` onto PATH
# on every session start is not this hook's call to make, so there it only reports.
#
# Never fatal: every failure becomes the printed line. Exits 0 when `gh stack` runs,
# 1 when it does not.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/resolve-personal-notes-config.sh"
source "${SCRIPT_DIR}/session-start-messages.sh"

GH_DIRECTORY="${GH_STACK_TOOLS_DIRECTORY}/gh_${GH_STACK_GH_VERSION}"
INSTALL_LOG="${GH_STACK_TOOLS_DIRECTORY}/install.log"

# stack_runs: whether gh has the gh-stack extension installed. gh >= 2.90 answers
# `gh stack` without it by printing how to install it and exiting 0, so the exit
# code of `gh stack` itself says nothing.
stack_runs() {
  command -v gh >/dev/null 2>&1 \
    && gh extension list 2>/dev/null | cut -f1 | grep -qx 'gh stack'
}

gh_version() {
  gh --version 2>/dev/null | awk 'NR == 1 { print $3 }'
}

# gh_is_recent_enough: whether the gh on PATH is at least the pinned version.
gh_is_recent_enough() {
  local installed
  installed="$(gh_version)"
  [ -n "${installed}" ] || return 1
  [ "$(printf '%s\n%s\n' "${GH_STACK_GH_VERSION}" "${installed}" | sort -V | head -1)" \
    = "${GH_STACK_GH_VERSION}" ]
}

unavailable() {
  gh_stack_line_unavailable "$1"
  printf '\n'
  exit 1
}

if stack_runs; then
  gh_stack_line_ready "$(gh_version)"
  printf '\n'
  exit 0
fi

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ] && [ "${GH_STACK_INSTALL:-}" != "1" ]; then
  unavailable "install gh >= ${GH_STACK_GH_VERSION}, then: gh extension install github/gh-stack"
fi

mkdir -p "${GH_STACK_TOOLS_DIRECTORY}" 2>/dev/null \
  || unavailable "cannot create ${GH_STACK_TOOLS_DIRECTORY}"
: > "${INSTALL_LOG}"

# A gh from an earlier run of this script, before deciding a download is needed.
if [ -x "${GH_DIRECTORY}/bin/gh" ]; then
  export PATH="${GH_DIRECTORY}/bin:${PATH}"
fi

if ! gh_is_recent_enough; then
  case "$(uname -s)/$(uname -m)" in
    Linux/x86_64) PLATFORM="linux_amd64" ;;
    Linux/aarch64 | Linux/arm64) PLATFORM="linux_arm64" ;;
    *) unavailable "no gh download for $(uname -s)/$(uname -m) - install gh >= ${GH_STACK_GH_VERSION} by hand" ;;
  esac
  ARCHIVE="gh_${GH_STACK_GH_VERSION}_${PLATFORM}"
  if ! curl -fsSL "${GH_RELEASES}/v${GH_STACK_GH_VERSION}/${ARCHIVE}.tar.gz" \
      -o "${GH_STACK_TOOLS_DIRECTORY}/${ARCHIVE}.tar.gz" >>"${INSTALL_LOG}" 2>&1 \
    || ! tar -xzf "${GH_STACK_TOOLS_DIRECTORY}/${ARCHIVE}.tar.gz" \
      -C "${GH_STACK_TOOLS_DIRECTORY}" >>"${INSTALL_LOG}" 2>&1; then
    unavailable "could not download gh ${GH_STACK_GH_VERSION} (log: ${INSTALL_LOG})"
  fi
  rm -rf "${GH_DIRECTORY}"
  mv "${GH_STACK_TOOLS_DIRECTORY}/${ARCHIVE}" "${GH_DIRECTORY}"
  rm -f "${GH_STACK_TOOLS_DIRECTORY}/${ARCHIVE}.tar.gz"
  export PATH="${GH_DIRECTORY}/bin:${PATH}"
fi

if ! stack_runs; then
  command -v go >/dev/null 2>&1 || unavailable "gh-stack has to be built from source and there is no go"
  if [ ! -d "${GH_STACK_EXTENSION_DIRECTORY}/.git" ]; then
    git clone --quiet --depth 1 "${GH_STACK_SOURCE}" "${GH_STACK_EXTENSION_DIRECTORY}" >>"${INSTALL_LOG}" 2>&1 \
      || unavailable "could not clone ${GH_STACK_SOURCE} (log: ${INSTALL_LOG})"
  fi
  ( cd "${GH_STACK_EXTENSION_DIRECTORY}" && go build -o gh-stack . ) >>"${INSTALL_LOG}" 2>&1 \
    || unavailable "could not build gh-stack (log: ${INSTALL_LOG})"
  ( cd "${GH_STACK_EXTENSION_DIRECTORY}" && gh extension install . ) >>"${INSTALL_LOG}" 2>&1
  stack_runs || unavailable "gh-stack built but 'gh stack' does not run (log: ${INSTALL_LOG})"
fi

if [ -x "${GH_DIRECTORY}/bin/gh" ] && [ -z "${CLAUDE_ENV_FILE:-}" ]; then
  gh_stack_line_installed_off_path "$(gh_version)" "${GH_DIRECTORY}/bin"
  printf '\n'
  exit 0
fi
if [ -x "${GH_DIRECTORY}/bin/gh" ]; then
  printf 'export PATH="%s:${PATH}"\n' "${GH_DIRECTORY}/bin" >> "${CLAUDE_ENV_FILE}"
fi
gh_stack_line_installed "$(gh_version)"
printf '\n'
exit 0
