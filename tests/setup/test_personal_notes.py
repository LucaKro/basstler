"""
Tests for the one-time personal-notes setup that needs no session: its command line, the
full setup it performs, and its safety around a remote someone else owns and labels it
was not asked to create.

The setup runs in-process against a scratch project root whose notes remote is a local
bare repository, and with GitHub answered from memory, so a full run needs no network
and no credential. The hook scripts it drives run for real.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from basstler.exceptions import MalformedEmailAddressError
from basstler.exit_codes import PersonalNotesSetupExitCode
from basstler.workflow_labels import WorkflowLabel
from basstler_domain_model.environment import (
    ClaudeCodeVariable,
)
from basstler.locations import (
    PluginLocation,
    PluginSkillLocation,
    ProjectLocation,
)
from basstler_domain_model.github.repository_client import GitHubPath, GitHubRepository
from basstler_domain_model.github.vocabulary import RestJSONKey
from basstler.plugin_root import PluginRoot
from basstler_domain_model.repository import (
    Repository,
)
from basstler.setup.exceptions import ForeignNotesRemoteError
from basstler.setup.personal_notes import (
    PersonalNotesSetup,
    SetupOption,
    SetupRequest,
)
from basstler.setup.report import CheckStatus, SetupCheck, SetupReport
from basstler.setup.steps import PersonalNotesSetting

from ..constants.environment import BashStubVariable
from ..constants.notes import ScratchBranch
from ..constants.paths import PersonalNotesPath, RepositoryLocation
from ..executable_stubs import ExecutableStubDirectory
from ..scratch_repository import SCRATCH_IDENTITY, GitIdentity, ScratchRepository

SCRIPTS_UNDER_TEST = (
    PluginLocation.PERSONAL_NOTES_CONFIGURATION_SCRIPT.name,
    PluginLocation.SETUP_CHECK_SCRIPT.name,
    PluginLocation.PERSONAL_NOTES_BRANCH_CREATION_SCRIPT.name,
    PluginLocation.PERSONAL_NOTES_WRITER_SCRIPT.name,
    PluginLocation.GIT_IDENTITY_SAVING_SCRIPT.name,
    PluginLocation.SESSION_START_SCRIPT.name,
    PluginLocation.SESSION_START_MESSAGES_SCRIPT.name,
)
"""
The hook scripts a full setup run drives, and what they source.
"""

CREDENTIAL_OWNER = Repository("the-credential-owner", "a-project")
"""
A repository owned by the account the credential belongs to.
"""

SOMEONE_ELSE = Repository("someone-else", "a-project")
"""
A repository owned by anybody but the credential's owner, which must be refused.
"""

# %% GitHub, answered from memory


@dataclass(frozen=True)
class GitHubAnsweringFromMemory(GitHubRepository):
    """
    A repository client whose account and labels live in memory, recording every label
    it is asked to create.
    """

    token: str = ""

    login: str = CREDENTIAL_OWNER.owner
    """
    The account the credential belongs to.
    """

    present_labels: set[str] = field(default_factory=set)
    """
    The labels the repository carries.
    """

    created_labels: list[Mapping[str, Any]] = field(default_factory=list)
    """
    Every label-creation payload sent, in order.
    """

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Any:
        if path == str(GitHubPath.AUTHENTICATED_USER):
            return {RestJSONKey.LOGIN: self.login}
        if payload is not None:
            self.created_labels.append(payload)
            self.present_labels.add(payload[RestJSONKey.NAME])
            return payload
        return [{RestJSONKey.NAME: label} for label in sorted(self.present_labels)]


# %% the scratch layout


@pytest.fixture
def setup_repository(scratch_repository: ScratchRepository) -> ScratchRepository:
    """
    A scratch repository that is deliberately not set up yet: the plugin, the package
    and the project's tooling files are in place, but there is no notes branch, no notes
    remote configured and no ``CLAUDE.local.md``.

    :param scratch_repository: The initialized scratch repository and notes remote.
    :return: The same repository, ready for a setup run.
    """
    scratch_repository.install_hook_scripts(*SCRIPTS_UNDER_TEST)
    scratch_repository.install_package()
    scratch_repository.install_package_environment()
    scratch_repository.write_setup_prerequisites()
    starter_notes = PluginRoot(scratch_repository.plugin_root).path_of(
        PluginSkillLocation.STARTER_NOTES
    )
    starter_notes.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(
        PluginRoot(RepositoryLocation.PLUGIN.value).path_of(
            PluginSkillLocation.STARTER_NOTES
        ),
        starter_notes,
    )
    scratch_repository.commit_everything("initial commit")
    return scratch_repository


def identity_arguments(identity: GitIdentity) -> list[str]:
    """
    :param identity: The identity to record.
    :return: The options recording it.
    """
    return [SetupOption.NAME, identity.name, SetupOption.EMAIL, identity.email]


def setup_for(
    repository: ScratchRepository,
    *arguments: str,
    github: GitHubRepository | None = None,
    environment: Mapping[str, str] | None = None,
) -> PersonalNotesSetup:
    """
    :param repository: A fixture-built scratch repository.
    :param arguments: The command line after ``--remote``, which names the scratch
        notes remote.
    :param github: The GitHub access the run has, none by default.
    :param environment: The environment the run has, the scratch layout's own by
        default.
    :return: A setup run against the scratch layout's plugin.
    """
    return PersonalNotesSetup(
        project_root=repository.project_root,
        environment=(
            repository.runner_environment() if environment is None else environment
        ),
        request=SetupRequest.from_arguments(
            [SetupOption.REMOTE, str(repository.notes_remote_path), *arguments]
        ),
        github=github,
        plugin_root=PluginRoot(repository.plugin_root),
    )


def check_setup_report(repository: ScratchRepository) -> SetupReport:
    """
    :param repository: The scratch repository a setup ran in.
    :return: What check-setup.sh reports there now.
    """
    return SetupReport.from_completed_process(
        repository.run_hook_script(PluginLocation.SETUP_CHECK_SCRIPT.name)
    )


def published_notes_commit(repository: ScratchRepository) -> str:
    """
    :param repository: A fixture-built scratch repository.
    :return: The commit the notes branch is at on the notes remote, empty if it has none.
    """
    return repository.run_git(
        "ls-remote", str(repository.notes_remote_path), ScratchBranch.PERSONAL_NOTES
    ).stdout


def labels_setup(*arguments: str, github: GitHubRepository) -> PersonalNotesSetup:
    """
    :param arguments: The command line after ``--remote``.
    :param github: The GitHub access the run has.
    :return: A setup whose GitHub-facing steps can run on their own.
    """
    return PersonalNotesSetup(
        project_root=Path(),
        environment={},
        request=SetupRequest.from_arguments(
            [SetupOption.REMOTE, CREDENTIAL_OWNER.full_name, *arguments]
        ),
        github=github,
        plugin_root=PluginRoot(RepositoryLocation.PLUGIN.value),
    )


# %% the command line


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        [SetupOption.REMOTE, "origin", "--not-an-option"],
        [SetupOption.REMOTE, "origin", SetupOption.NAME, SCRATCH_IDENTITY.name],
        [
            SetupOption.REMOTE,
            "origin",
            SetupOption.NAME,
            SCRATCH_IDENTITY.name,
            SetupOption.EMAIL,
            "not-an-email-address",
        ],
    ],
    ids=["no remote", "unknown option", "name without email", "malformed email"],
)
def test_refuses_an_incomplete_or_unknown_command_line(arguments: list[str]):
    with pytest.raises(SystemExit) as refusal:
        SetupRequest.from_arguments(arguments)
    assert refusal.value.code == PersonalNotesSetupExitCode.USAGE


def test_explains_a_malformed_email_in_its_own_words(
    capsys: pytest.CaptureFixture[str],
):
    malformed = "not-an-email-address"
    with pytest.raises(SystemExit):
        SetupRequest.from_arguments(
            [
                SetupOption.REMOTE,
                "origin",
                SetupOption.NAME,
                SCRATCH_IDENTITY.name,
                SetupOption.EMAIL,
                malformed,
            ]
        )
    assert (
        MalformedEmailAddressError(malformed).error_message() in capsys.readouterr().err
    )


# %% a full setup


def test_completes_a_full_setup(setup_repository: ScratchRepository):
    exit_code = setup_for(setup_repository, *identity_arguments(SCRATCH_IDENTITY)).run()

    assert exit_code is PersonalNotesSetupExitCode.SUCCESS
    assert check_setup_report(setup_repository).exit_code == exit_code


def test_runs_every_hook_script_in_the_bash_claude_code_is_configured_with(
    setup_repository: ScratchRepository, tmp_path: Path
):
    stubs = ExecutableStubDirectory.create(tmp_path)
    stubs.install("bash")
    call_log = tmp_path / "bash-calls.log"
    setup = setup_for(
        setup_repository,
        environment=setup_repository.runner_environment(
            **{
                ClaudeCodeVariable.GIT_BASH_PATH: str(stubs.path / "bash"),
                BashStubVariable.CALL_LOG: str(call_log),
            }
        ),
    )
    setup.run()
    scripts_before_the_sync = [
        PluginLocation.SETUP_CHECK_SCRIPT,
        PluginLocation.PERSONAL_NOTES_BRANCH_CREATION_SCRIPT,
        PluginLocation.SESSION_START_SCRIPT,
    ]
    # session start's base-branch sync reads the stack configuration through
    # ``bash -c``, which runs in the configured bash too
    assert call_log.read_text().splitlines() == [
        *(str(setup.plugin_root.path_of(script)) for script in scripts_before_the_sync),
        "-c",
        str(setup.plugin_root.path_of(PluginLocation.SETUP_CHECK_SCRIPT)),
    ]


def test_points_the_notes_remote_at_the_one_it_was_given(
    setup_repository: ScratchRepository,
):
    setup_for(setup_repository, *identity_arguments(SCRATCH_IDENTITY)).run()

    configured = setup_repository.run_git(
        "config", "--get", PersonalNotesSetting.REMOTE.git_config_key
    )
    assert configured.stdout.strip() == str(setup_repository.notes_remote_path)


def test_leaves_a_new_notes_file_empty_without_starter_notes(
    setup_repository: ScratchRepository, tmp_path: Path
):
    setup_for(setup_repository, *identity_arguments(SCRATCH_IDENTITY)).run()

    checkout = setup_repository.clone_notes_branch(tmp_path / "notes")
    assert (checkout / ProjectLocation.PERSONAL_NOTES_DOCUMENT).read_text() == ""


def test_seeds_a_new_notes_file_from_the_starter_notes_when_asked(
    setup_repository: ScratchRepository, tmp_path: Path
):
    setup_for(
        setup_repository,
        *identity_arguments(SCRATCH_IDENTITY),
        SetupOption.STARTER_NOTES,
    ).run()

    checkout = setup_repository.clone_notes_branch(tmp_path / "notes")
    assert (
        checkout / ProjectLocation.PERSONAL_NOTES_DOCUMENT
    ).read_text() == PluginRoot(RepositoryLocation.PLUGIN.value).path_of(
        PluginSkillLocation.STARTER_NOTES
    ).read_text()


def test_never_seeds_over_notes_that_already_exist(
    setup_repository: ScratchRepository, tmp_path: Path
):
    setup_repository.publish_notes_branch(
        {ProjectLocation.PERSONAL_NOTES_DOCUMENT: "my own notes\n"}
    )

    setup_for(
        setup_repository,
        *identity_arguments(SCRATCH_IDENTITY),
        SetupOption.STARTER_NOTES,
    ).run()

    checkout = setup_repository.clone_notes_branch(tmp_path / "notes")
    assert (
        checkout / ProjectLocation.PERSONAL_NOTES_DOCUMENT
    ).read_text() == "my own notes\n"


def test_a_second_run_changes_nothing(setup_repository: ScratchRepository):
    arguments = (*identity_arguments(SCRATCH_IDENTITY), SetupOption.STARTER_NOTES)
    setup_for(setup_repository, *arguments).run()
    commit_after_first_run = published_notes_commit(setup_repository)

    exit_code = setup_for(setup_repository, *arguments).run()

    assert exit_code is PersonalNotesSetupExitCode.SUCCESS
    assert published_notes_commit(setup_repository) == commit_after_first_run


# %% the git identity every clone authors as


def test_records_the_git_identity_it_is_given(
    setup_repository: ScratchRepository, tmp_path: Path
):
    setup_for(setup_repository, *identity_arguments(SCRATCH_IDENTITY)).run()

    checkout = setup_repository.clone_notes_branch(tmp_path / "notes")
    recorded = GitIdentity.from_git_config_file(
        checkout / PersonalNotesPath.GIT_IDENTITY
    )
    assert recorded == SCRATCH_IDENTITY


def test_finishes_everything_else_when_given_no_identity(
    setup_repository: ScratchRepository,
):
    exit_code = setup_for(setup_repository).run()

    assert exit_code is PersonalNotesSetupExitCode.SETUP_INCOMPLETE
    report = check_setup_report(setup_repository)
    assert report.results[SetupCheck.GIT_IDENTITY].status is CheckStatus.NEEDS_SETUP
    assert report.results[SetupCheck.NOTES_FILE].status is CheckStatus.OK


# %% the remote's owner


def test_refuses_a_remote_someone_else_owns_before_writing_anything(
    setup_repository: ScratchRepository,
):
    github = GitHubAnsweringFromMemory(SOMEONE_ELSE)

    with pytest.raises(ForeignNotesRemoteError) as refusal:
        setup_for(
            setup_repository, *identity_arguments(SCRATCH_IDENTITY), github=github
        ).run()

    assert refusal.value.repository == SOMEONE_ELSE
    assert refusal.value.login == github.login
    unset = setup_repository.run_git_allowing_failure(
        "config", "--get", PersonalNotesSetting.REMOTE.git_config_key
    )
    assert unset.returncode != 0
    assert published_notes_commit(setup_repository) == ""


def test_accepts_a_remote_whose_owner_differs_only_in_case():
    github = GitHubAnsweringFromMemory(
        CREDENTIAL_OWNER, login=CREDENTIAL_OWNER.owner.upper()
    )

    labels_setup(github=github).verify_notes_remote_owner()


# %% pull request labels


def test_reports_missing_labels_without_creating_them():
    github = GitHubAnsweringFromMemory(
        CREDENTIAL_OWNER, present_labels={WorkflowLabel.MERGED}
    )

    labels_setup(github=github).check_labels()

    assert github.created_labels == []


def test_creates_only_the_missing_labels_when_asked():
    github = GitHubAnsweringFromMemory(
        CREDENTIAL_OWNER, present_labels=set(WorkflowLabel) - {WorkflowLabel.IN_REVIEW}
    )

    labels_setup(SetupOption.CREATE_LABELS, github=github).check_labels()

    assert github.created_labels == [
        {
            RestJSONKey.NAME: WorkflowLabel.IN_REVIEW,
            RestJSONKey.DESCRIPTION: WorkflowLabel.IN_REVIEW.description,
        }
    ]


def test_creates_every_label_a_fresh_repository_lacks():
    github = GitHubAnsweringFromMemory(CREDENTIAL_OWNER)

    labels_setup(SetupOption.CREATE_LABELS, github=github).check_labels()

    assert github.present_labels == set(WorkflowLabel)
