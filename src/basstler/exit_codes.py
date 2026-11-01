"""
The exit statuses the package's commands end with, and the errors that carry their own.

Every command's table of statuses extends :class:`ExitCode`, so a status reads the same
way, and is typed the same way, whichever command it comes from.

An error a command reports to its caller declares the status it ends the command with,
as :attr:`CommandFailure.exit_code`. A command's entry point then catches
:class:`CommandFailure` once and returns ``error.exit_code``, instead of each entry point
keeping its own table from error to status - two tables that can disagree about the same
error. The error is the thing that knows what went wrong, so it is the one that says how a
caller is told.

The stacked-pull-request tools - ``basstler.stack`` and ``basstler.maintenance`` - share
:class:`StackExitCode`, so a caller acting on either one's status never has to remember
which produced it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum

from basstler.class_property import classproperty


class ExitCode(IntEnum):
    """
    The base every command's table of exit statuses extends.

    It defines no statuses of its own, since an enum with members cannot be extended:
    each command's table names its own, starting from ``SUCCESS = 0``.
    """

    @property
    def name_for_a_caller(self) -> str:
        """
        What this status means, in words rather than as a number to be looked up.

        A process exit status can only ever be an integer, so this accompanies the
        number rather than replacing it. Derived from the member itself, so a status can
        never end up carrying a name that belongs to a different one.

        :return: The status's name, lowercased, in the form a caller reads or matches
            on.
        """
        return self.name.lower()


class StackExitCode(ExitCode):
    """
    What the stacked-pull-request tools' exit status tells a caller.

    A distinct status per failure lets a caller - a shell script, or a session acting on
    what it gets back - tell "you asked for something that does not exist" from "the
    checkout is not in a state I can read", without parsing stderr.
    """

    SUCCESS = 0
    """
    The command ran and did what it reports.
    """

    USAGE = 2
    """
    No such command, or the wrong arguments; the conventional status for a usage error,
    as ``argparse`` also uses.
    """

    BOARD_UNAVAILABLE = 3
    """
    ``board.json`` is missing or unreadable, so the stack cannot be derived.
    """

    REMOTES_UNRESOLVED = 4
    """
    The fork could not be identified from this checkout's remotes.
    """

    GIT_COMMAND_FAILED = 6
    """
    A git command the run depended on failed; nothing further was attempted.
    """

    NOT_FAST_FORWARD = 7
    """
    The fork's base carries commits the upstream does not.
    """

    CREDENTIAL_UNAVAILABLE = 8
    """
    No GitHub token is set, so the fork cannot be read or written.
    """

    GITHUB_REQUEST_FAILED = 9
    """
    The API refused a call the run depends on; its status and reason are on stderr.
    """

    BRANCH_NEEDS_ATTENTION = 10
    """
    The pass itself ran, but left at least one branch unpublished for somebody to act on
    - a conflict, a withheld branch, or a push the fork rejected.
    """

    UPSTREAM_UNCONFIGURED = 11
    """
    The project names no upstream repository; stderr says where to name it.
    """

    LAYER_TAKEN = 12
    """
    The pull request to build on already has another stacked on it, and a stack is a
    single line; stderr names both.
    """

    @property
    def name_for_a_caller(self) -> str:
        """
        The status's name hyphenated, as the stacked-pull-request skills match on it.

        :return: The status's name, in the form a caller reads or matches on.
        """
        return super().name_for_a_caller.replace("_", "-")


class SetupCheckExitCode(ExitCode):
    """
    What check-setup.sh's exit status tells a caller.
    """

    SUCCESS = 0
    """
    Nothing is left to set up.
    """

    SETUP_INCOMPLETE = 1
    """
    At least one check still needs setting up.
    """


class PersonalNotesSetupExitCode(ExitCode):
    """
    What the personal-notes setup's exit status tells a caller.

    The first two are :class:`SetupCheckExitCode`'s, which a completed run passes on.
    """

    SUCCESS = SetupCheckExitCode.SUCCESS.value
    """
    The run finished and check-setup.sh reports nothing left to set up.
    """

    SETUP_INCOMPLETE = SetupCheckExitCode.SETUP_INCOMPLETE.value
    """
    The run finished and check-setup.sh still reports something to set up.
    """

    USAGE = 2
    """
    The wrong arguments, as ``argparse`` reports them.
    """

    FOREIGN_NOTES_REMOTE = 3
    """
    The notes remote belongs to somebody else, so nothing was written.
    """

    HOOK_SCRIPT_FAILED = 4
    """
    A hook script a step runs failed; its own output says why.
    """


@dataclass
class CommandFailure(Exception, ABC):
    """
    An error a command reports to its caller by an exit status of its own.

    Extended alongside the built-in exception that says what kind of error it is, e.g.
    ``class SomeError(CommandFailure, LookupError)``, so ``except LookupError`` still
    catches it. Each concrete error implements :attr:`exit_code` with its own status.
    """

    @classproperty
    @abstractmethod
    def exit_code(cls) -> ExitCode:
        """
        The status a command ends with when this error stops it.

        A property of the kind of error rather than a field, so no raise site can choose
        another, and abstract, so an error that names no status cannot be raised at all.

        :return: The status.
        """
