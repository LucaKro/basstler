#!/usr/bin/env python3
"""Stacked pull request helper for the fork-staging / upstream-review workflow.

GitHub is the single source of truth. The stack is **not** declared in a ledger: it is read from a
``board.json`` export of the fork's pull requests, combined with plain ``git``:

  * dependency tree = each fork pull request's **base branch** (base = parent);
  * ``draft`` <-> ``ready`` = the fork pull request's draft flag;
  * ``in-review`` = an upstream pull request opened from the branch is open, recorded on the board by the export;
  * ``merged`` = the branch is an ancestor of ``<upstream_remote>/<upstream_base>``.

``stack.toml`` carries the shipped defaults (label names, the base branch); the project's committed
``.claude/basstler/stack.toml`` names its upstream, and a ``.claude/personal/stack.toml`` on the
personal-notes branch, if present, layers per-user overrides on top of both (see
:meth:`basstler.stack.settings.Configuration.load`).

Commands (run from the repo root; ``--help`` on any of them for its flags)::

    python -m basstler.stack status         # the whole stack: parent, state, drift
    python -m basstler.stack check          # would each branch merge cleanly onto its parent now?
    python -m basstler.stack next           # which branches to submit upstream next
    python -m basstler.stack next --porcelain    # machine-readable: one 'name<TAB>pull request' line per branch
    python -m basstler.stack configuration  # every resolved setting, including the remotes
    python -m basstler.stack register       # put a pull request on top of its GitHub stack
    python -m basstler.stack labels         # the complete label set a write must send
    python -m basstler.stack promotion-link # the upstream compare-and-create URL for a branch
    python -m basstler.stack reparents      # children whose base has landed, and the base they need
    python -m basstler.stack landed         # open fork pull requests whose branch has landed

The last four exist so the steps most easily got wrong by hand are computed rather than recalled: a
label write replaces the whole set, an unencoded compare URL loses its prefill, and a landed parent
is decided by git ancestry rather than by pull-request state. ``landed`` reports only - GitHub
closes a pull request as merged by itself once its head is contained in its base, so nothing here
has to close one.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from basstler.exit_codes import CommandFailure, ExitCode, StackExitCode
from basstler.locations import RepositoryStateLocation
from basstler.maintenance.git_commands import GitCommandRunner
from basstler.maintenance.github import GitHubRepository
from basstler.repository import Repository
from basstler.stack.label_write import LabelWrite
from basstler.stack.model import Stack
from basstler.stack.promotion_link import PromotionLink
from basstler.stack.pull_request_stacks import StackRegistration
from basstler.stack.report import StackReport
from basstler.stack.settings import Configuration
from basstler.standard_streams import StandardStreamHandler

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command prints through.
"""

# %% commands


class Command(StrEnum):
    """Every command this tool answers, named once so no caller spells one out."""

    STATUS = "status"
    """Render the derived stack, one line per branch."""

    CHECK = "check"
    """Report every structural problem the derived stack has."""

    NEXT = "next"
    """Name the branches ready to be promoted upstream."""

    REPARENTS = "reparents"
    """Name the children whose base has landed and must be retargeted."""

    LANDED = "landed"
    """Name the branches whose own commits are already in the upstream base."""

    CONFIGURATION = "configuration"
    """Print the resolved configuration, one ``field<TAB>value`` line per setting."""

    LABELS = "labels"
    """Compute the complete label set a write must send, from the labels carried now."""

    PROMOTION_LINK = "promotion-link"
    """Build the upstream compare-and-create link for one branch."""

    REGISTER = "register"
    """Put a pull request on top of the GitHub stack the pull requests below it form."""

    @property
    def needs_a_board(self) -> bool:
        """Whether answering this command means deriving the stack.

        The ones that do not run before a board has ever been exported.

        :return: Whether ``board.json`` must exist.
        """
        return self not in {
            Command.CONFIGURATION,
            Command.LABELS,
            Command.PROMOTION_LINK,
            Command.REGISTER,
        }


class CommandOption(StrEnum):
    """Every option a command takes, named once so no caller spells one out."""

    PORCELAIN = "--porcelain"
    """Print only what a script reads, for :attr:`Command.NEXT`."""

    FORK = "--fork"
    """The fork, as ``owner/name``, when the caller already knows it."""

    UPSTREAM = "--upstream"
    """The upstream, as ``owner/name``, when the caller already knows it."""

    CURRENT = "--current"
    """A label the pull request carries now, repeated per label."""

    ADD = "--add"
    """A label to end up with."""

    REMOVE = "--remove"
    """A label to end up without."""

    BRANCH = "--branch"
    """The fork branch to build a promotion link for."""

    TITLE = "--title"
    """The title to prefill."""

    BODY = "--body"
    """The description to prefill."""

    REPOSITORY = "--repository"
    """The repository the pull requests are open on, as ``owner/name``."""

    PULL_REQUEST = "--pull-request"
    """The pull request to put on top of its stack."""

    BELOW = "--below"
    """The pull requests it builds on, bottom first."""


# %% entry point


@dataclass(frozen=True)
class StackCommandLine:
    """This module's command line: one subcommand per :class:`Command`."""

    @staticmethod
    def parser() -> argparse.ArgumentParser:
        """:return: The parser for every command and its own flags."""
        parser = argparse.ArgumentParser(
            prog="basstler.stack",
            description="Stacked pull request helper: read state, compute the writes.",
        )
        parser.set_defaults(porcelain=False)
        commands = parser.add_subparsers(dest="command", required=True)

        commands.add_parser(
            Command.STATUS, help="the whole stack: parent, state, drift"
        )
        commands.add_parser(
            Command.CHECK, help="would each branch merge cleanly onto its parent"
        )
        promote = commands.add_parser(
            Command.NEXT, help="which branches to submit upstream next"
        )
        promote.add_argument(
            CommandOption.PORCELAIN,
            action="store_true",
            help="print only 'name<TAB>pull request' per branch to promote",
        )
        commands.add_parser(
            Command.REPARENTS,
            help="children whose base has landed, and the base they need",
        )
        commands.add_parser(
            Command.LANDED, help="open pull requests whose branch has landed"
        )

        configuration = commands.add_parser(
            Command.CONFIGURATION, help="every resolved setting, including the remotes"
        )
        configuration.add_argument(
            CommandOption.FORK,
            type=Repository.parse,
            help="the fork as 'owner/name', when you already know it",
        )
        configuration.add_argument(
            CommandOption.UPSTREAM,
            type=Repository.parse,
            help="the upstream as 'owner/name', when you already know it",
        )

        labels = commands.add_parser(
            Command.LABELS, help="the complete label set a label write must send"
        )
        labels.add_argument(
            CommandOption.CURRENT,
            action="append",
            default=[],
            metavar="LABEL",
            help="a label the pull request carries now; repeat per label",
        )
        labels.add_argument(
            CommandOption.ADD,
            action="append",
            default=[],
            metavar="LABEL",
            help="a label to end up with",
        )
        labels.add_argument(
            CommandOption.REMOVE,
            action="append",
            default=[],
            metavar="LABEL",
            help="a label to end up without",
        )

        link = commands.add_parser(
            Command.PROMOTION_LINK,
            help="the upstream compare-and-create URL for a branch",
        )
        link.add_argument(
            CommandOption.BRANCH, required=True, help="the fork branch to promote"
        )
        link.add_argument(CommandOption.TITLE, required=True, help="title to prefill")
        link.add_argument(CommandOption.BODY, default="", help="description to prefill")

        register = commands.add_parser(
            Command.REGISTER,
            help="put a pull request on top of the GitHub stack below it",
        )
        register.add_argument(
            CommandOption.REPOSITORY,
            required=True,
            type=Repository.parse,
            help="the repository the pull requests are open on, as 'owner/name'",
        )
        register.add_argument(
            CommandOption.PULL_REQUEST,
            required=True,
            type=int,
            help="the pull request to put on top",
        )
        register.add_argument(
            CommandOption.BELOW,
            nargs="*",
            type=int,
            default=[],
            help="the pull requests it builds on, bottom first",
        )

        return parser

    @staticmethod
    def main() -> ExitCode:
        """Dispatch the command-line invocation, ending with the status the refusal carries.

        :return: The process exit code.
        """
        arguments = StackCommandLine.parser().parse_args()
        command = Command(arguments.command)
        try:
            if not command.needs_a_board:
                return StackCommandLine._run_without_a_board(command, arguments)
            return StackCommandLine._run_against_the_board(
                command,
                arguments,
                Stack.load(
                    GitCommandRunner(Path.cwd()).repository_state_path(
                        RepositoryStateLocation.BOARD
                    )
                ),
            )
        except CommandFailure as error:
            logger.error(f"{error}")
            return error.exit_code

    @staticmethod
    def _run_without_a_board(
        command: Command, arguments: argparse.Namespace
    ) -> StackExitCode:
        """Run a command that needs no ``board.json``.

        :param command: The command to run.
        :param arguments: The parsed command line.
        :return: The process exit code.
        """
        if command is Command.LABELS:
            LabelWrite.replacing(
                arguments.current, arguments.add, arguments.remove
            ).print_labels()
            return StackExitCode.SUCCESS
        if command is Command.CONFIGURATION:
            Configuration.load(
                fork_repository=arguments.fork,
                upstream_repository=arguments.upstream,
            ).print_settings()
            return StackExitCode.SUCCESS
        if command is Command.REGISTER:
            StackRegistration(
                GitHubRepository.from_environment(arguments.repository),
                tuple(arguments.below),
                arguments.pull_request,
            ).print_registration()
            return StackExitCode.SUCCESS
        PromotionLink.build(
            Configuration.load(), arguments.branch, arguments.title, arguments.body
        ).print_url()
        return StackExitCode.SUCCESS

    @staticmethod
    def _run_against_the_board(
        command: Command, arguments: argparse.Namespace, stack: Stack
    ) -> StackExitCode:
        """Run a command that reads the derived stack.

        :param command: The command to run.
        :param arguments: The parsed command line.
        :param stack: The derived stack.
        :return: The process exit code.
        """
        report = StackReport(stack)
        reporters = {
            Command.STATUS: report.print_status,
            Command.CHECK: report.print_check,
            Command.NEXT: (
                report.print_next_porcelain
                if arguments.porcelain
                else report.print_next
            ),
            Command.REPARENTS: report.print_reparents,
            Command.LANDED: report.print_landed,
        }
        reporters[command]()
        return StackExitCode.SUCCESS


if __name__ == "__main__":
    raise SystemExit(StackCommandLine.main())
