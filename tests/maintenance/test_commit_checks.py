"""
Tests for reading the verdict over a fork commit's checks through the REST client the
maintenance pass calls GitHub with.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

from basstler_domain_model.github.repository_client import (
    CommitReport,
    GitHubRepository,
    ListingParameter,
    RepositoryPath,
)
from basstler_domain_model.github.exceptions import (
    GitHubRequestFailed,
)
from basstler_domain_model.github.vocabulary import (
    RollupState,
)

from ..upstream_reviews.replay import RecordedRestResponse
from ..upstream_reviews.test_rest import (
    HEAD_SHA,
    NUMBER,
    make_reader,
    pull_request_responses,
)
from .fork import PullRequestHeadRepository

# %% a client answering from recorded pages


@dataclass(frozen=True)
class RepositoryAnsweringFromPages(GitHubRepository):
    """
    A repository client answering each listing from its recorded pages, one per page
    number.
    """

    pages_by_listing: Mapping[str, list[Any]] = field(default_factory=dict)
    """
    Every page of each listing, by the listing's path without its query.
    """

    def _call(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Any:
        listing, query = path.split("?")
        parameters = dict(parameter.split("=") for parameter in query.split("&"))
        pages = self.pages_by_listing.get(listing)
        if pages is None:
            raise GitHubRequestFailed(
                status=HTTPStatus.NOT_FOUND, detail="", method=method, path=path
            )
        return pages[int(parameters[ListingParameter.PAGE]) - 1]


def commit_report_path(report: CommitReport) -> str:
    """
    :param report: Which listing of the recorded head commit to read.
    :return: Its path below the repository.
    """
    return f"{RepositoryPath.COMMITS}/{HEAD_SHA}/{report}"


def a_repository_reporting(
    check_runs: RecordedRestResponse, status: RecordedRestResponse
) -> RepositoryAnsweringFromPages:
    """
    :param check_runs: The recorded check-run pages of the head commit.
    :param status: The recorded commit-status pages of the head commit.
    :return: A client whose pages hold two entries, so the recorded check runs span
        more than one page.
    """
    return RepositoryAnsweringFromPages(
        repository=PullRequestHeadRepository.FORK.value,
        token="",
        page_size=2,
        pages_by_listing={
            commit_report_path(CommitReport.CHECK_RUNS): check_runs.load(),
            commit_report_path(CommitReport.STATUS): status.load(),
        },
    )


# %% reading the verdict


def test_the_verdict_covers_every_page_of_check_runs_and_the_commit_statuses():
    repository = a_repository_reporting(
        RecordedRestResponse.CHECK_RUNS, RecordedRestResponse.STATUS
    )
    expected = make_reader(pull_request_responses()).read_current_state(NUMBER).checks

    assert repository.check_state(HEAD_SHA) is expected.state


def test_a_commit_nothing_reported_on_is_not_reported():
    repository = a_repository_reporting(
        RecordedRestResponse.CHECK_RUNS_NONE, RecordedRestResponse.STATUS_NONE
    )

    assert repository.check_state(HEAD_SHA) is RollupState.NOT_REPORTED
