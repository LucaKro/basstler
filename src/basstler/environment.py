"""
The environment variables this package reads, grouped by who sets them.

Every group is an :class:`EnvironmentVariable`, so reading one is the same call
whichever group it belongs to, and a variable is named once rather than typed out where
it is read.
"""

from __future__ import annotations

from basstler_domain_model.environment import EnvironmentVariable


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
