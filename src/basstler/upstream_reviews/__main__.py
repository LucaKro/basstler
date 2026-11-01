#!/usr/bin/env python3
"""
Report the checks, failing job logs and review threads a fork's pull request has
collected upstream.

It reads through whichever API the place it runs in can reach. A Claude Code cloud
session's proxy refuses GitHub's GraphQL API but answers REST for the repositories the
session has attached, with a review-threads route of its own carrying the resolved
state GitHub otherwise keeps behind GraphQL; anywhere else - a runner, a developer's
machine - reads through GraphQL. Both readers parse into the same models, so the report
is the same either way. ``gh`` is what talks to GitHub, rather than a hand-rolled client:
runners ship it, ``GITHUB_TOKEN`` or the session proxy authenticates it, so no access
rule is implemented here a fourth time.

Every repository name comes from configuration or the runner's own environment, so the
script works unchanged in any contributor's fork.
"""

from __future__ import annotations

from basstler_domain_model.github.exceptions import (
    GitHubCredentialUnavailableError,
    GitHubRequestFailed,
    PullRequestReadError,
)

import argparse
import sys
from pathlib import Path

from basstler_domain_model.environment import (
    GitHubActionsVariable,
)
from basstler_domain_model.repository import (
    Repository,
)
from basstler.stack.configuration import (
    CommittedStackConfiguration,
    MissingUpstreamRepositoryError,
)
from basstler.standard_streams import StandardStreamHandler
from basstler_domain_model.github.clients import (
    GitHubApi,
)
from basstler_domain_model.github.readers import (
    ReadSource,
)
from basstler.upstream_reviews.request import UpstreamReadRequest

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command print through.
"""

# %% configuration


def resolve_upstream_repository(
    configuration: CommittedStackConfiguration, override: str | None = None
) -> Repository:
    """
    Decide which repository the fork's pull requests are reviewed on.

    Reads the committed settings only. The per-user layer lives on the personal-notes
    branch, which a runner does not check out, so *override* is the escape hatch for a
    checkout whose upstream differs.

    :param configuration: The shipped defaults and the project's committed settings.
    :param override: An ``owner/name`` reference outranking the files.
    :return: The upstream repository.
    :raises MissingUpstreamRepositoryError: If neither names one and no override is
        given.
    """
    if override:
        return Repository.parse(override)
    return configuration.upstream_repository()


# %% command line


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """
    Parse the command line.

    :param argv: The arguments to parse, defaulting to the process's own.
    :return: The parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--pull-request", type=int, help="the upstream pull request number to read"
    )
    target.add_argument(
        "--branch", help="the fork branch whose upstream pull request to read"
    )
    parser.add_argument(
        "--fork-owner",
        default=GitHubActionsVariable.REPOSITORY_OWNER.read() or "",
        help="the owner whose branches to claim, defaulting to the runner's own",
    )
    parser.add_argument(
        "--upstream", help="an owner/name upstream outranking the configured one"
    )
    parser.add_argument(
        "--source",
        type=ReadSource,
        choices=list(ReadSource),
        default=None,
        help="the API to read through, defaulting to the one this place can reach",
    )
    parser.add_argument(
        "--include-resolved",
        action="store_true",
        help="show threads already marked resolved as well",
    )
    parser.add_argument(
        "--failure-logs",
        action="store_true",
        help="quote the log behind each check that failed",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """
    Read one upstream pull request and print its report.

    A branch that was never promoted upstream is an ordinary answer rather than a crash,
    so this boundary turns the script's own errors into a stated reason. Anything else
    still propagates with its traceback intact.

    :param argv: The arguments to parse, defaulting to the process's own.
    :return: The process exit status.
    """
    arguments = _parse_arguments(argv)
    try:
        report = _build_report(arguments)
    except (
        PullRequestReadError,
        GitHubRequestFailed,
        GitHubCredentialUnavailableError,
        MissingUpstreamRepositoryError,
    ) as failure:
        logger.error(failure)
        return 1
    logger.info(report)
    summary_path = GitHubActionsVariable.STEP_SUMMARY.read()
    if summary_path:
        Path(summary_path).write_text(report)
    return 0


def _build_report(arguments: argparse.Namespace) -> str:
    """
    Read the requested pull request and render its report.

    :param arguments: The parsed command line.
    :return: The rendered markdown.
    """
    client = GitHubApi.from_environment()
    upstream = resolve_upstream_repository(
        CommittedStackConfiguration(), override=arguments.upstream
    )
    source = arguments.source or ReadSource.detect()
    return UpstreamReadRequest.from_arguments(arguments, upstream).report(
        source.reader(client, upstream, arguments.fork_owner),
        client,
        arguments.include_resolved,
    )


if __name__ == "__main__":
    sys.exit(main())
