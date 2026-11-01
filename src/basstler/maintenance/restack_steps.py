"""
What a restack does to one branch, one step at a time.

A step either concludes the branch - returning the outcome its owner acts on - or lets
the next one run. The order they run in is the procedure itself, and lives with the pass
that runs them rather than here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from basstler.maintenance.constants import (
    CommitTrailer,
    MaintenanceText,
)
from basstler_domain_model.github.vocabulary import (
    MergeableState,
)
from basstler_domain_model.github.pull_request_fields import (
    PullRequestField,
)
from basstler_domain_model.git.commands import (
    GitCommandResult,
    GitCommandRunner,
)
from basstler_domain_model.github.repository_client import (
    ForkPullRequests,
)
from basstler.stack.commit_move import (
    CommitMoveAction,
    CommitMoveChecks,
    ProposedCommitMove,
    RefusalReason,
)
from basstler_domain_model.github.labels import (
    LabelWrite,
)
from basstler.stack.model import (
    Branch,
    Stack,
)
from basstler_domain_model.git.commands import (
    IntegrationStrategy,
)
from basstler.stack.settings import Configuration

# %% what became of one branch


class RestackOutcome(StrEnum):
    """
    What became of one branch during a restack.
    """

    PUSHED = "pushed"
    """
    Its parent was integrated and the result published.
    """

    UP_TO_DATE = "up-to-date"
    """
    Its parent's tip was already contained in it.
    """

    CONFLICT = "conflict"
    """
    Its parent could not be integrated cleanly; nothing was published.
    """

    INTEGRATION_FAILED = "integration-failed"
    """
    Integrating its parent failed without conflicting on anything, so the branch is not
    the thing to fix and its owner was not told; nothing was published.
    """

    REFUSED = "refused"
    """
    Move check refused the push; nothing was published.
    """

    PUSH_REJECTED = "push-rejected"
    """
    The fork rejected the push, so the branch moved under this pass; nothing was
    published, and nothing was forced over whatever moved it.
    """

    WITHHELD = "withheld"
    """
    It is still conflicted against its base from a previous pass, so it was left
    untouched rather than re-reported.
    """

    BLOCKED_BY_ANCESTOR = "blocked-by-ancestor"
    """
    A branch below it was left unpublished this pass, so its parent's tip is not what
    the pass meant it to hold; it was not attempted, and nothing was published.
    """

    @property
    def blocks_branches_above(self) -> bool:
        """
        :return: Whether the branch was left without its parent's tip because of
            something about the branch itself, so the branches stacked on it cannot take
            what the pass meant them to. An :attr:`INTEGRATION_FAILED` is the pass's own
            environment, which the branches above report for themselves.
        """
        return self in {
            RestackOutcome.CONFLICT,
            RestackOutcome.REFUSED,
            RestackOutcome.PUSH_REJECTED,
            RestackOutcome.WITHHELD,
            RestackOutcome.BLOCKED_BY_ANCESTOR,
        }


@dataclass(frozen=True)
class BranchOutcome:
    """
    What became of one branch, in terms its owner can act on.
    """

    branch: str
    """
    The branch this is about.
    """

    parent: str
    """
    The branch whose tip was to be integrated into it.
    """

    strategy: IntegrationStrategy
    """
    How the parent was to be integrated.
    """

    outcome: RestackOutcome
    """
    What became of it.
    """

    conflicting_paths: tuple[str, ...] = ()
    """
    The paths that conflicted, empty unless the outcome is a conflict.
    """

    refusals: tuple[RefusalReason, ...] = ()
    """
    Why the push was refused, empty unless the outcome is a refusal.
    """

    pushed_commit: str | None = None
    """
    The commit published, absent unless the outcome is a push.
    """

    explanation: str | None = None
    """
    Why this outcome happened in words its owner can act on, absent unless the outcome
    carries one.
    """

    reported_at: str | None = None
    """
    URL of the comment telling this branch's owner about it, absent unless one was
    posted.
    """

    blocking_ancestor: str | None = None
    """
    The branch below this one that was left unpublished, absent unless the outcome is
    :attr:`RestackOutcome.BLOCKED_BY_ANCESTOR`.
    """


def conflict_report(
    branch: Branch, conflicting_paths: Sequence[str], parent: str
) -> str:
    """
    Write the comment telling a branch's owner that their branch needs them.

    :param branch: The branch that could not be integrated.
    :param conflicting_paths: The paths that conflicted.
    :param parent: The branch whose tip was being integrated.
    :return: The comment body.
    """
    files = "\n".join(f"- `{path}`" for path in conflicting_paths)
    addressed = (
        f"\n\n{branch.session}"
        if branch.session
        else "\n\nThis pull request's description names no session to address."
    )
    return (
        f"{MaintenanceText.CONFLICT_COMMENT_PREFIX} integrating `{parent}` into `{branch.name}` "
        f"conflicts, so this branch was left untouched and skipped.\n\n"
        f"Conflicting files:\n{files}\n\n"
        f"Please resolve and push. This branch is labelled "
        f"`needs-resolution` so later passes skip it rather than re-reporting the same "
        f"conflict; the label is cleared automatically once it merges cleanly again, "
        f"and the branch rejoins the pass.{addressed}"
    )


# %% who answers a collision


@dataclass(frozen=True)
class ConflictResponse(ABC):
    """
    What becomes of a branch whose parent could not be integrated cleanly.

    The collision itself is never resolved by a pass - that is a change to somebody's
    branch, made by whoever is in a position to judge which side is right. Who that is
    depends on why the pass is running, which is what the members of this family differ
    on.
    """

    @classmethod
    def chosen_for(cls, stacked_on: str | None) -> ConflictResponse:
        """
        The response a pass answers its collisions through.

        :param stacked_on: The branch whose subtree is being restacked, or ``None`` when
            the whole board is.
        :return: The response to answer through.
        """
        if stacked_on is None:
            return TellTheBranchOwner()
        return LeaveItToTheCaller()

    @abstractmethod
    def answer(
        self, restacking: BranchUnderRestack, conflicting_paths: Sequence[str]
    ) -> BranchOutcome:
        """
        Conclude the branch that could not take its parent's tip.

        :param restacking: The branch being restacked.
        :param conflicting_paths: The paths that conflicted.
        :return: The outcome concluding the branch.
        """


@dataclass(frozen=True)
class TellTheBranchOwner(ConflictResponse):
    """
    Labels the branch and comments on its pull request.

    What an unattended pass over the whole board owes a branch it had to leave alone:
    nobody is watching the run, so a report reaching only its summary reaches nobody.
    The label is what makes the next pass withhold the branch rather than re-report the
    same collision.
    """

    def answer(
        self, restacking: BranchUnderRestack, conflicting_paths: Sequence[str]
    ) -> BranchOutcome:
        """:param restacking: The branch being restacked.
        :param conflicting_paths: The paths that conflicted.
        :return: The outcome, carrying where its owner was told."""
        branch = restacking.branch
        restacking.fork.replace_labels(
            branch.pull_request_number,
            LabelWrite.replacing(
                branch.labels,
                added=[restacking.configuration.needs_resolution_label],
            ).labels,
        )
        return restacking.concluded(
            RestackOutcome.CONFLICT,
            conflicting_paths=conflicting_paths,
            reported_at=restacking.fork.add_comment(
                branch.pull_request_number,
                conflict_report(branch, conflicting_paths, restacking.parent),
            ),
        )


@dataclass(frozen=True)
class LeaveItToTheCaller(ConflictResponse):
    """
    Returns the collision without writing anything to the fork.

    What a pass over one named subtree owes it instead: the caller named that subtree
    because a fix they just made is what the parent carries, so they are present, the
    judgement is theirs, and both writes would be addressed to themselves.
    """

    def answer(
        self, restacking: BranchUnderRestack, conflicting_paths: Sequence[str]
    ) -> BranchOutcome:
        """:param restacking: The branch being restacked.
        :param conflicting_paths: The paths that conflicted.
        :return: The outcome, naming the branch, its parent and what collided."""
        return restacking.concluded(
            RestackOutcome.CONFLICT, conflicting_paths=conflicting_paths
        )


@dataclass(frozen=True)
class BranchUnderRestack:
    """
    One branch's restack, and everything a step needs to carry it out.
    """

    branch: Branch
    """
    The branch being restacked.
    """

    parent: str
    """
    The branch whose tip is to be integrated into it.
    """

    strategy: IntegrationStrategy
    """
    How that parent is to be integrated, which is also what authorises a rewrite.
    """

    stack: Stack
    """
    The derived stack it belongs to.
    """

    git: GitCommandRunner
    """
    The runner to execute through.
    """

    fork: ForkPullRequests
    """
    The fork, read for conflict state and written to when reporting.
    """

    checks: CommitMoveChecks
    """
    The checks its push is put through.
    """

    conflict_response: ConflictResponse = field(default_factory=TellTheBranchOwner)
    """
    How a collision with the parent is answered.
    """

    @property
    def configuration(self) -> Configuration:
        """:return: The resolved configuration."""
        return self.stack.configuration

    @property
    def branch_reference(self) -> str:
        """:return: The fork's copy of this branch, which every step starts from."""
        return self.configuration.reference_on_fork(self.branch.name)

    @property
    def parent_reference(self) -> str:
        """:return: The fork's copy of the parent being integrated."""
        return self.configuration.reference_on_fork(self.parent)

    @property
    def merge_message(self) -> str:
        """:return: The message of the merge integrating the parent, marked as the
        restack's own by :attr:`CommitTrailer.RESTACKED_PARENT` and
        :attr:`CommitTrailer.RESTACKED_PARENT_COMMIT`."""
        return (
            f"Merge {self.parent_reference} into {self.branch.name}\n\n"
            f"{CommitTrailer.RESTACKED_PARENT}: {self.parent_reference}\n"
            f"{CommitTrailer.RESTACKED_PARENT_COMMIT}: "
            f"{self.git.commit_at(self.parent_reference)}"
        )

    def concluded(self, outcome: RestackOutcome, **detail: Any) -> BranchOutcome:
        """
        Finish this branch with an outcome its owner can act on.

        :param outcome: What became of it.
        :param detail: Whatever that outcome carries.
        :return: The outcome, naming this branch and its parent.
        """
        return BranchOutcome(
            self.branch.name, self.parent, self.strategy, outcome, **detail
        )


# %% merges a restack replaces


@dataclass(frozen=True)
class SupersededMerges:
    """
    The merges at a branch's tip that a restack made and that only brought in what the
    branch is about to hold anyway.

    Every restack merges the parent's moved tip in, so a branch waiting through many
    passes would collect one merge per pass. A merge is superseded when a restack made it,
    the parent and the branch's own line beneath it together hold every change it brought
    in, and its content is exactly what merging its two parents produces - so it carries
    no work of its own, and merging the parent's current tip into the commit beneath it
    reproduces everything it held. A restack's merge carries both
    :class:`CommitTrailer` trailers, the commit one naming the parent commit it merged in;
    a merge a person made, even one reusing a restack's message, is never superseded.
    """

    git: GitCommandRunner
    """
    The runner to read history through.
    """

    parent_reference: str
    """
    The fork's copy of the parent about to be integrated.
    """

    def replacement_start_points(self, tip: str) -> list[str]:
        """
        :param tip: The branch's published tip.
        :return: The commit beneath each superseded merge at *tip*, deepest first, so a
            merge onto the first replaces them all; empty when *tip* is not one.
        """
        start_points = []
        commit = tip
        while self.is_superseded(commit):
            commit = self.git.parents_of(commit)[0]
            start_points.append(commit)
        return start_points[::-1]

    def is_superseded(self, commit: str) -> bool:
        """:param commit: A commit on the branch's own line.
        :return: Whether it is a restack's merge the parent's current tip supersedes."""
        match self.git.parents_of(commit):
            case (own_line, merged_in):
                return (
                    self.git.trailer_value(commit, CommitTrailer.RESTACKED_PARENT) != ""
                    and self.git.trailer_value(
                        commit, CommitTrailer.RESTACKED_PARENT_COMMIT
                    )
                    == merged_in
                    and not self.git.contains(own_line, self.parent_reference)
                    and self.git.adds_only_merges(
                        merged_in, self.parent_reference, own_line
                    )
                    and self.git.automatic_merge_tree(own_line, merged_in)
                    == self.git.tree_of(commit)
                )
            case _:
                return False


# %% the steps themselves


@dataclass(frozen=True)
class RestackStep(ABC):
    """
    One step of a branch's restack.

    A step either concludes the branch - returning the outcome its owner acts on - or
    returns nothing and lets the next step run. Adding a step is writing a subclass and
    placing it in :func:`basstler.maintenance.restack_procedure.restack_steps`, whose order is
    the procedure.
    """

    @abstractmethod
    def attempt(self, restacking: BranchUnderRestack) -> BranchOutcome | None:
        """
        Carry out this step.

        :param restacking: The branch being restacked.
        :return: The outcome concluding the branch, or ``None`` to continue.
        """


@dataclass(frozen=True)
class WithholdBranchStillConflicting(RestackStep):
    """
    Leaves a branch alone while it is still conflicted from an earlier pass.

    Clears the label as a side effect when it is not, since that is what lets the branch
    rejoin the pass without anybody remembering to remove it by hand.
    """

    def attempt(self, restacking: BranchUnderRestack) -> BranchOutcome | None:
        """:param restacking: The branch being restacked.
        :return: A withheld outcome while it still conflicts, otherwise ``None``."""
        branch = restacking.branch
        label = restacking.configuration.needs_resolution_label
        if label not in branch.labels:
            return None
        state = PullRequestField.MERGEABLE_STATE.read(
            restacking.fork.pull_request(branch.pull_request_number),
            branch.pull_request_number,
        )
        if state == MergeableState.DIRTY:
            return restacking.concluded(
                RestackOutcome.WITHHELD,
                explanation="still conflicted against its base since a previous pass",
            )
        restacking.fork.replace_labels(
            branch.pull_request_number,
            LabelWrite.replacing(branch.labels, removed=[label]).labels,
        )
        return None


@dataclass(frozen=True)
class SkipBranchAlreadyCurrent(RestackStep):
    """
    Leaves a branch alone when its parent's tip is already contained in it.
    """

    def attempt(self, restacking: BranchUnderRestack) -> BranchOutcome | None:
        """:param restacking: The branch being restacked.
        :return: An up-to-date outcome when nothing has to move, otherwise ``None``."""
        if restacking.git.contains(
            restacking.parent_reference, restacking.branch_reference
        ):
            return restacking.concluded(RestackOutcome.UP_TO_DATE)
        return None


@dataclass(frozen=True)
class IntegrateParent(RestackStep):
    """
    Integrates the parent's tip, answering a conflict through the branch's
    :class:`ConflictResponse`.

    A conflict is never resolved here - that is a change to somebody else's branch.

    A merge is made onto the deepest commit beneath the branch's
    :class:`SupersededMerges` it succeeds on, so the branch carries as few merges of its
    parent as merge cleanly however many passes it waits through. When every such merge
    fails, the parent is merged onto the published tip instead, so replacing earlier
    merges never turns a clean restack into a reported conflict.

    Unmerged paths are what make a failed integration a conflict, not its exit status:
    a merge also refuses when an untracked file is in the way, when the histories are
    unrelated, or when a reference does not resolve. Labelling those would name a
    branch that merges perfectly well, and the branch's owner would have nothing to
    fix - so they are reported as a plain failure of the pass instead.
    """

    def attempt(self, restacking: BranchUnderRestack) -> BranchOutcome | None:
        """:param restacking: The branch being restacked.
        :return: A conflict outcome when the parent left unmerged paths, a failure
            outcome when the integration failed without any, otherwise ``None``."""
        git = restacking.git
        integration = self._integrate(restacking)
        if integration.succeeded:
            return None
        conflicting = git.unmerged_paths()
        git.abandon(restacking.strategy)
        if not conflicting:
            return restacking.concluded(
                RestackOutcome.INTEGRATION_FAILED,
                explanation=integration.error_output,
            )
        return restacking.conflict_response.answer(restacking, conflicting)

    @classmethod
    def _integrate(cls, restacking: BranchUnderRestack) -> GitCommandResult:
        """
        :param restacking: The branch being restacked.
        :return: The integration that stands - the deepest merge replacing superseded
            merges that succeeded, otherwise the one onto the published tip.
        """
        tip = restacking.branch_reference
        if restacking.strategy is IntegrationStrategy.REBASE:
            return cls._integrate_onto(restacking, tip)
        superseded = SupersededMerges(restacking.git, restacking.parent_reference)
        for start_point in superseded.replacement_start_points(tip):
            replacing = cls._integrate_onto(restacking, start_point)
            if replacing.succeeded:
                return replacing
            restacking.git.abandon(restacking.strategy)
        return cls._integrate_onto(restacking, tip)

    @staticmethod
    def _integrate_onto(
        restacking: BranchUnderRestack, start_point: str
    ) -> GitCommandResult:
        """
        :param restacking: The branch being restacked.
        :param start_point: The commit to put the branch at before integrating.
        :return: The finished integration of the parent's tip.
        """
        git = restacking.git
        git.checkout(restacking.branch.name, start_point)
        if restacking.strategy is IntegrationStrategy.REBASE:
            return git.rebase(restacking.parent_reference)
        return git.merge(restacking.parent_reference, restacking.merge_message)


@dataclass(frozen=True)
class RefuseAnUnsafeMove(RestackStep):
    """
    Puts the push through the checks before it is made, without exception.
    """

    def attempt(self, restacking: BranchUnderRestack) -> BranchOutcome | None:
        """:param restacking: The branch being restacked.
        :return: A refused outcome carrying every reason, otherwise ``None``."""
        checks = CommitMoveChecks(
            stack=restacking.checks.stack,
            checked_out_branch=restacking.git.checked_out_branch(),
            is_ancestor=restacking.checks.is_ancestor,
        )
        refusals = tuple(
            refusal.reason
            for refusal in checks.refusals(
                ProposedCommitMove(
                    action=CommitMoveAction.RESTACK,
                    source=restacking.branch.name,
                    destination=restacking.branch.name,
                    destination_remote=restacking.configuration.fork_remote,
                )
            )
        )
        if refusals:
            return restacking.concluded(RestackOutcome.REFUSED, refusals=refusals)
        return None


@dataclass(frozen=True)
class PublishBranch(RestackStep):
    """
    Publishes the integrated branch, reporting rather than forcing a rejection.

    A merge that no longer contains the published tip replaced superseded merges, which
    is the one rewrite a merge publishes - under a lease, like a rebase.
    """

    def attempt(self, restacking: BranchUnderRestack) -> BranchOutcome:
        """:param restacking: The branch being restacked.
        :return: What became of the push - this step always concludes the branch."""
        git = restacking.git
        push = git.push(
            restacking.configuration.propose_push(
                restacking.branch.name,
                restacking.strategy,
                replaces_superseded_merges=not git.contains(
                    restacking.branch_reference, "HEAD"
                ),
            )
        )
        if not push.succeeded:
            return restacking.concluded(
                RestackOutcome.PUSH_REJECTED, explanation=push.error_output
            )
        git.fetch(restacking.configuration.fork_remote, restacking.branch.name)
        return restacking.concluded(
            RestackOutcome.PUSHED, pushed_commit=git.commit_at("HEAD")
        )
