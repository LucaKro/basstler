"""
The maintenance executor's hand-maintained values.

Everything here is a decision somebody made and may change again - the heading a promotion
link is written under, the label marking it sent. They are gathered in one module, each
in the enum of the values it is one of, so changing one is an edit rather than a search
through whichever module happens to read it.

Values a program derives for itself belong beside the code that derives them, not here.
"""

from __future__ import annotations

import re
from enum import Enum, StrEnum


class DescriptionPattern(Enum):
    """
    What the tooling looks for in a pull request description.
    """

    SESSION_LINK = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9_-]+")
    """
    The session link a pull request description carries, which is the only channel for
    telling a branch's owner that their branch needs them.
    """

    HEADING = re.compile(r"^#{1,6} ", re.MULTILINE)
    """
    The start of a Markdown heading line, which ends the section before it.
    """

    LINK_TARGET = re.compile(r"(?<=\]\()[^)\s]+(?=\))")
    """
    The URL a Markdown hyperlink points at.
    """

    def first_in(self, text: str | None) -> str | None:
        """
        :param text: The text to search, which may be absent.
        :return: The first match, or ``None`` if the text holds none.
        """
        if not text:
            return None
        found = self.value.search(text)
        return found.group(0) if found else None


class MaintenanceText(StrEnum):
    """
    Text a maintenance pass writes onto GitHub, and later finds again by.
    """

    CONFLICT_COMMENT_PREFIX = "🔴 MAINTENANCE - NEEDS RESOLUTION:"
    """
    Opens the comment a conflict is reported in, so the branch's owner can find every one
    of them at a glance.
    """

    PROMOTION_HEADING = "## Promote"
    """
    Heading the compare-and-create link is written under, in the fork pull request's own
    description - the summary that carried it is delivered once and then gone, and the
    description is still there a week later.
    """

    UPSTREAM_PULL_REQUEST_HEADING = "## Upstream pull request"
    """
    Heading the link to the open upstream pull request opened from the branch is written
    under, in the fork pull request's own description.
    """

    FULL_DETAIL = "Full detail:"
    """
    Opens the line an upstream pull request's description ends with, linking back to the
    fork pull request it was promoted from.
    """


class CommitTrailer(StrEnum):
    """
    The trailer keys a maintenance pass writes into the commits it makes, and later
    recognises those commits by.
    """

    RESTACKED_PARENT = "Restacked-parent"
    """
    Names the parent a restack merged in, marking the merge as the restack's own rather
    than one a person made.
    """

    RESTACKED_PARENT_COMMIT = "Restacked-parent-commit"
    """
    The commit of the parent a restack merged in, which a restack's own merge records as
    its second parent.
    """
