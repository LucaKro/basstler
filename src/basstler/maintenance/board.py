"""
Reading fetched pull requests into the board the stack is derived from.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from basstler_domain_model.github.pull_request_fields import (
    PullRequestField,
    PullRequestHead,
    PullRequestPayload,
)
from basstler_domain_model.github.repository_client import CommitChecks
from basstler_domain_model.repository import Repository

from basstler.maintenance.description import PullRequestDescription
from basstler.stack.model import BoardKey, PullRequest

# %% what the export reads beside the fork's pull requests


class UpstreamPullRequestsKey(StrEnum):
    """
    The keys :class:`UpstreamPullRequests` is written to JSON under.
    """

    FORK = "fork"
    PULL_REQUEST_BY_BRANCH = "pull_request_by_branch"


@dataclass(frozen=True)
class UpstreamPullRequests:
    """
    The open upstream pull requests opened from the fork's branches.
    """

    fork: Repository
    """
    The fork whose branches are reviewed.
    """

    pull_request_by_branch: Mapping[str, int]
    """
    Each fork branch under review, and the upstream pull request reviewing it.
    """

    def pull_request_for(self, head: PullRequestHead) -> int | None:
        """:param head: A fork pull request's head.
        :return: The open upstream pull request opened from it, ``None`` when there is
            none or the head lives outside the fork."""
        if head.repository != self.fork.full_name:
            return None
        return self.pull_request_by_branch.get(head.branch)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> UpstreamPullRequests:
        """
        :param data: What :meth:`to_json` wrote.
        :return: The pull requests it holds.
        """
        return cls(
            Repository.parse(data[UpstreamPullRequestsKey.FORK]),
            dict(data[UpstreamPullRequestsKey.PULL_REQUEST_BY_BRANCH]),
        )

    def to_json(self) -> dict[str, Any]:
        """:return: The fork and each of its branches' upstream pull request."""
        return {
            UpstreamPullRequestsKey.FORK: self.fork.full_name,
            UpstreamPullRequestsKey.PULL_REQUEST_BY_BRANCH: dict(
                self.pull_request_by_branch
            ),
        }

    @classmethod
    def from_api_records(
        cls, records: Iterable[PullRequestPayload], fork: Repository
    ) -> UpstreamPullRequests:
        """
        :param records: The upstream's open pull requests.
        :param fork: The fork whose branches to look for.
        :return: The ones whose head is a branch of *fork*.
        :raises MissingPullRequestFieldError: If a record omits its number or its head.
        """
        pull_request_by_branch = {}
        for record in records:
            number = int(PullRequestField.NUMBER.read(record))
            head = PullRequestHead.from_record(record, number)
            if head.repository == fork.full_name:
                pull_request_by_branch[head.branch] = number
        return cls(fork, pull_request_by_branch)


# %% the export itself


@dataclass(frozen=True)
class BoardExport:
    """
    The fork's open pull requests, in the shape the derived stack is read from.
    """

    pull_requests: tuple[PullRequest, ...]
    """
    The exported pull requests.
    """

    @classmethod
    def from_api_records(
        cls,
        records: Iterable[PullRequestPayload],
        upstream_pull_requests: UpstreamPullRequests,
        checks: CommitChecks,
    ) -> BoardExport:
        """
        Build the export from what the REST API returned.

        :param records: The fork's open pull requests.
        :param upstream_pull_requests: The upstream pull requests open for the fork's
            branches.
        :param checks: Where the verdict over each head's checks is read from.
        :return: The export.
        :raises MissingPullRequestFieldError: If any record omits a required field.
        """
        return cls(
            tuple(
                cls._pull_request(record, upstream_pull_requests, checks)
                for record in records
            )
        )

    @staticmethod
    def _pull_request(
        record: PullRequestPayload,
        upstream_pull_requests: UpstreamPullRequests,
        checks: CommitChecks,
    ) -> PullRequest:
        """
        Read one fetched pull request into a board entry.

        :param record: The fetched pull request.
        :param upstream_pull_requests: The upstream pull requests open for the fork's
            branches.
        :param checks: Where the verdict over its head's checks is read from.
        :return: The board entry.
        :raises MissingPullRequestFieldError: If a required field is absent.
        """
        number = int(PullRequestField.NUMBER.read(record))
        head = PullRequestHead.from_record(record, number)
        description = PullRequestDescription(
            str(PullRequestField.BODY.read(record, number) or "")
        )
        return PullRequest(
            number=number,
            head=head.branch,
            base=PullRequestField.BASE.read(record, number),
            draft=bool(PullRequestField.DRAFT.read(record, number)),
            head_repository=head.repository,
            labels=PullRequestField.LABELS.read(record, number),
            check_state=checks.check_state(head.commit),
            upstream_pull_request=upstream_pull_requests.pull_request_for(head),
            session=description.session_link,
            title=PullRequestField.TITLE.read(record, number),
            description=description.prose,
            upstream_link=description.upstream_link,
        )

    def as_json(self) -> str:
        """:return: The export, in the document :meth:`basstler.stack.model.PullRequest.read_board` parses."""
        return json.dumps(
            {BoardKey.PULL_REQUESTS: [asdict(entry) for entry in self.pull_requests]},
            indent=2,
        )

    def write(self, path: Path) -> Path:
        """
        Write the export where the derived stack is read from.

        :param path: Where to write it.
        :return: The path written to.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.as_json() + "\n")
        return path
