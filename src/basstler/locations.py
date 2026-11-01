"""
Every file and directory this package names, each written once.

..note:: Imports nothing outside the standard library, since :mod:`basstler.setup.dependencies`
    reads its declaration's location from here before anything is installed.
"""

from __future__ import annotations

import os
from enum import ReprEnum
from pathlib import Path

from basstler.environment import EnvironmentVariable


class PathEnumeration(Path, ReprEnum):
    """
    An enumeration whose members are paths.

    A member is the path it names - joined onto another with ``/``, opened, passed to a
    subprocess, formatted as its text - and every path derived from one is a plain
    :class:`~pathlib.Path`.

    ..warning:: ``Path.name`` shadows the enumeration's own ``name``: ``member.name`` is
        the path's last component, and the member's name is ``member._name_``.
    """

    def __hash__(self) -> int:
        """
        Hash as the path does, so a member and its path are one key; the enumeration's own
        hash would tell them apart.
        """
        return hash(self.value)

    def __eq__(self, value: object, /) -> bool:
        """
        The value of the Enum should be equal to an equivalent Path instance.

        :param value: A Path instance to compare.
        :return: True if the value is equal to an equivalent Path instance, False otherwise.
        """
        return self.value == value

    def with_segments(self, *path_segments: str | os.PathLike[str]) -> Path:
        """
        :param path_segments: The segments of a path derived from this one.
        :return: That path, as a plain path rather than a lookup of a member by value.
        """
        return Path(*path_segments)


class PackageLocation(PathEnumeration):
    """
    The package's own directory and the files shipped in it, as absolute paths.
    """

    DIRECTORY = Path(__file__).parent
    """
    This package's own directory.
    """

    SOURCE_TREE = DIRECTORY.parent.parent
    """
    The directory holding the package's ``pyproject.toml``, ``README.md`` and ``src`` when
    the package is imported from a source checkout - through an editable install or
    ``PYTHONPATH``. Meaningless for a package installed as a copy.
    """

    STACK_CONFIGURATION = DIRECTORY / "stack" / "stack.toml"
    """
    The shipped stack configuration every run starts from, before a project's and a
    user's overrides.
    """

    DEPENDENCY_DECLARATION = SOURCE_TREE / "pyproject.toml"
    """
    The package metadata of a source checkout, whose ``[project] dependencies`` this
    package installs.
    """

    LICENSE = SOURCE_TREE / "LICENSE"
    """
    The licence text of a source checkout.
    """

    TEMPLATES = DIRECTORY / "templates"
    """
    The page templates dashboards are rendered from.
    """

    QUERIES = DIRECTORY / "upstream_reviews" / "queries"
    """
    The GraphQL documents the upstream review reader sends.
    """

    PLAN_ITEM_MODE_DEFAULTS = DIRECTORY / "plan" / "plan-item-modes.toml"
    """
    The shipped plan-item execution modes.
    """


class RepositoryStateLocation(PathEnumeration):
    """
    Scratch state kept per repository, relative to its common git directory, which every
    worktree of the repository shares and no checkout or install of the package holds.
    """

    DIRECTORY = Path("basstler")
    """
    Everything this package keeps there.
    """

    BOARD = DIRECTORY / "board.json"
    """
    The exported snapshot of the fork's open pull requests.
    """

    MAINTENANCE_REPORT = DIRECTORY / "maintenance-report.json"
    """
    Everything the last maintenance pass did, in full.
    """


class DashboardTemplate(PathEnumeration):
    """
    The templates a plan's dashboard is rendered from.
    """

    SELF_CONTAINED_PAGE = PackageLocation.TEMPLATES.value / "dashboard.html"
    """
    The whole dashboard as one page, with its style, content and script inline.
    """

    ARTIFACT_PAGE = PackageLocation.TEMPLATES.value / "dashboard_artifact.html"
    """
    The short page published as the dashboard Artifact, which loads the other parts.
    """

    STYLE = PackageLocation.TEMPLATES.value / "dashboard_style.css"
    """
    The dashboard's style sheet.
    """

    SCRIPT = PackageLocation.TEMPLATES.value / "dashboard_script.js"
    """
    The dashboard's behaviour: copying commands, choosing a model, revealing an item.
    """

    CONTENT = PackageLocation.TEMPLATES.value / "dashboard_content.html"
    """
    The dashboard's body: the plan, its items and the next-step sidebar.
    """


class DashboardArtifactFile(PathEnumeration):
    """
    The files a plan's dashboard Artifact is published as, by their published path.
    """

    PAGE = Path("dashboard.html")
    """
    The Artifact's page.
    """

    STYLE = Path("dashboard.css")
    """
    The style sheet the page links.
    """

    SCRIPT = Path("dashboard.js")
    """
    The script the page loads.
    """

    CONTENT = Path("dashboard-content.html")
    """
    The body the page fetches and shows.
    """

    @property
    def template(self) -> DashboardTemplate:
        """
        The template this file is rendered from.
        """
        if self is DashboardArtifactFile.PAGE:
            return DashboardTemplate.ARTIFACT_PAGE
        return DashboardTemplate[self._name_]


class BasstlerVariable(EnvironmentVariable):
    """
    The environment variables this package's own shell configuration sets.
    """

    PLUGIN_ROOT = "BASSTLER_PLUGIN_ROOT"
    """
    The root of the Claude Code plugin whose hooks and skills run this package.
    """


class PluginLocation(PathEnumeration):
    """
    The plugin's files this package runs, relative to the plugin's root.
    """

    HOOKS = Path("hooks")
    """
    The shell entry points that read and write personal-notes data.
    """

    PERSONAL_NOTES_CONFIGURATION_SCRIPT = HOOKS / "resolve-personal-notes-config.sh"
    """
    The shell configuration that resolves the personal-notes remote and branch, and
    fetches it.
    """

    PERSONAL_NOTES_WRITER_SCRIPT = HOOKS / "write-personal-notes-file.sh"
    """
    The helper that commits one file to the personal-notes branch and pushes it.
    """

    PERSONAL_NOTES_BRANCH_CREATION_SCRIPT = HOOKS / "create-personal-notes-branch.sh"
    """
    The helper that creates the personal-notes branch where it does not exist yet.
    """

    ENTRY_POINT_SCRIPT = HOOKS / "basstler.sh"
    """
    Runs one module of the package once the package is ready to run, saying in one
    line why when it cannot be.
    """

    SESSION_START_SCRIPT = HOOKS / "session-start.sh"
    """
    The session-start hook: sets up the package's environment and loads the personal
    notes.
    """

    SESSION_START_MESSAGES_SCRIPT = HOOKS / "session-start-messages.sh"
    """
    The messages the session-start hook prints, sourced by it.
    """

    SETUP_CHECK_SCRIPT = HOOKS / "check-setup.sh"
    """
    Reports, one row per prerequisite, what the personal-notes setup still lacks.
    """

    PLAN_SAVING_SCRIPT = HOOKS / "save-plan.sh"
    """
    Pushes an edited plan manifest and roadmap to the personal-notes branch.
    """

    PLAN_UPDATES_SINCE_SCRIPT = HOOKS / "plan-updates-since.sh"
    """
    Reports what changed in the plans since this clone last read them.
    """

    GIT_IDENTITY_SAVING_SCRIPT = HOOKS / "save-git-identity.sh"
    """
    Saves the user's git identity to the personal-notes branch.
    """

    PERSONAL_SETTINGS_SAVING_SCRIPT = HOOKS / "save-personal-settings.sh"
    """
    Saves the user's personal settings to the personal-notes branch.
    """

    STACK_EXTENSION_INSTALLATION_SCRIPT = HOOKS / "install-gh-stack.sh"
    """
    Makes ``gh stack`` runnable in the session, installing it in a cloud session.
    """

    @property
    def path_under_exported_root(self) -> str:
        """
        This file as bash spells it under :attr:`BasstlerVariable.PLUGIN_ROOT`, for a
        message that names it without knowing the root.
        """
        return f"${BasstlerVariable.PLUGIN_ROOT}/{self}"


class PluginSkillLocation(PathEnumeration):
    """
    The files of the plugin's skills this package reads, relative to the plugin's root.
    """

    SKILLS = Path("skills")
    """
    The directory holding one directory per skill.
    """

    STARTER_NOTES = SKILLS / "setup-personal-notes" / "starter-notes.md"
    """
    The conventions a new personal-notes file can be seeded with.
    """


class ProjectLocation(PathEnumeration):
    """
    The files this package reads, runs or writes in a project, relative to its root.

    Those on the personal-notes branch are relative to that branch's root, which is the
    same tree.
    """

    CLAUDE_CODE_DIRECTORY = Path(".claude")
    """
    The directory Claude Code reads a project's settings from.
    """

    PERSONAL_NOTES = CLAUDE_CODE_DIRECTORY / "personal"
    """
    Where the personal-notes branch keeps everything it holds.
    """

    PROJECT_STACK_CONFIGURATION = CLAUDE_CODE_DIRECTORY / "basstler" / "stack.toml"
    """
    The stack settings a project commits, naming its own upstream - layered over
    :attr:`PackageLocation.STACK_CONFIGURATION`.
    """

    PERSONAL_NOTES_DOCUMENT = PERSONAL_NOTES / "notes.md"
    """
    The notes file itself, unless a clone's settings put it elsewhere.
    """

    PLANS = PERSONAL_NOTES / "plans"
    """
    Where plans live on the personal-notes branch.

    Mirrors ``PLANS_DIR`` in ``resolve-personal-notes-config.sh``, which is the shell
    half of the same tooling; a test holds the two equal so the mirror cannot drift.
    """

    PLAN_HUB = PLANS / "_hub.yaml"
    """
    The plan hub file: the site's address and every repository it publishes.

    Mirrors ``PLAN_HUB_PATH`` in ``resolve-personal-notes-config.sh``.
    """

    PERSONAL_STACK_CONFIGURATION = PERSONAL_NOTES / "stack.toml"
    """
    The per-user override of :attr:`PROJECT_STACK_CONFIGURATION`.
    """

    PERSONAL_PLAN_ITEM_MODES = PERSONAL_NOTES / "plan-item-modes.toml"
    """
    The per-user override of :attr:`PackageLocation.PLAN_ITEM_MODE_DEFAULTS`.
    """


class ClaudeCodeVariable(EnvironmentVariable):
    """
    The environment variables Claude Code sets for the commands it runs.
    """

    PROJECT_DIRECTORY = "CLAUDE_PROJECT_DIR"
    """
    The root of the project the session runs in.
    """

    REMOTE = "CLAUDE_CODE_REMOTE"
    """
    ``true`` in a Claude Code cloud session, whose proxy answers GitHub's REST API but
    not its GraphQL API.
    """

    SESSION_IDENTIFIER = "CLAUDE_CODE_SESSION_ID"
    """
    The session's own identifier, which names its transcript file.
    """

    CONFIGURATION_DIRECTORY = "CLAUDE_CONFIG_DIR"
    """
    Where Claude Code keeps its state, transcripts included, when not ``~/.claude``.
    """

    GIT_BASH_PATH = "CLAUDE_CODE_GIT_BASH_PATH"
    """
    The bash Claude Code runs its commands in on Windows, when set.
    """
