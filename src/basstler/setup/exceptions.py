"""
The reasons the personal-notes setup stops before it finishes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler_domain_model.exceptions import (
    DataclassException,
)
from basstler.exit_codes import CommandFailure, PersonalNotesSetupExitCode
from basstler_domain_model.repository import (
    Repository,
)


@dataclass
class ForeignNotesRemoteError(CommandFailure, DataclassException, PermissionError):
    """
    Raised when GitHub says the notes remote belongs to somebody other than the caller.
    """

    @classproperty
    def exit_code(cls) -> PersonalNotesSetupExitCode:
        """:return: The status this kind of error ends a command with."""
        return PersonalNotesSetupExitCode.FOREIGN_NOTES_REMOTE

    repository: Repository
    """
    The repository the notes remote names.
    """

    login: str
    """
    The account the available credential belongs to.
    """

    def error_message(self) -> str:
        return (
            f"refusing to set up personal notes on '{self.repository.full_name}': it "
            f"is owned by '{self.repository.owner}', but the credential belongs to "
            f"'{self.login}'"
        )

    def suggest_correction(self) -> str:
        return "pass --remote naming your own fork"


@dataclass
class HookScriptFailedError(CommandFailure, DataclassException, RuntimeError):
    """
    Raised when a hook script a setup step runs exits with a failure.
    """

    @classproperty
    def exit_code(cls) -> PersonalNotesSetupExitCode:
        """:return: The status this kind of error ends a command with."""
        return PersonalNotesSetupExitCode.HOOK_SCRIPT_FAILED

    script: Path
    """
    The script that failed.
    """

    status: int
    """
    The status it exited with.
    """

    def error_message(self) -> str:
        return f"{self.script.name} exited with status {self.status}"

    def suggest_correction(self) -> str:
        return "its own output above says why; fix that and run the setup again"
