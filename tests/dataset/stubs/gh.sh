#!/bin/bash
set -uo pipefail

# Stands in for `gh` on PATH in tests (stub_bin, stubbed_gh fixtures). One stub for every
# suite; an unrecognized invocation exits 64 so a changed call fails loudly.
#   api graphql --input -              prints STUB_GH_GRAPHQL_JSON
#   api --allow-escape-sequences <ep>  prints STUB_GH_JOB_LOG (the flag must be passed)
#   api --paginate .../comments?...    prints STUB_GH_ISSUE_COMMENTS_JSON
#   api --paginate --slurp <path>      prints STUB_GH_REST_JSON
#   api repos/<path>                   prints STUB_GH_REST_JSON
#   stack <subcommand> ...             exits STUB_GH_STACK_EXIT_CODE (default 0), printing
#                                      STUB_GH_STACK_ERROR on stderr when it fails and
#                                      logging GH_REPO and its arguments
#   extension list                     prints STUB_GH_EXTENSIONS, one extension per line
#   --version                          prints STUB_GH_VERSION as gh does
# STUB_GH_EXIT_CODE simulates a failure; STUB_GH_CALL_LOG records each request.

if [ "${1:-}" = "stack" ]; then
  if [ -n "${STUB_GH_CALL_LOG:-}" ]; then
    printf 'GH_REPO=%s %s\n' "${GH_REPO:-}" "$*" >> "${STUB_GH_CALL_LOG}"
  fi
  EXIT_CODE="${STUB_GH_STACK_EXIT_CODE:-0}"
  if [ "${EXIT_CODE}" -ne 0 ]; then
    printf '%s\n' "${STUB_GH_STACK_ERROR:-stub gh stack: simulated exit ${EXIT_CODE}}" >&2
  fi
  exit "${EXIT_CODE}"
fi

if [ "${1:-}" = "extension" ] && [ "${2:-}" = "list" ]; then
  if [ -n "${STUB_GH_EXTENSIONS:-}" ]; then
    printf '%s\n' "${STUB_GH_EXTENSIONS}" | while IFS= read -r extension; do
      printf '%s\t%s\t%s\n' "${extension}" "github/gh-stack" "v0.0.1"
    done
  fi
  exit 0
fi

if [ "${1:-}" = "--version" ]; then
  printf 'gh version %s (2026-09-03)\n' "${STUB_GH_VERSION:-2.90.0}"
  exit 0
fi

if [ "${1:-}" = "api" ] && [ "${2:-}" = "graphql" ] && [ "${3:-}" = "--input" ]; then
  REQUEST_BODY="$(cat)"
  if [ -n "${STUB_GH_CALL_LOG:-}" ]; then
    printf '%s\n' "${REQUEST_BODY}" >> "${STUB_GH_CALL_LOG}"
  fi
  EXIT_CODE="${STUB_GH_EXIT_CODE:-0}"
  if [ "${EXIT_CODE}" -ne 0 ]; then
    echo "stub gh: simulated failure" >&2
    exit "${EXIT_CODE}"
  fi
  printf '%s' "${STUB_GH_GRAPHQL_JSON:-{\}}"
  exit 0
fi

if [ "${1:-}" = "api" ] && [ "${2:-}" = "--allow-escape-sequences" ]; then
  if [ -n "${STUB_GH_CALL_LOG:-}" ]; then
    printf '%s\n' "${3:-}" >> "${STUB_GH_CALL_LOG}"
  fi
  EXIT_CODE="${STUB_GH_EXIT_CODE:-0}"
  if [ "${EXIT_CODE}" -ne 0 ]; then
    echo "stub gh: simulated failure" >&2
    exit "${EXIT_CODE}"
  fi
  printf '%s' "${STUB_GH_JOB_LOG:-}"
  exit 0
fi

if [ -n "${STUB_GH_CALL_LOG:-}" ]; then
  printf '%s\n' "$*" >> "${STUB_GH_CALL_LOG}"
fi

if [ "${1:-}" = "api" ] && [ "${2:-}" = "--paginate" ] && [ "${3:-}" = "--slurp" ]; then
  printf '%s' "${STUB_GH_REST_JSON:-[]}"
  exit 0
fi

if [ "${1:-}" = "api" ] && [ "$#" -eq 2 ] && [[ "${2:-}" == repos/* ]]; then
  printf '%s' "${STUB_GH_REST_JSON:-{\}}"
  exit 0
fi

if [ "${1:-}" = "api" ] && [ "${2:-}" = "--paginate" ]; then
  case "${3:-}" in
    repos/*/issues/*/comments\?*)
      printf '%s' "${STUB_GH_ISSUE_COMMENTS_JSON:-[]}"
      exit 0
      ;;
  esac
fi

echo "stub gh: unexpected invocation: $*" >&2
exit 64
