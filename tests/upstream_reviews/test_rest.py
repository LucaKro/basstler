"""
Tests for reading an upstream pull request over REST, the way a Claude Code session can.

A session's proxy refuses GraphQL but answers REST, including a review-threads route of
its own that carries each thread's resolved state. The REST reader parses those
responses into the same models the GraphQL reader builds, so the recordings here
describe the same pull request the GraphQL recordings do and the two readers are held to
the same report.
"""

from __future__ import annotations

from enum import StrEnum

import pytest

from basstler_domain_model.environment import (
    ClaudeCodeVariable,
)
from basstler_domain_model.repository import (
    Repository,
)
from basstler_domain_model.github.clients import (
    GitHubApi,
)
from basstler_domain_model.github.exceptions import (
    UpstreamPullRequestNotFound,
)
from basstler_domain_model.github.routes import RepositoryRoute
from basstler_domain_model.github.readers import (
    ReadSource,
    RestPullRequestReader,
    UpstreamPullRequestReader,
)
from basstler.upstream_reviews.report import (
    UpstreamPullRequestReport,
)
from basstler_domain_model.github.rest_models import (
    RestPullRequestSummary,
)
from basstler_domain_model.github.vocabulary import (
    CheckOutcome,
    PullRequestState,
    RollupState,
)

from .replay import (
    RecordedResponse,
    RecordedRestResponse,
    ReplayingClient,
    ReplayingRestClient,
)


class Example(StrEnum):
    """
    The identities the recorded responses were built around.
    """

    UPSTREAM_OWNER = "example-upstream"
    UPSTREAM_NAME = "example-repo"
    FORK_OWNER = "example-fork-owner"
    BRANCH = "some-branch"


class StubEnvironmentVariable(StrEnum):
    """
    The knobs the ``gh`` stub reads for a REST call.
    """

    REST_JSON = "STUB_GH_REST_JSON"
    CALL_LOG = "STUB_GH_CALL_LOG"


UPSTREAM = Repository(Example.UPSTREAM_OWNER, Example.UPSTREAM_NAME)
ROUTE = RepositoryRoute(UPSTREAM)
NUMBER = 513
HEAD_SHA = RestPullRequestSummary.from_json(
    RecordedRestResponse.PULL_REQUEST.load()
).head.sha


def pull_request_responses(
    check_runs: RecordedRestResponse = RecordedRestResponse.CHECK_RUNS,
    status: RecordedRestResponse = RecordedRestResponse.STATUS,
) -> dict[str, object]:
    """
    Every response reading the recorded pull request takes, keyed by the path it
    answers.

    :param check_runs: The check runs the head commit carries.
    :param status: The commit statuses it carries.
    :return: The responses.
    """
    return {
        ROUTE.pull_request(NUMBER): RecordedRestResponse.PULL_REQUEST.load(),
        ROUTE.reviews(NUMBER): RecordedRestResponse.REVIEWS.load(),
        ROUTE.review_comments(NUMBER): RecordedRestResponse.REVIEW_COMMENTS.load(),
        ROUTE.review_threads(NUMBER): RecordedRestResponse.REVIEW_THREADS.load(),
        ROUTE.check_runs(HEAD_SHA): check_runs.load(),
        ROUTE.commit_status(HEAD_SHA): status.load(),
    }


def make_reader(responses: dict[str, object]) -> RestPullRequestReader:
    """
    :param responses: What each path answers with.
    :return: A REST reader over them, for the recorded upstream and fork.
    """
    return RestPullRequestReader(
        ReplayingRestClient(responses), UPSTREAM, Example.FORK_OWNER
    )


def graphql_report(include_resolved: bool) -> str:
    """
    :param include_resolved: Whether to list resolved threads too.
    :return: The report the GraphQL reader renders for the recorded pull request.
    """
    snapshot = UpstreamPullRequestReader(
        ReplayingClient(
            [
                RecordedResponse.PULL_REQUEST_PAGE_ONE.load(),
                RecordedResponse.PULL_REQUEST_PAGE_TWO.load(),
            ]
        ),
        UPSTREAM,
        Example.FORK_OWNER,
    ).read_current_state(NUMBER)
    return UpstreamPullRequestReport(
        snapshot, include_resolved=include_resolved
    ).render()


# %% the same answer as GraphQL


@pytest.mark.parametrize("include_resolved", [False, True])
def test_the_rest_read_reports_what_the_graphql_read_does(include_resolved: bool):
    """
    Both readers describe one pull request, so a session and a runner tell the user the
    same thing.
    """
    snapshot = make_reader(pull_request_responses()).read_current_state(NUMBER)

    assert UpstreamPullRequestReport(
        snapshot, include_resolved=include_resolved
    ).render() == graphql_report(include_resolved)


# %% threads


def test_a_thread_gathers_its_comments_from_every_page():
    snapshot = make_reader(pull_request_responses()).read_current_state(NUMBER)

    [middle] = [thread for thread in snapshot.threads if thread.line == 506]
    assert [comment.author.login for comment in middle.comments] == [
        "first-reviewer",
        "pull-request-author",
    ]


def test_a_thread_carries_its_resolved_and_outdated_state():
    snapshot = make_reader(pull_request_responses()).read_current_state(NUMBER)

    assert [
        (thread.is_resolved, thread.is_outdated) for thread in snapshot.threads
    ] == [
        (True, False),
        (False, False),
        (False, True),
    ]


def test_a_comment_no_longer_listed_is_left_out_of_its_thread():
    """
    A comment deleted between the two reads leaves the rest of its thread readable.
    """
    responses = pull_request_responses()
    threads = RecordedRestResponse.REVIEW_THREADS.load()
    threads[1]["comment_ids"].append(1)
    responses[ROUTE.review_threads(NUMBER)] = threads

    snapshot = make_reader(responses).read_current_state(NUMBER)

    assert len(snapshot.threads[1].comments) == 2


# %% checks


def test_check_runs_and_commit_statuses_are_read_together_in_order():
    checks = make_reader(pull_request_responses()).read_current_state(NUMBER).checks

    assert [(result.name, result.outcome) for result in checks.results] == [
        ("test (krrood)", CheckOutcome.SUCCESS),
        ("test (robokudo)", CheckOutcome.FAILURE),
        ("test (coraplex)", CheckOutcome.PENDING),
        ("continuous-integration/legacy", CheckOutcome.ERROR),
    ]


def test_a_head_commit_nothing_reported_against_has_no_checks():
    responses = pull_request_responses(
        RecordedRestResponse.CHECK_RUNS_NONE, RecordedRestResponse.STATUS_NONE
    )

    assert make_reader(responses).read_current_state(NUMBER).checks is None


@pytest.mark.parametrize(
    ("outcomes", "verdict"),
    [
        ([CheckOutcome.SUCCESS, CheckOutcome.FAILURE], RollupState.FAILURE),
        ([CheckOutcome.SUCCESS, CheckOutcome.TIMED_OUT], RollupState.FAILURE),
        ([CheckOutcome.ERROR, CheckOutcome.PENDING], RollupState.ERROR),
        ([CheckOutcome.SUCCESS, CheckOutcome.PENDING], RollupState.PENDING),
        ([CheckOutcome.SUCCESS, CheckOutcome.EXPECTED], RollupState.EXPECTED),
        (
            [CheckOutcome.SUCCESS, CheckOutcome.NEUTRAL, CheckOutcome.SKIPPED],
            RollupState.SUCCESS,
        ),
    ],
)
def test_the_verdict_is_the_worst_outcome_reported(
    outcomes: list[CheckOutcome], verdict: RollupState
):
    """
    REST carries no rollup of its own, so the verdict GitHub computes is computed here,
    the way GitHub does.
    """
    assert RollupState.over(outcomes) is verdict


# %% finding the pull request a branch opened


def test_a_branch_resolves_to_its_open_pull_request_over_a_merged_one():
    reader = make_reader(
        {
            ROUTE.pull_requests_for_head(
                Example.FORK_OWNER, Example.BRANCH
            ): RecordedRestResponse.PULL_REQUESTS_FOR_BRANCH.load()
        }
    )

    assert reader.resolve_pull_request_number(Example.BRANCH) == NUMBER


def test_the_search_names_the_fork_owner_and_the_branch():
    path = ROUTE.pull_requests_for_head(Example.FORK_OWNER, Example.BRANCH)

    assert f"head={Example.FORK_OWNER}:{Example.BRANCH}" in path
    assert "state=all" in path


def test_a_branch_path_is_escaped_in_the_search():
    path = ROUTE.pull_requests_for_head(Example.FORK_OWNER, "feature/part&more")

    assert "feature%2Fpart%26more" in path


@pytest.mark.parametrize(
    "recorded",
    [
        RecordedRestResponse.PULL_REQUESTS_FOR_BRANCH_FOREIGN_OWNER,
        RecordedRestResponse.PULL_REQUESTS_FOR_BRANCH_NONE,
    ],
)
def test_a_branch_with_no_pull_request_of_the_forks_own_is_not_found(
    recorded: RecordedRestResponse,
):
    reader = make_reader(
        {
            ROUTE.pull_requests_for_head(
                Example.FORK_OWNER, Example.BRANCH
            ): recorded.load()
        }
    )

    with pytest.raises(UpstreamPullRequestNotFound):
        reader.resolve_pull_request_number(Example.BRANCH)


def test_a_closed_pull_request_that_merged_reads_as_merged():
    [merged, _] = RecordedRestResponse.PULL_REQUESTS_FOR_BRANCH.load()[0]

    summary = RestPullRequestSummary.from_json(merged)

    assert summary.branch_pull_request.state is PullRequestState.MERGED


# %% which reader runs


def test_a_claude_code_cloud_session_reads_over_rest(monkeypatch):
    monkeypatch.setenv(ClaudeCodeVariable.REMOTE, "true")

    assert ReadSource.detect() is ReadSource.REST


def test_anywhere_else_reads_over_graphql(monkeypatch):
    monkeypatch.delenv(ClaudeCodeVariable.REMOTE, raising=False)

    assert ReadSource.detect() is ReadSource.GRAPHQL


@pytest.mark.parametrize(
    ("source", "reader_type"),
    [
        (ReadSource.REST, RestPullRequestReader),
        (ReadSource.GRAPHQL, UpstreamPullRequestReader),
    ],
)
def test_each_source_builds_its_own_reader(source: ReadSource, reader_type: type):
    reader = source.reader(GitHubApi("a-token"), UPSTREAM, Example.FORK_OWNER)

    assert isinstance(reader, reader_type)
