#!/usr/bin/env python3
"""
Prints the setup steps that live outside this clone, filled in for the fork the
clone actually points at.

check-setup.sh reports on everything a shell can inspect - the notes branch, the
recorded git identity, the dashboard dependencies. What it cannot reach is
everything held by a service rather than a file: labels in a GitHub repository,
Claude's access to that repository, and the variables a fresh-clone environment
has to carry. Those are the steps a first-time user is left holding, so they are
printed here as a short list with the values already substituted, rather than
described in prose the reader has to translate.

It also performs the one step a script can that check-setup.sh reports on but
session start cannot fill in for itself: naming the project's upstream in the user's
personal ``stack.toml``, which only the user knows.

It also installs ``/upstream-reviews``' Action on the fork's own branch, which a
script can do once the user has asked for it - everything but switching a fork's
Actions on, which is printed here.

Usage:
    "${BASSTLER_PYTHON}" -m basstler.setup.steps
    "${BASSTLER_PYTHON}" -m basstler.setup.steps configure-upstream --repository <owner>/<name>
    "${BASSTLER_PYTHON}" -m basstler.setup.steps install-upstream-reviews-action --branch-or-tag <branch or tag>
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path

from basstler.bash import Bash
from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.github_labels import GitHubLabel
from basstler_domain_model.environment import (
    ClaudeCodeVariable,
)
from basstler.locations import (
    PluginLocation,
    ProjectLocation,
)
from basstler_domain_model.git.exceptions import (
    GitCommandFailed,
)
from basstler_domain_model.git.commands import (
    GitCommandRunner,
)
from basstler.plugin_root import PluginRoot
from basstler_domain_model.repository import (
    Repository,
)
from basstler.shell_configuration import (
    FetchedBranchReference,
    ShellConfigurationFunction,
)
from basstler.stack.configuration import StackSetting
from basstler.standard_streams import StandardStreamHandler
from basstler.upstream_reviews.action.branch import (
    ActionInstallation,
    RemoteBranch,
)
from basstler.upstream_reviews.vocabulary import (
    ActionBranch,
)

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command prints through.
"""


class Host(StrEnum):
    """
    The services the printed steps send a reader to, each named once.

    A member is the bare host, which is how a remote URL carries it; :attr:`url` is
    the same host addressed over HTTPS, which is how a link carries it.
    """

    CLAUDE = "claude.ai"
    """
    Where Claude's own settings and cloud sessions live.
    """

    CLAUDE_DOCUMENTATION = "code.claude.com"
    """
    Where Claude Code's documentation lives.
    """

    GITHUB = "github.com"
    """
    Where the fork, its labels and its pull requests live.
    """

    @property
    def url(self) -> str:
        """
        This host addressed the way every link in this module addresses it.
        """
        return f"https://{self}"


class SetupLink(StrEnum):
    """
    Every fixed page the printed steps link to, composed from the host serving it.

    A link that varies with the repository is built by the step that shows it instead -
    see :attr:`ForkLabels.labels_url`.
    """

    GITHUB_AUTHORIZATION = (
        f"{Host.CLAUDE.url}/customize/connectors"
        "?auth_start=github&auth_start_force=1"
    )
    """
    Where a user grants Claude access to their own GitHub repositories.

    The query opens the GitHub authorization directly rather than the connector list.
    """

    ORGANIZATION_CONNECTORS = f"{Host.CLAUDE.url}/admin-settings/connectors"
    """
    Where an owner enables the GitHub connector for a Team or Enterprise Claude plan.

    Until they do, the authorization above offers no sign-in button at all.
    """

    ENVIRONMENT_SELECTOR = f"{Host.CLAUDE.url}/code"
    """
    Where an environment is edited, which is the selector rather than a settings page.
    """

    ENVIRONMENT_VARIABLES = (
        f"{Host.CLAUDE_DOCUMENTATION.url}/docs/en/cloud-environments"
        "#set-environment-variables"
    )
    """
    Where the environment-level variable list for cloud sessions is documented.

    The section rather than the page: the overview only mentions that an environment
    carries variables, and points here for the list itself.
    """


# %% reading a value out of the clone


def git_value(project_root: Path, *arguments: str) -> str | None:
    """
    Read a value git can answer for a clone, or nothing when it has no answer.

    An absent answer is an ordinary outcome here - an unset configuration key, a remote
    the clone never added - so it is reported rather than raised, and every caller in
    this module falls through to the next thing it knows.

    :param project_root: The clone to run in.
    :param arguments: The git subcommand and its arguments.
    :return: The trimmed output, or ``None`` when the command failed or said nothing.
    """
    completed = subprocess.run(
        ["git", *arguments],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


# %% the settings whose values the printed steps depend on


@dataclass(frozen=True)
class PersonalNotesSettingSpecification:
    """
    Where one personal-notes setting is read from, and what it falls back to.

    resolve-personal-notes-config.sh is the definition of both the precedence and the
    defaults; this mirrors them because the shell file exports nothing a child process
    could read them from.
    """

    git_config_key: str
    """
    The git config key that takes precedence over everything else.
    """

    environment_variable: str
    """
    The environment variable read when no git config value is set.
    """

    default: str
    """
    The value in force when neither of the two above is set.
    """

    def resolve(self, project_root: Path, environment: Mapping[str, str]) -> str:
        """
        Read this setting's value as the hooks themselves would.

        :param project_root: The clone whose git config to read.
        :param environment: The environment to read the variable from.
        :return: The value in force.
        """
        configured = git_value(project_root, "config", "--get", self.git_config_key)
        if configured is not None:
            return configured
        return environment.get(self.environment_variable) or self.default


class PersonalNotesSetting(PersonalNotesSettingSpecification, Enum):
    """
    The three settings that decide where personal notes live, in the order the printed
    variable list uses them.

    Each member *is* a specification, so a setting's own key, variable and default are
    reached directly and no caller holds a parallel list of them.

    .. note::
       Both :meth:`__new__` and :meth:`__init__` are needed: the enum machinery builds a
       member's value by calling the mixed-in type with the member's value as arguments,
       which a specification with no defaulted field cannot survive.
    """

    def __new__(
        cls, specification: PersonalNotesSettingSpecification
    ) -> PersonalNotesSetting:
        """
        Make the member without letting the enum rebuild the specification from it.

        :param specification: Where this setting is read from.
        :return: The member, carrying that specification as its value.
        """
        member = object.__new__(cls)
        member._value_ = specification
        return member

    def __init__(self, specification: PersonalNotesSettingSpecification) -> None:
        """
        Carry the specification's values on the member itself.

        :param specification: Where this setting is read from.
        """
        for field in dataclasses.fields(PersonalNotesSettingSpecification):
            object.__setattr__(self, field.name, getattr(specification, field.name))

    REMOTE = PersonalNotesSettingSpecification(
        git_config_key="claude.personalNotesRemote",
        environment_variable="CLAUDE_PERSONAL_NOTES_REMOTE",
        default="origin",
    )
    """
    The remote, or raw URL, the notes branch lives on.
    """

    BRANCH = PersonalNotesSettingSpecification(
        git_config_key="claude.personalNotesBranch",
        environment_variable="CLAUDE_PERSONAL_NOTES_BRANCH",
        default="claude/personal-notes",
    )
    """
    The branch the notes are stored on.
    """

    PATH = PersonalNotesSettingSpecification(
        git_config_key="claude.personalNotesPath",
        environment_variable="CLAUDE_PERSONAL_NOTES_PATH",
        default=str(ProjectLocation.PERSONAL_NOTES_DOCUMENT),
    )
    """
    Where on that branch the notes file sits.
    """


# %% the repository the steps are about


def resolve_repository(project_root: Path, notes_remote: str) -> Repository | None:
    """
    Find the repository a user's pull requests and notes go to.

    :param project_root: The clone to read remotes from.
    :param notes_remote: The resolved notes remote, either a remote name or a URL.
    :return: The repository, or ``None`` when no remote resolves to a GitHub URL, so
        nothing is guessed from another host's path.
    """
    remote_url = git_value(project_root, "remote", "get-url", notes_remote)
    for candidate in (remote_url, notes_remote):
        if candidate is None:
            continue
        if Host.GITHUB in candidate and Repository.names_a_repository(candidate):
            return Repository.from_remote_url(candidate)
    return None


# %% the steps themselves


@dataclass(frozen=True)
class SetupStep(ABC):
    """
    One setup step that no script can perform or verify, because it changes a setting
    held by GitHub or by Claude rather than a file in this clone.
    """

    @property
    @abstractmethod
    def title(self) -> str:
        """
        What the step changes, in a few words.
        """

    @property
    @abstractmethod
    def reason(self) -> str:
        """
        What stops working until it is done.
        """

    @abstractmethod
    def instructions(self) -> list[str]:
        """
        What to do, one line per action.

        :return: The lines, printed under the title.
        """

    def render(self, number: int) -> str:
        """
        Render this step as the numbered block the reader sees.

        :param number: The step's position in the printed list.
        :return: The block, without a trailing newline.
        """
        lines = [f"{number}. {self.title}", f"   {self.reason}", ""]
        lines.extend(f"   {instruction}" for instruction in self.instructions())
        return "\n".join(lines)


@dataclass(frozen=True)
class ForkLabels(SetupStep):
    """
    Creating the labels this tooling reads and applies, in the user's fork.
    """

    repository: Repository
    """
    The fork the labels belong in.
    """

    @property
    def title(self) -> str:
        """See :attr:`SetupStep.title`."""
        return f"Add {len(GitHubLabel)} labels to {self.repository.full_name}"

    @property
    def reason(self) -> str:
        """See :attr:`SetupStep.reason`."""
        return (
            "A fresh fork has none of them: dashboards misread landed work, and "
            "applying a label a repository lacks fails mid-pull-request."
        )

    @property
    def labels_url(self) -> str:
        """
        The page where the fork's labels are created by hand.
        """
        return f"{Host.GITHUB.url}/{self.repository.full_name}/labels"

    def instructions(self) -> list[str]:
        """See :meth:`SetupStep.instructions`."""
        commands = [label.creation_command(self.repository) for label in GitHubLabel]
        return [
            f"By hand: {self.labels_url}",
            "Or, if you have the gh CLI:",
            *commands,
        ]


@dataclass(frozen=True)
class RepositoryAccess(SetupStep):
    """
    Giving Claude access to the repository sessions read and open pull requests
    against.
    """

    repository: Repository
    """
    The repository the access has to cover.
    """

    @property
    def title(self) -> str:
        """See :attr:`SetupStep.title`."""
        return f"Connect GitHub, so Claude can work in {self.repository.full_name}"

    @property
    def reason(self) -> str:
        """See :attr:`SetupStep.reason`."""
        return (
            "This is what lets a session clone the fork, push a branch, open a pull "
            "request and comment on one - and read the state a dashboard is built from."
        )

    def instructions(self) -> list[str]:
        """See :meth:`SetupStep.instructions`."""
        return [
            f"Authorize GitHub: {SetupLink.GITHUB_AUTHORIZATION}",
            "One authorization covers every repository your GitHub account can see.",
            "If your Claude plan is Team or Enterprise, an owner enables the GitHub "
            f"connector first or no sign-in appears: "
            f"{SetupLink.ORGANIZATION_CONNECTORS}",
            "The Claude GitHub App is separate and optional: it adds auto-fix on pull "
            "requests, and is not what grants access.",
        ]


@dataclass(frozen=True)
class ForkActions(SetupStep):
    """
    Switching Actions on in a fork, where ``/upstream-reviews``' Action runs.
    """

    repository: Repository
    """
    The fork.
    """

    @property
    def title(self) -> str:
        """See :attr:`SetupStep.title`."""
        return f"Enable Actions on {self.repository.full_name}, if it is a fork"

    @property
    def reason(self) -> str:
        """See :attr:`SetupStep.reason`."""
        return (
            "GitHub starts every fork with Actions off, and /upstream-reviews reads "
            f"the upstream from a run on the fork's {ActionBranch.NAME} branch."
        )

    def instructions(self) -> list[str]:
        """See :meth:`SetupStep.instructions`."""
        return [
            f"Open {Host.GITHUB.url}/{self.repository.full_name}/actions and press "
            '"I understand my workflows, go ahead and enable them".',
            "Nothing to do if that page lists workflows already, or the repository "
            "is not a fork.",
        ]


@dataclass(frozen=True)
class PersistentVariables(SetupStep):
    """
    Carrying the personal-notes settings into environments that clone fresh every
    session.
    """

    variable_lines: tuple[str, ...]
    """
    The ``NAME=value`` lines to paste, empty when every setting is still its default.
    """

    @classproperty
    def comment_marker(cls) -> str:
        """
        What starts a comment in the ``.env`` format an environment's variable list uses.
        """
        return "#"

    @classmethod
    def quoted_if_needed(cls, value: str) -> str:
        """
        Quote a variable's value when leaving it bare would truncate it.

        An unquoted value is read to the first comment marker and the rest of the line
        is dropped, which is silent - the variable is set, to a prefix of what was
        pasted.

        :param value: The value to write after the ``=``.
        :return: The value, quoted only when it carries a comment marker.
        """
        if cls.comment_marker not in value:
            return value
        return f'"{value}"'

    @classmethod
    def resolve(
        cls, project_root: Path, environment: Mapping[str, str]
    ) -> PersistentVariables:
        """
        Work out which settings this clone has moved off their defaults.

        :param project_root: The clone to read settings from.
        :param environment: The environment to read them from.
        :return: The step, carrying only the lines that differ from the defaults.
        """
        lines = []
        for setting in PersonalNotesSetting:
            value = setting.resolve(project_root, environment)
            if value != setting.default:
                lines.append(
                    f"{setting.environment_variable}={cls.quoted_if_needed(value)}"
                )
        return cls(variable_lines=tuple(lines))

    @property
    def title(self) -> str:
        """See :attr:`SetupStep.title`."""
        return "Carry your settings into environments that clone fresh"

    @property
    def reason(self) -> str:
        """See :attr:`SetupStep.reason`."""
        return (
            "Claude Code on the web starts from a new clone every session, so git "
            "config set inside one is gone by the next."
        )

    def instructions(self) -> list[str]:
        """See :meth:`SetupStep.instructions`."""
        if not self.variable_lines:
            return ["Nothing to paste: every setting is still its default."]
        return [
            f"Open your environment at {SetupLink.ENVIRONMENT_SELECTOR} and paste these into "
            f"its variable list ({SetupLink.ENVIRONMENT_VARIABLES}):",
            *self.variable_lines,
        ]


# %% the checklist they add up to


@dataclass(frozen=True)
class SetupChecklist:
    """
    The steps one clone leaves to its user, in the order they should be done.
    """

    steps: tuple[SetupStep, ...]
    """
    The steps that apply to this clone.
    """

    @classproperty
    def heading(cls) -> str:
        """
        The line introducing the printed list.
        """
        return "These steps are yours - no script can do them:"

    @classmethod
    def for_clone(
        cls, project_root: Path, environment: Mapping[str, str]
    ) -> SetupChecklist:
        """
        Work out which steps a clone leaves to its user, and fill them in from it.

        The repository-specific steps are omitted when no GitHub repository can be
        resolved from the clone's remotes, since neither can be acted on without one.

        :param project_root: The clone the steps are about.
        :param environment: The environment its settings resolve from.
        :return: The checklist.
        """
        notes_remote = PersonalNotesSetting.REMOTE.resolve(project_root, environment)
        repository = resolve_repository(project_root, notes_remote)
        steps: list[SetupStep] = []
        if repository is not None:
            steps.extend(
                [
                    ForkLabels(repository),
                    RepositoryAccess(repository),
                    ForkActions(repository),
                ]
            )
        steps.append(PersistentVariables.resolve(project_root, environment))
        return cls(steps=tuple(steps))

    def render(self) -> str:
        """
        Render the whole checklist as it is printed.

        :return: The full output.
        """
        blocks = [
            step.render(number) for number, step in enumerate(self.steps, start=1)
        ]
        return "\n\n".join([self.heading, *blocks])


# %% the steps a script performs


@dataclass
class PersonalNotesWriteRefusedError(RuntimeError):
    """
    Raised when the personal-notes writer would not push a file.
    """

    detail: str
    """
    What the writer said.
    """

    def __str__(self) -> str:
        """:return: Why nothing was saved."""
        return f"could not save to the personal-notes branch: {self.detail}"


@dataclass(frozen=True)
class PersonalUpstream:
    """
    Naming the project's upstream in the user's personal ``stack.toml``, so a project
    that commits nothing of basstler's still has one.
    """

    repository: Repository
    """
    The repository the project's pull requests are reviewed on.
    """

    @classproperty
    def setting_line(cls) -> re.Pattern[str]:
        """
        A ``stack.toml`` line naming the upstream, wherever it sits in the file.
        """
        return re.compile(
            rf"^[ \t]*{StackSetting.UPSTREAM_REPOSITORY}[ \t]*=.*(?:\n|$)", re.MULTILINE
        )

    def written_over(self, existing: str) -> str:
        """
        Name the upstream in a personal ``stack.toml``, keeping every other setting.

        :param existing: The file as it is, empty when there is none yet.
        :return: The file with any earlier upstream replaced by this one.
        """
        kept = self.setting_line.sub("", existing)
        if kept and not kept.endswith("\n"):
            kept += "\n"
        return (
            f"{kept}{StackSetting.UPSTREAM_REPOSITORY} = "
            f'"{self.repository.full_name}"\n'
        )

    @staticmethod
    def existing(project_root: Path) -> str:
        """
        :param project_root: The clone to fetch the personal-notes branch from.
        :return: The personal ``stack.toml`` on that branch, empty when there is none.
        """
        fetched = ShellConfigurationFunction.FETCH_PERSONAL_NOTES_BRANCH.run(
            project_root
        )
        if fetched.returncode != 0:
            return ""
        shown = subprocess.run(
            [
                "git",
                "show",
                FetchedBranchReference.PERSONAL_NOTES.file_at(
                    ProjectLocation.PERSONAL_STACK_CONFIGURATION
                ),
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        return shown.stdout if shown.returncode == 0 else ""

    def save(self, project_root: Path) -> None:
        """
        Push the personal ``stack.toml`` naming this upstream to the notes branch.

        :param project_root: The clone to save from.
        :raises PersonalNotesWriteRefusedError: If the writer would not push it.
        """
        with tempfile.TemporaryDirectory() as scratch_directory:
            scratch_file = Path(scratch_directory) / "stack.toml"
            scratch_file.write_text(self.written_over(self.existing(project_root)))
            written = subprocess.run(
                Bash.locate(os.environ).command(
                    PluginRoot.locate(os.environ).path_of(
                        PluginLocation.PERSONAL_NOTES_WRITER_SCRIPT
                    ),
                    "--source",
                    str(scratch_file),
                    "--destination",
                    ProjectLocation.PERSONAL_STACK_CONFIGURATION,
                    "--message",
                    f"Name {self.repository.full_name} as the upstream",
                ),
                cwd=project_root,
                capture_output=True,
                text=True,
            )
        if written.returncode != 0:
            raise PersonalNotesWriteRefusedError(
                written.stderr.strip() or "the notes writer reported no detail"
            )


# %% command line


class SetupCommand(StrEnum):
    """
    The steps this module performs rather than prints.
    """

    CONFIGURE_UPSTREAM = "configure-upstream"
    """
    Name the project's upstream in the personal ``stack.toml``.
    """

    INSTALL_UPSTREAM_REVIEWS_ACTION = "install-upstream-reviews-action"
    """
    Write ``/upstream-reviews``' Action onto the fork's own branch, or update it.
    """


class SetupCommandOption(StrEnum):
    """
    Every option a command takes, named once so no caller spells one out.
    """

    REPOSITORY = "--repository"
    """
    The ``owner/name`` a command is about.
    """

    BRANCH_OR_TAG = "--branch-or-tag"
    """
    The basstler branch or release tag an installed workflow runs.
    """


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """
    Parse the command line.

    :param argv: The arguments to parse, defaulting to the process's own.
    :return: The parsed arguments, with no command when the checklist is wanted.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    configure_upstream = commands.add_parser(
        SetupCommand.CONFIGURE_UPSTREAM,
        help="name the project's upstream in your personal stack.toml",
    )
    configure_upstream.add_argument(
        SetupCommandOption.REPOSITORY,
        type=Repository.parse,
        required=True,
        help="the owner/name repository the project's pull requests are reviewed on",
    )
    install_action = commands.add_parser(
        SetupCommand.INSTALL_UPSTREAM_REVIEWS_ACTION,
        help="write /upstream-reviews' Action onto the fork's own branch",
    )
    install_action.add_argument(
        SetupCommandOption.BRANCH_OR_TAG,
        required=True,
        help="the basstler branch or release tag it runs, the one the session installed",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """
    Print the checklist for this clone, or perform the step a command names.

    :param argv: The arguments to parse, defaulting to the process's own.
    :return: The process exit status.
    """
    arguments = _parse_arguments(argv)
    project_root = Path(
        os.environ.get(ClaudeCodeVariable.PROJECT_DIRECTORY, Path.cwd())
    )
    if arguments.command == SetupCommand.CONFIGURE_UPSTREAM:
        try:
            PersonalUpstream(arguments.repository).save(project_root)
        except PersonalNotesWriteRefusedError as failure:
            logger.error(failure)
            return 1
        logger.info(
            f"{StackSetting.UPSTREAM_REPOSITORY} = {arguments.repository.full_name} "
            f"saved to {ProjectLocation.PERSONAL_STACK_CONFIGURATION} on the "
            "personal-notes branch"
        )
        return 0
    if arguments.command == SetupCommand.INSTALL_UPSTREAM_REVIEWS_ACTION:
        remote = PersonalNotesSetting.REMOTE.resolve(project_root, os.environ)
        branch = RemoteBranch(GitCommandRunner(project_root), remote)
        try:
            changed = ActionInstallation(arguments.branch_or_tag).install(branch)
        except GitCommandFailed as failure:
            logger.error(failure)
            return 1
        logger.info(
            f"{ActionBranch.NAME} on {branch.description} "
            f"{'now runs' if changed else 'already runs'} basstler {arguments.branch_or_tag}"
        )
        return 0
    logger.info(SetupChecklist.for_clone(project_root, os.environ).render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
