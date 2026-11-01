"""
Reading the upstream's open pull requests through the fork's own Action, for a session
whose credential cannot reach the upstream's API.

The session pushes an :class:`OpenPullRequestsRequest` to the fork's Action branch; the
run reads the upstream with the runner's own token and commits an
:class:`OpenPullRequestsAnswer`; the session fetches it. Nothing is ever written to the
upstream.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler_domain_model.environment import (
    ClaudeCodeVariable,
)
from basstler.maintenance.board import UpstreamPullRequests
from basstler_domain_model.github.repository_client import (
    PullRequestReader,
)
from basstler_domain_model.repository import (
    Repository,
)
from basstler.upstream_reviews.action.exchange import (
    ActionAnswer,
    ActionRequest,
    ActionRequestKind,
)
from basstler.upstream_reviews.action.recording import ActionJSONKey

# %% vocabulary


class UpstreamReadRoute(StrEnum):
    """
    How a pass reads the upstream's open pull requests.
    """

    DIRECT = "direct"
    """
    Through the GitHub API, with the session's own credential.
    """

    ACTION = "action"
    """
    Through the fork's Action, with the runner's credential.
    """

    @classmethod
    def for_this_session(cls) -> UpstreamReadRoute:
        """
        :return: :attr:`ACTION` in a Claude Code cloud session, whose proxy refuses the
            upstream's API until the upstream is attached; :attr:`DIRECT` elsewhere.
        """
        if ClaudeCodeVariable.REMOTE.read() == "true":
            return cls.ACTION
        return cls.DIRECT


class OpenPullRequestsKey(StrEnum):
    """
    The keys of a request and of its answer.
    """

    UPSTREAM = "upstream"
    FORK = "fork"
    UPSTREAM_PULL_REQUESTS = "upstream_pull_requests"


# %% the exchange


@dataclass(frozen=True)
class OpenPullRequestsRequest(ActionRequest):
    """
    A request for the upstream's open pull requests opened from the fork's branches.
    """

    upstream: Repository
    """
    The repository to read.
    """

    fork: Repository
    """
    The fork whose branches to look for.
    """

    @classproperty
    def kind(cls) -> ActionRequestKind:
        """
        The read it asks for, which the run dispatches on.
        """
        return ActionRequestKind.OPEN_PULL_REQUESTS

    @classmethod
    def fresh(cls, upstream: Repository, fork: Repository) -> OpenPullRequestsRequest:
        """
        :param upstream: The repository to read.
        :param fork: The fork whose branches to look for.
        :return: A request with an identifier of its own.
        """
        return cls(secrets.token_hex(8), upstream, fork)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> OpenPullRequestsRequest:
        """
        :param data: A pushed request.
        :return: The parsed request.
        """
        return cls(
            data[ActionJSONKey.REQUEST_IDENTIFIER],
            Repository.parse(data[OpenPullRequestsKey.UPSTREAM]),
            Repository.parse(data[OpenPullRequestsKey.FORK]),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            ActionJSONKey.REQUEST_IDENTIFIER: self.identifier,
            OpenPullRequestsKey.UPSTREAM: self.upstream.full_name,
            OpenPullRequestsKey.FORK: self.fork.full_name,
        }


@dataclass(frozen=True)
class OpenPullRequestsAnswer(ActionAnswer):
    """
    The upstream pull requests the run found opened from the fork's branches.
    """

    upstream_pull_requests: UpstreamPullRequests
    """
    Each fork branch under upstream review, and the pull request reviewing it.
    """

    @classmethod
    def answering(
        cls, request: OpenPullRequestsRequest, upstream: PullRequestReader
    ) -> OpenPullRequestsAnswer:
        """
        :param request: The branch's request.
        :param upstream: What reads the upstream, with the runner's credential.
        :return: What the run commits.
        """
        return cls(
            request.identifier,
            UpstreamPullRequests.from_api_records(
                upstream.open_pull_requests(), request.fork
            ),
        )

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> OpenPullRequestsAnswer:
        """
        :param data: A committed answer.
        :return: The parsed answer.
        """
        return cls(
            data[ActionJSONKey.REQUEST_IDENTIFIER],
            UpstreamPullRequests.from_json(
                data[OpenPullRequestsKey.UPSTREAM_PULL_REQUESTS]
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            ActionJSONKey.REQUEST_IDENTIFIER: self.request_identifier,
            OpenPullRequestsKey.UPSTREAM_PULL_REQUESTS: (
                self.upstream_pull_requests.to_json()
            ),
        }
