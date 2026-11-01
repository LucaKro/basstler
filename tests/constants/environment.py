"""
Environment variables and file modes the scratch-environment suites share.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

from basstler_domain_model.environment import (
    EnvironmentVariable,
)


class ScrubbedEnvironmentPrefix(StrEnum):
    """
    Variables a scratch run must not inherit, by the prefix of their name.

    The session running this suite legitimately has all of them set, and every one of them
    changes what a hook resolves - so a test asserting the default resolution has to be run
    without them rather than around them.
    """

    PERSONAL_NOTES = "CLAUDE_PERSONAL_NOTES_"
    """
    The personal-notes remote, branch and path overrides.
    """

    PROJECT_DIRECTORY = "CLAUDE_PROJECT_DIR"
    """
    The project Claude Code names, which the hooks resolve their project from first.
    """

    ENVIRONMENT_FILE = "CLAUDE_ENV_FILE"
    """
    The file a SessionStart hook exports variables to the session through.
    """

    BASSTLER = "BASSTLER_"
    """
    The plugin root, the package's home and anything else the configuration exports.
    """

    GIT_AUTHOR = "GIT_AUTHOR_"
    """
    The commit author git would otherwise take from the configuration.
    """

    GIT_COMMITTER = "GIT_COMMITTER_"
    """
    The committer git would otherwise take from the configuration.
    """


class ConfigurationVariable(StrEnum):
    """
    The variables of the shell configuration the tests read back, named once.
    """

    ENVIRONMENT_DIRECTORY = "BASSTLER_ENVIRONMENT_DIRECTORY"
    """
    The package's own environment.
    """

    ENVIRONMENT_INTERPRETER = "BASSTLER_ENVIRONMENT_INTERPRETER"
    """
    The interpreter of the package's own environment.
    """

    PYTHON = "BASSTLER_PYTHON"
    """
    The interpreter every caller runs a module of the package with.
    """

    INSTALLED_SOURCE_FILE = "BASSTLER_INSTALLED_SOURCE_FILE"
    """
    The file recording what the environment's package was installed from.
    """

    INSTALL_LOCK = "BASSTLER_INSTALL_LOCK"
    """
    The directory held while the package's environment is installed into.
    """

    INSTALL_WAIT_SECONDS = "BASSTLER_INSTALL_WAIT_SECONDS"
    """
    How long a caller waits for an install another process holds the lock for.
    """

    PYTHON_REQUIREMENT = "BASSTLER_PYTHON_REQUIREMENT"
    """
    The interpreters the package supports.
    """

    INTERPRETER_CANDIDATES = "BASSTLER_INTERPRETER_CANDIDATES"
    """
    The supported interpreters looked for by name, separated by spaces.
    """

    HOME = "BASSTLER_HOME"
    """
    Where the package's environment and its installed ``uv`` live.
    """

    REPOSITORY_URL = "BASSTLER_REPOSITORY_URL"
    """
    The repository an installed plugin's package is installed from.
    """

    RELEASE_FILE = "BASSTLER_RELEASE_FILE"
    """
    The file recording the latest release a session start read from that repository.
    """

    UV_DIRECTORY = "BASSTLER_UV_DIRECTORY"
    """
    Where a session start installs ``uv`` when it finds neither ``uv`` nor a supported
    interpreter.
    """

    INSTALL_MARKER = "BASSTLER_INSTALL_MARKER"
    """
    The file a session start holds its process id in while it installs the package.
    """

    PERSONAL_NOTES_REFERENCE = "PERSONAL_NOTES_REFERENCE"
    """
    The ref the personal-notes branch is fetched into.
    """


class SearchPathVariable(EnvironmentVariable):
    """
    The search-path variables a test sets or reads for a subprocess.
    """

    EXECUTABLE_SEARCH_PATH = "PATH"
    """
    The directories a shell searches for a command.
    """

    IMPORT_PATH = "PYTHONPATH"
    """
    The directories an interpreter searches for imports ahead of its installed packages.
    """


class FileMode(IntEnum):
    """
    The permission bits a test gives a file it writes.
    """

    EXECUTABLE = 0o755
    """
    Readable and runnable by everyone, writable by its owner.
    """


class BashStubVariable(StrEnum):
    """
    The variables ``dataset/stubs/bash.sh`` reads.
    """

    CALL_LOG = "STUB_BASH_CALL_LOG"
    """
    The file every script the stub is asked to run is recorded in, one per line.
    """
