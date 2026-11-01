"""
The fork pull requests a pass reports to, recorded rather than written, and the passes
the maintenance suites run against a scratch fork.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field as dataclasses_field

from basstler.stack.model import (
    PullRequest,
    Stack,
)

from basstler.locations import RepositoryStateLocation
from basstler_domain_model.github.pull_request_fields import (
    PullRequestField,
)
from basstler.maintenance.commands import (
    MaintenanceCommand,
    MaintenancePass,
)
from basstler_domain_model.environment import (
    GitHubCredentialVariable,
)
from basstler_domain_model.git.commands import (
    GitCommandRunner,
)
from basstler_domain_model.github.repository_client import (
    ForkPullRequests,
)

from ..script_runner import PythonModuleRunner

from .fork import (
    PromotionSampleText,
    MAINTENANCE_MODULE,
    make_configuration,
    ForkCheckout,
    a_stack,
)


# %% reporting a branch back to its owner


@dataclass(frozen=True)
class RecordedLabelWrite:
    """
    One label set written to a pull request.
    """

    pull_request_number: int
    """
    The pull request written to.
    """

    labels: tuple[str, ...]
    """
    The complete set it was left carrying.
    """


@dataclass(frozen=True)
class RecordedComment:
    """
    One comment posted on a pull request.
    """

    pull_request_number: int
    """
    The pull request commented on.
    """

    body: str
    """
    What the comment said.
    """


@dataclass(frozen=True)
class RecordedDescription:
    """
    One description written to a pull request.
    """

    pull_request_number: int
    """
    The pull request written to.
    """

    body: str
    """
    The description it was left carrying.
    """


@dataclass(frozen=True)
class RecordingPullRequests(ForkPullRequests):
    """
    Stands in for the fork, recording every write instead of making it.

    The three writes it records were each probed against the live API before this existed
    - a label replace, an issue comment and a body-only description write all succeed on
    the credential a session carries - so what is faked here is the network, not the
    permission.
    """

    states: dict[int, str] = dataclasses_field(default_factory=dict)
    """
    What ``mergeable_state`` to report per pull request number.
    """

    descriptions: dict[int, str] = dataclasses_field(default_factory=dict)
    """
    What description to report per pull request number.
    """

    titles: dict[int, str] = dataclasses_field(default_factory=dict)
    """
    What title to report per pull request number.
    """

    labels: dict[int, list[str]] = dataclasses_field(default_factory=dict)
    """
    What labels to report per pull request number, which is what the branch carries
    *now* rather than what the board snapshot opened the pass with.
    """

    label_writes: list[RecordedLabelWrite] = dataclasses_field(default_factory=list)
    """
    Every label set written, in order.
    """

    comments: list[RecordedComment] = dataclasses_field(default_factory=list)
    """
    Every comment posted, in order.
    """

    description_writes: list[RecordedDescription] = dataclasses_field(
        default_factory=list
    )
    """
    Every description written, in order.
    """

    def open_pull_requests(self) -> list[dict]:
        """
        :return: Every pull request this stand-in has been given state for, in number
            order - the same records reading one of them by number answers with.
        """
        known = {*self.states, *self.descriptions, *self.titles, *self.labels}
        return [self.pull_request(number) for number in sorted(known)]

    def pull_request(self, number: int) -> dict:
        """
        :param number: The pull request to read.
        :return: The fields the executor reads off one pull request, keyed the way the
            executor itself names them.
        """
        return {
            PullRequestField.NUMBER.key: number,
            PullRequestField.MERGEABLE_STATE.key: self.states.get(number, "clean"),
            PullRequestField.BODY.key: self.descriptions.get(number, ""),
            PullRequestField.TITLE.key: self.titles.get(
                number, f"Pull request {number}"
            ),
            PullRequestField.LABELS.key: list(self.labels.get(number, [])),
        }

    def replace_labels(self, number: int, labels: Sequence[str]) -> None:
        """
        :param number: The pull request to write.
        :param labels: The complete label set to write.
        """
        self.label_writes.append(RecordedLabelWrite(number, tuple(labels)))

    def add_comment(self, number: int, body: str) -> str:
        """
        :param number: The pull request to comment on.
        :param body: The comment.
        :return: A stand-in for the comment's URL.
        """
        self.comments.append(RecordedComment(number, body))
        return f"{PromotionSampleText.COMMENT_LINK_PREFIX}{len(self.comments)}"

    def set_description(self, number: int, body: str) -> None:
        """
        :param number: The pull request to write.
        :param body: The new description.
        """
        self.description_writes.append(RecordedDescription(number, body))
        self.descriptions[number] = body


@dataclass(frozen=True)
class PullRequestsWatchingTheCaller(RecordingPullRequests):
    """
    Reads the invoking checkout at the one moment a pass is provably mid-restack.

    A write to the fork only happens from inside a branch's restack, so it is the hook
    for asserting what the invoking checkout looks like while the restack has its
    branch - which is otherwise over before a test can look.
    """

    caller: GitCommandRunner = dataclasses_field(kw_only=True)
    """
    The invoking checkout to read.
    """

    branches_held_while_restacking: list[str] = dataclasses_field(default_factory=list)
    """
    What it had checked out at each write, in order, empty if no write ever came.
    """

    def replace_labels(self, number: int, labels: Sequence[str]) -> None:
        """
        :param number: The pull request to write.
        :param labels: The complete label set to write.
        """
        self.branches_held_while_restacking.append(self.caller.checked_out_branch())
        super().replace_labels(number, labels)


@dataclass
class ReportingRefused(RuntimeError):
    """
    Raised instead of writing to the fork, to abandon a pass where it stands.
    """

    def __str__(self) -> str:
        """
        :return: Why the write did not happen.
        """
        return "the fork refused the write"


@dataclass(frozen=True)
class PullRequestsRefusingToReport(RecordingPullRequests):
    """
    A fork whose label writes raise, so a pass dies part-way through a branch.
    """

    def replace_labels(self, number: int, labels: Sequence[str]) -> None:
        """
        :param number: The pull request that would have been written.
        :param labels: The label set that would have been written.
        :raises ReportingRefused: Always.
        """
        raise ReportingRefused


# %% the command line a caller acts on the exit status of


@dataclass(frozen=True)
class AlreadyResolvedPass(MaintenancePass):
    """
    A pass whose stack and fork are handed to it, so a whole run can be performed
    without a network or a board file to read.
    """

    resolved_stack: Stack = None  # type: ignore[assignment]
    """
    The stack the commands run against.
    """

    recorded_fork: RecordingPullRequests = dataclasses_field(
        default_factory=RecordingPullRequests
    )
    """
    The stand-in that records writes instead of making them.
    """

    @classmethod
    def over(
        cls, checkout: ForkCheckout, pull_requests: list[PullRequest]
    ) -> AlreadyResolvedPass:
        """
        :param checkout: The checkout to execute in.
        :param pull_requests: The board entries the stack is derived from.
        :return: The pass, with its stack already derived.
        """
        return cls(
            configuration=make_configuration(),
            git=checkout.git,
            board=checkout.project_root / RepositoryStateLocation.BOARD.name,
            resolved_stack=a_stack(checkout, pull_requests),
        )

    def stack(self) -> Stack:
        """
        :return: The stack derived from the scratch checkout.
        """
        return self.resolved_stack

    def fresh_stack(self) -> Stack:
        """
        :return: The stack derived from the scratch checkout, which stands for a board
            exported just now.
        """
        return self.resolved_stack

    def fork(self) -> RecordingPullRequests:
        """
        :return: The stand-in that records writes instead of making them.
        """
        return self.recorded_fork


def maintenance_runner(checkout: ForkCheckout) -> PythonModuleRunner:
    """
    :param checkout: The fork checkout to run in.
    :return: A runner for the executor, without the credentials this environment carries.
    """
    return PythonModuleRunner(
        project_root=checkout.project_root,
        removed_variable_prefixes=tuple(GitHubCredentialVariable),
        module_name=MAINTENANCE_MODULE,
    )


def run_maintenance(
    checkout: ForkCheckout, command: MaintenanceCommand, *flags: str
) -> subprocess.CompletedProcess[str]:
    """
    Invoke the executor as a caller does, so its exit status is exercised.

    The credential is stripped from the environment for every one of these, so no
    assertion about an exit status can come out differently on a machine that happens to
    have a token exported. A run that needed one is exactly the case worth asserting
    about, and it cannot be asserted at all if the ambient environment answers it.

    :param checkout: The checkout to run in.
    :param command: The command to invoke.
    :param flags: That command's own flags.
    :return: The finished subprocess.
    """
    checkout.name_the_upstream()
    return maintenance_runner(checkout).run(command.invoked_as, *flags)
