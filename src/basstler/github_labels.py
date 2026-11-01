"""
The GitHub labels this tooling reads or applies, in one table.

The dashboard reads some of them, a maintenance pass writes one, and setup creates
all of them on a fork. One enum names them all, so a label one part recognizes cannot be
missing from the set setup creates.

Kept free of third-party imports other than :mod:`basstler_domain_model`, so the setup
script can read it before the dashboard's dependencies are installed.
"""

from __future__ import annotations

from enum import ReprEnum

from basstler_domain_model.github.labels import Label
from basstler_domain_model.repository import (
    Repository,
)


class GitHubLabel(Label, ReprEnum):
    """
    Every label this tooling reads or applies, and therefore every one a fork must
    carry, each with the description it is created with.

    A member is its own label name, so it compares equal to the label as GitHub returns
    it.
    """

    def __new__(cls, label: str, description: str) -> GitHubLabel:
        """
        Make a member that is its own label name and carries what the label means.

        :param label: The label's name, as GitHub stores it.
        :param description: What it means, which it is created with.
        :return: The member.
        """
        member = str.__new__(cls, label)
        member._value_ = label
        member.description = description
        return member

    MERGED = ("merged", "The changes landed even though GitHub never recorded a merge")
    """
    Read by the dashboard, which treats the label exactly like a real merge.
    """

    IN_REVIEW = ("in-review", "Under review")
    """
    Kept in step by a maintenance pass with the open upstream pull request opened from the
    branch.
    """

    BUG = ("bug", "A bug fix")
    """
    Applied by a session opening a bug-fix pull request, and shown as a dashboard chip.
    """

    PROMOTION_LINK_SENT = (
        "promotion-link-sent",
        "A maintenance pass has written this branch's upstream create-link",
    )
    """
    Applied by a maintenance pass to a fork pull request whose link it has built,
    so a later pass does not rebuild it.
    """

    def creation_command(self, repository: Repository) -> str:
        """
        The ``gh`` command that creates this label.

        :param repository: The repository to create it in.
        :return: The command, ready to paste.
        """
        return (
            f"gh label create {self.value} --repo {repository.full_name} "
            f'--description "{self.description}"'
        )
