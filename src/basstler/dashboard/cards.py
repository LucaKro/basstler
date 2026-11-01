"""
What an item card and its sections show beyond the item itself: its dependency chips, its
action button and model dropdown, and its place in its track's dependency stack.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from basstler_domain_model.class_property import (
    classproperty,
)

if TYPE_CHECKING:
    from basstler.dashboard.plan import Item, Track, Wave
    from basstler.plan.dependencies import (
        Dependency,
        ResolvedDependency,
        UnresolvedDependency,
    )


# %% dependency chips


@dataclass
class DependencyChip:
    """One ready-to-render ``needs`` chip on an item's card - see
    :attr:`Item.dependency_chips`."""

    dependency: Dependency
    """The dependency the chip stands for."""

    tooltip: str
    """The chip's hover title: the dependency's title, plus the plan holding
    it and its live state when that plan isn't this one, or the raw entry if
    it doesn't resolve at all."""

    card_url: str | None = None
    """Where the dependency's own card is shown: an anchor on this page for an
    item of this plan, or that card on its own plan's dashboard - ``None`` when
    the entry resolves to nothing, or that dashboard has no known address."""

    @classproperty
    def tooltip_separator(cls) -> str:
        """Joins the parts of a cross-plan chip's hover title."""
        return " · "

    @classmethod
    def unresolved(cls, dependency: UnresolvedDependency) -> DependencyChip:
        """Build the chip for a ``depends_on`` entry nothing resolves.

        :param dependency: The dependency this chip stands for.
        """
        return cls(dependency=dependency, tooltip=dependency.reference.text)

    @classmethod
    def of_dependency(
        cls,
        dependency: ResolvedDependency,
        locate_plan_page: Callable[[str], str] | None,
    ) -> DependencyChip:
        """Build the chip for a resolved dependency.

        :param dependency: The dependency this chip stands for.
        :param locate_plan_page: Where another plan's dashboard is published,
            given its id, or ``None`` when plan pages have no known address.
        """
        if not dependency.is_in_another_plan:
            return cls(
                dependency=dependency,
                tooltip=dependency.item.title,
                card_url=f"#{dependency.item.anchor}",
            )
        card_url = (
            f"{locate_plan_page(dependency.plan.id)}#{dependency.item.anchor}"
            if locate_plan_page is not None
            else None
        )
        return cls(
            dependency=dependency,
            tooltip=cls.tooltip_separator.join(
                [
                    dependency.item.title,
                    dependency.plan.title,
                    dependency.item.live_state.display_label,
                ]
            ),
            card_url=card_url,
        )


# %% action buttons


@dataclass(frozen=True)
class ItemAction(ABC):
    """One not-done item's actionable dashboard button - see
    :attr:`Item.action`. The label matches what the status actually calls
    for (starting fresh work reads differently from resolving a blocker),
    but every non-``done`` status gets one: there's always something
    actionable to do next besides waiting.

    Not a plain enum: each action carries the plan/item it targets, so its
    members can't be fixed singletons - one subclass per ``/plan-item-...``
    skill instead. Not instantiated directly - see :class:`StartNowAction`/
    :class:`ResolveAction`."""

    label: str
    """The button's text, e.g. ``"Start now"`` or ``"Resolve"``."""

    plan_id: str
    """The plan this action's command targets."""

    item_identifier: str
    """The item this action's command targets."""

    @classproperty
    @abstractmethod
    def skill_command_name(cls) -> str:
        """The ``/plan-item-...`` skill this action invokes - fixed per
        subclass, since which skill an action routes to never varies per
        instance (only :attr:`label` does, e.g. ``"Resolve"`` vs. ``"Resume"``
        for the same :class:`ResolveAction`)."""

    @property
    def command(self) -> str:
        """The full command copied to the clipboard when the button is
        clicked."""
        return f"{self.skill_command_name} {self.plan_id} {self.item_identifier}"


@dataclass(frozen=True)
class StartNowAction(ItemAction):
    """Routes a not-started item, once every dependency is ready, to
    ``plan-item-kickoff``."""

    @classproperty
    def skill_command_name(cls) -> str:
        """:return: The ``plan-item-kickoff`` skill."""
        return "/plan-item-kickoff"


@dataclass(frozen=True)
class ResolveAction(ItemAction):
    """Routes a blocked, in-progress, or deferred item to
    ``plan-item-resolve`` - :attr:`ItemAction.label` is worded to match
    which of those three it actually is."""

    @classproperty
    def skill_command_name(cls) -> str:
        """:return: The ``plan-item-resolve`` skill."""
        return "/plan-item-resolve"


class ModelOption(StrEnum):
    """One choice in a dashboard action button's model dropdown, in the order the
    dropdown lists them.

    Every model is offered dashboard-wide - not plan-specific, so declared once
    here rather than threaded through :class:`Plan`. A member is its own model
    id, the one to prepend as ``/model <value>`` before the copied command, or
    ``""`` to copy the command alone and inherit whatever model the
    pasted-into session is already running.
    """

    label: str
    """The human-readable label shown in the dropdown."""

    def __new__(cls, model_identifier: str, label: str) -> ModelOption:
        """Make a member that is its own model id and carries its dropdown label.

        :param model_identifier: The model id, or ``""`` for the session default.
        :param label: The human-readable label shown in the dropdown.
        :return: The member.
        """
        member = str.__new__(cls, model_identifier)
        member._value_ = model_identifier
        member.label = label
        return member

    SESSION_DEFAULT = ("", "Session default")
    """Inherit whatever model the pasted-into session is already running."""

    OPUS_5 = ("claude-opus-5", "Opus 5")
    """Claude Opus 5."""

    SONNET_5 = ("claude-sonnet-5", "Sonnet 5")
    """Claude Sonnet 5."""

    HAIKU_4_5 = ("claude-haiku-4-5-20251001", "Haiku 4.5")
    """Claude Haiku 4.5."""

    FABLE_5 = ("claude-fable-5", "Fable 5")
    """Claude Fable 5."""


# %% track and wave sections


@dataclass
class StackedItem:
    """One item's position within its track's dependency stack, for the
    ``item_card`` template macro to render.

    Carries two independent indent computations - as normally shown, and as
    shown once done items are hidden - so the page can switch between them
    client-side (see ``hide-done`` in dashboard.html) without a re-render:
    a done dependency, once hidden, no longer visually justifies indenting
    its dependents, so they dedent as if they had no dependency on it at
    all rather than merely one level shallower."""

    item: Item
    """The item itself."""

    indent_level: int
    """How deeply nested this item is under its same-track dependency chain,
    capped at :attr:`DashboardRenderer.maximum_dependency_stack_level`."""

    wrap_parent: Item | None
    """The item this one visually continues from, if the chain wrapped back
    to indent level 0 past the cap; ``None`` otherwise."""

    indent_level_with_done_hidden: int
    """:attr:`indent_level`, recomputed as if every ``done`` item in the
    same-track dependency chain weren't a dependency at all."""

    wrap_parent_with_done_hidden: Item | None
    """:attr:`wrap_parent`, recomputed the same way - never itself a
    ``done`` item, since a done wrap-parent would be invisible in that view."""

    @property
    def indent_style(self) -> str:
        """The card's ``style`` attribute value: both indent levels as CSS
        custom properties, so plain CSS (keyed off the page's ``hide-done``
        class) picks whichever applies without any per-toggle re-render."""
        return (
            f"--indent-level: {self.indent_level}; "
            f"--indent-level-hidden-done: {self.indent_level_with_done_hidden};"
        )


@dataclass
class TrackSection:
    """One track's rendering context: its declared info plus its items,
    already ordered into a dependency stack."""

    track: Track
    """The track itself."""

    stacked_items: list[StackedItem]
    """The track's items, stacked - empty if the track has none yet."""

    @property
    def empty_state_message(self) -> str:
        """What to show in place of an item list when the track has none yet:
        the track's own :attr:`Track.description`, or a generic fallback."""
        return self.track.description or "No tracked items."


@dataclass
class WaveSection:
    """One wave's rendering context: its declared info plus its tracks, in order."""

    wave: Wave
    """The wave itself."""

    tracks: list[TrackSection]
    """The wave's tracks, in declaration order."""
