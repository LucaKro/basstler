"""
The one-click link that opens a fork branch's pull request on the upstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.stack.exceptions import PromotionLinkTooLongError
from basstler.stack.settings import Configuration
from basstler.standard_streams import StandardStreamHandler

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command prints through.
"""

# %% promotion links


class PromotionLinkParameter(StrEnum):
    """The query parameters of a compare-and-create link."""

    EXPAND = "expand"
    """Opens the pull request form rather than only the comparison."""

    TITLE = "title"
    """The title to prefill."""

    BODY = "body"
    """The description to prefill."""


@dataclass(frozen=True)
class PromotionLink:
    """A one-click compare-and-create link opening a fork branch's pull request upstream.

    The prefill travels in the query string, so an unencoded character truncates it and an
    oversized one is discarded whole by the server - both silently.
    """

    @classproperty
    def url_character_limit(cls) -> int:
        """Longest URL to build, the conventional 8 KiB request-line limit servers accept."""
        return 8192

    @classproperty
    def truncation_marker(cls) -> str:
        """Appended to a body that had to be shortened, so the cut is visible in the prefill."""
        return "..."

    url: str
    """The compare-and-create URL, ready to open."""

    body_was_truncated: bool
    """Whether the body had to be shortened to fit :attr:`url_character_limit`."""

    @classmethod
    def build(
        cls, configuration: Configuration, branch: str, title: str, body: str
    ) -> PromotionLink:
        """Build the link promoting a fork branch to the upstream base.

        :param configuration: The resolved configuration naming both repositories.
        :param branch: The fork branch to promote.
        :param title: Title to prefill.
        :param body: Description to prefill, shortened if the URL would not otherwise fit.
        :return: The link, flagged if the body was shortened.
        """

        def url_for(text: str) -> str:
            return (
                f"{configuration.upstream_repository.url}/compare/"
                f"{configuration.upstream_base}..."
                f"{configuration.fork_repository.owner}:{branch}"
                f"?{PromotionLinkParameter.EXPAND}=1"
                f"&{PromotionLinkParameter.TITLE}={quote(title)}"
                f"&{PromotionLinkParameter.BODY}={quote(text)}"
            )

        if len(url_for(body)) <= cls.url_character_limit:
            return cls(url_for(body), body_was_truncated=False)
        if len(url_for(cls.truncation_marker)) > cls.url_character_limit:
            raise PromotionLinkTooLongError(
                branch, len(url_for("")), cls.url_character_limit
            )
        shortest, longest = 0, len(body)
        while shortest < longest:
            midpoint = (shortest + longest + 1) // 2
            candidate = url_for(body[:midpoint] + cls.truncation_marker)
            shortest, longest = (
                (midpoint, longest)
                if len(candidate) <= cls.url_character_limit
                else (shortest, midpoint - 1)
            )
        return cls(
            url_for(body[:shortest] + cls.truncation_marker), body_was_truncated=True
        )

    def print_url(self) -> None:
        """Print the compare-and-create URL, reporting on stderr if the body was shortened."""
        logger.info(self.url)
        if self.body_was_truncated:
            logger.error("the description was shortened to fit the URL length limit")
