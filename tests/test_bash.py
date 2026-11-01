"""
Tests for finding the bash that hook scripts and the shell configuration run in, so a
Windows machine never starts WSL's launcher in place of Git Bash.

Each test lays out a search path of empty executables under ``tmp_path``, so the
platform being stood in for is the directory layout, not the machine running the tests.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from basstler.bash import Bash, LocatedProgram
from basstler.environment import OperatingSystemVariable
from basstler.exceptions import BashNotFoundError
from basstler_domain_model.environment import (
    ClaudeCodeVariable,
)

from .constants.environment import FileMode

# %% the layout


def executable(directory: Path, program: LocatedProgram) -> Path:
    """
    :param directory: Where to put it, created if missing.
    :param program: The program it stands in for.
    :return: An empty executable named for *program*, which a search finds.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / program
    path.touch()
    path.chmod(FileMode.EXECUTABLE)
    return path


def search_path(*directories: Path) -> str:
    """
    :param directories: The directories to search, in order.
    :return: Them, as a search path.
    """
    return os.pathsep.join(str(directory) for directory in directories)


# %% a bash that is not WSL's launcher


def test_prefers_the_bash_claude_code_is_configured_with(tmp_path: Path):
    configured = executable(tmp_path / "configured", LocatedProgram.BASH)
    on_path = tmp_path / "on-path"
    executable(on_path, LocatedProgram.BASH)
    environment = {
        ClaudeCodeVariable.GIT_BASH_PATH: str(configured),
        OperatingSystemVariable.EXECUTABLE_SEARCH_PATH: search_path(on_path),
    }
    assert Bash.locate(environment) == Bash(configured)


def test_takes_the_first_bash_on_the_search_path(tmp_path: Path):
    first = executable(tmp_path / "first", LocatedProgram.BASH)
    second = tmp_path / "second"
    executable(second, LocatedProgram.BASH)
    environment = {
        OperatingSystemVariable.EXECUTABLE_SEARCH_PATH: search_path(
            first.parent, second
        )
    }
    assert Bash.locate(environment) == Bash(first)


def test_passes_over_the_launcher_in_the_windows_directory_for_git_bash(
    tmp_path: Path,
):
    windows = tmp_path / "Windows"
    launcher_directory = windows / "System32"
    executable(launcher_directory, LocatedProgram.BASH)
    git_installation = tmp_path / "Git"
    git = executable(git_installation / "cmd", LocatedProgram.GIT)
    git_bash = executable(git_installation / "bin", LocatedProgram.BASH)
    environment = {
        OperatingSystemVariable.WINDOWS_DIRECTORY: str(windows),
        OperatingSystemVariable.EXECUTABLE_SEARCH_PATH: search_path(
            launcher_directory, git.parent
        ),
    }
    assert Bash.locate(environment) == Bash(git_bash)


# %% no usable bash


def test_refuses_when_only_the_windows_launcher_is_found(tmp_path: Path):
    windows = tmp_path / "Windows"
    launcher = executable(windows / "System32", LocatedProgram.BASH)
    environment = {
        OperatingSystemVariable.WINDOWS_DIRECTORY: str(windows),
        OperatingSystemVariable.EXECUTABLE_SEARCH_PATH: search_path(launcher.parent),
    }
    with pytest.raises(BashNotFoundError) as raised:
        Bash.locate(environment)
    assert raised.value.passed_over == [launcher]


def test_refuses_when_no_bash_is_found(tmp_path: Path):
    environment = {OperatingSystemVariable.EXECUTABLE_SEARCH_PATH: str(tmp_path)}
    with pytest.raises(BashNotFoundError) as raised:
        Bash.locate(environment)
    assert raised.value.passed_over == []


# %% the command it builds


def test_runs_its_arguments_in_the_located_bash(tmp_path: Path):
    bash = Bash(tmp_path / LocatedProgram.BASH)
    script = tmp_path / "script.sh"
    assert bash.command(script, "--flag") == [
        str(bash.executable),
        str(script),
        "--flag",
    ]
