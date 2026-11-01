"""
The bash the plugin's scripts and its shell configuration run in.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from basstler.environment import OperatingSystemVariable
from basstler.exceptions import BashNotFoundError
from basstler_domain_model.environment import (
    ClaudeCodeVariable,
)

# %% the programs searched for


class LocatedProgram(StrEnum):
    """
    The programs searched for to find bash, by the name a search finds them under.
    """

    BASH = "bash"
    """
    The shell the scripts are written for.
    """

    GIT = "git"
    """
    Git, whose Windows installation ships Git Bash beside it.
    """


# %% the bash itself


@dataclass(frozen=True)
class Bash:
    """
    A bash program, named by its full path so Windows cannot start WSL's launcher in its
    place.
    """

    executable: Path
    """
    The program itself.
    """

    @classmethod
    def locate(cls, environment: Mapping[str, str]) -> Bash:
        """
        Find the bash to run scripts in: the one Claude Code is configured with, else the
        first on the search path, else the one installed with the git on the search
        path - never one in the Windows directory, which starts WSL.

        :param environment: The environment the scripts will run in.
        :return: The bash found.
        :raises BashNotFoundError: If every bash found is in the Windows directory, or
            none is found.
        """
        configured = environment.get(ClaudeCodeVariable.GIT_BASH_PATH)
        if configured:
            return cls(Path(configured))
        found = list(
            cls.candidates(
                environment.get(OperatingSystemVariable.EXECUTABLE_SEARCH_PATH)
            )
        )
        windows_directory = environment.get(OperatingSystemVariable.WINDOWS_DIRECTORY)
        usable = [
            candidate
            for candidate in found
            if not cls.is_within(candidate, windows_directory)
        ]
        if not usable:
            raise BashNotFoundError(passed_over=found)
        return cls(usable[0])

    @staticmethod
    def candidates(search_path: str | None) -> Iterator[Path]:
        """
        :param search_path: The directories to search, the process's own when ``None``.
        :return: Every bash found, in the order they are preferred: the first on the
            search path, then those in a ``bin`` directory of an ancestor of the first
            git on it.
        """
        on_search_path = shutil.which(LocatedProgram.BASH, path=search_path)
        if on_search_path is not None:
            yield Path(on_search_path)
        git = shutil.which(LocatedProgram.GIT, path=search_path)
        if git is None:
            return
        for installation in Path(git).parents:
            beside_git = shutil.which(
                LocatedProgram.BASH, path=str(installation / "bin")
            )
            if beside_git is not None:
                yield Path(beside_git)

    @staticmethod
    def is_within(program: Path, directory: str | None) -> bool:
        """
        :param program: A program found.
        :param directory: A directory, or ``None`` when there is none.
        :return: Whether *program* is inside *directory*, compared as the platform
            compares paths.
        """
        if directory is None:
            return False
        return Path(os.path.normcase(program)).is_relative_to(
            os.path.normcase(directory)
        )

    def command(self, *arguments: str | os.PathLike[str]) -> list[str]:
        """
        :param arguments: The script to run and its arguments, or ``-c`` and a command.
        :return: The command line running them in this bash.
        """
        return [str(self.executable), *(os.fspath(argument) for argument in arguments)]
