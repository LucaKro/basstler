"""
Tests for reading the verdict over a fork commit's checks through the REST client the
maintenance pass calls GitHub with.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

from basstler_domain_model.class_property import classproperty
from basstler_domain_model.github.clients import GitHubApi, RestAnswer
from basstler_domain_model.github.repository_client import GitHubRepository
from basstler_domain_model.github.routes import RepositoryRoute
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

# %% an API answering from recorded pages


@dataclass(frozen=True)
class ApiAnsweringFromPages(GitHubApi):
    """
    The API answering each listing from its recorded pages, each page linking to the
    next the way GitHub's ``Link`` header does.
    """

    token: str = ""

    pages_by_listing: Mapping[str, list[Any]] = field(default_factory=dict)
    """
    Every page of each listing, by the path its first page is read from.
    """

    @classproperty
    def page_marker(cls) -> str:
        """
        What the double appends to a listing's path to address one of its later pages.
        """
        return "#page="

    def attempt(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> RestAnswer:
        listing, _, page = path.partition(self.page_marker)
        pages = self.pages_by_listing.get(listing)
        if pages is None:
            return RestAnswer(HTTPStatus.NOT_FOUND, "")
        index = int(page or 0)
        link = (
            {"link": f'<{listing}{self.page_marker}{index + 1}>; rel="next"'}
            if index + 1 < len(pages)
            else {}
        )
        return RestAnswer(HTTPStatus.OK, json.dumps(pages[index]), link)


def a_repository_reporting(
    check_runs: RecordedRestResponse, status: RecordedRestResponse
) -> GitHubRepository:
    """
    :param check_runs: The recorded check-run pages of the head commit.
    :param status: The recorded commit-status pages of the head commit.
    :return: A client whose recorded check runs span more than one page.
    """
    repository = PullRequestHeadRepository.FORK.value
    route = RepositoryRoute(repository)
    return GitHubRepository(
        repository,
        ApiAnsweringFromPages(
            pages_by_listing={
                route.check_runs(HEAD_SHA): check_runs.load(),
                route.commit_status(HEAD_SHA): status.load(),
            }
        ),
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
