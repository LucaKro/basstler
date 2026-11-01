"""
One read of an upstream pull request, as a session hands it to a helper session or to
the fork's Action.
"""

from __future__ import annotations

import argparse
import json
import secrets
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler_domain_model.repository import (
    Repository,
)
from basstler.upstream_reviews.action.exchange import (
    ActionRequest,
    ActionRequestKind,
)
from basstler_domain_model.github.clients import (
    JobLogReader,
)
from basstler.upstream_reviews.exceptions import (
    NoRecordedReadRequest,
)
from basstler.upstream_reviews.failure_logs import FailureLogReader
from basstler_domain_model.github.readers import (
    PullRequestSource,
)
from basstler.upstream_reviews.report import UpstreamPullRequestReport
from basstler_domain_model.github.vocabulary import (
    JSONModel,
)

# %% the request


class HelperJSONKey(StrEnum):
    """
    Every field name in a recorded request and in the recording the helper prints.
    """

    REQUEST_IDENTIFIER = "request_identifier"
    UPSTREAM = "upstream"
    FORK_OWNER = "fork_owner"
    PULL_REQUEST = "pull_request"
    BRANCH = "branch"
    FAILURE_LOGS = "failure_logs"
    RESPONSES = "responses"
    JOB_LOGS = "job_logs"
    PATH = "path"
    BODY = "body"
    JOB = "job"
    LOG = "log"


@dataclass(frozen=True)
class UpstreamReadRequest(ActionRequest):
    """
    One read of an upstream pull request: what to read, and how much of it.
    """

    upstream: Repository
    """
    The repository to read.
    """

    fork_owner: str
    """
    Whose branches to claim.
    """

    pull_request: int | None
    """
    The upstream pull request to read, when given by number.
    """

    branch: str | None
    """
    The fork branch whose upstream pull request to read, when given by branch.
    """

    failure_logs: bool
    """
    Whether to read the log behind each check that did not pass.
    """

    @classproperty
    def kind(cls) -> ActionRequestKind:
        """
        The read it asks for, which the run dispatches on.
        """
        return ActionRequestKind.PULL_REQUEST_REPORT

    @classproperty
    def location(cls) -> Path:
        """
        Where the latest request is recorded: scratch state of this container only.
        """
        return (
            Path(tempfile.gettempdir()) / "basstler-upstream-reviews" / "request.json"
        )

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> UpstreamReadRequest:
        """
        :param data: A recorded request.
        :return: The parsed request.
        """
        return cls(
            identifier=data[HelperJSONKey.REQUEST_IDENTIFIER],
            upstream=Repository.parse(data[HelperJSONKey.UPSTREAM]),
            fork_owner=data[HelperJSONKey.FORK_OWNER],
            pull_request=data[HelperJSONKey.PULL_REQUEST],
            branch=data[HelperJSONKey.BRANCH],
            failure_logs=data[HelperJSONKey.FAILURE_LOGS],
        )

    @classmethod
    def from_arguments(
        cls, arguments: argparse.Namespace, upstream: Repository
    ) -> UpstreamReadRequest:
        """
        :param arguments: A command line naming the pull request or branch, the fork
            owner and whether to read failure logs.
        :param upstream: The repository to read.
        :return: The request they describe, with a fresh identifier.
        """
        return cls(
            secrets.token_hex(8),
            upstream,
            arguments.fork_owner,
            arguments.pull_request,
            arguments.branch,
            arguments.failure_logs,
        )

    def to_json(self) -> dict[str, Any]:
        """:return: The request as recorded."""
        return {
            HelperJSONKey.REQUEST_IDENTIFIER: self.identifier,
            HelperJSONKey.UPSTREAM: str(self.upstream),
            HelperJSONKey.FORK_OWNER: self.fork_owner,
            HelperJSONKey.PULL_REQUEST: self.pull_request,
            HelperJSONKey.BRANCH: self.branch,
            HelperJSONKey.FAILURE_LOGS: self.failure_logs,
        }

    def save(self) -> None:
        """
        Record this as the latest request.
        """
        self.location.parent.mkdir(parents=True, exist_ok=True)
        self.location.write_text(json.dumps(self.to_json()))

    @classmethod
    def load(cls) -> UpstreamReadRequest:
        """
        :return: The latest recorded request.
        :raises NoRecordedReadRequest: If none was recorded.
        """
        if not cls.location.is_file():
            raise NoRecordedReadRequest(cls.location)
        return cls.from_json(json.loads(cls.location.read_text()))

    def report(
        self,
        reader: PullRequestSource,
        job_logs: JobLogReader,
        include_resolved: bool,
    ) -> str:
        """
        Read the requested pull request and render its report.

        :param reader: What reads the pull request.
        :param job_logs: What reads the log behind a failed check.
        :param include_resolved: Whether to list resolved threads too.
        :return: The rendered markdown.
        :raises UpstreamPullRequestNotFound: If the branch has no upstream pull request.
        """
        number = self.pull_request or reader.resolve_pull_request_number(
            self.branch or ""
        )
        snapshot = reader.read_current_state(number)
        failure_logs = (
            FailureLogReader(job_logs, self.upstream).read(snapshot.checks)
            if self.failure_logs
            else []
        )
        return UpstreamPullRequestReport(
            snapshot, include_resolved=include_resolved, failure_logs=failure_logs
        ).render()
