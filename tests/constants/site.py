"""
The sample plans and repositories the plan-site suites build from.
"""

from __future__ import annotations

from enum import Enum, StrEnum
from pathlib import Path

from basstler_domain_model.repository import (
    Repository,
)

from .paths import DatasetLocation


class SitePlan(StrEnum):
    """
    The plan directories :attr:`DatasetLocation.SITE_PLANS` holds, each the plan's id.
    """

    BROKEN = "broken-plan"
    """
    A plan whose manifest fails validation, so it is never published.
    """

    FIRST = "first-plan"
    """
    A plan whose items have pull requests in two repositories, and a tracking issue.
    """

    SECOND = "second-plan"
    """
    A plan with no pull requests of its own, whose one item depends on an item of
    :attr:`FIRST`.
    """

    @property
    def directory(self) -> Path:
        """
        The plan's directory in the dataset.
        """
        return DatasetLocation.SITE_PLANS / self


class SiteRepository(Enum):
    """
    The repositories the plan site suites list in a hub file.
    """

    NOTES = Repository("owner", "notes")
    """
    The repository whose notes the dataset's plans stand in for.
    """

    MISSING = Repository("owner", "missing")
    """
    A listed repository with no notes branch to fetch.
    """


class SiteSampleText(StrEnum):
    """
    The values the plan site suites build a site with.
    """

    SITE_URL = "https://owner.github.io/site/"
    """
    The address the site under test is served from.
    """

    TOKEN = "token"
    """
    The credential a recorded GitHub client is handed, and never checks.
    """
