"""
Reading the upstream's open pull requests through the fork's Action, for the board.
"""

from __future__ import annotations

import json

import pytest

import basstler.upstream_reviews.action.__main__
from basstler_domain_model.environment import (
    ClaudeCodeVariable,
)
from basstler.maintenance.board import UpstreamPullRequests
from basstler.maintenance.commands import MaintenancePass
from basstler_domain_model.git.commands import (
    GitCommandRunner,
)
from basstler_domain_model.github.repository_client import (
    GitHubRepository,
)
from basstler.maintenance.upstream_action import (
    OpenPullRequestsAnswer,
    OpenPullRequestsRequest,
    UpstreamReadRoute,
)
from basstler.upstream_reviews.action.__main__ import ActionCommand
from basstler.upstream_reviews.action.branch import ActionInstallation
from basstler.upstream_reviews.action.exchange import (
    ActionRequest,
    ActionRequestKind,
)
from basstler.upstream_reviews.action.read import ActionRead
from basstler.upstream_reviews.exceptions import ActionNotInstalled
from basstler.upstream_reviews.vocabulary import ActionFile

from ..maintenance.fork import (
    PullRequestHeadRepository,
    StackBranch,
    StackPullRequestNumber,
    an_api_record,
    make_configuration,
)
from ..maintenance.test_board import ListedRepository
from .test_action import BRANCH_OR_TAG, REQUEST_IDENTIFIER, UPSTREAM, Fork, fork

# %% the request and its answer


def a_request() -> OpenPullRequestsRequest:
    """:return: A request for the upstream's pull requests from the board's fork."""
    return OpenPullRequestsRequest(
        REQUEST_IDENTIFIER, UPSTREAM, PullRequestHeadRepository.FORK.value
    )


def an_upstream() -> ListedRepository:
    """:return: An upstream with one pull request from the fork and one from another."""
    return ListedRepository(
        records=(
            an_api_record(
                number=StackPullRequestNumber.UPSTREAM, head=StackBranch.PARENT
            ),
            an_api_record(
                number=StackPullRequestNumber.CHILD,
                head=StackBranch.CHILD,
                head_repository=PullRequestHeadRepository.ANOTHER_CONTRIBUTORS_FORK.value,
            ),
        )
    )


def test_a_sent_request_names_its_kind_and_reads_back_as_itself():
    sent = a_request().as_sent()

    assert ActionRequest.kind_of(sent) is ActionRequestKind.OPEN_PULL_REQUESTS
    assert OpenPullRequestsRequest.from_json(sent) == a_request()


def test_the_answer_holds_only_the_pull_requests_opened_from_the_fork():
    answer = OpenPullRequestsAnswer.answering(a_request(), an_upstream())

    assert answer.upstream_pull_requests == UpstreamPullRequests(
        PullRequestHeadRepository.FORK.value,
        {StackBranch.PARENT: StackPullRequestNumber.UPSTREAM},
    )


def test_a_committed_answer_reads_back_as_itself():
    answer = OpenPullRequestsAnswer.answering(a_request(), an_upstream())

    assert (
        OpenPullRequestsAnswer.from_json(json.loads(json.dumps(answer.to_json())))
        == answer
    )


# %% the run


def test_the_run_answers_an_open_pull_requests_request_from_the_upstream(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        GitHubRepository,
        "from_environment",
        classmethod(lambda cls, repository: an_upstream()),
    )
    request_file = tmp_path / ActionFile.REQUEST
    result_file = tmp_path / ActionFile.RESULT
    request_file.write_text(json.dumps(a_request().as_sent()))

    status = basstler.upstream_reviews.action.__main__.main(
        [
            ActionCommand.RUN,
            "--request",
            str(request_file),
            "--result",
            str(result_file),
        ]
    )

    assert status == 0
    assert OpenPullRequestsAnswer.from_json(
        json.loads(result_file.read_text())
    ) == OpenPullRequestsAnswer.answering(a_request(), an_upstream())


# %% the session's side


def test_the_answer_committed_for_the_request_is_collected(fork: Fork):
    ActionInstallation(BRANCH_OR_TAG).install(fork.branch)
    read = ActionRead(fork.branch, a_request(), answer_type=OpenPullRequestsAnswer)
    read.send()
    answer = OpenPullRequestsAnswer.answering(a_request(), an_upstream())
    fork.branch.write({ActionFile.RESULT: json.dumps(answer.to_json())}, "Answer")

    assert read.wait(timeout=0) == answer


def test_a_pass_reading_through_the_action_asks_the_fork_s_action_branch(fork: Fork):
    maintenance = MaintenancePass(
        configuration=make_configuration(),
        git=GitCommandRunner(fork.clone),
        board=fork.clone / "board.json",
        upstream_read=UpstreamReadRoute.ACTION,
    )

    with pytest.raises(ActionNotInstalled):
        maintenance.upstream_pull_requests()


# %% choosing the route


def test_a_cloud_session_reads_through_the_action(monkeypatch):
    monkeypatch.setenv(ClaudeCodeVariable.REMOTE, "true")

    assert UpstreamReadRoute.for_this_session() is UpstreamReadRoute.ACTION


def test_anywhere_else_reads_directly(monkeypatch):
    monkeypatch.delenv(ClaudeCodeVariable.REMOTE, raising=False)

    assert UpstreamReadRoute.for_this_session() is UpstreamReadRoute.DIRECT
