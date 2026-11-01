#!/usr/bin/env python3
"""
Render the master index of every plan from a list of plan summaries.

Generic - takes a list of already-computed plan summaries (see --plans
below) and renders them; it has no idea what a plan actually contains
beyond that. Pair with dashboard/plan_page.py, whose --output JSON summary
gives you the done/total counts to build one of these entries from.

Usage:
    python3 dashboard/index_page.py --plans /tmp/plans.json --output /tmp/index.html \
        [--repository <owner/repo> --hub-url <the plan hub's url>]

plans.json shape: a JSON list of objects, each:
    {
        "id": "<plan-id>",
        "title": "...",
        "description": "...",
        "done": <int>,
        "total": <int>,
        "dashboard_url": "<url>" or null   // null if never published yet
    }
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.render_common import TemplatePage, sanitize_http_url


@dataclass(kw_only=True)
class ProgressCard(ABC):
    """
    One card on a site page: a heading, a progress label and a bar showing how many
    items are done.

    The plan cards on a repository's index and the repository cards on the hub are drawn
    by the same markup, so what the bar and the card's class are worked out from is
    shared here.
    """

    done: int
    """
    How many items are done.
    """

    total: int
    """
    How many items there are.
    """

    open_pull_requests: int = 0
    """
    How many of the items' pull requests are open.
    """

    reviewed_pull_requests: int = 0
    """
    How many of those open pull requests are out of draft: their author has reviewed
    them and marked them ready for review.
    """

    @staticmethod
    def counts_over(cards: Sequence[ProgressCard]) -> dict[str, int]:
        """
        :param cards: The cards a larger card stands for.
        :return: Every count a card carries, summed over *cards*, by field name - ready
            to pass to the larger card's constructor.
        """
        return {
            count.name: sum(getattr(card, count.name) for card in cards)
            for count in dataclasses.fields(ProgressCard)
        }

    @classproperty
    def card_css_class(cls) -> str:
        """
        The CSS class every card carries.
        """
        return "plan-card"

    @classproperty
    def complete_css_class(cls) -> str:
        """
        The CSS class a card adds once every item is done.
        """
        return "complete"

    @property
    @abstractmethod
    def progress_label(self) -> str:
        """
        The human-readable progress shown on the card.
        """

    @property
    def is_complete(self) -> bool:
        """
        Whether every item is done.
        """
        return self.total > 0 and self.done == self.total

    @property
    def completion_percentage(self) -> float:
        """
        The share of items done, from 0 to 100.
        """
        return (self.done / self.total * 100) if self.total else 0.0

    @property
    def completion_percentage_label(self) -> str:
        """
        :attr:`completion_percentage`, formatted to one decimal place with a
        trailing ``%`` - ready to drop straight into the progress bar's
        ``width`` style without the template doing any formatting itself.
        """
        return f"{self.completion_percentage:.1f}%"

    @property
    def review_label(self) -> str | None:
        """
        How many open pull requests are out of draft, or ``None`` when none are open.
        """
        if not self.open_pull_requests:
            return None
        return (
            f"{self.reviewed_pull_requests} / {self.open_pull_requests} open pull "
            f"requests reviewed"
        )

    @property
    def css_class(self) -> str:
        """
        The card's CSS class list: :attr:`card_css_class`, plus
        :attr:`complete_css_class` once every item is done.
        """
        if self.is_complete:
            return f"{self.card_css_class} {self.complete_css_class}"
        return self.card_css_class


@dataclass(kw_only=True)
class PlanSummary(ProgressCard):
    """
    One plan's entry in the master index, as gathered by the skill.
    """

    id: str
    """The plan's stable identifier: a short kebab-case slug (e.g.
    ``rdr-refactor``), not a UUID - it is chosen by whoever bootstraps the
    plan, doubles as its directory name, and is what a person types as the
    ``<plan-id>`` argument to ``/plan-dashboard``/``/plan-create``, so it
    needs to stay human-readable and human-typable."""

    title: str
    """
    The plan's display title.
    """

    description: str
    """
    A one-line description shown under the title.
    """

    dashboard_url: str | None = None
    """
    The plan's published dashboard URL, or ``None`` if it has never been published yet.
    """

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> PlanSummary:
        """
        Build a summary from one entry of ``plans.json``.
        """
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            done=data["done"],
            total=data["total"],
            dashboard_url=sanitize_http_url(data.get("dashboard_url")),
        )

    @property
    def progress_label(self) -> str:
        return f"{self.done} / {self.total} done" if self.total else "no items yet"


@dataclass
class IndexPage(TemplatePage):
    """
    The master index of one repository's plans, a card per plan.
    """

    plans: list[PlanSummary]
    """
    Every plan to list, in the order they should appear.
    """

    repository: str | None = None
    """
    The ``owner/repo`` whose plans these are, shown in the header.
    """

    hub_url: str | None = None
    """
    The plan hub's URL, linked back to from the header.
    """

    @classproperty
    def template(cls) -> str:
        return "index.html"

    def render(self) -> str:
        """
        :return: The rendered HTML page.
        """
        return self.render_template(
            plans=self.plans,
            repository=self.repository,
            hub_url=sanitize_http_url(self.hub_url),
        )


def main() -> int:
    """
    Parse arguments, render the index page, and write it out.

    See the module docstring for the CLI contract.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--plans", required=True, help="Path to a JSON list of plan summaries"
    )
    parser.add_argument(
        "--output", required=True, help="Path to write the index HTML to"
    )
    parser.add_argument(
        "--repository", default=None, help="The repository whose plans these are"
    )
    parser.add_argument("--hub-url", default=None, help="The plan hub's URL")
    arguments = parser.parse_args()

    plans = [
        PlanSummary.from_mapping(entry)
        for entry in json.loads(Path(arguments.plans).read_text())
    ]
    output = IndexPage(plans, arguments.repository, arguments.hub_url).render()
    Path(arguments.output).write_text(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
