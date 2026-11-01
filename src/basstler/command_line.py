"""
One command of a command-line tool, and how a tool finds every one of its commands.

A command owns its own name, its own flags and its own work, so adding one is writing a
subclass: :meth:`Command.every_command` finds it, and nothing else has to be told it
exists. What a command is handed to run and what it answers with differ per tool, so each
tool's own base declares ``run``.
"""

from __future__ import annotations

import argparse
import functools
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TypeVar

from basstler_domain_model.class_property import (
    classproperty,
)

# %% a command


CommandType = TypeVar("CommandType", bound="Command")
"""
The concrete command type one tool's commands are found as.
"""


@dataclass(frozen=True)
class Command(ABC):
    """
    One operation a command-line tool offers.

    The name and the description belong to the class rather than to an instance - the
    parser reads them to build the command line before anything is constructed - so they
    are :class:`basstler_domain_model.class_property.classproperty` members. The name follows from the
    class name; the description is abstract, and :meth:`every_command` builds every
    subclass, so a command that cannot say what it does is refused as its tool is
    imported.
    """

    @classmethod
    @functools.cache
    def every_command(cls: type[CommandType]) -> tuple[CommandType, ...]:
        """
        Every command of the tool whose base this is asked of, in the order they are
        defined.

        Asked of a tool's own base, never of :class:`Command` itself: only direct
        subclasses are found.

        :return: One instance of every direct subclass of the class asked.
        """
        return tuple(subclass() for subclass in cls.__subclasses__())

    @classproperty
    def invoked_as(cls) -> str:
        """
        The name it is invoked by on the command line: its class name in lower case,
        words joined by hyphens, without a trailing ``Command``.
        """
        words = cls.__name__.removesuffix(Command.__name__)
        return re.sub(r"(?<!^)(?=[A-Z])", "-", words).lower()

    @classproperty
    @abstractmethod
    def description(cls) -> str:
        """
        What it does, as ``--help`` puts it.
        """

    def declare_arguments(self, parser: argparse.ArgumentParser) -> None:
        """
        Declare this command's own flags.

        Concrete rather than abstract: most commands take none, and requiring an empty
        override of every one of them would say nothing.

        :param parser: The subparser to declare them on.
        """
