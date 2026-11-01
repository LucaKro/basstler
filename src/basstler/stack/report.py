"""
The read-only reports the stack commands print about one derived stack.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.stack.checkout import CurrentCheckout, GitArgument, MergeTreeStatus
from basstler.stack.model import Branch, BranchStatus, Stack
from basstler.standard_streams import StandardStreamHandler

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command prints through.
"""

# %% the status table


class StatusColumn(StrEnum):
    """
    The columns of the status table, in the order they are printed, each with its width.
    """

    width: int
    """
    How many characters the column is padded to.
    """

    def __new__(cls, title: str, width: int) -> StatusColumn:
        """
        Make a member that is its own column title and carries the column's width.

        :param title: The column's heading.
        :param width: How many characters the column is padded to.
        :return: The member.
        """
        member = str.__new__(cls, title)
        member._value_ = title
        member.width = width
        return member

    BRANCH = ("branch", 38)
    """
    The branch's name.
    """

    STATE = ("state", 10)
    """
    Its :class:`BranchStatus`.
    """

    CHECKS = ("checks", 9)
    """
    The verdict over the checks on its head, blank while nothing has reported.
    """

    PULL_REQUEST = ("pull request", 13)
    """
    Its fork pull request's number.
    """

    DRIFT = ("ahead/behind parent", 40)
    """
    How far it and its parent have diverged, and how it integrates the parent.
    """

    BEHIND_BASE = ("behind base", 11)
    """
    How many upstream base commits it does not have yet.
    """

    @classmethod
    def line(cls, *cells: object) -> str:
        """
        :param cells: One value per column, in column order.
        :return: The cells as one table line, each padded to its column's width.
        """
        return " ".join(
            f"{cell!s:<{column.width}}" for column, cell in zip(cls, cells)
        ).rstrip()

    @classproperty
    def rule(cls) -> str:
        """
        The line separating the heading from the rows, as wide as the padded columns.
        """
        return "-" * sum(column.width + 1 for column in cls)


# %% the merge probe


class MergeVerdict(StrEnum):
    """
    Whether a branch would merge cleanly onto its parent right now.
    """

    CLEAN = "CLEAN"
    """
    It would merge without a conflict.
    """

    CONFLICTS = "CONFLICTS"
    """
    It would conflict with its parent.
    """

    UNKNOWN = "UNKNOWN"
    """
    One of the two references is missing, so the merge could not run.
    """


# %% the reports


@dataclass(frozen=True)
class StackReport:
    """
    The read-only reports the commands print about one derived stack.
    """

    stack: Stack
    """
    The stack reported on.
    """

    def print_status(self) -> None:
        """
        Print the whole stack: parent, state, checks, and drift versus the upstream.
        """
        configuration = self.stack.configuration
        upstream = f"{configuration.upstream_remote}/{configuration.upstream_base}"
        logger.info(f"Stack ({len(self.stack.branches)} branches) vs {upstream}\n")
        logger.info(StatusColumn.line(*StatusColumn))
        logger.info(StatusColumn.rule)
        for branch in self.stack.ordered():
            reference = configuration.reference_on_fork(branch.name)
            parent_reference = configuration.reference_on_fork(branch.parent)
            ahead = CurrentCheckout.commit_count(f"{parent_reference}..{reference}")
            behind_parent = CurrentCheckout.commit_count(
                f"{reference}..{parent_reference}"
            )
            logger.info(
                StatusColumn.line(
                    branch.name,
                    branch.status,
                    branch.check_state,
                    f"#{branch.pull_request_number}",
                    f"+{ahead}/-{behind_parent} "
                    f"({branch.strategy} onto {branch.parent})",
                    CurrentCheckout.commit_count(f"{reference}..{upstream}"),
                )
            )

    def print_check(self) -> None:
        """
        Print whether each branch would merge cleanly onto its parent right now.
        """
        configuration = self.stack.configuration
        logger.info(
            "Integration probe - would each branch merge cleanly onto its parent "
            "right now?\n"
        )
        for branch in self.stack.ordered():
            reference = configuration.reference_on_fork(branch.name)
            parent_reference = configuration.reference_on_fork(branch.parent)
            status = CurrentCheckout.run(
                GitArgument.MERGE_TREE,
                GitArgument.WRITE_TREE,
                parent_reference,
                reference,
            ).returncode
            match status:
                case MergeTreeStatus.CLEAN:
                    verdict = f"{MergeVerdict.CLEAN}"
                case MergeTreeStatus.CONFLICTED:
                    verdict = f"{MergeVerdict.CONFLICTS} onto {branch.parent}"
                case _:
                    verdict = (
                        f"{MergeVerdict.UNKNOWN} (reference missing: "
                        f"{parent_reference} / {reference})"
                    )
            logger.info(f"  {branch.name:<{StatusColumn.BRANCH.width + 2}} {verdict}")

    def print_next(self) -> None:
        """
        Print which branches are ready to submit to the upstream next.
        """
        promotable = self.stack.promotion_order()
        withheld = [
            branch
            for branch in self.stack.branches
            if branch.status == BranchStatus.READY
            and self.stack.parent_landed(branch)
            and self.stack.needs_resolution(branch)
        ]
        if promotable:
            self._print_promotable(promotable)
            self._print_withheld(withheld)
            self._print_withheld_by_checks()
            return
        waiting_on_a_parent = [
            branch
            for branch in self.stack.branches
            if branch.status == BranchStatus.READY
            and not self.stack.parent_landed(branch)
        ]
        drafts = [
            branch
            for branch in self.stack.ordered()
            if branch.status == BranchStatus.DRAFT
        ]
        logger.info("Nothing to promote - no branch is both approved and unblocked.")
        if waiting_on_a_parent:
            logger.info(
                "  Approved but waiting on a parent to land: "
                f"{', '.join(branch.name for branch in waiting_on_a_parent)}"
            )
        self._print_withheld(withheld)
        self._print_withheld_by_checks()
        if drafts:
            logger.info(
                "  The gate: self-review a fork pull request, then un-draft it. "
                f"Draft candidates: {drafts[0].name}"
            )

    def _print_promotable(self, promotable: list[Branch]) -> None:
        """
        :param promotable: The branches to submit upstream next, in dependency order.
        """
        plural = "es" if len(promotable) != 1 else ""
        logger.info(
            f"NEXT to submit to {self.stack.configuration.upstream_remote} "
            f"({len(promotable)} branch{plural}):"
        )
        for branch in promotable:
            logger.info(
                f"  {branch.name} (pull request #{branch.pull_request_number}) - "
                f"approved, parent '{branch.parent}' landed"
            )

    def _print_withheld(self, withheld: list[Branch]) -> None:
        """
        :param withheld: The promotable branches withheld pending conflict resolution.
        """
        if not withheld:
            return
        logger.info(
            f"  Withheld ({self.stack.configuration.needs_resolution_label}): "
            f"{', '.join(branch.name for branch in withheld)}"
        )

    def _print_withheld_by_checks(self) -> None:
        """
        Print the approved branches withheld for their failed checks, with the verdict.
        """
        withheld = self.stack.withheld_by_failing_checks()
        if not withheld:
            return
        logger.info(
            "  Withheld for failing checks: "
            + ", ".join(f"{branch.name} ({branch.check_state})" for branch in withheld)
        )

    def print_next_porcelain(self) -> None:
        """
        Print machine-readable :meth:`print_next`: one ``name<TAB>pull request`` line
        per promotable branch.
        """
        for branch in self.stack.promotion_order():
            logger.info(f"{branch.name}\t{branch.pull_request_number}")

    def print_restack_plan(self) -> None:
        """
        Print the bottom-up restack plan as JSON, one object per not-yet-landed branch.
        """
        logger.info(
            json.dumps([asdict(entry) for entry in self.stack.restack_plan()], indent=2)
        )

    def print_reparents(self) -> None:
        """
        Print one ``branch<TAB>pull request<TAB>current base<TAB>target base`` line per
        orphaned child.
        """
        for reparent in self.stack.reparents():
            logger.info(
                f"{reparent.branch}\t{reparent.pull_request_number}\t"
                f"{reparent.current_base}\t{reparent.target_base}"
            )

    def print_landed(self) -> None:
        """
        Print one ``branch<TAB>pull request`` line per open pull request whose branch
        has landed.
        """
        for branch in self.stack.landed_branches():
            logger.info(f"{branch.name}\t{branch.pull_request_number}")
