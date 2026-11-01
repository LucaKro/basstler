"""
The errors the stack commands end with, each carrying the status it exits with.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from basstler.class_property import classproperty
from basstler.exceptions import DataclassException
from basstler.exit_codes import CommandFailure, StackExitCode
from basstler.repository import Repository
from basstler.stack.configuration import StackSetting

if TYPE_CHECKING:
    from basstler.stack.settings import Remote

# %% base


@dataclass
class StackError(CommandFailure, DataclassException, ABC):
    """
    Base for every refusal the stack commands report, each carrying its own exit status.
    """


# %% remotes


@dataclass
class ForkRemoteNotFoundError(StackError, LookupError):
    """
    Raised when no remote points at a repository other than the upstream.
    """

    @classproperty
    def exit_code(cls) -> StackExitCode:
        """:return: The status this kind of error ends a command with."""
        return StackExitCode.REMOTES_UNRESOLVED

    upstream_repository: Repository
    """The upstream every candidate turned out to be."""

    def error_message(self) -> str:
        return f"no remote points at a fork: every remote is {self.upstream_repository}"

    def suggest_correction(self) -> str:
        return (
            f"Add a remote for your fork, or set {StackSetting.FORK_REPOSITORY} in "
            f"stack.toml."
        )


@dataclass
class AmbiguousForkRemoteError(StackError, LookupError):
    """
    Raised when several remotes could each be the fork.
    """

    @classproperty
    def exit_code(cls) -> StackExitCode:
        """:return: The status this kind of error ends a command with."""
        return StackExitCode.REMOTES_UNRESOLVED

    candidates: tuple[Remote, ...]
    """The remotes that are not the upstream, in the order git reported them."""

    def error_message(self) -> str:
        listed = ", ".join(
            f"{remote.name} -> {remote.repository}" for remote in self.candidates
        )
        return f"several remotes could be the fork ({listed})"

    def suggest_correction(self) -> str:
        return f"Set {StackSetting.FORK_REPOSITORY} in stack.toml to say which."


# %% the board


@dataclass
class BoardUnavailable(StackError, RuntimeError):
    """
    Raised when ``board.json`` is missing.
    """

    @classproperty
    def exit_code(cls) -> StackExitCode:
        """:return: The status this kind of error ends a command with."""
        return StackExitCode.BOARD_UNAVAILABLE

    board: Path
    """
    Where the board export was looked for.
    """

    def error_message(self) -> str:
        return f"{self.board.name} not found"

    def suggest_correction(self) -> str:
        return 'Export it with `"${BASSTLER_PYTHON}" -m basstler.maintenance board --write`.'


# %% label writes


@dataclass
class ContradictoryLabelWriteError(StackError, ValueError):
    """
    Raised when one write is asked both to carry a label and to drop it.
    """

    @classproperty
    def exit_code(cls) -> StackExitCode:
        """:return: The status this kind of error ends a command with."""
        return StackExitCode.USAGE

    labels: tuple[str, ...]
    """
    The labels asked for in both directions.
    """

    def error_message(self) -> str:
        return (
            f"cannot both add and remove {', '.join(self.labels)}: "
            f"either outcome would be a guess at what was meant"
        )

    def suggest_correction(self) -> str:
        return "Ask for each label to be either added or removed, not both."


# %% promotion links


@dataclass
class PromotionLinkTooLongError(StackError, ValueError):
    """
    Raised when a link cannot fit even with its whole description dropped.
    """

    @classproperty
    def exit_code(cls) -> StackExitCode:
        """:return: The status this kind of error ends a command with."""
        return StackExitCode.USAGE

    branch: str
    """
    The branch whose link was being built.
    """
    length_without_a_description: int
    """
    How long the URL already is before any description is added.
    """
    url_character_limit: int
    """
    The longest URL the link may be.
    """

    def error_message(self) -> str:
        return (
            f"the compare link for {self.branch} is {self.length_without_a_description} "
            f"characters before its description, over the "
            f"{self.url_character_limit}-character limit"
        )

    def suggest_correction(self) -> str:
        return "Shorten the title."


# %% stacks on GitHub


@dataclass
class LayerTakenError(StackError, ValueError):
    """
    Raised when a pull request would be stacked on one that already has others stacked
    on it.
    """

    @classproperty
    def exit_code(cls) -> StackExitCode:
        """:return: The status this kind of error ends a command with."""
        return StackExitCode.LAYER_TAKEN

    below: int
    """
    The pull request it would be stacked on.
    """
    above: tuple[int, ...]
    """
    The pull requests already stacked on that one, bottom first.
    """

    def error_message(self) -> str:
        stacked = ", ".join(f"#{number}" for number in self.above)
        return (
            f"pull request #{self.below} already has {stacked} stacked on it, and a "
            f"stack is a single line"
        )

    def suggest_correction(self) -> str:
        return (
            "Land one of the two first, or change the plan's depends_on so the items "
            "build on each other in one line."
        )
