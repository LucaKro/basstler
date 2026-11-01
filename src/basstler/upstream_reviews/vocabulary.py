"""
Where the fork's upstream-reviews Action lives, and where what it runs comes from.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from basstler_domain_model.class_property import classproperty

# %% the fork's Action branch


class ActionBranch(StrEnum):
    """
    Where the Action lives in the fork.
    """

    NAME = "basstler/upstream-reviews"
    """
    The branch, outside both the fork's default branch and the ``claude/`` branches
    stack maintenance owns.
    """

    LOCAL_REFERENCE = "refs/basstler/upstream-reviews"
    """
    Where a fetch of it is kept, so it never moves ``FETCH_HEAD`` under anyone else.
    """

    @classproperty
    def install_command(cls) -> str:
        """
        How a reader is told to install the branch or bring it up to date.
        """
        return (
            '"${BASSTLER_PYTHON}" -m basstler.setup.steps install-upstream-reviews-action '
            "--branch-or-tag <the basstler release the session runs> "
            "(/setup-personal-notes runs it)"
        )


class ActionFile(StrEnum):
    """
    Every file on the branch.
    """

    WORKFLOW = ".github/workflows/upstream-reviews.yml"
    README = "README.md"
    REQUEST = "request.json"
    RESULT = "result.json"

    @property
    def file_name(self) -> str:
        """:return: The file's own name, without its directory."""
        return Path(self.value).name


class BasstlerSource(StrEnum):
    """
    Where the reusable workflow and the package a run installs come from.
    """

    REPOSITORY = "AbdelrhmanBassiouny/basstler"
    REUSABLE_WORKFLOW = ".github/workflows/upstream-reviews-action.yml"
