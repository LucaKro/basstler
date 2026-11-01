"""
The stack configuration a project commits, layered over the defaults the package ships.

The shipped ``stack.toml`` holds what is the same for every project - label names, the
base branch - and names nobody's repository. A project names its own upstream in
``.claude/basstler/stack.toml``, committed beside its code, so every contributor and every
Actions runner checking the project out reads the same answer. The per-user layer on the
personal-notes branch goes on top of both, and is :mod:`basstler.stack`'s to read, since
a runner never fetches that branch.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.exit_codes import CommandFailure, StackExitCode
from basstler.locations import PackageLocation, ProjectLocation
from basstler_domain_model.repository import (
    Repository,
)


class StackSetting(StrEnum):
    """
    The keys of a ``stack.toml``.
    """

    IN_REVIEW_LABEL = "in_review_label"
    """
    The fork pull request label marking a branch promoted to the upstream.
    """

    REBASE_LABEL = "rebase_label"
    """
    The label opting a branch into rebasing instead of merging.
    """

    NEEDS_RESOLUTION_LABEL = "needs_resolution_label"
    """
    The label withholding a branch from promotion until a conflict is resolved.
    """

    FORK_REPOSITORY = "fork_repository"
    """
    The fork, for a checkout whose fork remote is ambiguous.
    """

    FORK_REMOTE = "fork_remote"
    """
    The git remote of the fork.
    """

    UPSTREAM_REPOSITORY = "upstream_repository"
    """
    The repository every fork is forked from and reviewed on.
    """

    UPSTREAM_REMOTE = "upstream_remote"
    """
    The name the upstream's tracking references are kept under when no remote points at
    it.
    """

    UPSTREAM_BASE = "upstream_base"
    """
    The upstream branch every stack ultimately targets.
    """

    UPSTREAM_LOCATION = "upstream_location"
    """
    What the upstream is fetched from: its remote's name, or its URL when the checkout
    has none. Resolved from the checkout rather than read from a ``stack.toml``.
    """


@dataclass
class MissingUpstreamRepositoryError(CommandFailure, LookupError):
    """
    Raised when neither the project nor anything above it names the upstream repository.
    """

    @classproperty
    def exit_code(cls) -> StackExitCode:
        """:return: The status this kind of error ends a command with."""
        return StackExitCode.UPSTREAM_UNCONFIGURED

    project_file: Path
    """
    The file the project names its upstream in.
    """

    personal_file: Path | None = None
    """
    The personal-notes file that can name it instead, for a caller that reads that layer.
    """

    def __str__(self) -> str:
        """:return: Where to name the upstream, and how."""
        alternative = (
            f", or to {self.personal_file} on your personal-notes branch"
            if self.personal_file
            else ""
        )
        return (
            f"no {StackSetting.UPSTREAM_REPOSITORY} is configured - add "
            f'{StackSetting.UPSTREAM_REPOSITORY} = "<owner>/<name>" to {self.project_file}'
            f"{alternative}"
        )


@dataclass(frozen=True)
class CommittedStackConfiguration:
    """
    The shipped defaults with a project's own committed settings layered on top.
    """

    shipped: Path = PackageLocation.STACK_CONFIGURATION.value
    """
    The defaults the package ships.
    """

    project_root: Path = field(default_factory=Path.cwd)
    """
    The project whose committed settings outrank them.
    """

    @property
    def project_file(self) -> Path:
        """
        The project's own settings file, which may not exist.
        """
        return self.project_root / ProjectLocation.PROJECT_STACK_CONFIGURATION

    def values(self) -> dict[str, Any]:
        """
        :return: The shipped values, with every key the project sets replaced by its own.
        """
        values = tomllib.loads(self.shipped.read_text())
        if self.project_file.is_file():
            values.update(tomllib.loads(self.project_file.read_text()))
        return values

    def upstream_repository(self) -> Repository:
        """
        :return: The repository the project's pull requests are reviewed on.
        :raises MissingUpstreamRepositoryError: If the project names none.
        """
        return self.upstream_repository_in(self.values())

    def upstream_repository_in(
        self, values: dict[str, Any], personal_file: Path | None = None
    ) -> Repository:
        """
        :param values: Layered stack settings, of which this project's are a layer.
        :param personal_file: Where the user could name the upstream instead, when
            *values* include the personal-notes layer.
        :return: The upstream repository the settings name.
        :raises MissingUpstreamRepositoryError: If they name none.
        """
        if StackSetting.UPSTREAM_REPOSITORY not in values:
            raise MissingUpstreamRepositoryError(self.project_file, personal_file)
        return Repository.parse(values[StackSetting.UPSTREAM_REPOSITORY])
