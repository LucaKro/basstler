"""
What a ``depends_on`` entry names: the entry parsed, the item it resolves to - in this plan
or, through a plans directory, in another - and that item's readiness.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.dashboard.cards import DependencyChip
from basstler.dashboard.plan import Item, Plan
from basstler.plan.model import PlanDocument
from basstler.pull_request_state import LiveState, PullRequestsByRepository


# %% references


@dataclass(frozen=True)
class DependencyReference:
    """
    One parsed ``depends_on`` entry: an item of the plan the entry is written in, or
    ``<plan-id>/<item-id>`` naming an item of another plan.
    """

    item_identifier: str
    """
    The referenced item's own id.
    """

    plan_id: str | None = None
    """
    The plan holding it, or ``None`` when the entry named no plan.
    """

    @classproperty
    def separator(cls) -> str:
        """
        Separates the plan id from the item id in an entry naming an item in another
        plan.

        Neither kebab-case id can contain it, so a bare entry keeps meaning an item of
        the plan it is written in.
        """
        return "/"

    @classmethod
    def from_text(cls, text: str) -> DependencyReference:
        """
        Parse one raw ``depends_on`` entry.

        :param text: The entry as written in the manifest.
        :return: The parsed reference, whose :attr:`is_well_formed` says whether the
            text was a reference at all.
        """
        plan_id, separator, item_identifier = text.partition(cls.separator)
        if not separator:
            return cls(item_identifier=plan_id)
        return cls(item_identifier=item_identifier, plan_id=plan_id)

    @property
    def text(self) -> str:
        """
        The entry as a manifest writes it.
        """
        if self.plan_id is None:
            return self.item_identifier
        return f"{self.plan_id}{self.separator}{self.item_identifier}"

    @property
    def names_another_plan(self) -> bool:
        """
        Whether this reference reaches outside the plan it is written in.
        """
        return self.plan_id is not None

    @property
    def is_well_formed(self) -> bool:
        """Whether both halves are present and neither carries a further
        separator - a third segment would have to name a plan inside a plan,
        which means nothing."""
        if self.plan_id is not None and (
            not self.plan_id or self.separator in self.plan_id
        ):
            return False
        return bool(self.item_identifier) and (
            self.separator not in self.item_identifier
        )


# %% dependencies


@dataclass(frozen=True)
class Dependency(ABC):
    """One ``depends_on`` entry, seen from the side that reads it - what
    every reader of ``depends_on`` works from, so the readiness rule, the
    chips and the sidebar lists cannot disagree about what an entry means.

    An entry that names nothing is a :class:`UnresolvedDependency` rather
    than a missing one, so a caller always gets one dependency per entry and
    can never skip past a mistyped entry as if the item had one dependency
    fewer.
    """

    reference: DependencyReference
    """
    The entry this dependency was read from.
    """

    @property
    def is_in_another_plan(self) -> bool:
        """
        Whether this dependency lives outside the plan being rendered.
        """
        return self.reference.names_another_plan

    @property
    @abstractmethod
    def title(self) -> str | None:
        """
        The referenced item's title, or ``None`` when nothing resolves this entry.
        """

    @property
    @abstractmethod
    def live_state(self) -> LiveState | None:
        """
        The referenced item's live GitHub state as last classified, or ``None`` when
        nothing resolves this entry.
        """

    @abstractmethod
    def is_ready_to_unblock_dependents(self) -> bool:
        """
        Whether a dependent item can safely start stacking its own branch on this
        dependency.
        """

    @abstractmethod
    def is_ready_for_dependent_review(self) -> bool:
        """
        Whether a dependent item's own pull request is worth reviewing behind this
        dependency.
        """

    @abstractmethod
    def chip(self, locate_plan_page: Callable[[str], str] | None) -> DependencyChip:
        """
        Render this dependency as a ``needs`` chip on its dependent's card.

        :param locate_plan_page: Where another plan's dashboard is published, given its
            id, or ``None`` when plan pages have no known address.
        """

    @abstractmethod
    def classify_live_state(
        self, pull_requests_by_repository: PullRequestsByRepository
    ) -> None:
        """
        Fill in the live GitHub state of the item this dependency names, against the
        repository that item's own plan uses.

        :param pull_requests_by_repository: Live pull request state, keyed by
            repository.
        """


@dataclass(frozen=True)
class ResolvedDependency(Dependency):
    """
    A ``depends_on`` entry that named an item, and the plan holding it.
    """

    item: Item
    """
    The item the entry names.
    """

    plan: Plan
    """
    The plan holding that item.
    """

    @property
    def repository(self) -> str:
        """
        The repository this dependency's pull request lives in: its own override, else
        its plan's default.
        """
        return self.item.repository or self.plan.default_repository

    @property
    def title(self) -> str | None:
        """
        See :attr:`Dependency.title`.
        """
        return self.item.title

    @property
    def live_state(self) -> LiveState | None:
        """
        See :attr:`Dependency.live_state`.
        """
        return self.item.live_state

    def is_ready_to_unblock_dependents(self) -> bool:
        """See :meth:`Dependency.is_ready_to_unblock_dependents` - the
        referenced item's own answer."""
        return self.item.is_ready_to_unblock_dependents()

    def is_ready_for_dependent_review(self) -> bool:
        """See :meth:`Dependency.is_ready_for_dependent_review` - the
        referenced item's own answer."""
        return self.item.is_ready_for_dependent_review()

    def chip(self, locate_plan_page: Callable[[str], str] | None) -> DependencyChip:
        return DependencyChip.of_dependency(self, locate_plan_page)

    def classify_live_state(
        self, pull_requests_by_repository: PullRequestsByRepository
    ) -> None:
        """
        See :meth:`Dependency.classify_live_state`.
        """
        self.item.live_state = LiveState.classify(
            self.item.pull_request_number,
            self.repository,
            pull_requests_by_repository,
        )


@dataclass(frozen=True)
class UnresolvedDependency(Dependency):
    """A ``depends_on`` entry that named nothing - a plan, or an item, that
    doesn't exist.

    Never ready, in either sense: an entry nobody can resolve used to be
    skipped and so counted as satisfied, which handed a mistyped dependency's
    dependent a "Start now" button. :meth:`PlanValidator.validate` rejects such an
    entry outright, so one only reaches a reader that renders without
    validating first.
    """

    @property
    def title(self) -> str | None:
        """See :attr:`Dependency.title` - always ``None``."""
        return None

    @property
    def live_state(self) -> LiveState | None:
        """See :attr:`Dependency.live_state` - always ``None``."""
        return None

    def is_ready_to_unblock_dependents(self) -> bool:
        """See :meth:`Dependency.is_ready_to_unblock_dependents` - always
        ``False``."""
        return False

    def is_ready_for_dependent_review(self) -> bool:
        """See :meth:`Dependency.is_ready_for_dependent_review` - always
        ``False``."""
        return False

    def chip(self, locate_plan_page: Callable[[str], str] | None) -> DependencyChip:
        """See :meth:`Dependency.chip` - the entry's own text, marked unmet and
        linking nowhere."""
        return DependencyChip.unresolved(self)

    def classify_live_state(
        self, pull_requests_by_repository: PullRequestsByRepository
    ) -> None:
        """See :meth:`Dependency.classify_live_state` - nothing to classify,
        since this entry named no item."""


# %% resolution


@dataclass
class PlanDirectory:
    """
    The directory holding every plan's own ``<plan-id>/`` subdirectory - what a cross-
    plan ``depends_on`` entry resolves against.

    A manifest is read the first time a reference names its plan, and never
    otherwise: a plan that nothing here depends on is exactly as expensive as
    a plan that isn't in the directory at all, which is what keeps one plan's
    dashboard from pulling in every other plan's contents.
    """

    directory: Path
    """
    Where the plans live, one subdirectory per plan, named by its id.
    """

    plans_by_id: dict[str, Plan] = field(default_factory=dict)
    """The manifests read so far, keyed by plan id - filled in on demand by
    :meth:`plan`, so a plan appears here only once something named it."""

    def plan(self, plan_id: str | None) -> Plan | None:
        """
        Look one plan up by id, reading its manifest the first time.

        :param plan_id: The plan's own ``id``, which is also its directory's name.
        :return: That plan, or ``None`` if the directory doesn't hold it.
        """
        if plan_id is None:
            return None
        if plan_id in self.plans_by_id:
            return self.plans_by_id[plan_id]
        manifest_path = self.directory / plan_id / PlanDocument.MANIFEST
        if not manifest_path.is_file():
            return None
        plan = Plan.from_mapping(yaml.safe_load(manifest_path.read_text()))
        self.plans_by_id[plan_id] = plan
        return plan

    def resolve(self, reference: DependencyReference) -> Dependency:
        """
        Resolve one cross-plan reference to the item it names.

        :param reference: A reference naming another plan's item.
        :return: The resolved dependency, or an :class:`UnresolvedDependency` if either
            half of the reference doesn't exist.
        """
        plan = self.plan(reference.plan_id)
        if plan is None:
            return UnresolvedDependency(reference=reference)
        item = plan.items_by_identifier.get(reference.item_identifier)
        if item is None:
            return UnresolvedDependency(reference=reference)
        return ResolvedDependency(reference=reference, item=item, plan=plan)


@dataclass
class DependencyResolver:
    """
    Every ``depends_on`` entry one plan writes, resolved to the item it names.

    The single place an entry becomes a dependency, so the readiness rule, the chips and
    the sidebar lists cannot disagree about what an entry means. Only the plans some
    entry actually names are read; the rest of the plans directory is never touched.
    """

    plan: Plan
    """
    The plan whose entries are being resolved.
    """

    plan_directory: PlanDirectory | None = None
    """
    The other plans a ``<plan-id>/<item-id>`` entry resolves against, or ``None`` when
    only this plan's own items are available.
    """

    dependencies_by_reference: dict[str, Dependency] = field(init=False)
    """
    What each entry resolved to, keyed by the entry that names it: each of this plan's
    own items by its bare id, plus every entry its items write.
    """

    def __post_init__(self) -> None:
        self.dependencies_by_reference = {
            item.identifier: ResolvedDependency(
                reference=DependencyReference(item_identifier=item.identifier),
                item=item,
                plan=self.plan,
            )
            for item in self.plan.items
        }
        for item in self.plan.items:
            self.dependencies_of(item)

    def resolve(self, dependency_identifier: str) -> Dependency:
        """
        Resolve one ``depends_on`` entry to the item it names.

        :param dependency_identifier: The entry, as the manifest wrote it.
        :return: The dependency it names, or an :class:`UnresolvedDependency`
            when nothing does - never nothing at all, so no reader can skip
            past a mistyped entry as though the item had one dependency
            fewer.
        """
        known = self.dependencies_by_reference.get(dependency_identifier)
        if known is not None:
            return known
        reference = DependencyReference.from_text(dependency_identifier)
        resolved: Dependency = UnresolvedDependency(reference=reference)
        if reference.names_another_plan and self.plan_directory is not None:
            resolved = self.plan_directory.resolve(reference)
        self.dependencies_by_reference[dependency_identifier] = resolved
        return resolved

    @property
    def resolved_dependencies(self) -> list[ResolvedDependency]:
        """
        Every item an entry here names: each of this plan's own items, plus every item
        of another plan that some entry names.
        """
        return [
            dependency
            for dependency in self.dependencies_by_reference.values()
            if isinstance(dependency, ResolvedDependency)
        ]

    def dependencies_of(self, item: Item) -> list[Dependency]:
        """
        Resolve one item's whole ``depends_on``.

        :param item: The depending item.
        :return: One dependency per entry, in the order the manifest wrote them.
        """
        return [self.resolve(entry) for entry in item.depends_on]

    def classify_live_states(
        self, pull_requests_by_repository: PullRequestsByRepository
    ) -> None:
        """
        Fill in the live GitHub state of every item some entry here names, each against
        its own plan's repository.

        :param pull_requests_by_repository: Live pull request state, keyed by
            repository.
        """
        for dependency in self.dependencies_by_reference.values():
            dependency.classify_live_state(pull_requests_by_repository)
