#!/usr/bin/env python3
"""
Read the upstream through the fork's own Actions, from a branch of its own: one pull
request's report, or the open pull requests opened from the fork's branches.

The fork carries one branch, :attr:`ActionBranch.NAME`, sharing no history with its
other branches and holding nothing but a workflow calling basstler's reusable one, a
short README, the latest request and the latest result. Pushing a new request is what
starts a run - a push runs the workflow file on the pushed branch, so the fork's default
branch never carries a basstler file, and nothing needs a dispatch permission. The run
reads the upstream over GraphQL, records every response, and commits the recording back
to the same branch; the session fetches it and replays it through
:class:`UpstreamPullRequestReader`, so the report is exactly what a direct read renders.

Commands::

    # once, and again after each basstler upgrade (basstler.setup.steps runs it)
    python -m basstler.upstream_reviews.action install --branch-or-tag vX.Y.Z
    # what check-setup.sh reports: missing, stale <branch or tag> or current
    python -m basstler.upstream_reviews.action status --branch-or-tag vX.Y.Z
    # request a read, wait for the run, print the report
    python -m basstler.upstream_reviews.action read --upstream O/N --fork-owner F \\
        (--pull-request N | --branch B) [--failure-logs] [--include-resolved]
    # on the runner: answer the branch's request
    python -m basstler.upstream_reviews.action run
"""

from __future__ import annotations

from basstler_domain_model.github.exceptions import PullRequestReadError

import argparse
import sys
from enum import StrEnum
from pathlib import Path

from basstler.exit_codes import ExitCode
from basstler_domain_model.git.exceptions import (
    GitCommandFailed,
)
from basstler_domain_model.git.commands import (
    GitCommandRunner,
)
from basstler_domain_model.repository import (
    Repository,
)
from basstler.standard_streams import StandardStreamHandler
from basstler.upstream_reviews.action.branch import (
    ActionInstallation,
    RemoteBranch,
)
from basstler.upstream_reviews.action.read import (
    ActionRead,
)
from basstler.upstream_reviews.action.run import ActionRun
from basstler_domain_model.github.clients import (
    GitHubCommandLineClient,
)
from basstler.upstream_reviews.exceptions import (
    ActionUnavailable,
)
from basstler.upstream_reviews.request import (
    UpstreamReadRequest,
)
from basstler.upstream_reviews.vocabulary import (
    ActionBranch,
    ActionFile,
)

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its commands print through.
"""


# %% exit status


class ActionExitCode(ExitCode):
    """
    What ``read``'s exit status tells the skill.
    """

    SUCCESS = 0
    """
    The report was printed.
    """

    READ_FAILED = 1
    """
    The read itself failed - the branch was never promoted, say - which is the answer.
    """

    USAGE = 2
    """
    The wrong arguments.
    """

    UNAVAILABLE = 3
    """
    The fork's Action could not answer, so the skill reads the upstream another way.
    """


# %% command line


class ActionCommand(StrEnum):
    """
    The module's commands.
    """

    INSTALL = "install"
    """
    Write the Action branch, or bring it to this release.
    """

    STATUS = "status"
    """
    Print whether the Action branch is missing, stale or current.
    """

    READ = "read"
    """
    Request a read, wait for its result and print the report.
    """

    RUN = "run"
    """
    On the runner: answer the branch's request.
    """


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """
    Parse the command line.

    :param argv: The arguments to parse, defaulting to the process's own.
    :return: The parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--remote", default="origin", help="the fork's remote")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in (ActionCommand.INSTALL, ActionCommand.STATUS):
        subcommand = commands.add_parser(command)
        subcommand.add_argument(
            "--branch-or-tag",
            required=True,
            help="the basstler branch or release tag the workflow runs",
        )
    read = commands.add_parser(ActionCommand.READ)
    target = read.add_mutually_exclusive_group(required=True)
    target.add_argument("--pull-request", type=int)
    target.add_argument("--branch")
    read.add_argument("--upstream", required=True, type=Repository.parse)
    read.add_argument("--fork-owner", required=True)
    read.add_argument("--failure-logs", action="store_true")
    read.add_argument("--include-resolved", action="store_true")
    read.add_argument(
        "--timeout", type=float, default=480, help="seconds to wait for the run"
    )
    run = commands.add_parser(ActionCommand.RUN)
    run.add_argument("--request", type=Path, default=Path(ActionFile.REQUEST))
    run.add_argument("--result", type=Path, default=Path(ActionFile.RESULT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """
    Run one command.

    :param argv: The arguments to parse, defaulting to the process's own.
    :return: The process exit status.
    """
    arguments = _parse_arguments(argv)
    try:
        if arguments.command == ActionCommand.RUN:
            ActionRun(arguments.request, arguments.result).write_answer()
            return 0
        branch = RemoteBranch(
            GitCommandRunner(arguments.project_root), arguments.remote
        )
        if arguments.command == ActionCommand.INSTALL:
            installation = ActionInstallation(arguments.branch_or_tag)
            changed = installation.install(branch)
            logger.info(
                f"{ActionBranch.NAME} on {branch.description} "
                f"{'now runs' if changed else 'already runs'} basstler {arguments.branch_or_tag}"
            )
            return 0
        if arguments.command == ActionCommand.STATUS:
            state, installed = ActionInstallation(arguments.branch_or_tag).state(branch)
            logger.info(f"{state} {installed}" if installed else state)
            return 0
        action_read = ActionRead(
            branch,
            UpstreamReadRequest.from_arguments(arguments, arguments.upstream),
            GitHubCommandLineClient(),
        )
        commit = action_read.send()
        result = action_read.wait(arguments.timeout, commit)
        logger.info(result.report(action_read.request, arguments.include_resolved))
    except ActionUnavailable as failure:
        logger.error(failure)
        return ActionExitCode.UNAVAILABLE
    except (PullRequestReadError, GitCommandFailed) as failure:
        logger.error(failure)
        return ActionExitCode.READ_FAILED
    return ActionExitCode.SUCCESS


if __name__ == "__main__":
    sys.exit(main())
