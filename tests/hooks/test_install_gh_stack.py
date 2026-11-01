"""
Tests for install-gh-stack.sh, which makes ``gh stack`` runnable in a session and says in
one line how that went.
"""

from __future__ import annotations

import subprocess
from enum import StrEnum
from pathlib import Path

import pytest

from basstler.locations import ClaudeCodeVariable, PluginLocation
from basstler.stack.cascading_rebase import StackExtensionArgument

from ..constants.environment import SearchPathVariable
from ..executable_stubs import ExecutableStubDirectory, path_hiding_executables
from ..scratch_repository import ScratchRepository
from .session_start_summary import SummaryMessage, summary_message

# %% the scratch layout


class InstallerVariable(StrEnum):
    """
    The variables the installer and the ``gh`` stub read, named once.
    """

    EXTENSIONS = "STUB_GH_EXTENSIONS"
    """
    The extensions the ``gh`` stub lists as installed.
    """

    GH_VERSION = "STUB_GH_VERSION"
    """
    The version the ``gh`` stub reports.
    """

    TOOLS_DIRECTORY = "GH_STACK_TOOLS_DIRECTORY"
    """
    Where the installer puts what it installs.
    """

    PINNED_GH_VERSION = "GH_STACK_GH_VERSION"
    """
    The oldest ``gh`` the installer accepts.
    """

    EXTENSION_DIRECTORY = "GH_STACK_EXTENSION_DIRECTORY"
    """
    Where the installer clones and builds the extension.
    """


INSTALLED_EXTENSION = (
    f"{StackExtensionArgument.EXECUTABLE} {StackExtensionArgument.STACK}"
)
"""
How ``gh extension list`` names the gh-stack extension once it is installed.
"""

PINNED_VERSION = "2.90.0"
"""
The ``gh`` version the tests pin the installer to, and the stub reports.
"""


@pytest.fixture
def installer_repository(scratch_repository: ScratchRepository) -> ScratchRepository:
    """
    :return: A scratch repository whose plugin carries the installer and the files it
        sources.
    """
    scratch_repository.install_hook_scripts(
        PluginLocation.PERSONAL_NOTES_CONFIGURATION_SCRIPT.name,
        PluginLocation.SESSION_START_MESSAGES_SCRIPT.name,
        PluginLocation.STACK_EXTENSION_INSTALLATION_SCRIPT.name,
    )
    return scratch_repository


def run_installer(
    repository: ScratchRepository,
    stub_bin: ExecutableStubDirectory,
    tools_directory: Path,
    search_path: str | None = None,
    **environment: str,
) -> subprocess.CompletedProcess[str]:
    """
    :param repository: The fixture-built scratch repository.
    :param stub_bin: The stub directory, with ``gh`` installed into it.
    :param tools_directory: Where the installer may put what it installs.
    :param search_path: The ``PATH`` to fall back to behind the stubs.
    :param environment: Further variables to set for the run.
    :return: The finished installer.
    """
    stub_bin.install(StackExtensionArgument.EXECUTABLE)
    return repository.run_hook_script(
        PluginLocation.STACK_EXTENSION_INSTALLATION_SCRIPT.name,
        **{
            SearchPathVariable.EXECUTABLE_SEARCH_PATH: (
                stub_bin.ahead_of_inherited_path()
                if search_path is None
                else stub_bin.ahead_of(search_path)
            ),
            InstallerVariable.TOOLS_DIRECTORY: str(tools_directory),
            InstallerVariable.PINNED_GH_VERSION: PINNED_VERSION,
            InstallerVariable.GH_VERSION: PINNED_VERSION,
            **environment,
        },
    )


# %% reporting


def test_an_installed_extension_is_reported_ready(
    installer_repository: ScratchRepository,
    stub_bin: ExecutableStubDirectory,
    tmp_path: Path,
):
    result = run_installer(
        installer_repository,
        stub_bin,
        tmp_path / "tools",
        **{InstallerVariable.EXTENSIONS: INSTALLED_EXTENSION},
    )

    assert result.returncode == 0
    assert result.stdout == (
        summary_message(SummaryMessage.GH_STACK_READY, PINNED_VERSION) + "\n"
    )


def test_a_local_session_is_told_what_to_install_and_nothing_is_installed(
    installer_repository: ScratchRepository,
    stub_bin: ExecutableStubDirectory,
    tmp_path: Path,
):
    """
    Downloading a second ``gh`` onto someone's own machine at every session start is not
    the hook's call to make.
    """
    tools = tmp_path / "tools"

    result = run_installer(installer_repository, stub_bin, tools)

    assert result.returncode == 1
    assert result.stdout.startswith(
        summary_message(SummaryMessage.GH_STACK_UNAVAILABLE, "")
    )
    assert not tools.exists()


def test_a_cloud_session_without_go_reports_why_it_cannot_build_the_extension(
    installer_repository: ScratchRepository,
    stub_bin: ExecutableStubDirectory,
    tmp_path: Path,
):
    """
    The extension has to be built from source there, so a session without Go stops
    before cloning anything and says so rather than failing the session start.
    """
    tools = tmp_path / "tools"

    result = run_installer(
        installer_repository,
        stub_bin,
        tools,
        search_path=path_hiding_executables(tmp_path, "go"),
        **{ClaudeCodeVariable.REMOTE: "true"},
    )

    extension_directory = installer_repository.configuration_output(
        f'printf "%s" "${{{InstallerVariable.EXTENSION_DIRECTORY}}}"',
        **{InstallerVariable.TOOLS_DIRECTORY: str(tools)},
    )
    assert result.returncode == 1
    assert result.stdout.startswith(
        summary_message(SummaryMessage.GH_STACK_UNAVAILABLE, "")
    )
    assert not Path(extension_directory).exists()
