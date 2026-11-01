"""
Rebasing a line of branches onto each other, layer by layer, and publishing them
together: what GitHub's ``gh stack`` extension does to a stack of pull requests.
"""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path

from basstler.environment import GitHubCommandLineVariable
from basstler.repository import Repository

# %% what one step came to


class CascadeStepOutcome(StrEnum):
    """
    What became of one step of a cascading rebase.
    """

    COMPLETED = "completed"
    """
    The step did what it was asked.
    """

    CONFLICTED = "conflicted"
    """
    Rebasing a layer stopped on a conflict, which is left in place until it is aborted.
    """

    ALREADY_ADOPTED = "already-adopted"
    """
    A branch to adopt already belongs to a local stack, which is used as it is.
    """

    FAILED = "failed"
    """
    The step failed for a reason other than a conflict.
    """


@dataclass(frozen=True)
class CascadeStep:
    """
    One finished step of a cascading rebase.
    """

    outcome: CascadeStepOutcome
    """
    What became of it.
    """

    detail: str = ""
    """
    What the tool said about a step that did not complete, empty otherwise.
    """


# %% the cascade


@dataclass(frozen=True)
class CascadingRebase(ABC):
    """
    A line of local branches, each rebased onto the one below it and published together.

    Every step runs in one checkout and leaves a conflict in it until
    :meth:`abort_rebase` is called.
    """

    @abstractmethod
    def adopt(self, trunk: str, branches: Sequence[str]) -> CascadeStep:
        """
        Track existing local branches as one line.

        :param trunk: The branch the bottom layer is based on.
        :param branches: The layers, bottom first.
        :return: The finished step.
        """

    @abstractmethod
    def rebase_up_to(self, branch: str) -> CascadeStep:
        """
        Rebase every layer from the bottom of *branch*'s line up to *branch* itself onto
        the one below it, the bottom layer onto the trunk's published tip.

        :param branch: The highest layer to rebase.
        :return: The finished step.
        """

    @abstractmethod
    def abort_rebase(self) -> CascadeStep:
        """
        Give up a conflicted rebase, putting every layer it touched back where it was.

        :return: The finished step.
        """

    @abstractmethod
    def publish(self) -> CascadeStep:
        """
        Push every layer of the line the checkout is on, each only if the remote still
        holds what this checkout last saw of it.

        :return: The finished step.
        """


# %% GitHub's extension


class StackExtensionArgument(StrEnum):
    """
    The executable, subcommands and flags of ``gh stack`` this module runs.
    """

    EXECUTABLE = "gh"
    """
    GitHub's command line, which the extension is a subcommand of.
    """

    STACK = "stack"
    """
    The extension itself.
    """

    INITIALIZE = "init"
    """
    Starts tracking a stack, adopting branches that already exist.
    """

    BASE = "--base"
    """
    Names the trunk the stack's bottom layer is based on.
    """

    REBASE = "rebase"
    """
    Runs a cascading rebase.
    """

    DOWNSTACK = "--downstack"
    """
    Limits :attr:`REBASE` to the layers from the trunk up to the branch it is given.
    """

    ABORT = "--abort"
    """
    Makes :attr:`REBASE` give up a conflicted rebase and restore every branch.
    """

    PUSH = "push"
    """
    Pushes the layers of the current stack with a lease each.
    """

    REMOTE = "--remote"
    """
    The remote to fetch the trunk from and push the layers to.
    """


class StackExtensionExitCode(IntEnum):
    """
    The exit statuses of ``gh stack`` this module tells apart.
    """

    SUCCESS = 0
    """
    The command did what it was asked.
    """

    REBASE_CONFLICT = 3
    """
    A rebase stopped on a conflict.
    """

    INVALID_ARGUMENTS = 5
    """
    The arguments were refused - for branches this module names, because one already
    belongs to a local stack.
    """


@dataclass(frozen=True)
class GitHubStackExtension(CascadingRebase):
    """
    GitHub's ``gh stack`` extension, run in one checkout against the fork.

    Every API call it makes is pinned to :attr:`fork`, since a checkout with an upstream
    remote beside the fork leaves the extension's own guess at the repository open.
    """

    working_directory: Path
    """
    The checkout the branches are rebased in.
    """

    fork: Repository
    """
    The repository the stacks' pull requests are open on.
    """

    remote: str
    """
    The checkout's remote for :attr:`fork`.
    """

    def adopt(self, trunk: str, branches: Sequence[str]) -> CascadeStep:
        finished = self.run(
            StackExtensionArgument.INITIALIZE,
            StackExtensionArgument.BASE,
            trunk,
            *branches,
        )
        if finished.returncode == StackExtensionExitCode.INVALID_ARGUMENTS:
            return CascadeStep(CascadeStepOutcome.ALREADY_ADOPTED, finished.stderr)
        return self.step(finished)

    def rebase_up_to(self, branch: str) -> CascadeStep:
        return self.step(
            self.run(
                StackExtensionArgument.REBASE,
                StackExtensionArgument.DOWNSTACK,
                StackExtensionArgument.REMOTE,
                self.remote,
                branch,
            )
        )

    def abort_rebase(self) -> CascadeStep:
        return self.step(
            self.run(StackExtensionArgument.REBASE, StackExtensionArgument.ABORT)
        )

    def publish(self) -> CascadeStep:
        return self.step(
            self.run(
                StackExtensionArgument.PUSH, StackExtensionArgument.REMOTE, self.remote
            )
        )

    def run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        """
        :param arguments: The ``gh stack`` subcommand and its arguments.
        :return: The finished command, whose failure the caller reads.
        """
        return subprocess.run(
            [
                StackExtensionArgument.EXECUTABLE,
                StackExtensionArgument.STACK,
                *arguments,
            ],
            cwd=self.working_directory,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                GitHubCommandLineVariable.REPOSITORY: self.fork.full_name,
            },
        )

    @staticmethod
    def step(finished: subprocess.CompletedProcess[str]) -> CascadeStep:
        """
        :param finished: A finished ``gh stack`` command.
        :return: What it came to.
        """
        match finished.returncode:
            case StackExtensionExitCode.SUCCESS:
                return CascadeStep(CascadeStepOutcome.COMPLETED)
            case StackExtensionExitCode.REBASE_CONFLICT:
                return CascadeStep(CascadeStepOutcome.CONFLICTED, finished.stderr)
            case _:
                return CascadeStep(
                    CascadeStepOutcome.FAILED,
                    finished.stderr.strip() or finished.stdout.strip(),
                )
