"""
Keeping each fork pull request's record of its upstream review - the in-review label and
the link to the upstream pull request - in step with the upstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from basstler_domain_model.github.pull_request_fields import (
    PullRequestField,
)
from basstler.maintenance.description import (
    PullRequestDescription,
    UpstreamPullRequestLink,
)
from basstler_domain_model.github.repository_client import (
    ForkPullRequests,
)
from basstler_domain_model.github.labels import (
    LabelWrite,
)
from basstler.stack.model import Branch, BranchStatus, Stack


class UpstreamPullRequestSynchronizationAction(StrEnum):
    """
    What was written to a fork pull request to bring its record of the upstream review
    in step.
    """

    LABEL_ADDED = "in-review label added"
    """
    The in-review label was added, because an upstream pull request opened from the
    branch is open.
    """

    LABEL_REMOVED = "in-review label removed"
    """
    The in-review label was removed, because no upstream pull request opened from the
    branch is open.
    """

    LINK_WRITTEN = "upstream link written"
    """
    The description now links the open upstream pull request opened from the branch.
    """

    LINK_REMOVED = "upstream link removed"
    """
    The description's link to an upstream pull request was removed, because none opened
    from the branch is open.
    """


@dataclass(frozen=True)
class UpstreamPullRequestSynchronizationChange:
    """
    One write bringing a fork pull request's record of its upstream review in step.
    """

    branch: str
    """
    The branch whose pull request was written.
    """

    pull_request_number: int
    """
    Its fork pull request.
    """

    action: UpstreamPullRequestSynchronizationAction
    """
    What was written.
    """


@dataclass(frozen=True)
class UpstreamPullRequestSynchronization:
    """
    The in-review label and the upstream pull request link on each fork pull request,
    kept in step with the open upstream pull request opened from its branch.
    """

    stack: Stack
    """
    The derived stack, whose board snapshot names each branch's upstream pull request.
    """

    fork: ForkPullRequests
    """
    The fork to read pull requests from and write them back to.
    """

    def synchronize(self) -> list[UpstreamPullRequestSynchronizationChange]:
        """
        Bring the label and the link of every branch not yet landed in step with its
        upstream pull request.

        Each write keeps what the pull request carries *now*, not what the board was
        exported with.

        :return: One entry per write, in dependency order.
        """
        changes: list[UpstreamPullRequestSynchronizationChange] = []
        for branch in self.stack.maintained_branches():
            if branch.status is BranchStatus.MERGED:
                continue
            link = self.upstream_link_of(branch)
            if self.label_in_step(branch.labels, link) and self.link_in_step(
                branch.upstream_link, link
            ):
                continue
            changes.extend(self.write(branch, link))
        return changes

    def upstream_link_of(self, branch: Branch) -> UpstreamPullRequestLink | None:
        """
        :param branch: A branch of the stack.
        :return: The link to the open upstream pull request opened from it, ``None``
            when none is open.
        """
        if branch.upstream_pull_request is None:
            return None
        return UpstreamPullRequestLink(
            self.stack.configuration.upstream_repository, branch.upstream_pull_request
        )

    def label_in_step(
        self, labels: list[str], link: UpstreamPullRequestLink | None
    ) -> bool:
        """
        :param labels: The labels a pull request carries.
        :param link: The upstream pull request it should link.
        :return: Whether it carries the in-review label exactly when *link* exists.
        """
        return (self.stack.configuration.in_review_label in labels) == (
            link is not None
        )

    @staticmethod
    def link_in_step(
        linked_url: str | None, link: UpstreamPullRequestLink | None
    ) -> bool:
        """
        :param linked_url: The upstream pull request URL a description links.
        :param link: The upstream pull request it should link.
        :return: Whether the description links exactly *link*.
        """
        return linked_url == (None if link is None else link.url)

    def write(
        self, branch: Branch, link: UpstreamPullRequestLink | None
    ) -> list[UpstreamPullRequestSynchronizationChange]:
        """
        Write the label and the link that disagree with what *branch*'s pull request
        carries now.

        :param branch: The branch whose pull request is written.
        :param link: The upstream pull request it should link.
        :return: The writes made.
        """
        number = branch.pull_request_number
        current = self.fork.pull_request(number)
        labels = PullRequestField.LABELS.read(current, number)
        description = PullRequestDescription(
            str(PullRequestField.BODY.read(current, number) or "")
        )
        actions = [
            action
            for action in (
                self.write_label(number, labels, link),
                self.write_link(number, description, link),
            )
            if action is not None
        ]
        return [
            UpstreamPullRequestSynchronizationChange(branch.name, number, action)
            for action in actions
        ]

    def write_label(
        self, number: int, labels: list[str], link: UpstreamPullRequestLink | None
    ) -> UpstreamPullRequestSynchronizationAction | None:
        """
        :param number: The fork pull request to write.
        :param labels: The labels it carries now.
        :param link: The upstream pull request it should link.
        :return: What was written, ``None`` when its label was already in step.
        """
        if self.label_in_step(labels, link):
            return None
        label = self.stack.configuration.in_review_label
        if link is None:
            write = LabelWrite.replacing(labels, removed=[label])
            action = UpstreamPullRequestSynchronizationAction.LABEL_REMOVED
        else:
            write = LabelWrite.replacing(labels, added=[label])
            action = UpstreamPullRequestSynchronizationAction.LABEL_ADDED
        self.fork.replace_labels(number, write.labels)
        return action

    def write_link(
        self,
        number: int,
        description: PullRequestDescription,
        link: UpstreamPullRequestLink | None,
    ) -> UpstreamPullRequestSynchronizationAction | None:
        """
        :param number: The fork pull request to write.
        :param description: The description it carries now.
        :param link: The upstream pull request it should link.
        :return: What was written, ``None`` when its link was already in step.
        """
        if self.link_in_step(description.upstream_link, link):
            return None
        self.fork.set_description(number, description.with_upstream_link(link))
        if link is None:
            return UpstreamPullRequestSynchronizationAction.LINK_REMOVED
        return UpstreamPullRequestSynchronizationAction.LINK_WRITTEN
