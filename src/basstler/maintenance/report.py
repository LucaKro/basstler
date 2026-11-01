"""
What a pass did, as a document, as printed lines, and as an exit status.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum

from basstler.exit_codes import StackExitCode
from basstler.maintenance.fast_forward import FastForwardOutcome, FastForwardReport
from basstler.maintenance.promotion import Promotion
from basstler.maintenance.restack import BranchOutcome, RestackOutcome
from basstler.maintenance.upstream_pull_request_synchronization import (
    UpstreamPullRequestSynchronizationChange,
)
from basstler.stack.model import Reparent, SkippedPullRequest, Stack
from basstler.standard_streams import StandardStreamHandler

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command prints through.
"""

# %% vocabulary


class MaintenanceReportKey(StrEnum):
    """
    The keys :meth:`MaintenanceReport.as_json` adds beside the report's own fields.
    """

    STATUS = "status"
    """
    The exit status, by the name a caller reads.
    """

    EXIT_CODE = "exit_code"
    """
    The exit status, as the process exit code.
    """


# %% the report a caller renders or emits


@dataclass(frozen=True)
class MaintenanceReport:
    """
    Everything one pass did, and the one thing it leaves for its caller.

    ``reparents`` is that one thing: retargeting a base is the single write GitHub
    refuses to the credential this runs on, so it is reported rather than performed.

    Every field defaults to nothing done, so a single command reports the part of the
    pass it performed through the same object - and therefore through the same exit
    status - as a whole pass does.
    """

    fast_forward: FastForwardReport | None = None
    """
    What became of the fork's base branch, absent when it was not attempted.
    """

    restacked: tuple[BranchOutcome, ...] = ()
    """
    What became of each branch in the restack plan.
    """

    promoted: tuple[Promotion, ...] = ()
    """
    The branches whose upstream link was built and recorded this pass.
    """

    promotion_labels_cleared: tuple[str, ...] = ()
    """
    The branches whose spent link label was removed this pass.
    """

    upstream_pull_request_synchronization_changes: tuple[
        UpstreamPullRequestSynchronizationChange, ...
    ] = ()
    """
    The writes bringing each pull request's in-review label and upstream link in step
    this pass.
    """

    reparents: tuple[Reparent, ...] = ()
    """
    The children whose base has landed, for the caller to retarget.
    """

    landed: tuple[str, ...] = ()
    """
    The branches whose own commits are already in the upstream base.
    """

    promotable: tuple[str, ...] = ()
    """
    The branches approved and unblocked, whether or not a link was built this pass.
    """

    skipped: tuple[SkippedPullRequest, ...] = ()
    """
    The pull requests whose head is not on the fork, which the pass left alone.
    """

    @classmethod
    def of_pass(
        cls,
        stack: Stack,
        fast_forward_report: FastForwardReport | None,
        restacked: Sequence[BranchOutcome],
        promoted: Sequence[Promotion] = (),
        promotion_labels_cleared: Sequence[str] = (),
        upstream_pull_request_synchronization_changes: Sequence[
            UpstreamPullRequestSynchronizationChange
        ] = (),
    ) -> MaintenanceReport:
        """
        Assemble one pass's outcomes and its leftovers into a single report.

        :param stack: The derived stack, read for what the caller still has to do.
        :param fast_forward_report: What became of the fork's base branch, if attempted.
        :param restacked: What became of each branch in the restack plan.
        :param promoted: The branches whose upstream link was built this pass.
        :param promotion_labels_cleared: The branches whose spent link label was
            removed.
        :param upstream_pull_request_synchronization_changes: The writes bringing each pull request's in-review
            label and upstream link in step.
        :return: The report.
        """
        return cls(
            fast_forward=fast_forward_report,
            restacked=tuple(restacked),
            promoted=tuple(promoted),
            promotion_labels_cleared=tuple(promotion_labels_cleared),
            upstream_pull_request_synchronization_changes=tuple(
                upstream_pull_request_synchronization_changes
            ),
            reparents=tuple(stack.reparents()),
            landed=tuple(branch.name for branch in stack.landed_branches()),
            promotable=tuple(branch.name for branch in stack.promotion_order()),
            skipped=tuple(stack.skipped_pull_requests()),
        )

    def as_json(self) -> str:
        """:return: The report as one machine-readable document."""
        status = self.exit_code
        return json.dumps(
            {
                MaintenanceReportKey.STATUS: status.name_for_a_caller,
                MaintenanceReportKey.EXIT_CODE: int(status),
                **asdict(self),
            },
            indent=2,
        )

    @property
    def branches_left_unpublished(self) -> tuple[BranchOutcome, ...]:
        """:return: Every branch the pass could not leave in the state it wanted."""
        return tuple(
            outcome
            for outcome in self.restacked
            if outcome.outcome not in {RestackOutcome.PUSHED, RestackOutcome.UP_TO_DATE}
        )

    @property
    def fast_forward_was_refused(self) -> bool:
        """:return: Whether the fork's base was left behind the upstream."""
        return (
            self.fast_forward is not None
            and self.fast_forward.outcome is FastForwardOutcome.REFUSED_NOT_FAST_FORWARD
        )

    @property
    def exit_code(self) -> StackExitCode:
        """
        One pass's exit status, from what it actually left behind.

        Every command that produces a report derives its status here, so none of them
        can disagree about what counts as a clean pass.
        """
        if self.fast_forward_was_refused:
            return StackExitCode.NOT_FAST_FORWARD
        if self.branches_left_unpublished:
            return StackExitCode.BRANCH_NEEDS_ATTENTION
        return StackExitCode.SUCCESS

    def print_summary(self) -> None:
        """
        Print one line per thing the pass did, in the order the pass did them.
        """
        self.print_fast_forward()
        self.print_restack()
        self.print_promotions()
        self.print_upstream_pull_request_synchronization_changes()
        self.print_skipped()

    def print_fast_forward(self) -> None:
        """
        Print what became of the fork's base branch, if it was attempted.
        """
        if self.fast_forward is None:
            return
        report = self.fast_forward
        logger.info(f"{report.fork_reference}\t{report.outcome}\t{report.commit}")
        if report.explanation:
            logger.error(report.explanation)

    def print_restack(self) -> None:
        """
        Print what became of each branch in the restack plan.
        """
        for outcome in self.restacked:
            detail = (
                ",".join(outcome.conflicting_paths)
                or outcome.pushed_commit
                or outcome.explanation
                or ""
            )
            logger.info(f"{outcome.branch}\t{outcome.outcome}\t{detail}")

    def print_promotions(self) -> None:
        """
        Print every upstream link built this pass, and every spent link label removed.
        """
        for promotion in self.promoted:
            logger.info(
                f"{promotion.branch}\t#{promotion.pull_request_number}\t{promotion.url}"
            )
            if promotion.body_was_truncated:
                logger.error(
                    f"{promotion.branch}: the prefilled description was shortened to "
                    f"fit the URL limit"
                )
        for branch in self.promotion_labels_cleared:
            logger.info(f"{branch}\tlink-label-cleared\t")

    def print_upstream_pull_request_synchronization_changes(self) -> None:
        """
        Print every in-review label and upstream link written this pass.
        """
        for change in self.upstream_pull_request_synchronization_changes:
            logger.info(
                f"{change.branch}\t#{change.pull_request_number}\t{change.action}"
            )

    def print_skipped(self) -> None:
        """
        Print every pull request the pass left alone, with why and where its head lives.
        """
        for skipped in self.skipped:
            pull_request = skipped.pull_request
            logger.info(
                f"{pull_request.head}\t#{pull_request.number}\t{skipped.reason}"
                f"\t{pull_request.head_repository or ''}"
            )
