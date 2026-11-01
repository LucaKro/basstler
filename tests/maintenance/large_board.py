"""
A pass over a large stack, built without git or a network, for the tests that bound how
much a pass prints.

Its shape is that of a fork with about 150 open stacked pull requests: most branches
already up to date, a couple of dozen pushed, a few conflicting on hundreds of files, a
handful promoted with full-length create-links, and a backlog of links from earlier
passes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum

from basstler.workflow_labels import WorkflowLabel
from basstler.maintenance.fast_forward import FastForwardOutcome, FastForwardReport
from basstler.maintenance.promotion import Promotion
from basstler.maintenance.report import MaintenanceReport
from basstler.maintenance.restack_steps import BranchOutcome, RestackOutcome
from basstler_domain_model.git.commands import (
    IntegrationStrategy,
)
from basstler.stack.model import (
    PullRequest,
    Reparent,
    SkippedPullRequest,
    SkipReason,
    Stack,
)
from basstler.stack.promotion_link import PromotionLink
from basstler_domain_model.github.vocabulary import (
    RollupState,
)

from .fork import (
    A_SESSION_LINK,
    UPSTREAM_BASE,
    PullRequestHeadRepository,
    a_board_entry,
    make_configuration,
)


class OutputBudget(IntEnum):
    """
    The most a command a session runs may print, in bytes of UTF-8.
    """

    FINISH_SUMMARY = 7 * 1024
    """
    The summary of a whole pass, however large the stack.
    """

    PROMOTION_CONTEXT = 3 * 1024
    """
    What one branch's upstream summary is written from, however large its change.
    """


@dataclass(frozen=True)
class LargeBoard:
    """
    The counts a large pass is built from, each a part of the pass that grows with the
    stack.
    """

    pull_requests: int = 150
    """
    How many open pull requests the fork has.
    """

    pushed: int = 22
    """
    How many branches the restack publishes.
    """

    conflicting_files: tuple[int, ...] = (150, 220, 40)
    """
    How many files each conflicting branch conflicts on, one entry per branch.
    """

    withheld: int = 6
    """
    How many branches are withheld from an earlier conflict.
    """

    promoted: int = 5
    """
    How many branches have their upstream link built this pass.
    """

    pending_links: int = 30
    """
    How many links built by earlier passes are not yet acted on.
    """

    reparents: int = 12
    """
    How many pull requests still have to be retargeted.
    """

    skipped: int = 6
    """
    How many pull requests have their head outside the fork.
    """

    failing_checks: int = 9
    """
    How many approved branches, each based on the upstream base, fail their checks.
    """

    def doubled(self) -> LargeBoard:
        """
        :return: A board twice this size, with twice as much of every part of the pass.
        """
        return replace(
            self,
            pull_requests=self.pull_requests * 2,
            pushed=self.pushed * 2,
            conflicting_files=self.conflicting_files * 2,
            withheld=self.withheld * 2,
            promoted=self.promoted * 2,
            pending_links=self.pending_links * 2,
            reparents=self.reparents * 2,
            skipped=self.skipped * 2,
            failing_checks=self.failing_checks * 2,
        )

    @staticmethod
    def branch_name(index: int) -> str:
        """
        :param index: The branch's position on the board.
        :return: A branch name as long as a session's own usually are.
        """
        return f"claude/implement-the-stacked-feature-number-{index:03d}-a1b2c3"

    @staticmethod
    def title(index: int) -> str:
        """
        :param index: The branch's position on the board.
        :return: A pull request title as long as they usually are.
        """
        return (
            f"Add the {index:03d}th capability to the planner, with its tests and docs"
        )

    def board(self) -> list[PullRequest]:
        """
        :return: A chain of open pull requests, each based on the one before, the
            first on the upstream base; the first :attr:`pending_links` past the
            promoted ones carry the link label, the next :attr:`failing_checks` are
            based on the upstream base with failed checks, and the next
            :attr:`pushed` have checks still running.
        """
        linked_end = self.promoted + self.pending_links
        failing_end = linked_end + self.failing_checks
        return [
            a_board_entry(
                index,
                self.branch_name(index),
                (
                    UPSTREAM_BASE
                    if index == 0 or linked_end <= index < failing_end
                    else self.branch_name(index - 1)
                ),
                labels=(
                    [WorkflowLabel.PROMOTION_LINK_SENT]
                    if self.promoted <= index < linked_end
                    else []
                ),
                title=self.title(index),
                session=A_SESSION_LINK,
                check_state=(
                    RollupState.FAILURE
                    if linked_end <= index < failing_end
                    else (
                        RollupState.PENDING
                        if failing_end <= index < failing_end + self.pushed
                        else RollupState.SUCCESS
                    )
                ),
            )
            for index in range(self.pull_requests)
        ]

    def stack(self) -> Stack:
        """
        :return: The stack derived from :meth:`board`, with only the upstream base
            landed.
        """
        return Stack.build(
            make_configuration(), self.board(), lambda branch: branch == UPSTREAM_BASE
        )

    def restacked(self) -> tuple[BranchOutcome, ...]:
        """
        :return: One outcome per branch: the conflicts first, then the withheld, the
            pushed, and every other branch up to date.
        """
        outcomes = []
        for index in range(self.pull_requests):
            name = self.branch_name(index)
            conflict = index - len(self.conflicting_files)
            withheld = conflict - self.withheld
            if index < len(self.conflicting_files):
                outcome = self.conflict(name, self.conflicting_files[index])
            elif conflict < self.withheld:
                outcome = self.outcome(name, RestackOutcome.WITHHELD)
            elif withheld < self.pushed:
                outcome = self.outcome(
                    name, RestackOutcome.PUSHED, pushed_commit=f"{index:040d}"
                )
            else:
                outcome = self.outcome(name, RestackOutcome.UP_TO_DATE)
            outcomes.append(outcome)
        return tuple(outcomes)

    @staticmethod
    def outcome(
        branch: str, outcome: RestackOutcome, **detail: object
    ) -> BranchOutcome:
        """
        :param branch: The branch it is about.
        :param outcome: What became of it.
        :param detail: Any further :class:`BranchOutcome` fields.
        :return: The outcome, integrating the parent by merge.
        """
        return BranchOutcome(
            branch, UPSTREAM_BASE, IntegrationStrategy.MERGE, outcome, **detail
        )

    def conflict(self, branch: str, files: int) -> BranchOutcome:
        """
        :param branch: The branch that conflicts.
        :param files: How many files it conflicts on.
        :return: The conflict, reported in a comment.
        """
        return self.outcome(
            branch,
            RestackOutcome.CONFLICT,
            conflicting_paths=tuple(
                f"src/package/subpackage/module_number_{number:03d}.py"
                for number in range(files)
            ),
            reported_at=f"https://github.com/owner/repository/pull/1#issuecomment-{files}",
        )

    def promotions(self, stack: Stack) -> tuple[Promotion, ...]:
        """
        :param stack: The stack the links are built for.
        :return: One promotion per promoted branch, each prefilling a description long
            enough to reach the URL limit.
        """
        return tuple(
            Promotion(
                branch=self.branch_name(index),
                pull_request_number=index,
                url=PromotionLink.build(
                    stack.configuration,
                    self.branch_name(index),
                    self.title(index),
                    "A point an upstream reviewer reads. " * 400,
                ).url,
                body_was_truncated=True,
            )
            for index in range(self.promoted)
        )

    def report(self, stack: Stack) -> MaintenanceReport:
        """
        :param stack: The stack the pass ran over.
        :return: Everything the pass did.
        """
        other_fork = PullRequestHeadRepository.ANOTHER_CONTRIBUTORS_FORK.value
        return MaintenanceReport(
            fast_forward=FastForwardReport(
                FastForwardOutcome.PUSHED,
                f"upstream/{UPSTREAM_BASE}",
                f"origin/{UPSTREAM_BASE}",
                "0" * 40,
            ),
            restacked=self.restacked(),
            promoted=self.promotions(stack),
            reparents=tuple(
                Reparent(
                    self.branch_name(index),
                    index,
                    self.branch_name(index - 1),
                    UPSTREAM_BASE,
                )
                for index in range(1, self.reparents + 1)
            ),
            skipped=tuple(
                SkippedPullRequest(
                    PullRequest(
                        number=self.pull_requests + index,
                        head=self.branch_name(self.pull_requests + index),
                        base=UPSTREAM_BASE,
                        draft=False,
                        head_repository=other_fork.full_name,
                    ),
                    SkipReason.HEAD_ON_ANOTHER_FORK,
                )
                for index in range(self.skipped)
            ),
        )
