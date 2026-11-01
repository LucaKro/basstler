"""
Where a plan item's branch goes: on top of the line of unlanded items it builds on, the
shape a GitHub stack of pull requests takes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from basstler.plan.dependencies import DependencyReference
from basstler.plan.item_bootstrap.documents import PlanDocuments
from basstler.plan.item_bootstrap.exceptions import (
    DependencyInAnotherRepositoryError,
    ForkingDependenciesError,
    UnknownItemError,
    UnstartedDependencyError,
)
from basstler.plan.item_bootstrap.report import OperationReport
from basstler.plan.item_bootstrap.vocabulary import PlanItemBootstrapExitCode, ReportKey
from basstler.plan.model import ItemStatus, ManifestKey, PlanItem, PlanManifest
from basstler.stack.configuration import CommittedStackConfiguration, StackSetting

# %% the placement


@dataclass(frozen=True)
class StackPlacement(OperationReport):
    """
    An item's place in its stack: the layers below it and the trunk they rest on.
    """

    plan_identifier: str
    """
    The plan the item belongs to.
    """

    item_identifier: str
    """
    The item placed.
    """

    repository: str
    """
    The ``owner/name`` every layer's pull request is open on.
    """

    trunk: str
    """
    The branch the bottom layer is based on.
    """

    layers_below: tuple[PlanItem, ...]
    """
    The unlanded items the item builds on, bottom first.
    """

    dependencies_in_other_plans: tuple[str, ...]
    """
    The dependencies naming another plan's items, which this placement does not follow.
    """

    @property
    def exit_code(self) -> PlanItemBootstrapExitCode:
        return PlanItemBootstrapExitCode.SUCCESS

    @property
    def base(self) -> str:
        """
        The branch the item's branch is cut from and its pull request based on.
        """
        if not self.layers_below:
            return self.trunk
        return str(self.layers_below[-1].content[ManifestKey.BRANCH.key])

    def to_json(self) -> dict[str, Any]:
        return {
            ReportKey.STATUS: self.exit_code.name_for_a_caller,
            ReportKey.EXIT_CODE: int(self.exit_code),
            ReportKey.PLAN: self.plan_identifier,
            ReportKey.ITEM: self.item_identifier,
            ManifestKey.REPOSITORY.key: self.repository,
            ReportKey.BASE: self.base,
            ReportKey.BRANCHES: [
                layer.content[ManifestKey.BRANCH.key] for layer in self.layers_below
            ],
            ReportKey.PULL_REQUESTS_BELOW: [
                layer.content.get(ManifestKey.PULL_REQUEST_NUMBER.key)
                for layer in self.layers_below
            ],
            ReportKey.DEPENDENCIES_IN_OTHER_PLANS: list(
                self.dependencies_in_other_plans
            ),
        }


# %% placing an item


@dataclass(frozen=True)
class ItemPlacementRequest:
    """
    Asks where one item's branch goes.
    """

    plan_identifier: str
    """
    The plan the item belongs to.
    """

    item_identifier: str
    """
    The item to place.
    """

    def place(self, project_root: Path) -> StackPlacement:
        """
        Follow the item's unlanded dependencies in its own plan down to the trunk.

        A dependency the plan no longer lists, or lists as done, has landed and is no
        layer; a dependency naming another plan's item is reported rather than followed.

        :param project_root: The repository to read the plan and the stack settings in.
        :return: The item's place in its stack.
        :raises UnknownItemError: If the plan has no such item.
        :raises ForkingDependenciesError: If some layer rests on more than one unlanded
            item.
        :raises DependencyInAnotherRepositoryError: If a layer lives in another
            repository.
        :raises UnstartedDependencyError: If a layer has no branch yet.
        """
        documents = PlanDocuments.load(self.plan_identifier, project_root)
        manifest = PlanManifest(documents.manifest)
        unlanded = {
            item.identifier: item
            for item in manifest.items
            if item.status is not ItemStatus.DONE
        }
        if self.item_identifier not in unlanded:
            raise UnknownItemError(self.plan_identifier, self.item_identifier)
        repository = documents.repository_for(self.item_identifier)
        layers: list[PlanItem] = []
        in_other_plans: list[str] = []
        current = unlanded[self.item_identifier]
        while True:
            references = [
                DependencyReference.from_text(text) for text in current.dependencies
            ]
            in_other_plans += [
                reference.text
                for reference in references
                if reference.names_another_plan
            ]
            below = [
                unlanded[reference.item_identifier]
                for reference in references
                if not reference.names_another_plan
                and reference.item_identifier in unlanded
            ]
            if not below:
                break
            if len(below) > 1:
                raise ForkingDependenciesError(
                    current.identifier, tuple(item.identifier for item in below)
                )
            current = below[0]
            self.check_layer(
                current, documents.repository_for(current.identifier), repository
            )
            layers.append(current)
        return StackPlacement(
            plan_identifier=self.plan_identifier,
            item_identifier=self.item_identifier,
            repository=repository,
            trunk=CommittedStackConfiguration(project_root=project_root).values()[
                StackSetting.UPSTREAM_BASE
            ],
            layers_below=tuple(reversed(layers)),
            dependencies_in_other_plans=tuple(in_other_plans),
        )

    @staticmethod
    def check_layer(layer: PlanItem, layer_repository: str, repository: str) -> None:
        """
        :param layer: An unlanded item below the one placed.
        :param layer_repository: The repository its pull request is open on.
        :param repository: The repository the placed item's pull request is open on.
        :raises DependencyInAnotherRepositoryError: If the two differ.
        :raises UnstartedDependencyError: If the layer has no branch yet.
        """
        if layer_repository != repository:
            raise DependencyInAnotherRepositoryError(layer.identifier, layer_repository)
        if not layer.content.get(ManifestKey.BRANCH.key):
            raise UnstartedDependencyError(layer.identifier)
