"""
Tests for placing a plan item on top of the line of unlanded items it builds on.
"""

from __future__ import annotations

import json

import pytest
import yaml

from basstler.plan.dependencies import DependencyReference
from basstler.plan.item_bootstrap.commands import PlaceCommand
from basstler.plan.item_bootstrap.exceptions import (
    DependencyInAnotherRepositoryError,
    ForkingDependenciesError,
    UnknownItemError,
    UnstartedDependencyError,
)
from basstler.plan.item_bootstrap.placement import ItemPlacementRequest
from basstler.plan.item_bootstrap.vocabulary import PlanItemBootstrapExitCode, ReportKey
from basstler.plan.model import ManifestKey
from basstler.stack.configuration import CommittedStackConfiguration, StackSetting

from ...constants.paths import DatasetLocation
from ...scratch_repository import ScratchRepository
from .bootstrap_scenario import PLAN_IDENTIFIER, item_named
from .test_command_line import run_bootstrap

# %% the fixture plan

STACKING_PLAN = DatasetLocation.STACKING_PLAN.read_text()
"""
The plan every test places an item of.
"""

BOTTOM, MIDDLE, TOP = "a-bottom-item", "a-middle-item", "a-top-item"
"""
The fixture's line of three unlanded items, bottom first.
"""


def placing(repository: ScratchRepository, item_identifier: str):
    """
    :param repository: The scratch repository the plan is published in.
    :param item_identifier: The item to place.
    :return: Its placement.
    """
    return ItemPlacementRequest(PLAN_IDENTIFIER, item_identifier).place(
        repository.project_root
    )


def field_of(item_identifier: str, manifest_key: ManifestKey):
    """
    :param item_identifier: An item of the fixture plan.
    :param manifest_key: The field to read.
    :return: That field as the fixture writes it.
    """
    return item_named(STACKING_PLAN, item_identifier).get(manifest_key.key)


# %% placing an item


def test_an_item_goes_on_top_of_the_unlanded_items_it_builds_on(
    stacking_plan_repository: ScratchRepository,
):
    placement = placing(stacking_plan_repository, TOP)

    assert [layer.identifier for layer in placement.layers_below] == [BOTTOM, MIDDLE]
    assert placement.base == field_of(MIDDLE, ManifestKey.BRANCH)


def test_an_item_whose_dependencies_have_all_landed_goes_on_the_trunk(
    stacking_plan_repository: ScratchRepository,
):
    """
    A dependency marked done, like one the plan no longer lists, is in the trunk already
    and no layer of the stack.
    """
    placement = placing(stacking_plan_repository, BOTTOM)

    assert placement.layers_below == ()
    assert (
        placement.base
        == CommittedStackConfiguration(
            project_root=stacking_plan_repository.project_root
        ).values()[StackSetting.UPSTREAM_BASE]
    )


def test_a_dependency_in_another_plan_is_reported_rather_than_followed(
    stacking_plan_repository: ScratchRepository,
):
    placement = placing(stacking_plan_repository, TOP)

    assert placement.dependencies_in_other_plans == tuple(
        dependency
        for dependency in field_of(TOP, ManifestKey.DEPENDS_ON)
        if DependencyReference.from_text(dependency).names_another_plan
    )


def test_the_report_names_the_branches_and_pull_requests_below(
    stacking_plan_repository: ScratchRepository,
):
    document = placing(stacking_plan_repository, TOP).to_json()

    assert document[ReportKey.BRANCHES] == [
        field_of(BOTTOM, ManifestKey.BRANCH),
        field_of(MIDDLE, ManifestKey.BRANCH),
    ]
    assert document[ReportKey.PULL_REQUESTS_BELOW] == [
        field_of(BOTTOM, ManifestKey.PULL_REQUEST_NUMBER),
        field_of(MIDDLE, ManifestKey.PULL_REQUEST_NUMBER),
    ]
    assert document[ManifestKey.REPOSITORY.key] == (
        yaml.safe_load(STACKING_PLAN)[ManifestKey.DEFAULT_REPOSITORY.key]
    )


# %% what a stack cannot take


def test_an_item_resting_on_two_unlanded_items_at_once_is_refused(
    stacking_plan_repository: ScratchRepository,
):
    with pytest.raises(ForkingDependenciesError) as refused:
        placing(stacking_plan_repository, "a-forking-item")

    assert refused.value.dependencies == tuple(
        field_of("a-forking-item", ManifestKey.DEPENDS_ON)
    )


def test_an_item_resting_on_one_without_a_branch_is_refused(
    stacking_plan_repository: ScratchRepository,
):
    with pytest.raises(UnstartedDependencyError) as refused:
        placing(stacking_plan_repository, "an-item-on-an-unstarted-one")

    assert refused.value.item_identifier == "an-unstarted-item"


def test_an_item_resting_on_one_in_another_repository_is_refused(
    stacking_plan_repository: ScratchRepository,
):
    with pytest.raises(DependencyInAnotherRepositoryError) as refused:
        placing(stacking_plan_repository, "an-item-on-one-elsewhere")

    assert refused.value.repository == field_of(
        "an-item-elsewhere", ManifestKey.REPOSITORY
    )


def test_a_landed_item_has_no_place_left_to_take(
    stacking_plan_repository: ScratchRepository,
):
    with pytest.raises(UnknownItemError):
        placing(stacking_plan_repository, "a-landed-item")


# %% the command line


def test_the_place_subcommand_prints_the_base_to_branch_from(
    stacking_plan_repository: ScratchRepository,
):
    result = run_bootstrap(
        stacking_plan_repository,
        PlaceCommand.invoked_as,
        "--plan",
        PLAN_IDENTIFIER,
        "--item",
        TOP,
    )

    assert result.returncode == PlanItemBootstrapExitCode.SUCCESS
    assert json.loads(result.stdout)[ReportKey.BASE] == field_of(
        MIDDLE, ManifestKey.BRANCH
    )


def test_the_place_subcommand_says_by_status_that_an_item_cannot_be_stacked(
    stacking_plan_repository: ScratchRepository,
):
    result = run_bootstrap(
        stacking_plan_repository,
        PlaceCommand.invoked_as,
        "--plan",
        PLAN_IDENTIFIER,
        "--item",
        "a-forking-item",
    )

    assert result.returncode == PlanItemBootstrapExitCode.UNSTACKABLE_ITEM
