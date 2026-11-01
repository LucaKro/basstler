"""
A fork pull request's description: what a pass reads out of it and writes into it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from basstler.maintenance.constants import DescriptionPattern, MaintenanceText
from basstler_domain_model.repository import (
    Repository,
)

# %% the upstream pull request a description links to


@dataclass(frozen=True)
class UpstreamPullRequestLink:
    """
    A hyperlink to one upstream pull request.
    """

    repository: Repository
    """
    The upstream repository the pull request is open on.
    """

    number: int
    """
    The upstream pull request's number.
    """

    @property
    def url(self) -> str:
        """
        The upstream pull request's page on GitHub.
        """
        return self.repository.pull_request_url(self.number)

    @property
    def markdown(self) -> str:
        """
        The Markdown hyperlink to it, titled with its ``owner/repo#number`` reference.
        """
        return f"[{self.repository.full_name}#{self.number}]({self.url})"


# %% the description


@dataclass(frozen=True)
class PullRequestDescription:
    """
    A pull request's description, with the session link a pass reads out of it and the
    promotion section it records in it.
    """

    text: str
    """
    The description as written, empty when the pull request has none.
    """

    @property
    def session_link(self) -> str | None:
        """
        The first session link the description names, or ``None`` when it names none.
        """
        return DescriptionPattern.SESSION_LINK.first_in(self.text)

    @property
    def prose(self) -> str:
        """
        The description as its author wrote it, without the sections a pass writes.
        """
        sections = (
            MaintenanceText.PROMOTION_HEADING,
            MaintenanceText.UPSTREAM_PULL_REQUEST_HEADING,
        )
        prose = self.text
        for heading in sections:
            prose = PullRequestDescription(prose).with_section(heading, None)
        return prose.strip()

    def with_promotion_link(self, url: str) -> str:
        """
        :param url: The link to record.
        :return: The description with *url* as its promotion section, replacing any
            section already there.
        """
        return self.with_section(MaintenanceText.PROMOTION_HEADING, url)

    @property
    def upstream_link(self) -> str | None:
        """
        The URL of the upstream pull request a pass linked under the upstream pull
        request heading, or ``None`` when the description has no such section.
        """
        return DescriptionPattern.LINK_TARGET.first_in(
            self.section(MaintenanceText.UPSTREAM_PULL_REQUEST_HEADING)
        )

    def with_upstream_link(self, link: UpstreamPullRequestLink | None) -> str:
        """
        :param link: The upstream pull request to link, ``None`` when there is none.
        :return: The description with *link* as its upstream pull request section,
            replacing any section already there, or without that section when *link*
            is ``None``.
        """
        return self.with_section(
            MaintenanceText.UPSTREAM_PULL_REQUEST_HEADING,
            None if link is None else link.markdown,
        )

    def section(self, heading: MaintenanceText) -> str | None:
        """
        :param heading: The heading the section opens with.
        :return: The text between *heading* and the next heading, ``None`` when the
            description has no such heading.
        """
        _, content, _ = self._partition(heading)
        return content

    def with_section(self, heading: MaintenanceText, content: str | None) -> str:
        """
        :param heading: The heading the section opens with.
        :param content: What the section holds, ``None`` to drop the section.
        :return: The description with that section written last, replacing any section
            under *heading* already there.
        """
        before, _, after = self._partition(heading)
        kept = "\n\n".join(part for part in (before.rstrip(), after.strip()) if part)
        if content is None:
            return f"{kept}\n"
        return f"{kept}\n\n{heading}\n\n{content}\n"

    def _partition(self, heading: MaintenanceText) -> tuple[str, str | None, str]:
        """
        :param heading: The heading the section opens with, matched only as a whole
            line.
        :return: The text before the section, the section's content (``None`` when the
            description has no such heading), and the text after it.
        """
        found = re.search(
            rf"^{re.escape(heading)}[ \t]*$", self.text, flags=re.MULTILINE
        )
        if found is None:
            return self.text, None, ""
        before, rest = self.text[: found.start()], self.text[found.end() :]
        following = DescriptionPattern.HEADING.value.search(rest)
        end = len(rest) if following is None else following.start()
        return before, rest[:end].strip(), rest[end:]
