"""
Scratch repositories with a plan published, for the item-bootstrap commands to run
against.
"""

from __future__ import annotations

import pytest

from basstler.locations import PluginLocation
from basstler.plan.model import PlanDocument

from ...constants.paths import DatasetLocation
from ...scratch_repository import ScratchRepository
from .bootstrap_scenario import (
    INDENTLESS_PLAN_MANIFEST,
    PLAN_IDENTIFIER,
    PLAN_MANIFEST,
    PLAN_ROADMAP,
)

# %% fixtures


def repository_publishing(
    scratch_repository: ScratchRepository, manifest: str
) -> ScratchRepository:
    """
    Install the hook scripts the commands drive and publish a plan on the notes branch.

    :param scratch_repository: The initialized scratch repository and notes remote.
    :param manifest: The plan manifest to publish.
    :return: The same repository, ready to bootstrap an item in.
    """
    scratch_repository.install_hook_scripts(
        PluginLocation.PERSONAL_NOTES_CONFIGURATION_SCRIPT.name,
        PluginLocation.PLAN_SAVING_SCRIPT.name,
    )
    scratch_repository.install_package()
    scratch_repository.write("README.md", "scratch repo\n")
    scratch_repository.commit_everything("initial commit")
    scratch_repository.publish_notes_branch(
        {
            PlanDocument.MANIFEST.path_within_notes_branch(PLAN_IDENTIFIER): manifest,
            PlanDocument.ROADMAP.path_within_notes_branch(PLAN_IDENTIFIER): (
                PLAN_ROADMAP
            ),
        }
    )
    scratch_repository.resolve_notes_remote_to()
    scratch_repository.add_work_remote()
    return scratch_repository


@pytest.fixture
def bootstrap_repository(scratch_repository: ScratchRepository) -> ScratchRepository:
    """
    :return: A scratch repository with the fixture plan published.
    """
    return repository_publishing(scratch_repository, PLAN_MANIFEST)


@pytest.fixture
def indentless_plan_repository(
    scratch_repository: ScratchRepository,
) -> ScratchRepository:
    """
    :return: A scratch repository whose published plan writes its items flush with
        ``items:``.
    """
    return repository_publishing(scratch_repository, INDENTLESS_PLAN_MANIFEST)


@pytest.fixture
def stacking_plan_repository(
    scratch_repository: ScratchRepository,
) -> ScratchRepository:
    """
    :return: A scratch repository whose published plan's items build on each other.
    """
    return repository_publishing(
        scratch_repository, DatasetLocation.STACKING_PLAN.read_text()
    )
