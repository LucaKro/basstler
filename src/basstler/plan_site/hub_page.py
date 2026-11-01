"""
Render the plan hub: one card per registered repository, opening its own index.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from basstler.dashboard.index_page import PlanSummary, ProgressCard
from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.render_common import TemplatePage


@dataclass(kw_only=True)
class RepositorySummary(ProgressCard):
    """
    One repository's card on the hub, counting the items of every one of its plans.
    """

    name: str
    """
    The repository, as ``owner/repo``.
    """

    index_url: str | None
    """
    The repository's own index page, or ``None`` when its notes could not be read.
    """

    plans: list[PlanSummary] = field(default_factory=list)
    """
    The repository's plans.
    """

    error: str | None = None
    """
    Why the repository's notes could not be read, if they could not.
    """

    @classmethod
    def over_plans(
        cls, name: str, index_url: str, plans: list[PlanSummary]
    ) -> RepositorySummary:
        """
        :param name: The repository, as ``owner/repo``.
        :param index_url: The repository's own index page.
        :param plans: The repository's plans.
        :return: The repository's card, counting every plan's items and pull requests.
        """
        return cls(
            name=name,
            index_url=index_url,
            plans=plans,
            **cls.counts_over(plans),
        )

    @classmethod
    def unreadable(cls, name: str, error: str) -> RepositorySummary:
        """
        :param name: The repository, as ``owner/repo``.
        :param error: Why its notes could not be read.
        :return: The repository's card, with no plans and no index to open.
        """
        return cls(name=name, index_url=None, error=error, done=0, total=0)

    @property
    def progress_label(self) -> str:
        """
        How many plans the repository has, and how many of their items are done.
        """
        return f"{len(self.plans)} plans · {self.done} / {self.total} items done"


@dataclass(kw_only=True)
class SiteSummary(ProgressCard):
    """
    The whole site at a glance: every repository's card, counting all their items.
    """

    repositories: list[RepositorySummary]
    """
    Every repository on the site, in the hub file's order.
    """

    @classmethod
    def over_repositories(cls, repositories: list[RepositorySummary]) -> SiteSummary:
        """
        :param repositories: Every repository on the site.
        :return: The site's summary, counting every repository's items and pull
            requests.
        """
        return cls(repositories=repositories, **cls.counts_over(repositories))

    @property
    def plan_count(self) -> int:
        """
        How many plans the site publishes, across every repository.
        """
        return sum(len(repository.plans) for repository in self.repositories)

    @property
    def progress_label(self) -> str:
        """
        How many repositories and plans the site has, and how many items are done.
        """
        return (
            f"{len(self.repositories)} repositories · {self.plan_count} plans · "
            f"{self.done} / {self.total} items done"
        )


@dataclass
class HubPage(TemplatePage):
    """
    The hub page: a card per registered repository.
    """

    site: SiteSummary
    """
    The whole site, whose repositories are listed in the order they should appear.
    """

    @classproperty
    def template(cls) -> str:
        return "hub.html"

    def render(self) -> str:
        """
        :return: The rendered HTML page.
        """
        return self.render_template(site=self.site, repositories=self.site.repositories)
