"""
The items a branch carries, and the blockers one owner writes on them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.plan.item_bootstrap.documents import PlanDocuments, ProjectCheckout
from basstler.plan.item_bootstrap.report import OperationReport
from basstler.plan.item_bootstrap.update import ItemUpdateRequest
from basstler.plan.item_bootstrap.vocabulary import PlanItemBootstrapExitCode, ReportKey
from basstler.plan.model import (
    ItemStatus,
    ManifestKey,
)

# %% the items a branch carries


@dataclass(frozen=True)
class TrackedItem:
    """
    One item a branch carries, as the manifest records it right now.
    """

    item_identifier: str
    """
    The item's id.
    """

    status: ItemStatus
    """
    The status the manifest records for it.
    """

    blockers: tuple[str, ...]
    """
    What the manifest records as blocking it.
    """

    def to_json(self) -> dict[str, Any]:
        """
        Render the item as the JSON a caller reads.
        """
        return {
            ReportKey.ITEM: self.item_identifier,
            ManifestKey.STATUS.key: self.status.value,
            ManifestKey.BLOCKERS.key: list(self.blockers),
        }


@dataclass(frozen=True)
class ItemWrite(TrackedItem):
    """
    One item's recorded state after a write, and the status it carried before it.
    """

    previous_status: ItemStatus
    """
    The status the manifest recorded before the write.
    """

    def to_json(self) -> dict[str, Any]:
        return {
            **super().to_json(),
            ReportKey.PREVIOUS_STATUS: self.previous_status.value,
        }


@dataclass(frozen=True)
class BranchReport(OperationReport):
    """
    Every item a branch carries, as an operation keyed on that branch found or left it.
    """

    branch: str
    """
    The branch that was looked up.
    """

    plan_identifier: str | None
    """
    The plan tracking it, or ``None`` when no plan claims the branch.
    """

    items: tuple[TrackedItem, ...]
    """
    Every item recording that branch, in manifest order.
    """

    @classmethod
    def of_branch(cls, branch: str, project_root: Path) -> BranchReport:
        """
        Find the plan and every item a branch belongs to.

        :param branch: The branch to resolve.
        :param project_root: The repository to run within.
        :raises UnknownPlanError: If the index names a plan with no manifest.
        :return: What the branch belongs to.
        """
        plan_identifier = ProjectCheckout(project_root).plan_tracking(branch)
        if plan_identifier is None:
            return cls(branch=branch, plan_identifier=None, items=())
        manifest = PlanDocuments.load(plan_identifier, project_root).manifest
        items = tuple(
            TrackedItem(
                item_identifier=item[ManifestKey.IDENTIFIER.key],
                status=ItemStatus(item[ManifestKey.STATUS.key]),
                blockers=tuple(item.get(ManifestKey.BLOCKERS.key) or ()),
            )
            for item in manifest[ManifestKey.ITEMS.key]
            if item.get(ManifestKey.BRANCH.key) == branch
        )
        return cls(branch=branch, plan_identifier=plan_identifier, items=items)

    @property
    def exit_code(self) -> PlanItemBootstrapExitCode:
        if self.items:
            return PlanItemBootstrapExitCode.SUCCESS
        return PlanItemBootstrapExitCode.BRANCH_TRACKS_NO_ITEM

    def to_json(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            ReportKey.STATUS: self.exit_code.name_for_a_caller,
            ReportKey.EXIT_CODE: int(self.exit_code),
            ManifestKey.BRANCH.key: self.branch,
            ReportKey.PLAN: self.plan_identifier,
            ReportKey.ITEMS: [item.to_json() for item in self.items],
        }
        if self.plan_identifier is not None:
            document[ReportKey.DASHBOARD_COMMAND] = self.dashboard_command_for(
                self.plan_identifier
            )
        return document


@dataclass(frozen=True)
class OwnedBlocker:
    """
    One owner's blocker on every item a branch carries, set or withdrawn.

    Written as ``<owner>: <reason>``, so the owner replaces and withdraws only its own
    entries and never one a person wrote.
    """

    branch: str
    """
    The branch whose items are written.
    """

    owner: str
    """
    Who the blocker belongs to.
    """

    reason: str | None = None
    """
    Why the branch is blocked, or ``None`` to withdraw the owner's blocker.
    """

    @classproperty
    def owner_separator(cls) -> str:
        """
        What separates a written blocker's owner from its reason.
        """
        return ": "

    def write(self, project_root: Path) -> BranchReport:
        """
        Rewrite every item on the branch with the owner's blocker set or withdrawn, then
        derive each item's status from what is left blocking it.

        Only fields that change are written, so withdrawing a blocker an item never
        carried leaves it untouched.

        :param project_root: The repository to run within.
        :return: What was written.
        """
        resolution = BranchReport.of_branch(self.branch, project_root)
        writes = tuple(
            self.write_item(item, resolution, project_root) for item in resolution.items
        )
        return BranchReport(
            branch=self.branch, plan_identifier=resolution.plan_identifier, items=writes
        )

    def write_item(
        self, item: TrackedItem, resolution: BranchReport, project_root: Path
    ) -> ItemWrite:
        """
        :param item: One item the branch carries.
        :param resolution: The plan and items the branch resolved to.
        :param project_root: The repository to run within.
        :return: The item as written.
        """
        prefix = f"{self.owner}{self.owner_separator}"
        blockers = tuple(
            blocker for blocker in item.blockers if not blocker.startswith(prefix)
        )
        if self.reason is not None:
            blockers += (f"{prefix}{self.reason}",)
        status = self.status_beside(blockers, item.status)
        changed = {
            manifest_key: value
            for manifest_key, value, recorded in (
                (ManifestKey.STATUS, status, item.status),
                (ManifestKey.BLOCKERS, blockers, item.blockers),
            )
            if value != recorded
        }
        if changed:
            ItemUpdateRequest(
                plan_identifier=resolution.plan_identifier,
                item_identifier=item.item_identifier,
                values_by_key=changed,
            ).update(project_root)
        return ItemWrite(
            item_identifier=item.item_identifier,
            status=status,
            blockers=blockers,
            previous_status=item.status,
        )

    @staticmethod
    def status_beside(blockers: tuple[str, ...], previous: ItemStatus) -> ItemStatus:
        """
        The status an item carries given what is now blocking it.

        An item nothing blocks keeps what it recorded, unless that was
        :attr:`ItemStatus.BLOCKED`, which the blockers it no longer has were the reason
        for.

        :param blockers: What blocks the item now.
        :param previous: The status it recorded before.
        :return: The status to record.
        """
        if blockers:
            return ItemStatus.BLOCKED
        if previous is ItemStatus.BLOCKED:
            return ItemStatus.IN_PROGRESS
        return previous
