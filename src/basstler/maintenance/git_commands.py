"""
Running git for a pass.

Every command a pass runs goes through :class:`GitCommandRunner`, and every push it
makes is built as a :class:`ProposedPush`, which never overwrites what is published.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from basstler.class_property import classproperty
from basstler.exit_codes import CommandFailure, StackExitCode
from basstler.locations import RepositoryStateLocation
from basstler.maintenance.exceptions import ExternalCallFailed

# %% running git


@dataclass
class GitCommandFailed(CommandFailure, ExternalCallFailed):
    """
    Raised when a git command this module depends on the result of fails.
    """

    @classproperty
    def exit_code(cls) -> StackExitCode:
        """:return: The status this kind of error ends a command with."""
        return StackExitCode.GIT_COMMAND_FAILED

    arguments: tuple[str, ...] = ()
    """
    The git subcommand and its arguments, as invoked.
    """

    @property
    def call(self) -> str:
        """:return: The git command line, as invoked."""
        return f"git {' '.join(self.arguments)}"


@dataclass(frozen=True)
class GitCommandResult:
    """
    One finished git command, whether or not it succeeded.
    """

    arguments: tuple[str, ...]
    """
    The git subcommand and its arguments, as invoked.
    """

    exit_status: int
    """
    The status git exited with.
    """

    output: str
    """
    Git's stripped stdout.
    """

    error_output: str
    """
    Git's stripped stderr.
    """

    @property
    def succeeded(self) -> bool:
        """:return: Whether git exited zero."""
        return self.exit_status == 0

    def raise_if_failed(self) -> GitCommandResult:
        """:return: This result, when the command succeeded.
        :raises GitCommandFailed: When it did not."""
        if not self.succeeded:
            raise GitCommandFailed(
                status=self.exit_status,
                detail=self.error_output,
                arguments=self.arguments,
            )
        return self


# %% what a pass is allowed to publish


@dataclass(frozen=True)
class ProposedPush:
    """
    One publication, made only where it moves the remote branch forward.
    """

    remote: str
    """
    The remote to publish to.
    """

    refspec: str
    """
    What to publish, as ``<source>:<destination>``.
    """


@dataclass(frozen=True)
class GitCommandRunner:
    """
    Runs git in one checkout, reporting failures rather than swallowing them.

    ``stack/checkout.py`` reads git through a helper that returns an empty string when a command
    fails. That is right for derivation, where a missing ref simply means "no answer",
    and wrong here: a push that silently did nothing would be indistinguishable from one
    that worked.
    """

    working_directory: Path
    """
    The checkout every command runs in.
    """

    def attempt(
        self, *arguments: str, environment: Mapping[str, str] | None = None
    ) -> GitCommandResult:
        """
        Run a command whose failure is an expected outcome.

        :param arguments: The git subcommand and its arguments.
        :param environment: Variables to set for this command on top of the process's
            own.
        :return: The finished command.
        """
        completed = subprocess.run(
            ["git", *arguments],
            cwd=self.working_directory,
            capture_output=True,
            text=True,
            env={**os.environ, **(environment or {})},
        )
        return GitCommandResult(
            arguments=arguments,
            exit_status=completed.returncode,
            output=completed.stdout.strip(),
            error_output=completed.stderr.strip(),
        )

    def run(self, *arguments: str, environment: Mapping[str, str] | None = None) -> str:
        """
        Run a command this module depends on the result of.

        :param arguments: The git subcommand and its arguments.
        :param environment: Variables to set for this command on top of the process's
            own.
        :return: Git's stripped stdout.
        :raises GitCommandFailed: If git exits non-zero.
        """
        return (
            self.attempt(*arguments, environment=environment).raise_if_failed().output
        )

    def repository_state_path(self, location: RepositoryStateLocation) -> Path:
        """
        :param location: Scratch state kept per repository.
        :return: Where it lives for this checkout's repository, the same from every
            worktree of it.
        """
        common_directory = self.run(
            "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        return Path(common_directory) / location

    def fetch(self, remote: str, *references: str) -> None:
        """
        Refresh what this checkout knows about a remote.

        :param remote: The remote to fetch from.
        :param references: The branches to fetch, all of them when none is named.
        """
        self.run("fetch", "--quiet", remote, *references)

    def fetch_branch(
        self, location: str, branch: str, tracking_remote: str
    ) -> GitCommandResult:
        """
        Fetch one branch into its tracking ref, leaving ``FETCH_HEAD`` alone.

        :param location: The remote name or URL to fetch from.
        :param branch: The branch to fetch.
        :param tracking_remote: The remote whose tracking refs the branch is kept under.
        :return: The finished fetch, whose failure is an expected outcome.
        """
        return self.attempt(
            "fetch",
            "--quiet",
            "--no-write-fetch-head",
            location,
            f"+refs/heads/{branch}:refs/remotes/{tracking_remote}/{branch}",
        )

    def commit_or_none(self, reference: str) -> str | None:
        """:param reference: A ref that may not exist.
        :return: The commit it names, or ``None`` if it does not exist."""
        resolved = self.attempt("rev-parse", "--verify", "--quiet", reference)
        return resolved.output if resolved.succeeded else None

    def commit_at(self, reference: str) -> str:
        """:param reference: Any reference git can resolve.
        :return: The commit it names."""
        return self.run("rev-parse", reference)

    def checked_out_branch(self) -> str:
        """:return: The branch whose content a push would move."""
        return self.run("branch", "--show-current")

    def unmerged_paths(self) -> tuple[str, ...]:
        """:return: The paths the rebase that just stopped left conflicted."""
        unmerged = self.attempt("diff", "--name-only", "--diff-filter=U")
        return tuple(path for path in unmerged.output.splitlines() if path)

    def push(self, proposed: ProposedPush) -> GitCommandResult:
        """
        Publish a refspec, never forcing it.

        :param proposed: What to publish.
        :return: The finished push, whose failure the caller reports rather than forces.
        """
        return self.attempt("push", "--quiet", proposed.remote, proposed.refspec)

    def contains(self, candidate: str, descendant: str) -> bool:
        """:param candidate: The reference that may be contained.
        :param descendant: The reference that may contain it.
        :return: Whether *candidate* is an ancestor of *descendant*."""
        return self.attempt(
            "merge-base", "--is-ancestor", candidate, descendant
        ).succeeded
