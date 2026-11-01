#!/usr/bin/env python3
"""
Render one plan's dashboard from its manifest, roadmap and pull-request state.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.dashboard.cards import (
    DependencyChip,
    ItemAction,
    ModelOption,
    ResolveAction,
    StackedItem,
    StartNowAction,
    TrackSection,
    WaveSection,
)
from basstler.dashboard.plan import Item, Plan, Track
from basstler.dashboard.publication import DashboardArtifact, PublicationField
from basstler.exceptions import PlanValidationError
from basstler.workflow_labels import WorkflowLabel
from basstler.locations import DashboardArtifactFile, DashboardTemplate
from basstler.plan.command_arguments import PlanCommandArguments
from basstler.plan.dependencies import DependencyResolver, PlanDirectory
from basstler.plan.model import ItemStatus
from basstler.plan.validation import PlanValidator
from basstler.pull_request_state import (
    LiveState,
    PullRequestRecord,
    PullRequestsByRepository,
)
from basstler.render_common import (
    TemplatePage,
    render_markdown_to_html,
    sanitize_http_url,
)
from basstler_domain_model.repository import (
    GitHubAddress,
)
from basstler.standard_streams import StandardStreamHandler

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command prints through.
"""

# %% the summary


@dataclass
class DashboardSummary:
    """
    The one-line JSON summary this script prints to stdout on success.
    """

    status_counts: dict[ItemStatus, int]
    """
    How many items carry each :class:`ItemStatus`.
    """

    drift_items: list[str]
    """
    Titles of items whose manifest status disagrees with live GitHub state.
    """

    ready_to_start: list[str]
    """
    Titles of not-started items whose dependencies are all ready.
    """

    blocker_maybe_cleared: list[str]
    """
    Titles of blocked items with at least one dependency ready.
    """

    ready_to_review: list[str]
    """
    Titles of items with an open draft pull request, not blocked, whose dependencies (if
    any) already have their own open pull request.
    """

    live_state_counts: dict[LiveState, int] = field(default_factory=dict)
    """
    How many items' pull requests are in each :class:`LiveState`.
    """

    @property
    def open_pull_requests(self) -> int:
        """
        How many items' pull requests are open, drafts included.
        """
        return sum(
            self.live_state_counts.get(state, 0)
            for state in (LiveState.OPEN_DRAFT, LiveState.OPEN_READY)
        )

    @property
    def reviewed_pull_requests(self) -> int:
        """How many items' pull requests are open and out of draft - reviewed by
        their author and marked ready for review."""
        return self.live_state_counts.get(LiveState.OPEN_READY, 0)

    def to_json_dict(self) -> dict[str, Any]:
        """
        Render to the plain-dict shape the calling skill expects.
        """
        return {
            "counts": {
                status.value: count for status, count in self.status_counts.items()
            },
            "drift_count": len(self.drift_items),
            "drift_items": self.drift_items,
            "ready_to_start": self.ready_to_start,
            "blocker_maybe_cleared": self.blocker_maybe_cleared,
            "ready_to_review": self.ready_to_review,
        }


@dataclass
class RenderedDashboard:
    """
    What rendering one plan's dashboard produces.
    """

    html: str
    """
    The dashboard as one self-contained page.
    """

    artifact: DashboardArtifact
    """
    The same dashboard as the files its Artifact is published as.
    """

    summary: DashboardSummary
    """
    The summary of the plan's state the page was rendered from.
    """


# %% the renderer


@dataclass
class DashboardRenderer(TemplatePage):
    """
    Renders one :class:`Plan` (plus its live pull request data and roadmap text) into
    the dashboard's HTML.

    A dataclass rather than a bag of closures over shared state: each
    concern (classifying live state, computing next steps, stacking a
    track's items by dependency) is an independently named, independently
    testable method. The HTML itself is produced by handing the computed
    data to the ``dashboard.html`` Jinja2 template, not built up as Python
    strings here.
    """

    plan: Plan
    """
    The plan being rendered.
    """

    roadmap_text: str
    """
    The plan's ``roadmap.md`` narrative content.
    """

    pull_requests_by_repository: PullRequestsByRepository
    """
    Live pull request state for every repository referenced by the plan's items.
    """

    tracking_url: str | None
    """
    The tracking issue's or pull request's ``html_url``, if the plan has one.
    """

    hub_url: str | None = None
    """
    The plan hub's URL, the first link of the page's breadcrumb.
    """

    index_url: str | None = None
    """
    The repository's own index page, the breadcrumb's second link.
    """

    plan_directory: PlanDirectory | None = None
    """
    The other plans this plan's ``depends_on`` entries may name, if any were loaded.
    """

    locate_plan_page: Callable[[str], str] | None = None
    """
    Where another plan's dashboard is published, given its id, or ``None`` when plan
    pages have no known address.
    """

    items_by_identifier: dict[str, Item] = field(init=False)
    """
    Every item of this plan, keyed by :attr:`Item.identifier`.
    """

    dependencies: DependencyResolver = field(init=False)
    """
    The one resolver every reader of ``depends_on`` goes through, so an entry means the
    same thing to all of them.
    """

    @classproperty
    def maximum_dependency_stack_level(cls) -> int:
        """
        Same-track dependency chains deeper than this wrap back to indent level 0.
        """
        return 4

    @classproperty
    def template(cls) -> str:
        return DashboardTemplate.SELF_CONTAINED_PAGE.name

    def __post_init__(self) -> None:
        self.items_by_identifier = {item.identifier: item for item in self.plan.items}
        self.dependencies = DependencyResolver(
            plan=self.plan, plan_directory=self.plan_directory
        )

    def render(self) -> RenderedDashboard:
        """
        Classify live state/drift for every item, then render the full page.

        :return: The rendered page, and the summary to print on stdout.
        """
        self._classify_items()
        drift_items = [item for item in self.plan.items if item.drift_description]
        ready_to_start, blocker_maybe_cleared = self._compute_next_steps()
        ready_to_review = self._compute_ready_to_review()
        next_step_items = (
            drift_items + ready_to_start + blocker_maybe_cleared + ready_to_review
        )

        context = dict(
            style_template=DashboardTemplate.STYLE.name,
            content_template=DashboardTemplate.CONTENT.name,
            script_template=DashboardTemplate.SCRIPT.name,
            style_file=str(DashboardArtifactFile.STYLE),
            content_file=str(DashboardArtifactFile.CONTENT),
            script_file=str(DashboardArtifactFile.SCRIPT),
            title=self.plan.title,
            description=self.plan.description,
            repository=self.plan.default_repository,
            repository_url=self.plan.repository_url,
            total_items=len(self.plan.items),
            tracking_url=self.tracking_url,
            hub_url=sanitize_http_url(self.hub_url),
            index_url=sanitize_http_url(self.index_url),
            item_statuses=list(ItemStatus),
            status_counts=self._status_counts(),
            drift_items=drift_items,
            ready_to_start=ready_to_start,
            blocker_maybe_cleared=blocker_maybe_cleared,
            ready_to_review=ready_to_review,
            has_bug_fix_next_steps=any(item.is_bug_fix for item in next_step_items),
            roadmap_html=render_markdown_to_html(self.roadmap_text),
            waves=self._build_wave_sections(),
            available_models=list(ModelOption),
        )
        html = self.render_template(**context)
        artifact = DashboardArtifact(
            files={
                file: self.render_named_template(file.template.name, **context)
                for file in DashboardArtifactFile
            }
        )

        summary = DashboardSummary(
            status_counts=self._status_counts(),
            drift_items=[item.title for item in drift_items],
            ready_to_start=[item.title for item in ready_to_start],
            blocker_maybe_cleared=[item.title for item in blocker_maybe_cleared],
            ready_to_review=[item.title for item in ready_to_review],
            live_state_counts=dict(
                Counter(item.live_state for item in self.plan.items)
            ),
        )
        return RenderedDashboard(html=html, artifact=artifact, summary=summary)

    def _classify_items(self) -> None:
        """
        Fill in every item's :attr:`Item.live_state`, :attr:`Item.drift_description`,
        :attr:`Item.pull_request_url`, :attr:`Item.upstream_pull_request_url`,
        :attr:`Item.is_bug_fix`, :attr:`Item.needs_review`,
        :attr:`Item.dependency_chips`, and :attr:`Item.action` from live pull request
        data and the plan's other items, in place.

        Runs in two passes: :attr:`Item.live_state` must be filled in for
        every item before :meth:`_action_for` can check whether *another*
        item's dependencies are ready, since dependencies can appear later
        in :attr:`Plan.items` than their dependents.
        """
        for item in self.plan.items:
            item.live_state = self._live_state_of(item)
            item.drift_description = self._drift_description_of(item)
            item.pull_request_url = self._pull_request_url_of(item)
            item.upstream_pull_request_url = self._upstream_pull_request_url_of(item)
            item.is_bug_fix = self._is_bug_fix(item)
            item.needs_review = (
                item.live_state is LiveState.OPEN_DRAFT
                and item.status is not ItemStatus.DEFERRED
            )
        self.dependencies.classify_live_states(self.pull_requests_by_repository)
        for item in self.plan.items:
            item.dependency_chips = self._dependency_chips_of(item)
            item.action = self._action_for(item)

    def _pull_request_url_of(self, item: Item) -> str | None:
        """
        Build one item's pull request URL on GitHub, or ``None`` if it has no pull
        request yet.
        """
        if item.pull_request_number is None:
            return None
        repository = item.repository or self.plan.default_repository
        return f"{GitHubAddress.WEB}/{repository}/pull/{item.pull_request_number}"

    def _action_for(self, item: Item) -> ItemAction | None:
        """
        Build one item's dashboard action button, or ``None`` if it isn't applicable.

        A ``done`` item has nothing left to do. A not-started item only
        gets a button once every dependency is actually safe to build on -
        starting now against an unready dependency would build on unsafe
        state. Every other status (blocked, in progress, deferred) always
        gets one: there's always something actionable to investigate next,
        so each routes to the same ``/plan-item-resolve`` skill, worded to
        match what that status actually calls for.
        """
        match item.status:
            case ItemStatus.DONE:
                return None
            case ItemStatus.NOT_STARTED:
                if not self._dependencies_are_ready(item):
                    return None
                return StartNowAction(
                    label="Start now",
                    plan_id=self.plan.id,
                    item_identifier=item.identifier,
                )
            case ItemStatus.BLOCKED:
                label = "Resolve"
            case ItemStatus.IN_PROGRESS:
                label = "Resume"
            case ItemStatus.DEFERRED:
                label = "Reconsider"
        return ResolveAction(
            label=label,
            plan_id=self.plan.id,
            item_identifier=item.identifier,
        )

    def _dependencies_are_ready(self, item: Item) -> bool:
        """
        Whether every entry in :attr:`Item.depends_on` resolves to an item that's itself
        ready to be built upon (:meth:`Dependency.is_ready_to_unblock_dependents`).

        Vacuously true for an item with no dependencies.
        """
        return all(
            dependency.is_ready_to_unblock_dependents()
            for dependency in self.dependencies.dependencies_of(item)
        )

    def _dependency_chips_of(self, item: Item) -> list[DependencyChip]:
        """
        Build one ready-to-render :class:`DependencyChip` per entry in
        :attr:`Item.depends_on`.
        """
        return [
            dependency.chip(self.locate_plan_page)
            for dependency in self.dependencies.dependencies_of(item)
        ]

    def _live_state_of(self, item: Item) -> LiveState:
        """
        Classify one item's live GitHub state from :attr:`pull_requests_by_repository`.
        """
        return LiveState.classify(
            item.pull_request_number,
            item.repository or self.plan.default_repository,
            self.pull_requests_by_repository,
        )

    def _pull_request_record_of(self, item: Item) -> PullRequestRecord | None:
        """
        Look up one item's live pull request record, or ``None`` if it has no pull
        request yet or GitHub returned nothing for the one it names.
        """
        if item.pull_request_number is None:
            return None
        repository = item.repository or self.plan.default_repository
        repository_pull_requests = self.pull_requests_by_repository.get(repository, {})
        return repository_pull_requests.get(str(item.pull_request_number))

    def _upstream_pull_request_url_of(self, item: Item) -> str | None:
        """
        The upstream pull request one item's pull request links, or ``None`` if it links
        none.
        """
        pull_request = self._pull_request_record_of(item)
        return None if pull_request is None else pull_request.upstream_link

    def _is_bug_fix(self, item: Item) -> bool:
        """
        Whether one item's pull request is labelled as a bug fix.
        """
        pull_request = self._pull_request_record_of(item)
        return (
            pull_request is not None
            and WorkflowLabel.BUG in pull_request.identified_labels
        )

    @staticmethod
    def _drift_description_of(item: Item) -> str | None:
        """
        Describe why :attr:`Item.status` disagrees with its live state, if it does.
        """
        live_state = item.live_state
        match live_state, item.status:
            case LiveState.NOT_FOUND, _:
                return f"pull request #{item.pull_request_number} not found on GitHub"
            case (LiveState.OPEN_DRAFT | LiveState.OPEN_READY), ItemStatus.DONE:
                return f"marked done, but pull request #{item.pull_request_number} is still open"
            case LiveState.MERGED, (
                ItemStatus.NOT_STARTED | ItemStatus.BLOCKED | ItemStatus.DEFERRED
            ):
                return f"marked {item.status.value}, but pull request #{item.pull_request_number} is already merged"
            case (LiveState.MERGED | LiveState.CLOSED_UNMERGED), (
                ItemStatus.IN_PROGRESS | ItemStatus.BLOCKED
            ):
                return f"marked {item.status.value}, but pull request #{item.pull_request_number} is {live_state.value.replace('_', ' ')}"
            case LiveState.CLOSED_UNMERGED, ItemStatus.DONE:
                return f"marked done, but pull request #{item.pull_request_number} was closed without merging"
            case _:
                return None

    def _compute_next_steps(self) -> tuple[list[Item], list[Item]]:
        """
        Compute the "ready to start" and "blocker may be cleared" lists for the sidebar,
        from each item's dependencies' effective status.

        A blocked item never lands in "ready to start", even once every
        dependency is ready - it's still blocked, and its actionable next
        step is to resolve that blocker, not to start fresh. It always
        lands in "blocker may be cleared" instead once at least one
        dependency is ready, whether that's all of them or only some.

        :return: ``(ready_to_start, blocker_maybe_cleared)``.
        """
        ready_to_start: list[Item] = []
        blocker_maybe_cleared: list[Item] = []
        for item in self.plan.items:
            if item.status not in (
                ItemStatus.NOT_STARTED,
                ItemStatus.BLOCKED,
            ):
                continue
            ready_count = sum(
                dependency.is_ready_to_unblock_dependents()
                for dependency in self.dependencies.dependencies_of(item)
            )
            if item.status is ItemStatus.NOT_STARTED and self._dependencies_are_ready(
                item
            ):
                ready_to_start.append(item)
            elif item.status is ItemStatus.BLOCKED and ready_count > 0:
                blocker_maybe_cleared.append(item)
        return ready_to_start, blocker_maybe_cleared

    def _compute_ready_to_review(self) -> list[Item]:
        """
        Items with an open draft pull request that are actually reviewable right now.

        Reviewable means not blocked, and every dependency (if any) has itself
        reached review (:meth:`Item.is_ready_for_dependent_review`) - reviewing a
        stacked pull request before its base even has one open yet is premature,
        even though the base need not itself be past review, nor still be open
        once it has landed. A deferred item never reaches here in the first place -
        :attr:`Item.needs_review` is already ``False`` for it.
        """
        ready_to_review: list[Item] = []
        for item in self.plan.items:
            if not item.needs_review or item.status is ItemStatus.BLOCKED:
                continue
            if all(
                dependency.is_ready_for_dependent_review()
                for dependency in self.dependencies.dependencies_of(item)
            ):
                ready_to_review.append(item)
        return ready_to_review

    def _status_counts(self) -> dict[ItemStatus, int]:
        """
        Count the plan's items by :class:`ItemStatus`, including zero counts.
        """
        counts = {status: 0 for status in ItemStatus}
        for item in self.plan.items:
            counts[item.status] += 1
        return counts

    def _build_wave_sections(self) -> list[WaveSection]:
        """
        Group the plan's tracks by wave and its items by track, stacking each track's
        items by dependency, ready for the template to render.
        """
        tracks_by_wave: dict[str, list[Track]] = {}
        for track in self.plan.tracks:
            tracks_by_wave.setdefault(track.wave, []).append(track)

        items_by_track: dict[str, list[Item]] = {}
        for item in self.plan.items:
            items_by_track.setdefault(item.track, []).append(item)

        return [
            WaveSection(
                wave=wave,
                tracks=[
                    TrackSection(
                        track=track,
                        stacked_items=self._build_track_stack(
                            items_by_track.get(track.id, [])
                        ),
                    )
                    for track in tracks_by_wave.get(wave.id, [])
                ],
            )
            for wave in self.plan.waves
        ]

    @classmethod
    def _build_track_stack(cls, track_items: list[Item]) -> list[StackedItem]:
        """Order a track's items into a dependency stack (same-plan,
        same-track depends_on only - an item in another plan has no card on
        this page to indent under), assign an indent level per item capped at
        :attr:`maximum_dependency_stack_level`, wrapping back to level 0
        (with a reference back to the real parent) past the cap. Also
        computes each item's indent as it would be with done items hidden -
        see :class:`StackedItem`."""
        items_by_identifier = {item.identifier: item for item in track_items}

        def same_track_parent(item: Item) -> Item | None:
            """
            The first same-track entry in :attr:`Item.depends_on`, if any.
            """
            for dependency_identifier in item.depends_on:
                dependency = items_by_identifier.get(dependency_identifier)
                if dependency is not None:
                    return dependency
            return None

        children_by_parent: dict[str, list[Item]] = {}
        roots: list[Item] = []
        for item in track_items:
            parent = same_track_parent(item)
            if parent is None:
                roots.append(item)
            else:
                children_by_parent.setdefault(parent.identifier, []).append(item)

        visible_stack_cache: dict[str, tuple[int, Item | None]] = {}

        def visible_stack_position(item: Item) -> tuple[int, Item | None]:
            """This item's indent level and wrap-parent once ``done`` items
            are excluded from the same-track dependency chain entirely - a
            done dependency is treated exactly as if it weren't a
            dependency at all, so its dependents dedent back to level 0
            rather than merely one level shallower."""
            if item.identifier in visible_stack_cache:
                return visible_stack_cache[item.identifier]
            parent = same_track_parent(item)
            if parent is None or parent.status is ItemStatus.DONE:
                result = (0, None)
            else:
                parent_level, parent_wrap_parent = visible_stack_position(parent)
                next_level = parent_level + 1
                if next_level > cls.maximum_dependency_stack_level:
                    result = (0, parent)
                else:
                    result = (next_level, parent_wrap_parent)
            visible_stack_cache[item.identifier] = result
            return result

        stacked_items: list[StackedItem] = []

        def walk(item: Item, level: int, wrap_parent: Item | None) -> None:
            """
            Depth-first-visit one item and its same-track dependents, appending a
            :class:`StackedItem` per visit.
            """
            next_level = level + 1
            wrap_for_children = None
            if next_level > cls.maximum_dependency_stack_level:
                next_level = 0
                wrap_for_children = item
            visible_level, visible_wrap_parent = visible_stack_position(item)
            stacked_items.append(
                StackedItem(
                    item=item,
                    indent_level=level,
                    wrap_parent=wrap_parent,
                    indent_level_with_done_hidden=visible_level,
                    wrap_parent_with_done_hidden=visible_wrap_parent,
                )
            )
            for child in children_by_parent.get(item.identifier, []):
                walk(child, next_level, wrap_for_children)

        for root in roots:
            walk(root, 0, None)

        return stacked_items


# %% the command


def main() -> int:
    """
    Parse arguments, validate the manifest, render the dashboard, and print its summary.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    PlanCommandArguments.add_to(parser)
    parser.add_argument("--roadmap", required=True, help="Path to roadmap.md")
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument(
        "--output", help="Path to write the dashboard as one self-contained page to"
    )
    output.add_argument(
        "--artifact-directory",
        help="Existing directory to write the files the dashboard Artifact is "
        "published as to",
    )
    parser.add_argument(
        "--tracking-url",
        default=None,
        help="The plan's tracking_issue html_url, if it has one",
    )
    parser.add_argument("--hub-url", default=None, help="The plan hub's URL")
    parser.add_argument(
        "--index-url", default=None, help="The repository's own index page URL"
    )
    arguments = parser.parse_args()
    plan_arguments = PlanCommandArguments.from_namespace(arguments)

    raw_plan = plan_arguments.read_manifest()
    try:
        PlanValidator(raw_plan, plan_arguments.plan_directory).validate()
    except PlanValidationError as error:
        logger.error(f"plan.yaml failed validation: {error}")
        return 1

    renderer = DashboardRenderer(
        plan=Plan.from_mapping(raw_plan),
        roadmap_text=Path(arguments.roadmap).read_text(),
        pull_requests_by_repository=plan_arguments.read_pull_requests_by_repository(),
        tracking_url=arguments.tracking_url,
        hub_url=arguments.hub_url,
        index_url=arguments.index_url,
        plan_directory=plan_arguments.plan_directory,
    )
    rendered = renderer.render()

    summary = rendered.summary.to_json_dict()
    if arguments.output is not None:
        Path(arguments.output).write_text(rendered.html)
    else:
        rendered.artifact.write_to(Path(arguments.artifact_directory))
        summary[PublicationField.CONTENT_DIGEST] = rendered.artifact.content_digest
    logger.info(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
