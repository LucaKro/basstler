"""
The stacks GitHub groups a repository's pull requests in, and putting a pull request on
top of one.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from basstler.stack.exceptions import LayerTakenError
from basstler.standard_streams import StandardStreamHandler

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command prints through.
"""

# %% a stack as GitHub holds it


class StackKey(StrEnum):
    """
    The keys of a stack as GitHub's stacks API answers with it.
    """

    NUMBER = "number"
    """
    The stack's number in its repository, which addresses it in the API.
    """

    PULL_REQUESTS = "pull_requests"
    """
    Its pull requests, bottom first, each an object carrying its number.
    """


@dataclass(frozen=True)
class RegisteredStack:
    """
    A stack of pull requests as GitHub holds it.
    """

    number: int
    """
    The stack's number in its repository.
    """

    pull_requests: tuple[int, ...]
    """
    Its pull requests' numbers, bottom first.
    """

    @classmethod
    def from_json(cls, representation: Mapping[str, Any]) -> RegisteredStack:
        """
        :param representation: A stack as the stacks API answers with it.
        :return: The stack.
        """
        return cls(
            number=int(representation[StackKey.NUMBER]),
            pull_requests=tuple(
                int(entry[StackKey.NUMBER])
                for entry in representation[StackKey.PULL_REQUESTS]
            ),
        )

    def above(self, pull_request: int) -> tuple[int, ...]:
        """
        :param pull_request: One of the stack's pull requests.
        :return: The pull requests stacked on it, bottom first.
        """
        return self.pull_requests[self.pull_requests.index(pull_request) + 1 :]


# %% reading and extending the stacks


@dataclass(frozen=True)
class PullRequestStacks(ABC):
    """
    The stacks a repository's pull requests are grouped in, read and extended.
    """

    @abstractmethod
    def stack_containing(self, pull_request: int) -> RegisteredStack | None:
        """
        :param pull_request: A pull request's number.
        :return: The stack holding it, ``None`` when it is in none.
        """

    @abstractmethod
    def create_stack(self, pull_requests: Sequence[int]) -> RegisteredStack:
        """
        :param pull_requests: The pull requests, bottom first, each based on the branch
            of the one before it.
        :return: The stack created from them.
        """

    @abstractmethod
    def add_to_stack(
        self, stack_number: int, pull_requests: Sequence[int]
    ) -> RegisteredStack:
        """
        :param stack_number: The stack to extend.
        :param pull_requests: The pull requests to put on top of it, bottom first, the
            first based on the branch of its current top.
        :return: The extended stack.
        """


# %% putting a pull request on top


class RegistrationKey(StrEnum):
    """
    The keys of the document :meth:`StackRegistration.print_registration` prints.
    """

    STACK = "stack"
    """
    The stack's number, ``null`` when the pull request is a single layer.
    """

    PULL_REQUESTS = "pull_requests"
    """
    The stack's pull requests, bottom first.
    """


@dataclass(frozen=True)
class StackRegistration:
    """
    One pull request put on top of the stack the pull requests below it form.
    """

    stacks: PullRequestStacks
    """
    The repository's stacks.
    """

    below: tuple[int, ...]
    """
    The pull requests it builds on, bottom first, each based on the branch of the one
    before it.
    """

    pull_request: int
    """
    The pull request to put on top.
    """

    def register(self) -> RegisteredStack | None:
        """
        Create the stack from :attr:`below` and :attr:`pull_request` when none of them is
        in one yet, otherwise extend the one the top of :attr:`below` is in. Registering
        again changes nothing.

        :return: The stack on GitHub, or ``None`` when nothing lies below.
        :raises LayerTakenError: If the top of :attr:`below` already has other pull
            requests stacked on it.
        """
        if not self.below:
            return None
        top = self.below[-1]
        existing = self.stacks.stack_containing(top)
        if existing is None:
            return self.stacks.create_stack([*self.below, self.pull_request])
        if self.pull_request in existing.pull_requests:
            return existing
        above = existing.above(top)
        if above:
            raise LayerTakenError(top, above)
        return self.stacks.add_to_stack(existing.number, [self.pull_request])

    def print_registration(self) -> None:
        """
        Register the pull request and print the stack it ended up in, as one JSON
        document.
        """
        registered = self.register()
        logger.info(
            json.dumps(
                {
                    RegistrationKey.STACK: (
                        None if registered is None else registered.number
                    ),
                    RegistrationKey.PULL_REQUESTS: (
                        [self.pull_request]
                        if registered is None
                        else list(registered.pull_requests)
                    ),
                }
            )
        )
