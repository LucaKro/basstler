"""
The scratch fork checkout and the boards the maintenance suites execute a pass against.

Bare repositories stand in for the fork and the upstream, so nothing touches the
network.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum, IntEnum, StrEnum
from collections.abc import Iterable, Mapping
from dataclasses import field as dataclasses_field
from pathlib import Path

from basstler.repository import Repository
from basstler.stack.model import (
    PullRequest,
    Stack,
)
from basstler.stack.settings import Configuration
from basstler.upstream_reviews.vocabulary import RollupState

import basstler.maintenance.__main__
from basstler.locations import ProjectLocation
from basstler.maintenance.board import (
    BoardExport,
    CommitChecks,
    PullRequestField,
    PullRequestHeadKey,
    PullRequestRecord,
    UpstreamPullRequests,
)
from basstler.maintenance.git_commands import GitCommandRunner

from ..scratch_repository import initialize_bare_repository
from ..constants.paths import DatasetLocation
from ..constants.stack import RemoteName, StackBranch, StackLabel

# %% shared maintenance inputs


class StackPullRequestNumber(IntEnum):
    """
    The numbers of the fork pull requests the two-branch stack under test is made of.

    A number is written into a board entry, keyed into a recorded write and read back in
    an assertion, so it is named once rather than spelled at each of those places.
    """

    PARENT = 40
    """
    The pull request of :attr:`StackBranch.PARENT`.
    """

    CHILD = 41
    """
    The pull request of :attr:`StackBranch.CHILD`, based on the parent's.
    """

    ON_ANOTHER_FORK = 42
    """
    The pull request of :attr:`StackBranch.ON_ANOTHER_FORK`, whose head is not on the
    fork.
    """

    UPSTREAM = 140
    """
    The upstream pull request opened from :attr:`StackBranch.PARENT`.
    """


class PullRequestHeadRepository(Enum):
    """
    The repositories a board entry's head branch can live in.
    """

    FORK = Repository("a-fork-owner", "a-fork")
    """
    The fork the pass maintains, which a scratch checkout's fork remote points at.
    """

    ANOTHER_CONTRIBUTORS_FORK = Repository("another-contributor", "a-fork")
    """
    Another contributor's fork, which the pass cannot push to.
    """


class PromotionSampleText(StrEnum):
    """
    The text a promotion test puts into a fork pull request and finds again in what the
    pass writes back.
    """

    TITLE = "A parent branch"
    """
    The parent pull request's title.
    """

    SUMMARY = "What this branch does."
    """
    The first paragraph of the parent pull request's description.
    """

    DETAIL = "More detail."
    """
    The paragraph after it.
    """

    PROSE = "Prose."
    """
    A description's text before its Promote section.
    """

    FIRST_LINK = "https://example.invalid/first"
    """
    The create-link a first promotion writes.
    """

    SECOND_LINK = "https://example.invalid/second"
    """
    The create-link a second promotion writes in its place.
    """

    COMMENT_LINK_PREFIX = "https://example.invalid/comment/"
    """
    What a recorded comment's stand-in URL starts with, before the comment's position.
    """


MAINTENANCE_MODULE = basstler.maintenance.__main__.__name__
"""
The executor under test, invoked as a subprocess wherever an exit status is the
assertion.

Named off the module rather than spelled out, and run with ``-m`` rather than by path: a
module run by path puts its own directory on ``sys.path`` in place of the project root,
so its absolute imports of its siblings would not resolve.
"""


UPSTREAM_BASE = "main"
"""
The branch every stack in these tests ultimately targets.
"""


class FixtureFile(StrEnum):
    """
    The files a test writes into the checkout to put the branches in the state it is
    about, named once because several tests write the same one from both sides.
    """

    CONTESTED = "a-contested-file"
    """
    Written on a branch and on its parent, with different content, so integrating them
    conflicts.
    """

    ON_THE_BASE = "a-base-file"
    """
    Written on the base branch, so every branch below it has a moved parent to take.
    """

    PUSHED_BY_SOMEBODY_ELSE = "a-file-somebody-else-pushed"
    """
    Written by a commit the pass did not make, standing for a branch that moved under it.
    """


A_SESSION_LINK = "https://claude.ai/code/session_01ABCdef"
"""
The session a pull request's description names, written into one and read back out of
another wherever a test follows that link through the pass.
"""


def make_configuration() -> Configuration:
    """
    :return: The configuration a scratch fork checkout resolves to.
    """
    return Configuration(
        in_review_label=StackLabel.IN_REVIEW,
        needs_resolution_label=StackLabel.NEEDS_RESOLUTION,
        fork_repository=PullRequestHeadRepository.FORK.value,
        fork_remote=RemoteName.ORIGIN,
        upstream_repository=Repository("an-upstream-owner", "a-project"),
        upstream_remote=RemoteName.UPSTREAM,
        upstream_base=UPSTREAM_BASE,
        upstream_location=RemoteName.UPSTREAM,
    )


# %% a real fork checkout to execute against


@dataclass
class ForkCheckout:
    """
    A work clone plus the bare repositories standing in for its fork and its upstream.

    Bare repositories live at paths ending ``<owner>/<name>.git`` and are addressed as
    ``file://`` URLs, because a remote is matched by the repository its URL names and a
    plain local path deliberately names none.
    """

    project_root: Path
    """
    The clone the executor runs in.
    """

    fork_path: Path
    """
    The bare repository the fork remote points at.
    """

    upstream_path: Path
    """
    The bare repository the upstream remote points at.
    """

    @classmethod
    def create(cls, parent_directory: Path) -> ForkCheckout:
        """
        Build a checkout with both remotes wired up and ``main`` published to each.

        :param parent_directory: Where to put the clone and the bare repositories.
        :return: The new checkout.
        """
        project_root = parent_directory / "project"
        project_root.mkdir(parents=True)
        checkout = cls(
            project_root,
            cls._bare_repository(parent_directory / "a-fork-owner" / "a-fork.git"),
            cls._bare_repository(
                parent_directory / "an-upstream-owner" / "a-project.git"
            ),
        )
        checkout.run_git("init", "--quiet")
        checkout.run_git("symbolic-ref", "HEAD", f"refs/heads/{UPSTREAM_BASE}")
        checkout.run_git("config", "user.name", "Scratch Fork")
        checkout.run_git("config", "user.email", "scratch-fork@example.com")
        checkout.run_git(
            "remote", "add", RemoteName.ORIGIN, checkout.fork_path.as_uri()
        )
        checkout.run_git(
            "remote", "add", RemoteName.UPSTREAM, checkout.upstream_path.as_uri()
        )
        checkout.commit("a-file", "the first line\n")
        checkout.run_git("push", "--quiet", RemoteName.ORIGIN, UPSTREAM_BASE)
        checkout.run_git("push", "--quiet", RemoteName.UPSTREAM, UPSTREAM_BASE)
        checkout.run_git("fetch", "--quiet", RemoteName.ORIGIN)
        checkout.run_git("fetch", "--quiet", RemoteName.UPSTREAM)
        return checkout

    def name_the_upstream(self) -> None:
        """
        Write the project's stack settings naming its upstream, untracked, as a real fork
        commits them.

        Untracked rather than committed because the branches these tests build stand for
        branches cut before the settings landed, and checking one out must not delete them.
        """
        destination = self.project_root / ProjectLocation.PROJECT_STACK_CONFIGURATION
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(DatasetLocation.SCRATCH_FORK_STACK_CONFIGURATION, destination)

    @staticmethod
    def _bare_repository(path: Path) -> Path:
        """
        :param path: Where to create the bare repository, parents included.
        :return: The same path.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        return initialize_bare_repository(path)

    def run_git(self, *arguments: str) -> str:
        """
        Run git in the clone, failing the test if it reports an error.

        :param arguments: The arguments to pass to git.
        :return: The command's stripped stdout.
        """
        result = subprocess.run(
            ["git", *arguments],
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def commit(self, name: str, content: str) -> str:
        """
        Write a file and commit it on the checked-out branch.

        :param name: The file to write.
        :param content: What to write into it.
        :return: The new commit's hash.
        """
        (self.project_root / name).write_text(content)
        self.run_git("add", name)
        self.run_git("commit", "--quiet", "-m", f"write {name}")
        return self.run_git("rev-parse", "HEAD")

    def branch_from(self, name: str, start_point: str) -> str:
        """
        Create a branch with a commit of its own, and publish it to the fork.

        The commit is what makes the branch a stack node rather than another name for
        its start point: a branch containing nothing of its own is an ancestor of the
        upstream base, which the derived stack reads - correctly - as already landed.

        :param name: The branch to create.
        :param start_point: What to start it from.
        :return: The branch's published commit hash.
        """
        self.run_git("checkout", "--quiet", "-B", name, start_point)
        commit = self.commit(self.own_file(name), f"the work on {name}\n")
        self.run_git("push", "--quiet", RemoteName.ORIGIN, f"{name}:{name}")
        self.run_git("fetch", "--quiet", RemoteName.ORIGIN)
        return commit

    @staticmethod
    def own_file(branch: str) -> str:
        """
        :param branch: The branch whose own file is wanted.
        :return: The file :meth:`branch_from` writes to give that branch a commit of its
            own, which is also what a test rewrites to move it. It sits at the top of
            the checkout whatever slashes the branch name has.
        """
        return f"{branch.replace('/', '-')}-file"

    def commit_on(self, branch: str, name: str, content: str) -> str:
        """
        Add a commit to a branch and publish it to the fork.

        :param branch: The branch to commit on.
        :param name: The file to write.
        :param content: What to write into it.
        :return: The branch's new published commit hash.
        """
        self.run_git("checkout", "--quiet", branch)
        commit = self.commit(name, content)
        self.run_git("push", "--quiet", RemoteName.ORIGIN, f"{branch}:{branch}")
        self.run_git("fetch", "--quiet", RemoteName.ORIGIN)
        return commit

    def published_commit(self, remote: str, branch: str) -> str:
        """
        :param remote: The remote to read from.
        :param branch: The branch to read.
        :return: The commit that branch points at on that remote.
        """
        return self.run_git("rev-parse", f"{remote}/{branch}")

    def published_file(self, remote: str, branch: str, name: str) -> str:
        """
        :param remote: The remote to read from.
        :param branch: The branch to read.
        :param name: The file to read out of it.
        :return: That file's contents on that branch.
        """
        return self.run_git("show", f"{remote}/{branch}:{name}") + "\n"

    def commit_on_the_fork(self, branch: str) -> str:
        """
        Read a branch from the fork itself rather than from this clone's view of it.

        :param branch: The branch to read.
        :return: The commit the fork has that branch pointing at.
        """
        return self.run_git(
            "ls-remote", RemoteName.ORIGIN, f"refs/heads/{branch}"
        ).split()[0]

    @property
    def git(self) -> GitCommandRunner:
        """
        :return: The runner the executor drives this checkout through.
        """
        return GitCommandRunner(working_directory=self.project_root)


def a_stack(checkout: ForkCheckout, pull_requests: list[PullRequest]):
    """
    Build the derived stack the executor consumes, with landedness read from real git.

    :param checkout: The checkout to answer ancestry questions from.
    :param pull_requests: The board entries.
    :return: The derived stack.
    """
    configuration = make_configuration()
    upstream = f"{configuration.upstream_remote}/{configuration.upstream_base}"

    def is_merged(name: str) -> bool:
        return checkout.git.contains(configuration.reference_on_fork(name), upstream)

    return Stack.build(configuration, pull_requests, is_merged)


# %% the board export


class HeadCommit(StrEnum):
    """
    The commits a fetched pull request's head can point at.
    """

    CHECKED = "c0ffee0000000000000000000000000000000000"
    """
    A head commit the checks under test reported on.
    """

    UNCHECKED = "dec0de0000000000000000000000000000000000"
    """
    A head commit nothing has reported on.
    """


@dataclass(frozen=True)
class ChecksReportedOn(CommitChecks):
    """
    Answers each commit's check verdict from a fixed table rather than from GitHub.
    """

    state_by_commit: Mapping[str, RollupState] = dataclasses_field(default_factory=dict)
    """
    The verdict over each commit's checks; a commit absent here has no checks.
    """

    def check_state(self, commit: str) -> RollupState:
        return self.state_by_commit.get(commit, RollupState.NOT_REPORTED)


def an_export(
    records: Iterable[PullRequestRecord],
    upstream_records: Iterable[PullRequestRecord] = (),
    checks: CommitChecks | None = None,
) -> BoardExport:
    """
    :param records: The fork's open pull requests, as the REST API returns them.
    :param upstream_records: The upstream's open pull requests.
    :param checks: The verdict over each head commit's checks, ``None`` for no checks
        on any commit.
    :return: The board the export builds from them.
    """
    return BoardExport.from_api_records(
        records,
        UpstreamPullRequests.from_api_records(
            upstream_records, PullRequestHeadRepository.FORK.value
        ),
        checks or ChecksReportedOn(),
    )


def an_api_record(
    number: int = 7,
    head: str = "a-branch",
    base: str = UPSTREAM_BASE,
    draft: bool = False,
    labels: list[str] | None = None,
    body: str = "",
    head_repository: Repository | None = PullRequestHeadRepository.FORK.value,
    title: str | None = None,
    head_commit: str = HeadCommit.UNCHECKED,
) -> dict:
    """
    :param number: The pull request number.
    :param head: The head branch reference.
    :param base: The base branch reference.
    :param draft: Whether the pull request is a draft.
    :param labels: The label names it carries.
    :param body: The description to read a session link out of.
    :param head_repository: The repository the head lives in, ``None`` once deleted.
    :param title: The pull request's title.
    :param head_commit: The commit the head points at.
    :return: One pull request in the shape the REST API returns it.
    """
    return {
        PullRequestField.NUMBER.key: number,
        PullRequestField.HEAD.key: {
            PullRequestHeadKey.REFERENCE: head,
            PullRequestHeadKey.HEAD_REPOSITORY: (
                None
                if head_repository is None
                else {PullRequestHeadKey.FULL_NAME: head_repository.full_name}
            ),
            PullRequestHeadKey.COMMIT: head_commit,
        },
        PullRequestField.BASE.key: {"ref": base},
        PullRequestField.DRAFT.key: draft,
        PullRequestField.LABELS.key: [{"name": name} for name in labels or []],
        PullRequestField.BODY.key: body,
        PullRequestField.TITLE.key: title,
    }


# %% restack


def a_parent_and_child(fork_checkout: ForkCheckout) -> None:
    """
    Publish a two-branch stack: the parent on the base, the child on the parent.

    :param fork_checkout: The checkout to build the branches in.
    """
    fork_checkout.branch_from(StackBranch.PARENT, UPSTREAM_BASE)
    fork_checkout.branch_from(StackBranch.CHILD, StackBranch.PARENT)


def a_board_entry(
    number: int,
    head: str,
    base: str,
    labels: list[str] | None = None,
    title: str | None = None,
    session: str | None = None,
    description: str | None = None,
    upstream_pull_request: int | None = None,
    check_state: RollupState = RollupState.NOT_REPORTED,
) -> PullRequest:
    """
    :param number: The pull request number.
    :param head: The head branch, on the fork.
    :param base: The base branch.
    :param labels: The labels it carries.
    :param title: Its title.
    :param session: The session its description names.
    :param description: Its description, without the sections a pass writes.
    :param upstream_pull_request: The open upstream pull request opened from it.
    :param check_state: The verdict over its head's checks.
    :return: One ready board entry whose head is on the fork.
    """
    return PullRequest(
        number=number,
        head=head,
        base=base,
        draft=False,
        head_repository=PullRequestHeadRepository.FORK.value.full_name,
        labels=labels or [],
        session=session,
        title=title,
        description=description,
        upstream_pull_request=upstream_pull_request,
        check_state=check_state,
    )


def the_board(labels: list[str] | None = None) -> list[PullRequest]:
    """
    :param labels: The labels the child's pull request carries.
    :return: The two-branch board matching :func:`a_parent_and_child`.
    """
    return [
        PullRequest(
            number=StackPullRequestNumber.PARENT,
            head=StackBranch.PARENT,
            base=UPSTREAM_BASE,
            draft=False,
            head_repository=PullRequestHeadRepository.FORK.value.full_name,
        ),
        PullRequest(
            number=StackPullRequestNumber.CHILD,
            head=StackBranch.CHILD,
            base=StackBranch.PARENT,
            draft=False,
            labels=labels or [],
            head_repository=PullRequestHeadRepository.FORK.value.full_name,
        ),
    ]
