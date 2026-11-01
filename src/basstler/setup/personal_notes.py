"""
Performs the one-time personal-notes setup without a session: points the notes remote at
a repository you own, creates the notes branch, optionally seeds it and records your git
identity, picks the notes up in this clone, and checks the pull request labels the
tooling applies.

Safe to re-run: each step is skipped when check-setup.sh already reports it done. Exits
with check-setup.sh's own final status, so a setup that only half worked never reports
success.

Usage:
    "${BASSTLER_PYTHON}" -m basstler.setup.personal_notes --remote <name-or-url> \\
        [--name "Your Name" --email you@example.com] [--starter-notes] [--create-labels]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from basstler.bash import Bash
from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.email_address import EmailAddress
from basstler_domain_model.environment import (
    GitHubCredentialVariable,
)
from basstler.exceptions import MalformedEmailAddressError
from basstler.exit_codes import (
    CommandFailure,
    DomainModelFailure,
    PersonalNotesSetupExitCode,
)
from basstler.workflow_labels import WorkflowLabel
from basstler_domain_model.environment import (
    ClaudeCodeVariable,
)
from basstler.locations import (
    PluginLocation,
    PluginSkillLocation,
)
from basstler_domain_model.github.clients import GitHubApi
from basstler_domain_model.github.repository_client import (
    GitHubRepository,
)
from basstler.plugin_root import PluginRoot
from basstler.setup.exceptions import ForeignNotesRemoteError, HookScriptFailedError
from basstler.setup.report import CheckStatus, SetupCheck, SetupReport
from basstler.setup.steps import PersonalNotesSetting, resolve_repository
from basstler.standard_streams import StandardStreamHandler

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command prints through.
"""

# %% what a run is asked to do


class SetupOption(StrEnum):
    """
    Every option the command takes, named once so no caller spells one out.
    """

    REMOTE = "--remote"
    """
    The remote, or raw URL, the notes belong on.
    """

    NAME = "--name"
    """
    The name the user's commits carry.
    """

    EMAIL = "--email"
    """
    The email the user's commits carry.
    """

    STARTER_NOTES = "--starter-notes"
    """
    Seed a new notes file from the starter template.
    """

    CREATE_LABELS = "--create-labels"
    """
    Create the labels the repository lacks, rather than only report them.
    """


@dataclass(frozen=True)
class GitIdentity:
    """
    The name and email every clone's commits are authored with.
    """

    name: str
    """
    The value of ``user.name``.
    """

    email: EmailAddress
    """
    The address ``user.email`` is set to.
    """


@dataclass(frozen=True)
class SetupRequest:
    """
    What one run is asked to set up.
    """

    remote: str
    """
    The remote the notes belong on, as a remote name in this clone or a raw URL.
    """

    identity: GitIdentity | None
    """
    The identity to record on the notes branch, or ``None`` to record none.
    """

    starter_notes: bool
    """
    Whether to seed a new notes file from the starter template.
    """

    create_labels: bool
    """
    Whether to create the labels the repository lacks, rather than only report them.
    """

    @classmethod
    def from_arguments(cls, arguments: Sequence[str] | None) -> SetupRequest:
        """
        Read a request from the command line.

        :param arguments: The command line without the program name, defaulting to the
            process's own.
        :return: The request.
        """
        parser = argparse.ArgumentParser(
            prog=f"python3 -m {__spec__.name}", description=__doc__.split("\n\n")[0]
        )
        parser.add_argument(
            SetupOption.REMOTE,
            required=True,
            help="the remote your notes belong on; required rather than guessed, since "
            "a wrong guess pushes them to a repository you may not own",
        )
        parser.add_argument(SetupOption.NAME, help="the name your commits carry")
        parser.add_argument(SetupOption.EMAIL, help="the email your commits carry")
        parser.add_argument(
            SetupOption.STARTER_NOTES,
            action="store_true",
            help="seed a new notes file from the starter template",
        )
        parser.add_argument(
            SetupOption.CREATE_LABELS,
            action="store_true",
            help="create the labels the repository lacks; off by default, since labels "
            "are visible to everyone who can see the repository",
        )
        parsed = parser.parse_args(arguments)
        if bool(parsed.name) != bool(parsed.email):
            parser.error(
                f"{SetupOption.NAME} and {SetupOption.EMAIL} go together: a commit "
                f"needs both"
            )
        if parsed.email and not EmailAddress.is_well_formed(parsed.email):
            parser.error(
                f"{SetupOption.EMAIL}: "
                f"{MalformedEmailAddressError(parsed.email).error_message()}"
            )
        return cls(
            remote=parsed.remote,
            identity=(
                GitIdentity(parsed.name, EmailAddress.parse(parsed.email))
                if parsed.name
                else None
            ),
            starter_notes=parsed.starter_notes,
            create_labels=parsed.create_labels,
        )


# %% the run


@dataclass(frozen=True)
class PersonalNotesSetup:
    """
    One setup run against one clone, in the order the steps depend on each other.
    """

    project_root: Path
    """
    The clone being set up.
    """

    environment: Mapping[str, str]
    """
    The environment the hook scripts run in and the settings resolve from.
    """

    request: SetupRequest
    """
    What the run was asked to do.
    """

    github: GitHubRepository | None
    """
    Access to the repository the notes remote names, or ``None`` when that is not a
    GitHub repository or no credential is available - which leaves the owner and the
    labels unchecked rather than stopping the run.
    """

    plugin_root: PluginRoot
    """
    The plugin whose hook scripts the steps run.
    """

    @classproperty
    def starter_notes_message(cls) -> str:
        """
        The commit message a new notes file is seeded with.
        """
        return "Initialize personal notes from the starter template"

    @classmethod
    def for_clone(
        cls, project_root: Path, environment: Mapping[str, str], request: SetupRequest
    ) -> PersonalNotesSetup:
        """
        Set up a run with whatever GitHub access the environment offers.

        :param project_root: The clone to set up.
        :param environment: The environment to run in.
        :param request: What the run was asked to do.
        :return: The run, not yet started.
        """
        repository = resolve_repository(project_root, request.remote)
        token = GitHubCredentialVariable.first_token()
        github = (
            None
            if repository is None or token is None
            else GitHubRepository(repository, GitHubApi(token))
        )
        return cls(
            project_root, environment, request, github, PluginRoot.locate(environment)
        )

    def run(self) -> PersonalNotesSetupExitCode:
        """
        Perform every step, then report what is true now.

        :return: The status check-setup.sh finishes with.
        :raises ForeignNotesRemoteError: If the notes remote is somebody else's.
        :raises HookScriptFailedError: If a step's hook script fails.
        :raises GitHubRequestFailed: If GitHub refuses a call a step depends on.
        """
        self.verify_notes_remote_owner()
        self.point_notes_remote()
        initial_report = SetupReport.from_completed_process(self.run_check_setup())
        self.create_notes_branch(initial_report)
        self.seed_notes(initial_report)
        self.record_identity()
        self.run_hook_script(PluginLocation.SESSION_START_SCRIPT)
        self.check_labels()
        final_check = self.run_check_setup()
        logger.info(
            f"Setup finished. check-setup.sh now reports:\n{final_check.stdout}"
        )
        final_report = SetupReport.from_completed_process(final_check)
        return PersonalNotesSetupExitCode(final_report.exit_code)

    def verify_notes_remote_owner(self) -> None:
        """
        Refuse a notes remote GitHub says is somebody else's.

        Without GitHub access the owner is reported unchecked and allowed, since
        ``--remote`` named it explicitly.

        :raises ForeignNotesRemoteError: If the remote's owner is not the credential's.
        """
        if self.github is None:
            logger.info(
                f"Did not verify who owns '{self.request.remote}': it names no GitHub "
                f"repository, or no GitHub token is set. Continuing, since "
                f"{SetupOption.REMOTE} named it."
            )
            return
        login = self.github.authenticated_login()
        if self.github.repository.owner.casefold() != login.casefold():
            raise ForeignNotesRemoteError(self.github.repository, login)
        logger.info(f"'{self.github.repository.full_name}' is owned by '{login}'.")

    def point_notes_remote(self) -> None:
        """
        Record the chosen remote in this clone's git config, where every hook reads it.
        """
        subprocess.run(
            [
                "git",
                "config",
                PersonalNotesSetting.REMOTE.git_config_key,
                self.request.remote,
            ],
            cwd=self.project_root,
            check=True,
        )
        logger.info(f"Notes remote set to '{self.request.remote}'.")

    def create_notes_branch(self, initial_report: SetupReport) -> None:
        """
        Create the notes branch unless check-setup.sh already found it.

        :param initial_report: check-setup.sh's report from before anything was written.
        """
        if (
            initial_report.results[SetupCheck.NOTES_BRANCH].status
            is not CheckStatus.NEEDS_SETUP
        ):
            logger.info("The notes branch already exists; left untouched.")
            return
        self.run_hook_script(PluginLocation.PERSONAL_NOTES_BRANCH_CREATION_SCRIPT)

    def seed_notes(self, initial_report: SetupReport) -> None:
        """
        Seed a notes file that did not exist before this run from the starter template,
        when asked to.

        :param initial_report: check-setup.sh's report from before anything was written.
        """
        if not self.request.starter_notes:
            return
        if (
            initial_report.results[SetupCheck.NOTES_FILE].status
            is not CheckStatus.NEEDS_SETUP
        ):
            logger.info("The notes file already exists; not seeding it.")
            return
        self.run_hook_script(
            PluginLocation.PERSONAL_NOTES_WRITER_SCRIPT,
            "--source",
            str(self.plugin_root.path_of(PluginSkillLocation.STARTER_NOTES)),
            "--destination",
            PersonalNotesSetting.PATH.resolve(self.project_root, self.environment),
            "--message",
            self.starter_notes_message,
        )

    def record_identity(self) -> None:
        """
        Record the requested identity on the notes branch, when given one.
        """
        if self.request.identity is None:
            return
        self.run_hook_script(
            PluginLocation.GIT_IDENTITY_SAVING_SCRIPT,
            "--name",
            self.request.identity.name,
            "--email",
            str(self.request.identity.email),
        )

    def check_labels(self) -> None:
        """
        Report which labels the repository lacks, and create them when asked to.

        A missing label blocks nothing else in the setup, so this never stops the run.
        """
        if self.github is None:
            logger.info("Did not check the repository's labels: no GitHub access.")
            return
        repository = self.github.repository.full_name
        present = self.github.labels()
        missing = [label for label in WorkflowLabel if label not in present]
        if not missing:
            logger.info(f"Every label this tooling uses is on '{repository}'.")
            return
        logger.info(f"Labels missing on '{repository}': {', '.join(missing)}")
        if not self.request.create_labels:
            logger.info(
                f"Not creating them; run again with {SetupOption.CREATE_LABELS}."
            )
            return
        for label in missing:
            self.github.create_label(label)
            logger.info(f"Created '{label}' on '{repository}'.")

    def run_check_setup(self) -> subprocess.CompletedProcess[str]:
        """
        Run check-setup.sh, whose non-zero status means only that something still needs
        setting up.

        :return: The finished process, its report captured.
        """
        return subprocess.run(
            Bash.locate(self.environment).command(
                self.plugin_root.path_of(PluginLocation.SETUP_CHECK_SCRIPT)
            ),
            cwd=self.project_root,
            env=dict(self.environment),
            capture_output=True,
            text=True,
        )

    def run_hook_script(self, script: PluginLocation, *arguments: str) -> None:
        """
        Run one hook script, its output going to this process's own.

        :param script: The script to run.
        :param arguments: Its arguments.
        :raises HookScriptFailedError: If it exits with a failure.
        """
        path = self.plugin_root.path_of(script)
        completed = subprocess.run(
            Bash.locate(self.environment).command(path, *arguments),
            cwd=self.project_root,
            env=dict(self.environment),
        )
        if completed.returncode != 0:
            raise HookScriptFailedError(path, completed.returncode)


# %% command line


def main(arguments: Sequence[str] | None = None) -> int:
    """
    Set up this clone as the command line asks, and report the result.

    :param arguments: The command line without the program name, defaulting to the
        process's own.
    :return: The process exit status.
    """
    request = SetupRequest.from_arguments(arguments)
    project_root = Path(
        os.environ.get(ClaudeCodeVariable.PROJECT_DIRECTORY, Path.cwd())
    )
    setup = PersonalNotesSetup.for_clone(project_root, os.environ, request)
    try:
        return setup.run()
    except CommandFailure as failure:
        logger.error(failure)
        return failure.exit_code
    except DomainModelFailure.errors as failure:
        logger.error(failure)
        return DomainModelFailure.exit_code_of(failure)


if __name__ == "__main__":
    sys.exit(main())
