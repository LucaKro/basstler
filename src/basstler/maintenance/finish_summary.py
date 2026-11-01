"""
The finish summary a whole pass prints: what it left for its developer, in markdown.

Its size is bounded whatever the size of the stack: every section lists at most a fixed
number of entries and every conflict a fixed number of files, and the rest is counted. The
whole report is written to a file beside it, and every conflict and failing check is
already in a comment on its pull request.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from basstler.workflow_labels import WorkflowLabel
from basstler.maintenance.check_overview import CheckOverview
from basstler.maintenance.constants import MaintenanceText
from basstler.maintenance.exceptions import MarkdownLinkTargetError
from basstler.maintenance.report import MaintenanceReport
from basstler.maintenance.restack_steps import BranchOutcome, RestackOutcome
from basstler.render_common import AllowedUrlScheme
from basstler.stack.model import Branch, BranchStatus, Stack
from basstler_domain_model.github.vocabulary import (
    RollupState,
)

# %% vocabulary


class FinishSummaryHeading(StrEnum):
    """
    The sections of the finish summary, in the order it prints them.
    """

    PENDING_LINKS = (
        f"Upstream pull requests waiting to be created - the link is under "
        f"{MaintenanceText.PROMOTION_HEADING} in each description"
    )
    """
    Every create-link not yet acted on, built this pass or an earlier one.
    """

    REPORTED = "Reported to their owners"
    """
    The branches whose owner was told, in a comment, that their branch needs them.
    """

    REPARENTS = "Reparents still to make"
    """
    The pull requests whose base has landed and still has to be retargeted.
    """

    FORK_BASE = "The fork's base"
    """
    What became of the fork's base branch.
    """

    LANDED = "Landed but still open"
    """
    The pull requests whose commits are already in the upstream base.
    """

    RESTACKED = "Restacked"
    """
    How many branches were published, and how many were already up to date.
    """

    UNPUBLISHED = "Left unpublished"
    """
    The branches the pass could not publish and told nobody about.
    """

    WITHHELD = "Withheld"
    """
    The branches still conflicted from an earlier pass, left untouched.
    """

    FAILING_CHECKS = "Withheld for failing checks"
    """
    The approved branches promotion would take but for their failed checks.
    """

    LINK_LABELS_CLEARED = "Spent link labels removed"
    """
    The branches whose link label was dropped because the link was acted on.
    """

    UPSTREAM_PULL_REQUEST_SYNCHRONIZATION = (
        "In-review labels and upstream links updated"
    )
    """
    The pull requests whose in-review label or upstream link was written to match the
    upstream.
    """

    SKIPPED = "Skipped"
    """
    The pull requests whose head the pass cannot maintain.
    """


class PassCount(StrEnum):
    """
    What the line under the status counts, each worded as it follows its number.
    """

    PUSHED = "pushed"
    """
    Branches the restack published.
    """

    UP_TO_DATE = "up to date"
    """
    Branches that already held their parent's tip.
    """

    LEFT_FOR_SOMEBODY = "left for somebody"
    """
    Branches the pass could not publish.
    """

    LINKS_RECORDED = "upstream links recorded"
    """
    Branches whose upstream link was recorded this pass.
    """

    FAILING_CHECKS = "failing checks"
    """
    Open branches whose checks failed.
    """

    CHECKS_RUNNING = "checks still running"
    """
    Open branches whose checks have not finished.
    """

    REPARENTS_LEFT = "reparents left"
    """
    Pull requests whose base still has to be retargeted.
    """


# %% links


@dataclass(frozen=True)
class MarkdownLink:
    """
    A markdown link whose target is a web URL or an anchor on the same page.
    """

    label: str
    """
    The text the link shows.
    """

    target: str
    """
    What the link points at.
    """

    def __post_init__(self) -> None:
        """
        :raises MarkdownLinkTargetError: If the target is neither a web URL nor an
            anchor, or holds whitespace, which ends a markdown link target early.
        """
        if any(character.isspace() for character in self.target):
            raise MarkdownLinkTargetError(self.label, self.target)
        if self.target.startswith("#") and len(self.target) > 1:
            return
        parts = urlsplit(self.target)
        if parts.scheme not in set(AllowedUrlScheme) or not parts.netloc:
            raise MarkdownLinkTargetError(self.label, self.target)

    def __str__(self) -> str:
        """:return: The link, as markdown."""
        return f"[{self.label}]({self.target})"


# %% the summary


@dataclass(frozen=True)
class FinishSummary:
    """
    One pass's report, written for the developer who reads it once.
    """

    report: MaintenanceReport
    """
    What the pass did.
    """

    stack: Stack
    """
    The stack the pass ran over, read for each pull request's number, title and session.
    """

    full_report: Path | None = None
    """
    The file holding the whole report, named under the summary when there is one.
    """

    entries_per_section: int = 5
    """
    How many entries a section lists before counting the rest.
    """

    paths_per_branch: int = 2
    """
    How many conflicting files an entry names before counting the rest.
    """

    @staticmethod
    def heading_line(heading: str) -> str:
        """
        :param heading: The section's heading.
        :return: The markdown line opening that section.
        """
        return f"### {heading}"

    @functools.cached_property
    def branches_by_name(self) -> dict[str, Branch]:
        """
        The stack's branches, by the name a report entry refers to them by.
        """
        return {branch.name: branch for branch in self.stack.branches}

    def as_markdown(self) -> str:
        """
        :return: The status, then every section that has something in it, in the order
            of :class:`FinishSummaryHeading`.
        """
        sections = {
            FinishSummaryHeading.PENDING_LINKS: self.capped(self.pending_links()),
            FinishSummaryHeading.REPORTED: self.capped(self.reported()),
            FinishSummaryHeading.REPARENTS: self.capped(self.reparents()),
            FinishSummaryHeading.FORK_BASE: self.fork_base(),
            FinishSummaryHeading.LANDED: self.capped(self.landed()),
            FinishSummaryHeading.RESTACKED: self.restacked(),
            FinishSummaryHeading.UNPUBLISHED: self.capped(self.unpublished()),
            FinishSummaryHeading.WITHHELD: self.branch_list(RestackOutcome.WITHHELD),
            FinishSummaryHeading.FAILING_CHECKS: self.capped(self.failing_checks()),
            FinishSummaryHeading.LINK_LABELS_CLEARED: self.code_list(
                self.report.promotion_labels_cleared
            ),
            FinishSummaryHeading.UPSTREAM_PULL_REQUEST_SYNCHRONIZATION: self.capped(
                self.upstream_pull_request_synchronization_changes()
            ),
            FinishSummaryHeading.SKIPPED: self.capped(self.skipped()),
        }
        lines = [
            f"**Maintenance pass:** {self.report.exit_code.name_for_a_caller}",
            self.counts(),
        ]
        for heading, entries in sections.items():
            if entries:
                lines += ["", self.heading_line(heading), "", *entries]
        if self.full_report is not None:
            lines += ["", f"Full report: `{self.full_report}`"]
        return "\n".join(lines)

    def counts(self) -> str:
        """
        :return: How many branches each part of the pass touched, naming only the parts
            that touched any.
        """
        report = self.report
        outcomes = [outcome.outcome for outcome in report.restacked]
        checks = CheckOverview(self.stack)
        counted = {
            PassCount.PUSHED: outcomes.count(RestackOutcome.PUSHED),
            PassCount.UP_TO_DATE: outcomes.count(RestackOutcome.UP_TO_DATE),
            PassCount.LEFT_FOR_SOMEBODY: len(report.branches_left_unpublished),
            PassCount.LINKS_RECORDED: len(report.promoted),
            PassCount.FAILING_CHECKS: len(
                checks.branches_in(RollupState.FAILURE)
                + checks.branches_in(RollupState.ERROR)
            ),
            PassCount.CHECKS_RUNNING: len(
                checks.branches_in(RollupState.PENDING)
                + checks.branches_in(RollupState.EXPECTED)
            ),
            PassCount.REPARENTS_LEFT: len(report.reparents),
        }
        named = [f"{count} {what}" for what, count in counted.items() if count]
        return ", ".join(named) or "nothing to do"

    def capped(self, entries: list[str]) -> list[str]:
        """
        :param entries: A section's entries.
        :return: The first :attr:`entries_per_section` of them, and a count of the rest.
        """
        hidden = len(entries) - self.entries_per_section
        if hidden <= 0:
            return entries
        return [*entries[: self.entries_per_section], f"- and {hidden} more"]

    def pull_request_link(self, number: int) -> str:
        """
        :param number: A fork pull request.
        :return: A markdown link to it, labelled by its number.
        """
        url = self.stack.configuration.fork_repository.pull_request_url(number)
        return str(MarkdownLink(f"#{number}", url))

    def branch_line(self, branch: Branch) -> str:
        """
        :param branch: A branch of the stack.
        :return: Its pull request, title and name, as an entry opens.
        """
        return (
            f"- {self.pull_request_link(branch.pull_request_number)} "
            f"{branch.title or branch.name} - `{branch.name}`"
        )

    def pending_links(self) -> list[str]:
        """
        :return: One entry per create-link built this pass, then one per link an
            earlier pass built that is not yet acted on, without the link itself: each
            pull request's description carries its own.
        """
        promoted = [promotion.branch for promotion in self.report.promoted]
        earlier = [
            branch.name
            for branch in self.stack.maintained_branches()
            if branch.name not in promoted
            and WorkflowLabel.PROMOTION_LINK_SENT in branch.labels
            and branch.status not in {BranchStatus.IN_REVIEW, BranchStatus.MERGED}
        ]
        return [
            self.branch_line(self.branches_by_name[name])
            for name in [*promoted, *earlier]
        ]

    def reported(self) -> list[str]:
        """
        :return: One entry per branch whose owner was told about it in a comment.
        """
        entries = []
        for outcome in self.report.restacked:
            if outcome.reported_at is None:
                continue
            branch = self.branches_by_name[outcome.branch]
            addressed = branch.session or "the description names no session"
            entries.append(
                f"{self.branch_line(branch)}: {self.outcome_detail(outcome)} - "
                f"addressed {addressed} - {MarkdownLink('comment', outcome.reported_at)}"
            )
        return entries

    def reparents(self) -> list[str]:
        """
        :return: One entry per pull request still to retarget, from base to base.
        """
        return [
            f"- {self.pull_request_link(reparent.pull_request_number)} "
            f"`{reparent.branch}`: `{reparent.current_base}` -> `{reparent.target_base}`"
            for reparent in self.report.reparents
        ]

    def fork_base(self) -> list[str]:
        """
        :return: What became of the fork's base, and why when it was refused.
        """
        report = self.report.fast_forward
        if report is None:
            return []
        lines = [f"`{report.fork_reference}`: {report.outcome} at `{report.commit}`"]
        return lines + ([report.explanation] if report.explanation else [])

    def landed(self) -> list[str]:
        """
        :return: One entry per landed branch whose pull request is still open.
        """
        return [
            self.branch_line(self.branches_by_name[name]) for name in self.report.landed
        ]

    def restacked(self) -> list[str]:
        """
        :return: How many branches were published and how many were already up to
            date, empty when the restack touched none.
        """
        outcomes = [outcome.outcome for outcome in self.report.restacked]
        pushed = outcomes.count(RestackOutcome.PUSHED)
        up_to_date = outcomes.count(RestackOutcome.UP_TO_DATE)
        if not pushed and not up_to_date:
            return []
        return [f"{pushed} pushed, {up_to_date} already up to date"]

    def unpublished(self) -> list[str]:
        """
        :return: One entry per branch left unpublished that no comment told anyone
            about.
        """
        return [
            f"{self.branch_line(self.branches_by_name[outcome.branch])}: "
            f"{outcome.outcome} - {self.outcome_detail(outcome)}"
            for outcome in self.report.branches_left_unpublished
            if outcome.reported_at is None
            and outcome.outcome is not RestackOutcome.WITHHELD
        ]

    def outcome_detail(self, outcome: BranchOutcome) -> str:
        """
        :param outcome: What became of a branch the pass could not publish.
        :return: The files, refusals or explanation its owner, or the pass, acts on.
        """
        if outcome.conflicting_paths:
            paths = outcome.conflicting_paths
            named = ", ".join(f"`{path}`" for path in paths[: self.paths_per_branch])
            hidden = len(paths) - self.paths_per_branch
            return f"conflicts in {named}" + (
                f" and {hidden} more" if hidden > 0 else ""
            )
        if outcome.refusals:
            return ", ".join(outcome.refusals)
        if outcome.outcome is RestackOutcome.INTEGRATION_FAILED:
            return f"the pass's own environment, not the branch: {outcome.explanation}"
        return outcome.explanation or ""

    def failing_checks(self) -> list[str]:
        """
        :return: One entry per approved branch withheld for its failed checks, with the
            verdict over them.
        """
        return [
            f"{self.branch_line(branch)}: {branch.check_state}"
            for branch in self.stack.withheld_by_failing_checks()
        ]

    def upstream_pull_request_synchronization_changes(self) -> list[str]:
        """
        :return: One entry per in-review label or upstream link written.
        """
        return [
            f"{self.branch_line(self.branches_by_name[change.branch])}: {change.action}"
            for change in self.report.upstream_pull_request_synchronization_changes
        ]

    def branch_list(self, outcome: RestackOutcome) -> list[str]:
        """
        :param outcome: The outcome to collect.
        :return: Every branch that had it, on one line.
        """
        return self.code_list(
            [
                entry.branch
                for entry in self.report.restacked
                if entry.outcome is outcome
            ]
        )

    def code_list(self, names: Sequence[str]) -> list[str]:
        """
        :param names: Branch names.
        :return: The first :attr:`entries_per_section` of them on one line with a count
            of the rest, empty when there are none.
        """
        if not names:
            return []
        line = ", ".join(f"`{name}`" for name in names[: self.entries_per_section])
        hidden = len(names) - self.entries_per_section
        return [line + (f" and {hidden} more" if hidden > 0 else "")]

    def skipped(self) -> list[str]:
        """
        :return: One entry per pull request the pass left alone, with why and where its
            head lives.
        """
        entries = []
        for skipped in self.report.skipped:
            pull_request = skipped.pull_request
            where = pull_request.head_repository or "deleted"
            entries.append(
                f"- {self.pull_request_link(pull_request.number)} "
                f"`{pull_request.head}`: {skipped.reason} ({where})"
            )
        return entries
