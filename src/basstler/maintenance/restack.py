"""
The restack: every line of the fork's stack rebased onto what it is based on, and
published together, one cascade per line.

The cascade itself is a :class:`basstler.stack.cascading_rebase.CascadingRebase` - GitHub's
``gh stack`` in a session. What stays here is what a pass owes the branches it rebases:
a branch still conflicted from an earlier pass is withheld, a conflict is answered rather
than resolved, and a branch above one left unpublished is not attempted.
"""

from __future__ import annotations

import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from basstler.maintenance.board import PullRequestField
from basstler.maintenance.constants import MaintenanceText, MergeableState
from basstler.maintenance.git_commands import GitCommandRunner
from basstler.maintenance.github import ForkPullRequests
from basstler.stack.cascading_rebase import CascadeStepOutcome, CascadingRebase
from basstler.stack.label_write import LabelWrite
from basstler.stack.model import Branch, LinearStack, Stack
from basstler.stack.settings import Configuration

if TYPE_CHECKING:
    from types import TracebackType

# %% what became of one branch


class RestackOutcome(StrEnum):
    """
    What became of one branch during a restack.
    """

    PUSHED = "pushed"
    """
    It was rebased onto its parent and the result published.
    """

    UP_TO_DATE = "up-to-date"
    """
    It already held its parent's tip.
    """

    CONFLICT = "conflict"
    """
    Rebasing it onto its parent conflicted; nothing of it was published.
    """

    INTEGRATION_FAILED = "integration-failed"
    """
    Rebasing it failed without conflicting on anything, so the branch is not the thing
    to fix and its owner was not told; nothing of it was published.
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
    The branch it was to be rebased onto.
    """

    outcome: RestackOutcome
    """
    What became of it.
    """

    conflicting_paths: tuple[str, ...] = ()
    """
    The paths that conflicted, empty unless the outcome is a conflict.
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

    :param branch: The branch that could not be rebased.
    :param conflicting_paths: The paths that conflicted.
    :param parent: The branch it was being rebased onto.
    :return: The comment body.
    """
    files = "\n".join(f"- `{path}`" for path in conflicting_paths)
    addressed = (
        f"\n\n{branch.session}"
        if branch.session
        else "\n\nThis pull request's description names no session to address."
    )
    return (
        f"{MaintenanceText.CONFLICT_COMMENT_PREFIX} rebasing `{branch.name}` onto "
        f"`{parent}` conflicts, so this branch was left untouched and skipped.\n\n"
        f"Conflicting files:\n{files}\n\n"
        f"Please rebase it with `gh stack rebase`, resolve, and push with "
        f"`gh stack push`. This branch is labelled `needs-resolution` so later passes "
        f"skip it rather than re-reporting the same conflict; the label is cleared "
        f"automatically once it rebases cleanly again, and the branch rejoins the "
        f"pass.{addressed}"
    )


# %% who answers a collision


@dataclass(frozen=True)
class ConflictResponse(ABC):
    """
    What becomes of a branch whose rebase onto its parent conflicted.

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
        self, layer: LayerUnderRestack, conflicting_paths: Sequence[str]
    ) -> BranchOutcome:
        """
        Conclude the branch whose rebase conflicted.

        :param layer: The branch being restacked.
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
        self, layer: LayerUnderRestack, conflicting_paths: Sequence[str]
    ) -> BranchOutcome:
        branch = layer.branch
        layer.fork.replace_labels(
            branch.pull_request_number,
            LabelWrite.replacing(
                branch.labels, added=[layer.configuration.needs_resolution_label]
            ).labels,
        )
        return layer.concluded(
            RestackOutcome.CONFLICT,
            conflicting_paths=tuple(conflicting_paths),
            reported_at=layer.fork.add_comment(
                branch.pull_request_number,
                conflict_report(branch, conflicting_paths, layer.parent),
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
        self, layer: LayerUnderRestack, conflicting_paths: Sequence[str]
    ) -> BranchOutcome:
        return layer.concluded(
            RestackOutcome.CONFLICT, conflicting_paths=tuple(conflicting_paths)
        )


# %% one layer


@dataclass(frozen=True)
class LayerUnderRestack:
    """
    One branch of a line being restacked, and what concluding it needs.
    """

    branch: Branch
    """
    The branch being restacked.
    """

    parent: str
    """
    The branch it is rebased onto: the layer below it, or its line's trunk.
    """

    configuration: Configuration
    """
    The resolved configuration naming the fork remote and the labels.
    """

    fork: ForkPullRequests
    """
    The fork, read for conflict state and written to when reporting.
    """

    def concluded(self, outcome: RestackOutcome, **detail: Any) -> BranchOutcome:
        """
        Finish this branch with an outcome its owner can act on.

        :param outcome: What became of it.
        :param detail: Whatever that outcome carries.
        :return: The outcome, naming this branch and its parent.
        """
        return BranchOutcome(self.branch.name, self.parent, outcome, **detail)

    def still_conflicting(self) -> bool:
        """
        Whether the branch is still conflicted from an earlier pass.

        Clears the label as a side effect when it is not, since that is what lets the
        branch rejoin the pass without anybody remembering to remove it by hand.

        :return: Whether it carries the label and GitHub still finds it conflicting.
        """
        branch = self.branch
        label = self.configuration.needs_resolution_label
        if label not in branch.labels:
            return False
        state = PullRequestField.MERGEABLE_STATE.read(
            self.fork.pull_request(branch.pull_request_number),
            branch.pull_request_number,
        )
        if state == MergeableState.DIRTY:
            return True
        self.fork.replace_labels(
            branch.pull_request_number,
            LabelWrite.replacing(branch.labels, removed=[label]).labels,
        )
        return False


# %% one line


@dataclass(frozen=True)
class LinearStackRestack:
    """
    One line of the stack rebased as a cascade, its lowest layers first, and published
    together.

    A layer is rebased by rebasing the line up to it, so the cascade stops at the first
    layer that does not rebase and that layer is the one it names. Every layer below it
    is published; every layer above it is not attempted.
    """

    line: LinearStack
    """
    The line to restack.
    """

    configuration: Configuration
    """
    The resolved configuration naming the fork remote and the labels.
    """

    git: GitCommandRunner
    """
    The worktree the branches are rebased in.
    """

    cascade: CascadingRebase
    """
    What rebases and publishes the line.
    """

    fork: ForkPullRequests
    """
    The fork, read for conflict state and written to when reporting.
    """

    conflict_response: ConflictResponse
    """
    How a collision with a parent is answered.
    """

    def layers(self) -> list[LayerUnderRestack]:
        """
        :return: Every layer of the line, bottom first, each with what it is rebased
            onto.
        """
        parents = [self.line.trunk, *(branch.name for branch in self.line.layers)]
        return [
            LayerUnderRestack(branch, parent, self.configuration, self.fork)
            for branch, parent in zip(self.line.layers, parents)
        ]

    def restack(self, blocking_ancestor: str | None) -> list[BranchOutcome]:
        """
        :param blocking_ancestor: The branch below the line this pass left without its
            parent's tip, if any.
        :return: One outcome per layer, bottom first.
        """
        layers = self.layers()
        if blocking_ancestor is not None:
            return self.blocked(layers, blocking_ancestor)
        attempted = self.attempted(layers)
        prepared = self.prepare(attempted)
        if prepared is not None:
            return [
                layer.concluded(RestackOutcome.INTEGRATION_FAILED, explanation=prepared)
                for layer in attempted
            ] + self.after_withheld(layers, attempted)
        outcomes = self.rebase(attempted)
        rebased = [
            layer
            for layer, outcome in zip(attempted, outcomes)
            if outcome is None and self.moved(layer)
        ]
        published = self.publish(rebased)
        concluded = [
            outcome
            or published.get(layer.branch.name)
            or layer.concluded(RestackOutcome.UP_TO_DATE)
            for layer, outcome in zip(attempted, outcomes)
        ]
        return concluded + self.after_withheld(layers, attempted)

    def attempted(self, layers: list[LayerUnderRestack]) -> list[LayerUnderRestack]:
        """
        :param layers: Every layer of the line, bottom first.
        :return: The layers below the first one still conflicted from an earlier pass.
        """
        for index, layer in enumerate(layers):
            if layer.still_conflicting():
                return layers[:index]
        return layers

    def after_withheld(
        self, layers: list[LayerUnderRestack], attempted: list[LayerUnderRestack]
    ) -> list[BranchOutcome]:
        """
        :param layers: Every layer of the line, bottom first.
        :param attempted: The layers below the first withheld one.
        :return: The withheld layer's outcome and those of the layers above it, empty
            when none was withheld.
        """
        remaining = layers[len(attempted) :]
        if not remaining:
            return []
        withheld = remaining[0].concluded(
            RestackOutcome.WITHHELD,
            explanation="still conflicted against its base since a previous pass",
        )
        return [withheld] + self.blocked(remaining[1:], withheld.branch)

    @staticmethod
    def blocked(
        layers: list[LayerUnderRestack], blocking_ancestor: str
    ) -> list[BranchOutcome]:
        """
        :param layers: Layers not to attempt.
        :param blocking_ancestor: The branch below them left unpublished this pass.
        :return: A blocked outcome per layer, naming that branch.
        """
        return [
            layer.concluded(
                RestackOutcome.BLOCKED_BY_ANCESTOR,
                blocking_ancestor=blocking_ancestor,
                explanation=f"not attempted: `{blocking_ancestor}` below it was left "
                "unpublished this pass",
            )
            for layer in layers
        ]

    def prepare(self, attempted: list[LayerUnderRestack]) -> str | None:
        """
        Put every attempted layer at the fork's copy of it, the trunk where the fork has
        it if the checkout lacks one, and have the cascade track the line.

        :param attempted: The layers to rebase, bottom first.
        :return: Why the line could not be prepared, ``None`` when it was.
        """
        if not attempted:
            return None
        trunk = self.line.trunk
        if self.git.commit_or_none(f"refs/heads/{trunk}") is None:
            created = self.git.attempt(
                "branch", trunk, self.configuration.reference_on_fork(trunk)
            )
            if not created.succeeded:
                return created.error_output
        for layer in attempted:
            placed = self.git.attempt(
                "branch",
                "--force",
                layer.branch.name,
                self.configuration.reference_on_fork(layer.branch.name),
            )
            if not placed.succeeded:
                return placed.error_output
        adopted = self.cascade.adopt(trunk, [layer.branch.name for layer in attempted])
        if adopted.outcome is CascadeStepOutcome.FAILED:
            return adopted.detail
        return None

    def rebase(self, attempted: list[LayerUnderRestack]) -> list[BranchOutcome | None]:
        """
        Rebase the line up to each attempted layer in turn.

        :param attempted: The layers to rebase, bottom first.
        :return: Per layer, the outcome concluding it, or ``None`` when it rebased and
            is still to be published; every layer above a conflict is blocked by it.
        """
        outcomes: list[BranchOutcome | None] = []
        for index, layer in enumerate(attempted):
            rebased = self.cascade.rebase_up_to(layer.branch.name)
            if rebased.outcome is CascadeStepOutcome.CONFLICTED:
                conflicting = self.git.unmerged_paths()
                self.cascade.abort_rebase()
                outcomes.append(self.conflict_response.answer(layer, conflicting))
                return outcomes + self.blocked(
                    attempted[index + 1 :], layer.branch.name
                )
            if rebased.outcome is CascadeStepOutcome.FAILED:
                self.cascade.abort_rebase()
                outcomes.append(
                    layer.concluded(
                        RestackOutcome.INTEGRATION_FAILED, explanation=rebased.detail
                    )
                )
                continue
            outcomes.append(None)
        return outcomes

    def moved(self, layer: LayerUnderRestack) -> bool:
        """
        :param layer: A layer the cascade rebased.
        :return: Whether its local branch no longer points where the fork's does.
        """
        return self.git.commit_at(
            f"refs/heads/{layer.branch.name}"
        ) != self.git.commit_at(self.configuration.reference_on_fork(layer.branch.name))

    def publish(self, rebased: list[LayerUnderRestack]) -> dict[str, BranchOutcome]:
        """
        Publish the line from its highest rebased layer.

        :param rebased: The layers whose rebase moved them, bottom first.
        :return: Each one's outcome, by branch name.
        """
        if not rebased:
            return {}
        self.git.run("checkout", "--quiet", rebased[-1].branch.name)
        published = self.cascade.publish()
        self.git.run("checkout", "--quiet", "--detach")
        if published.outcome is not CascadeStepOutcome.COMPLETED:
            return {
                layer.branch.name: layer.concluded(
                    RestackOutcome.PUSH_REJECTED, explanation=published.detail
                )
                for layer in rebased
            }
        self.git.fetch(
            self.configuration.fork_remote, *(layer.branch.name for layer in rebased)
        )
        return {
            layer.branch.name: layer.concluded(
                RestackOutcome.PUSHED,
                pushed_commit=self.git.commit_at(f"refs/heads/{layer.branch.name}"),
            )
            for layer in rebased
        }


# %% where the branch switching happens


@dataclass(frozen=True)
class DetachedCheckout:
    """
    The invoking checkout, detached so its branch can be restacked elsewhere.

    git refuses to check one branch out in two worktrees at once, and the caller of a
    pass is usually sitting on a branch of the stack. Detaching releases the name while
    changing nothing else - same commit, same files, same work in progress - and the
    branch is checked out again afterwards, which is also how the caller picks up a
    restack of their own branch.
    """

    git: GitCommandRunner
    """
    The invoking checkout.
    """

    branch: str
    """
    The branch it was on, empty when it was already detached.
    """

    @classmethod
    def of(cls, git: GitCommandRunner) -> DetachedCheckout:
        """:param git: The checkout to detach.
        :return: The detachment, to be used as a context manager so it is undone."""
        return cls(git, git.checked_out_branch())

    def __enter__(self) -> DetachedCheckout:
        """:return: This detachment, once the checkout is off its branch."""
        if self.branch:
            self.git.run("checkout", "--quiet", "--detach")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """
        Put the checkout back on the branch it was on.

        Attempted rather than depended on, so failing to restore never replaces the
        exception on its way out; the checkout is then left detached at the commit it
        started from, with everything it was carrying.
        """
        if self.branch:
            self.git.attempt("checkout", "--quiet", self.branch)


@dataclass(frozen=True)
class RestackWorktree:
    """
    A checkout of its own for the branch switching a restack does.

    Every step of the pass shells out to the package, whose tooling may be tracked
    content in the checkout the pass is invoked from. Most branches in a stack were cut
    before that tooling landed, so checking one out there deletes the tooling the rest
    of the pass needs and leaves the caller on a branch that is not theirs. This
    worktree is added outside the project instead, out of reach of the branches a
    restack switches to.

    Its refs are the same refs, so a branch it moves is moved for the whole repository.
    """

    git: GitCommandRunner
    """
    The runner every branch switch of a restack goes through.
    """

    origin: GitCommandRunner
    """
    The invoking checkout, which the worktree is added to and removed from.
    """

    @classmethod
    def added_to(cls, origin: GitCommandRunner) -> RestackWorktree:
        """
        Add a worktree, detached at whatever the invoking checkout has.

        :param origin: The checkout to add it to.
        :return: The worktree, to be used as a context manager so it is removed again.
        """
        path = Path(tempfile.mkdtemp(prefix="stack-restack-"))
        origin.run("worktree", "add", "--quiet", "--detach", str(path), "HEAD")
        return cls(GitCommandRunner(working_directory=path), origin)

    def __enter__(self) -> GitCommandRunner:
        """:return: The runner to restack through."""
        return self.git

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """
        Remove the worktree, whether the restack finished or was abandoned.

        Removal is attempted rather than depended on, so a failure to tidy up never
        replaces the exception that is on its way out.
        """
        self.origin.attempt(
            "worktree", "remove", "--force", str(self.git.working_directory)
        )


# %% the restack itself


def restack(
    stack: Stack,
    git: GitCommandRunner,
    fork: ForkPullRequests,
    cascade_in: Callable[[Path], CascadingRebase],
    stacked_on: str | None = None,
) -> list[BranchOutcome]:
    """
    Restack every line of the stack, each after the line holding its trunk.

    A line whose trunk is a branch this pass left unpublished is not attempted: that
    branch does not hold what the pass meant the line to be rebased onto.

    The cascades run in a :class:`RestackWorktree` rather than in the invoking checkout,
    which lends its branch through a :class:`DetachedCheckout` and gets it back with its
    own files still in place. The worktree goes first so it is gone before the branch is
    wanted again.

    Naming a branch in *stacked_on* carries whatever that branch now holds into the
    branches stacked on it, and leaves the rest of the board alone. It also decides who
    answers a collision - see :class:`ConflictResponse`.

    :param stack: The derived stack.
    :param git: The runner naming the checkout to add the worktree to.
    :param fork: The fork, read for conflict state and written to when reporting.
    :param cascade_in: Builds the cascade that rebases branches in a worktree.
    :param stacked_on: The branch whose subtree to restack, or ``None`` for every branch.
    :return: One outcome per branch restacked, each line bottom first.
    """
    conflict_response = ConflictResponse.chosen_for(stacked_on)
    with DetachedCheckout.of(git), RestackWorktree.added_to(git) as switching:
        cascade = cascade_in(switching.working_directory)
        blocking_ancestors: dict[str, str] = {}
        outcomes: list[BranchOutcome] = []
        for line in stack.linear_stacks(stacked_on):
            line_outcomes = LinearStackRestack(
                line, stack.configuration, switching, cascade, fork, conflict_response
            ).restack(blocking_ancestors.get(line.trunk))
            for outcome in line_outcomes:
                if outcome.outcome.blocks_branches_above:
                    blocking_ancestors[outcome.branch] = (
                        outcome.blocking_ancestor or outcome.branch
                    )
            outcomes.extend(line_outcomes)
        return outcomes
