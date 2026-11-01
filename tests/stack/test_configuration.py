"""
The stack configuration a project commits, layered over the defaults the package ships.

The package names nobody's repository; the project that uses it names its own upstream.
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

import pytest

from basstler.locations import (
    PackageLocation,
    ProjectLocation,
)
from basstler_domain_model.locations import (
    PathEnumeration,
)
from basstler_domain_model.repository import (
    Repository,
)
from basstler.stack.configuration import (
    CommittedStackConfiguration,
    MissingUpstreamRepositoryError,
    StackSetting,
)

from ..constants.paths import DatasetLocation


class ConfigurationLocation(PathEnumeration):
    """
    The stack configuration files these tests layer over one another.
    """

    SHIPPED_CONFIGURATION = DatasetLocation.STACK_CONFIGURATION / "shipped.toml"
    """
    Defaults standing in for the shipped file.
    """

    PROJECT_CONFIGURATION = DatasetLocation.STACK_CONFIGURATION / "project.toml"
    """
    A project's own settings.
    """


def project_committing(project_root: Path, settings: Path) -> Path:
    """
    :param project_root: The project to commit the settings into.
    :param settings: The settings to commit.
    :return: The project root.
    """
    destination = project_root / ProjectLocation.PROJECT_STACK_CONFIGURATION
    destination.parent.mkdir(parents=True)
    shutil.copy(settings, destination)
    return project_root


def test_the_shipped_defaults_name_no_repository():
    """
    A package installed into anyone's project cannot know which repository that project
    is reviewed on.
    """
    shipped = tomllib.loads(PackageLocation.STACK_CONFIGURATION.value.read_text())

    assert StackSetting.UPSTREAM_REPOSITORY not in shipped
    assert StackSetting.FORK_REPOSITORY not in shipped


def test_the_projects_settings_outrank_the_shipped_defaults(tmp_path: Path):
    configuration = CommittedStackConfiguration(
        shipped=ConfigurationLocation.SHIPPED_CONFIGURATION,
        project_root=project_committing(
            tmp_path, ConfigurationLocation.PROJECT_CONFIGURATION
        ),
    )
    project = tomllib.loads(ConfigurationLocation.PROJECT_CONFIGURATION.read_text())
    shipped = tomllib.loads(ConfigurationLocation.SHIPPED_CONFIGURATION.read_text())

    values = configuration.values()

    assert values[StackSetting.UPSTREAM_REMOTE] == project[StackSetting.UPSTREAM_REMOTE]
    assert values[StackSetting.UPSTREAM_BASE] == shipped[StackSetting.UPSTREAM_BASE]


def test_the_upstream_is_the_one_the_project_names(tmp_path: Path):
    configuration = CommittedStackConfiguration(
        shipped=ConfigurationLocation.SHIPPED_CONFIGURATION,
        project_root=project_committing(
            tmp_path, ConfigurationLocation.PROJECT_CONFIGURATION
        ),
    )

    assert configuration.upstream_repository() == Repository.parse(
        tomllib.loads(ConfigurationLocation.PROJECT_CONFIGURATION.read_text())[
            StackSetting.UPSTREAM_REPOSITORY
        ]
    )


def test_a_project_without_settings_runs_on_the_shipped_defaults(tmp_path: Path):
    configuration = CommittedStackConfiguration(
        shipped=ConfigurationLocation.SHIPPED_CONFIGURATION, project_root=tmp_path
    )

    assert configuration.values() == tomllib.loads(
        ConfigurationLocation.SHIPPED_CONFIGURATION.read_text()
    )


def test_a_project_naming_no_upstream_is_told_where_to_name_it(tmp_path: Path):
    """
    Nothing to fall back on: the upstream is the one setting only the project knows.
    """
    configuration = CommittedStackConfiguration(
        shipped=ConfigurationLocation.SHIPPED_CONFIGURATION, project_root=tmp_path
    )

    with pytest.raises(MissingUpstreamRepositoryError) as raised:
        configuration.upstream_repository()

    assert raised.value.project_file == (
        tmp_path / ProjectLocation.PROJECT_STACK_CONFIGURATION
    )
