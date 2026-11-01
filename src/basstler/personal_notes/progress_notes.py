"""
Deletes the progress notes of branches whose pull requests have all merged or closed.
"""

from __future__ import annotations

import argparse
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from basstler_domain_model.github.vocabulary import (
    PullRequestState,
)
from basstler_domain_model.environment import (
    GitHubCredentialVariable,
)
from basstler_domain_model.exceptions import (
    DataclassException,
)
from basstler_domain_model.github.pull_request_fields import (
    PullRequestField,
    PullRequestPayload,
)
from basstler_domain_model.github.pull_request_fields import PullRequestHeadKey
from basstler_domain_model.git.commands import (
    GitCommandRunner,
)
from basstler_domain_model.github.clients import GitHubApi
from basstler_domain_model.github.repository_client import (
    GitHubRepository,
)
from basstler_domain_model.repository import (
    Repository,
)
from basstler.standard_streams import StandardStreamHandler

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command prints through.
"""


class ProgressNotesFile(StrEnum):
    """
    How a branch's progress notes are named within their directory.
    """

    SUFFIX = ".md"
    """
    The extension every progress-notes file carries.
    """


@dataclass(frozen=True)
class BranchPullRequests:
    """
    Which of a repository's own branches have an open pull request, and which have only
    finished ones.
    """

    open_branches: frozenset[str]
    """
    Branches with at least one open pull request.
    """

    finished_branches: frozenset[str]
    """
    Branches every pull request of which has merged or closed.
    """

    @classmethod
    def from_pull_requests(
        cls, pull_requests: list[PullRequestPayload], repository: Repository
    ) -> BranchPullRequests:
        """
        :param pull_requests: Every pull request of the repository, in any state.
        :param repository: The repository whose own branches count; a pull request from
            a fork's branch of the same name says nothing about this one.
        :return: The branches, grouped by whether any of their pull requests is open.
        """
        open_branches: set[str] = set()
        branches_with_pull_requests: set[str] = set()
        for pull_request in pull_requests:
            head = pull_request[PullRequestField.HEAD.key]
            head_repository = head[PullRequestHeadKey.HEAD_REPOSITORY]
            if (
                head_repository is None
                or head_repository[PullRequestHeadKey.FULL_NAME] != repository.full_name
            ):
                continue
            branch = head[PullRequestHeadKey.REFERENCE]
            branches_with_pull_requests.add(branch)
            if pull_request[PullRequestField.STATE.key] == PullRequestState.OPEN:
                open_branches.add(branch)
        return cls(
            open_branches=frozenset(open_branches),
            finished_branches=frozenset(branches_with_pull_requests - open_branches),
        )


@dataclass(frozen=True)
class ProgressNotesDirectory:
    """
    The directory holding one progress-notes file per branch, named after the branch.
    """

    path: Path
    """
    The directory itself.
    """

    def branches(self) -> list[str]:
        """
        :return: The branch every progress-notes file in the directory belongs to.
        """
        return sorted(
            str(file.relative_to(self.path))[: -len(ProgressNotesFile.SUFFIX)]
            for file in self.path.rglob(f"*{ProgressNotesFile.SUFFIX}")
        )

    def remove_finished(
        self,
        pull_requests: BranchPullRequests,
        remote_branches: frozenset[str],
        kept_branch: str,
    ) -> list[str]:
        """
        Delete the progress notes no session will read again: those of a branch that is
        gone from the remote, or whose pull requests have all merged or closed.

        :param pull_requests: The repository's branches, grouped by pull request state.
        :param remote_branches: Every branch the remote still has.
        :param kept_branch: A branch whose notes stay whatever its state, because they are
            being saved.
        :raises EmptyRemoteBranchListError: If *remote_branches* is empty, which would
            read every branch as gone.
        :return: The branches whose notes were deleted.
        """
        if not remote_branches:
            raise EmptyRemoteBranchListError(directory=self.path)
        removed = [
            branch
            for branch in self.branches()
            if branch != kept_branch
            and (
                branch not in remote_branches
                or branch in pull_requests.finished_branches
            )
        ]
        for branch in removed:
            (self.path / f"{branch}{ProgressNotesFile.SUFFIX}").unlink()
        return removed


@dataclass
class EmptyRemoteBranchListError(DataclassException, ValueError):
    """
    Raised when pruning is asked to judge branches against a remote that lists none.
    """

    directory: Path
    """
    The progress-notes directory that was to be pruned.
    """

    def error_message(self) -> str:
        return (
            f"no remote branches were listed, so every note in {self.directory} would "
            "read as belonging to a deleted branch; nothing was pruned"
        )

    def suggest_correction(self) -> str:
        return ""


# %% pruning against the remote


@dataclass
class PruningSkipped(DataclassException, ABC):
    """
    Raised when a prune cannot judge any branch, so it removes nothing.
    """

    def error_message(self) -> str:
        return (
            "Finished branches' progress notes were not pruned and stay on the "
            f"personal-notes branch: {self.reason()}."
        )

    @abstractmethod
    def reason(self) -> str:
        """
        :return: Why no branch could be judged.
        """


@dataclass
class NoGitHubCredentialError(PruningSkipped):
    """
    Raised when no GitHub token is set, so the branches' pull requests cannot be listed.
    """

    def reason(self) -> str:
        return (
            f"no {' or '.join(GitHubCredentialVariable)} is set, so the branches' pull "
            "requests cannot be listed"
        )

    def suggest_correction(self) -> str:
        return (
            f"Set {' or '.join(GitHubCredentialVariable)} to a token that can read this "
            "repository's pull requests, then save again."
        )


@dataclass
class UnreadableRemoteError(PruningSkipped):
    """
    Raised when the remote's URL or branches cannot be read, so which branches still
    exist is unknown.
    """

    remote: str
    """
    The remote that could not be read.
    """

    def reason(self) -> str:
        return (
            f"the URL or branches of remote {self.remote!r} could not be read, so which "
            "branches still exist is unknown"
        )

    def suggest_correction(self) -> str:
        return (
            f"Make sure remote {self.remote!r} names a GitHub repository and can be "
            "reached, then save again."
        )


@dataclass(frozen=True)
class ProgressNotesPruning:
    """
    One prune of a progress-notes directory against a checkout's remote.
    """

    directory: ProgressNotesDirectory
    """
    The notes to prune.
    """

    git: GitCommandRunner
    """
    The checkout whose remote the branches live on.
    """

    remote: str
    """
    The remote the branches and pull requests belong to.
    """

    kept_branch: str
    """
    The branch whose notes are being saved.
    """

    def run(self) -> list[str]:
        """
        :raises NoGitHubCredentialError: If no GitHub token is set.
        :raises UnreadableRemoteError: If the remote's URL or branches cannot be read.
        :return: The branches whose notes were deleted.
        """
        token = GitHubCredentialVariable.first_token()
        if token is None:
            raise NoGitHubCredentialError()
        url = self.git.attempt("remote", "get-url", self.remote)
        heads = self.git.attempt("ls-remote", "--heads", self.remote)
        if not (
            url.succeeded
            and Repository.names_a_repository(url.output)
            and heads.succeeded
            and heads.output
        ):
            raise UnreadableRemoteError(remote=self.remote)
        repository = Repository.from_remote_url(url.output)
        pull_requests = BranchPullRequests.from_pull_requests(
            GitHubRepository(repository, GitHubApi(token)).all_pull_requests(),
            repository,
        )
        remote_branches = frozenset(
            line.split("refs/heads/", 1)[1] for line in heads.output.splitlines()
        )
        return self.directory.remove_finished(
            pull_requests, remote_branches, self.kept_branch
        )


# %% command line


class ProgressNotesCommand(StrEnum):
    """
    The subcommands this module's command line runs.
    """

    PRUNE = "prune"
    """
    Delete the progress notes of finished pull requests.
    """


class ProgressNotesOption(StrEnum):
    """
    The options this module's command line takes.
    """

    DIRECTORY = "--directory"
    """
    The progress-notes directory to prune.
    """

    REMOTE = "--remote"
    """
    The remote the branches and pull requests belong to.
    """

    KEEP = "--keep"
    """
    The branch whose notes are being saved, and so are never removed.
    """


class ProgressNotesArgument(StrEnum):
    """
    The names this module's command line parses its subcommand and arguments under.
    """

    SUBCOMMAND = "subcommand"
    """
    The subcommand to run, a :class:`ProgressNotesCommand`.
    """

    DIRECTORY = "directory"
    """
    The value of :attr:`ProgressNotesOption.DIRECTORY`.
    """

    REMOTE = "remote"
    """
    The value of :attr:`ProgressNotesOption.REMOTE`.
    """

    KEEP = "keep"
    """
    The value of :attr:`ProgressNotesOption.KEEP`.
    """


def main() -> int:
    """
    Prune a progress-notes directory and report which branches' notes were removed,
    or why none were judged.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subcommands = parser.add_subparsers(
        dest=ProgressNotesArgument.SUBCOMMAND, required=True
    )
    prune = subcommands.add_parser(
        ProgressNotesCommand.PRUNE,
        help="Delete the progress notes of finished pull requests",
    )
    prune.add_argument(
        ProgressNotesOption.DIRECTORY,
        dest=ProgressNotesArgument.DIRECTORY,
        required=True,
        type=Path,
    )
    prune.add_argument(
        ProgressNotesOption.REMOTE, dest=ProgressNotesArgument.REMOTE, required=True
    )
    prune.add_argument(
        ProgressNotesOption.KEEP, dest=ProgressNotesArgument.KEEP, required=True
    )
    arguments = vars(parser.parse_args())

    try:
        removed = ProgressNotesPruning(
            directory=ProgressNotesDirectory(
                arguments[ProgressNotesArgument.DIRECTORY]
            ),
            git=GitCommandRunner(Path.cwd()),
            remote=arguments[ProgressNotesArgument.REMOTE],
            kept_branch=arguments[ProgressNotesArgument.KEEP],
        ).run()
    except PruningSkipped as skipped:
        logger.warning(str(skipped))
        return 0
    if removed:
        logger.info(
            "Removed the progress notes of finished branches:\n" + "\n".join(removed)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
