"""
What a session asks the fork's Action and what the Action answers, whatever the read.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler_domain_model.github.vocabulary import (
    JSONModel,
)

# %% vocabulary


class ActionRequestKind(StrEnum):
    """
    The reads the fork's Action answers.
    """

    PULL_REQUEST_REPORT = "pull-request-report"
    """
    The checks and review threads of one upstream pull request.
    """

    OPEN_PULL_REQUESTS = "open-pull-requests"
    """
    The upstream's open pull requests opened from the fork's branches.
    """


class ActionRequestKey(StrEnum):
    """
    The field of a sent request that names its kind.
    """

    KIND = "kind"


# %% the exchange


@dataclass(frozen=True)
class ActionAnswer(JSONModel, ABC):
    """
    What the Action commits for one request.
    """

    request_identifier: str
    """
    The request it answers.
    """

    @abstractmethod
    def to_json(self) -> dict[str, Any]:
        """:return: The answer as committed."""


AnswerType = TypeVar("AnswerType", bound=ActionAnswer)


@dataclass(frozen=True)
class ActionRequest(JSONModel, ABC):
    """
    One read a session pushes to the Action branch, which starts a run answering it.
    """

    identifier: str
    """
    A random token telling this request's answer apart from any other's.
    """

    @classproperty
    @abstractmethod
    def kind(cls) -> ActionRequestKind:
        """
        The read it asks for, which the run dispatches on.
        """

    @abstractmethod
    def to_json(self) -> dict[str, Any]:
        """:return: The request's own fields."""

    def as_sent(self) -> dict[str, Any]:
        """:return: The request as pushed: its fields and its kind."""
        return {ActionRequestKey.KIND: self.kind, **self.to_json()}

    @staticmethod
    def kind_of(data: dict[str, Any]) -> ActionRequestKind:
        """
        :param data: A pushed request.
        :return: The read it asks for.
        """
        return ActionRequestKind(data[ActionRequestKey.KIND])
