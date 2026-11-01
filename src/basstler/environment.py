"""
The environment variables this package reads, grouped by who sets them.

Every group is an :class:`EnvironmentVariable`, so reading one is the same call
whichever group it belongs to, and a variable is named once rather than typed out where
it is read.
"""

from __future__ import annotations

import os
from enum import StrEnum


class EnvironmentVariable(StrEnum):
    """
    An environment variable, named by its value.

    The base every group of them extends.
    """

    def read(self) -> str | None:
        """
        :return: The variable's value in this process's environment, or ``None`` when it
            is not set.
        """
        return os.environ.get(self)


class OperatingSystemVariable(EnvironmentVariable):
    """
    The variables the operating system sets for every process.
    """

    EXECUTABLE_SEARCH_PATH = "PATH"
    """
    The directories a program is searched for in.
    """

    WINDOWS_DIRECTORY = "SYSTEMROOT"
    """
    The Windows installation's own directory, set only on Windows, spelled as Windows
    lists it in a copy of the environment.
    """


class GitHubCredentialVariable(EnvironmentVariable):
    """
    The variables a GitHub token is read from, in the order they are tried.
    """

    GH_TOKEN = "GH_TOKEN"
    """
    The token the ``gh`` CLI reads, preferred where both are set.
    """

    GITHUB_TOKEN = "GITHUB_TOKEN"
    """
    The token GitHub Actions hands a workflow.
    """

    @classmethod
    def first_token(cls) -> str | None:
        """
        :return: The first non-empty token, in the order the variables are tried, or
            ``None`` when none is set.
        """
        return next((token for variable in cls if (token := variable.read())), None)


class GitHubCommandLineVariable(EnvironmentVariable):
    """
    The variables the ``gh`` CLI and its extensions read besides a token.
    """

    REPOSITORY = "GH_REPO"
    """
    The ``owner/name`` every API call is made against, instead of a repository guessed
    from the checkout's remotes.
    """


class GitHubActionsVariable(EnvironmentVariable):
    """
    The variables a GitHub Actions runner sets for the steps it runs.
    """

    STEP_SUMMARY = "GITHUB_STEP_SUMMARY"
    """
    The file a step appends its Markdown job summary to.
    """

    OUTPUT = "GITHUB_OUTPUT"
    """
    The file a step appends its ``<key>=<value>`` outputs to.
    """

    RUNNER_TEMPORARY_DIRECTORY = "RUNNER_TEMP"
    """
    A directory the runner empties for each job, which steps hand files through.
    """

    REPOSITORY_OWNER = "GITHUB_REPOSITORY_OWNER"
    """
    The owner of the repository the workflow runs in.
    """
