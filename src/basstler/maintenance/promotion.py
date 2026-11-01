"""
Recording the upstream link on every branch ready to be promoted.

The upstream pull request is not opened here - the credential has no write access there.
What is written is the link that opens it prefilled, into the fork pull request's own
description, plus the label that stops a later pass rebuilding it.

What the upstream reviewer reads is a reading of the diff, so it is written by the caller
as a :class:`PromotionSummary`; the title, the link back to the fork pull request and the
length budget stay here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from basstler.github_labels import GitHubLabel
from basstler_domain_model.github.pull_request_fields import (
    PullRequestField,
)
from basstler.maintenance.constants import MaintenanceText
from basstler.maintenance.description import PullRequestDescription
from basstler_domain_model.github.repository_client import (
    ForkPullRequests,
    PullRequestWriter,
)
from basstler_domain_model.github.labels import (
    LabelWrite,
)
from basstler.stack.model import BranchStatus, Stack
from basstler.stack.promotion_link import PromotionLink

# %% the summaries a caller writes


class PromotionSummaryField(StrEnum):
    """
    The keys one summary is written under in the summaries document.
    """

    POINTS = "points"
    """
    The points the upstream pull request's description opens with.
    """

    TITLE = "title"
    """
    The title to open the upstream pull request with instead of the fork pull request's
    own.
    """


@dataclass(frozen=True)
class PromotionSummary:
    """
    What one upstream pull request opens with, as far as anybody has written it.

    Both halves are optional: with neither, the upstream pull request opens with the fork
    pull request's title and the link back to it.
    """

    points: tuple[str, ...] = ()
    """
    The points for the upstream reviewer, one bullet each; empty when nobody wrote them.
    """

    title: str | None = None
    """
    The upstream title, or ``None`` to keep the fork pull request's own.
    """

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> PromotionSummary:
        """
        :param data: One entry of the summaries document.
        :return: The summary it holds.
        """
        return cls(
            points=tuple(data.get(PromotionSummaryField.POINTS, ())),
            title=data.get(PromotionSummaryField.TITLE),
        )

    def upstream_description(self, fork_pull_request_url: str) -> str:
        """
        Build what the upstream pull request's description opens with.

        :param fork_pull_request_url: The fork pull request this summary was written for.
        :return: The points as a markdown bullet list where there are any, then the link
            back to the fork pull request.
        """
        link_back = f"{MaintenanceText.FULL_DETAIL} {fork_pull_request_url}"
        if not self.points:
            return link_back
        bullet_list = "\n".join(f"- {point}" for point in self.points)
        return f"{bullet_list}\n\n{link_back}"


@dataclass(frozen=True)
class PromotionSummaries:
    """
    The summaries written for one pass, by the fork pull request each belongs to.
    """

    by_pull_request_number: Mapping[int, PromotionSummary] = field(default_factory=dict)
    """
    Each summary, keyed by its fork pull request's number.
    """

    @classmethod
    def read_from(cls, path: Path | None) -> PromotionSummaries:
        """
        :param path: The summaries document, or ``None`` when the caller wrote none.
        :return: The summaries it holds, none when there is no document.
        """
        if path is None:
            return cls()
        return cls.from_json(json.loads(path.read_text()))

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> PromotionSummaries:
        """
        :param data: The summaries document, keyed by fork pull request number.
        :return: The summaries it holds.
        """
        return cls(
            {
                int(number): PromotionSummary.from_json(entry)
                for number, entry in data.items()
            }
        )

    def for_pull_request(self, number: int) -> PromotionSummary:
        """
        :param number: A fork pull request.
        :return: Its summary, empty when nobody wrote one.
        """
        return self.by_pull_request_number.get(number, PromotionSummary())


# %% recording the link


@dataclass(frozen=True)
class Promotion:
    """
    One branch's compare-and-create link, and where it was recorded.
    """

    branch: str
    """
    The branch promoted.
    """

    pull_request_number: int
    """
    Its fork pull request.
    """

    url: str
    """
    The compare-and-create link opening the upstream pull request.
    """

    body_was_truncated: bool
    """
    Whether the prefilled description had to be shortened to fit the URL limit.
    """


def promote(
    stack: Stack, fork: ForkPullRequests, summaries: PromotionSummaries
) -> list[Promotion]:
    """
    Build and record the upstream link for every branch ready to be promoted.

    The upstream pull request is not opened here - the app has no write access there, so
    that call fails every time. What is written is the link that opens it prefilled, into
    the fork pull request's own description, plus the label stopping a later pass
    rebuilding it. The upstream pull request does not exist until the developer clicks
    Create.

    Both the decision and the label write read the labels the branch carries *now*, not
    the ones the board was exported with: the restack runs between those two moments and
    withholds a branch by labelling it, so a snapshot is already out of date here.

    :param stack: The derived stack.
    :param fork: The fork to read descriptions from and write links back to.
    :param summaries: What each upstream pull request opens with, where anybody wrote it.
    :return: One entry per branch promoted, in dependency order.
    """
    promoted: list[Promotion] = []
    withheld = stack.configuration.needs_resolution_label
    for branch in stack.awaiting_promotion_link():
        number = branch.pull_request_number
        pull_request = fork.pull_request(number)
        labels = PullRequestField.LABELS.read(pull_request, number)
        if GitHubLabel.PROMOTION_LINK_SENT in labels or withheld in labels:
            continue
        description = PullRequestDescription(
            str(PullRequestField.BODY.read(pull_request, number) or "")
        )
        summary = summaries.for_pull_request(number)
        link = PromotionLink.build(
            stack.configuration,
            branch.name,
            summary.title
            or str(PullRequestField.TITLE.read(pull_request, number) or branch.name),
            summary.upstream_description(
                stack.configuration.fork_repository.pull_request_url(number)
            ),
        )
        fork.set_description(
            number,
            description.with_promotion_link(link.url),
        )
        fork.replace_labels(
            number,
            LabelWrite.replacing(
                labels, added=[GitHubLabel.PROMOTION_LINK_SENT]
            ).labels,
        )
        promoted.append(
            Promotion(
                branch=branch.name,
                pull_request_number=branch.pull_request_number,
                url=link.url,
                body_was_truncated=link.body_was_truncated,
            )
        )
    return promoted


def clear_spent_promotion_labels(
    stack: Stack, fork: PullRequestWriter
) -> tuple[str, ...]:
    """
    Drop the link label from every branch whose link has already been acted on.

    :param stack: The derived stack.
    :param fork: The fork to write to.
    :return: The branches whose label was cleared.
    """
    spent = [
        branch
        for branch in stack.maintained_branches()
        if GitHubLabel.PROMOTION_LINK_SENT in branch.labels
        and branch.status in {BranchStatus.IN_REVIEW, BranchStatus.MERGED}
    ]
    for branch in spent:
        fork.replace_labels(
            branch.pull_request_number,
            LabelWrite.replacing(
                branch.labels, removed=[GitHubLabel.PROMOTION_LINK_SENT]
            ).labels,
        )
    return tuple(branch.name for branch in spent)
