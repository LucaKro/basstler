"""
Tests for reading an upstream pull request through the fork's own Action branch.

The run's side is held to a direct read: what it records, once committed as JSON and
replayed, renders exactly the report the same responses render when read directly. The
session's side runs against a real git remote - a bare repository standing in for the
fork - so installing, requesting and collecting a result are exercised as git commands.
"""

from __future__ import annotations

from http import HTTPStatus

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import basstler.upstream_reviews.action.__main__
import basstler.upstream_reviews.action.run
from basstler.locations import PackageLocation
from basstler_domain_model.repository import (
    Repository,
)
from basstler.upstream_reviews.action.__main__ import (
    ActionCommand,
    ActionExitCode,
    main,
)
from basstler_domain_model.git.commands import (
    GitCommandRunner,
)
from basstler.upstream_reviews.action.branch import (
    ActionInstallation,
    InstallationState,
    RemoteBranch,
)
from basstler.upstream_reviews.action.read import (
    ActionRead,
    WorkflowRunPage,
)
from basstler.upstream_reviews.action.recording import (
    ActionResult,
    RecordedGraphQLResponse,
    ReplayingGraphQLClient,
)
from basstler.upstream_reviews.action.run import ActionRun
from basstler_domain_model.github.clients import (
    GitHubApi,
    RestClient,
)
from basstler.upstream_reviews.exceptions import (
    ActionNotInstalled,
    ActionResponseMissing,
    ActionResultMissing,
    ActionRunFailed,
    ActionRunNotStarted,
)
from basstler_domain_model.github.exceptions import (
    GitHubRequestFailed,
    UpstreamPullRequestNotFound,
)
from basstler_domain_model.github.readers import (
    UpstreamPullRequestReader,
)
from basstler.upstream_reviews.request import (
    UpstreamReadRequest,
)
from basstler.upstream_reviews.vocabulary import (
    ActionBranch,
    ActionFile,
    BasstlerSource,
)

from ..constants.paths import DatasetLocation, RepositoryLocation
from .test_command_line import (
    RECORDED_PULL_REQUEST_NUMBER,
    UPSTREAM,
    Example,
    failing_job_identifier,
)
from .replay import RecordedJobLog, RecordedResponse

BRANCH_OR_TAG = "v9.9.9"
"""
The release the tests install.
"""

REQUEST_IDENTIFIER = "0123456789abcdef"
"""
The request every read here makes.
"""

FORK = Repository(Example.FORK_OWNER, Example.UPSTREAM_NAME)
"""
The fork the bare remote stands in for.
"""


@dataclass(frozen=True)
class RecordedGitHub(GitHubApi):
    """
    Answers from the recorded responses, in order, instead of calling GitHub.
    """

    token: str = ""

    responses: list[dict[str, Any]] = field(default_factory=list)
    """
    The responses still to be returned.
    """

    logs: dict[int, str] = field(default_factory=dict)
    """
    The log each job answers with.
    """

    def execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """:return: The next recorded response."""
        return self.responses.pop(0)

    def read_job_log(self, repository: Repository, job_identifier: int) -> str:
        """:return: The job's recorded log."""
        return self.logs[job_identifier]


def pages() -> list[dict[str, Any]]:
    """:return: The two recorded pages of the pull request."""
    return [
        RecordedResponse.PULL_REQUEST_PAGE_ONE.load(),
        RecordedResponse.PULL_REQUEST_PAGE_TWO.load(),
    ]


def github(by_branch: bool = False) -> RecordedGitHub:
    """
    :param by_branch: Whether the read starts by resolving a branch.
    :return: A client answering one whole read.
    """
    branch = [RecordedResponse.BRANCH_PULL_REQUESTS.load()] if by_branch else []
    return RecordedGitHub(
        responses=branch + pages(),
        logs={failing_job_identifier(): RecordedJobLog.FAILED_JOB.load()},
    )


def request(
    pull_request: int | None = RECORDED_PULL_REQUEST_NUMBER,
    branch: str | None = None,
    failure_logs: bool = False,
) -> UpstreamReadRequest:
    """:return: A request over the recorded upstream."""
    return UpstreamReadRequest(
        REQUEST_IDENTIFIER,
        UPSTREAM,
        Example.FORK_OWNER,
        pull_request,
        branch,
        failure_logs,
    )


def direct_report(
    client: RecordedGitHub, read: UpstreamReadRequest, include_resolved: bool
) -> str:
    """:return: The report a direct GraphQL read through *client* renders."""
    return read.report(
        UpstreamPullRequestReader(client, read.upstream, read.fork_owner),
        client,
        include_resolved,
    )


def committed(result: ActionResult) -> ActionResult:
    """:return: The result as the session reads it back from the branch."""
    return ActionResult.from_json(json.loads(json.dumps(result.to_json())))


# %% the run's recording, replayed


@pytest.mark.parametrize(
    "read, by_branch",
    [
        (request(), False),
        (request(pull_request=None, branch=Example.BRANCH), True),
        (request(failure_logs=True), False),
    ],
    ids=["by number", "by branch", "with failure logs"],
)
@pytest.mark.parametrize("include_resolved", [False, True])
def test_the_replayed_report_is_the_direct_read(read, by_branch, include_resolved):
    result = committed(ActionResult.answering(read, github(by_branch)))
    direct = direct_report(github(by_branch), read, include_resolved)

    replayed = result.report(read, include_resolved)

    assert replayed == direct


def test_failure_logs_are_recorded_only_when_asked_for():
    assert ActionResult.answering(request(), github()).job_logs == []
    assert [
        log.job
        for log in ActionResult.answering(request(failure_logs=True), github()).job_logs
    ] == [failing_job_identifier()]


def test_a_branch_never_promoted_is_recorded_as_the_run_s_error():
    client = RecordedGitHub(
        responses=[RecordedResponse.BRANCH_PULL_REQUESTS_NONE.load()]
    )
    read = request(pull_request=None, branch=Example.UNPROMOTED_BRANCH)

    result = committed(ActionResult.answering(read, client))

    assert Example.UNPROMOTED_BRANCH in result.error
    with pytest.raises(ActionRunFailed, match=Example.UNPROMOTED_BRANCH):
        result.report(read, include_resolved=False)


def test_a_result_from_another_release_s_queries_is_refused_by_name():
    result = committed(ActionResult.answering(request(), github()))
    altered = ActionResult(
        result.request_identifier,
        None,
        [
            RecordedGraphQLResponse("0" * 16, response.variables, response.data)
            for response in result.responses
        ],
    )

    with pytest.raises(ActionResponseMissing, match="basstler release"):
        ReplayingGraphQLClient(altered).execute("query", {})
    with pytest.raises(ActionResponseMissing):
        altered.report(request(), include_resolved=False)


def test_the_error_is_what_the_reader_raises():
    client = RecordedGitHub(
        responses=[RecordedResponse.BRANCH_PULL_REQUESTS_NONE.load()]
    )
    read = request(pull_request=None, branch=Example.UNPROMOTED_BRANCH)

    with pytest.raises(UpstreamPullRequestNotFound) as raised:
        direct_report(client, read, include_resolved=False)

    assert ActionResult.answering(
        read,
        RecordedGitHub(responses=[RecordedResponse.BRANCH_PULL_REQUESTS_NONE.load()]),
    ).error == str(raised.value)


def test_the_run_answers_a_pull_request_report_request_with_its_recording(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(GitHubApi, "from_environment", github)
    request_file = tmp_path / ActionFile.REQUEST
    request_file.write_text(json.dumps(request().as_sent()))

    answer = ActionRun(request_file, tmp_path / ActionFile.RESULT).answer()

    assert answer == ActionResult.answering(request(), github())


# %% the workflows


def test_the_caller_runs_the_release_it_names_on_pushes_of_a_request():
    workflow = ActionInstallation(BRANCH_OR_TAG).workflow

    assert (
        f"{BasstlerSource.REPOSITORY}/{BasstlerSource.REUSABLE_WORKFLOW}@{BRANCH_OR_TAG}"
        in workflow
    )
    assert (
        ActionInstallation.branch_or_tag_line.search(workflow)["branch_or_tag"]
        == BRANCH_OR_TAG
    )
    assert f"branches: [{ActionBranch.NAME}]" in workflow
    assert f"paths: [{ActionFile.REQUEST}]" in workflow


def test_the_caller_workflow_is_rendered_without_html_escaping():
    workflow = ActionInstallation("tag&<name>").workflow

    assert (
        ActionInstallation.branch_or_tag_line.search(workflow)["branch_or_tag"]
        == "tag&<name>"
    )


def test_the_reusable_workflow_runs_this_module_and_commits_its_result():
    reusable = (
        PackageLocation.SOURCE_TREE / BasstlerSource.REUSABLE_WORKFLOW
    ).read_text()

    command_path = basstler.upstream_reviews.action.__main__.__package__
    assert f"python -m {command_path} {ActionCommand.RUN}" in reusable
    assert f"git add {ActionFile.RESULT}" in reusable
    assert "workflow_call" in reusable


# %% the branch


@dataclass(frozen=True)
class Fork:
    """
    A clone of a fork, and the bare repository standing in for it.
    """

    clone: Path
    remote: Path

    @property
    def branch(self) -> RemoteBranch:
        """:return: The Action branch on the fork."""
        return RemoteBranch(GitCommandRunner(self.clone), "origin")

    def files(self) -> dict[str, str]:
        """:return: Every file on the remote's Action branch, by path, without its
        final newline as the branch is written."""
        names = git(
            self.remote, "ls-tree", "-r", "--name-only", ActionBranch.NAME
        ).split()
        return {
            name: git(self.remote, "show", f"{ActionBranch.NAME}:{name}")
            for name in names
        }

    def answer(self, result: ActionResult) -> None:
        """Commit a result onto the branch, as the run does."""
        self.branch.write({ActionFile.RESULT: json.dumps(result.to_json())}, "Answer")


def git(directory: Path, *arguments: str) -> str:
    """:return: What git printed in *directory*, stripped."""
    return subprocess.run(
        ["git", *arguments], cwd=directory, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def fork(tmp_path, monkeypatch) -> Fork:
    """:return: A clone of an empty fork with one commit on its default branch."""
    for variable, value in {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setenv(variable, value)
    remote = tmp_path / FORK.owner / f"{FORK.name}.git"
    remote.parent.mkdir()
    git(tmp_path, "init", "--quiet", "--bare", "--initial-branch=main", str(remote))
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "--quiet", f"file://{remote}", str(clone))
    (clone / "project.txt").write_text("project\n")
    git(clone, "add", "project.txt")
    git(clone, "commit", "--quiet", "-m", "Project")
    git(clone, "push", "--quiet", "origin", "HEAD:main")
    return Fork(clone, remote)


def test_installing_writes_only_the_workflow_and_readme_on_their_own_branch(fork):
    assert ActionInstallation(BRANCH_OR_TAG).install(fork.branch)

    assert set(fork.files()) == {ActionFile.WORKFLOW, ActionFile.README}
    assert (
        fork.files()[ActionFile.WORKFLOW] == ActionInstallation(BRANCH_OR_TAG).workflow
    )
    assert git(fork.remote, "ls-tree", "--name-only", "main") == "project.txt"
    assert git(fork.remote, "rev-list", "--count", ActionBranch.NAME) == "1"


def test_installing_again_pushes_nothing(fork):
    ActionInstallation(BRANCH_OR_TAG).install(fork.branch)
    tip = git(fork.remote, "rev-parse", ActionBranch.NAME)

    assert not ActionInstallation(BRANCH_OR_TAG).install(fork.branch)
    assert git(fork.remote, "rev-parse", ActionBranch.NAME) == tip


def test_the_state_is_missing_then_current_then_stale(fork):
    installation = ActionInstallation(BRANCH_OR_TAG)

    assert installation.state(fork.branch) == (InstallationState.MISSING, None)
    installation.install(fork.branch)
    assert installation.state(fork.branch) == (InstallationState.CURRENT, BRANCH_OR_TAG)
    assert ActionInstallation("v10.0.0").state(fork.branch) == (
        InstallationState.STALE,
        BRANCH_OR_TAG,
    )


def test_upgrading_keeps_the_last_request_and_result(fork):
    ActionInstallation(BRANCH_OR_TAG).install(fork.branch)
    ActionRead(fork.branch, request()).send()

    ActionInstallation("v10.0.0").install(fork.branch)

    assert ActionFile.REQUEST in fork.files()
    assert "v10.0.0" in fork.files()[ActionFile.WORKFLOW]


def test_a_request_is_pushed_beside_the_workflow(fork):
    ActionInstallation(BRANCH_OR_TAG).install(fork.branch)

    ActionRead(fork.branch, request(failure_logs=True)).send()

    sent = UpstreamReadRequest.from_json(json.loads(fork.files()[ActionFile.REQUEST]))
    assert sent == request(failure_logs=True)
    assert (
        fork.files()[ActionFile.WORKFLOW] == ActionInstallation(BRANCH_OR_TAG).workflow
    )


def test_a_request_to_a_fork_without_the_action_is_refused(fork):
    with pytest.raises(ActionNotInstalled, match="install-upstream-reviews-action"):
        ActionRead(fork.branch, request()).send()


def test_the_result_answering_the_request_is_collected(fork):
    ActionInstallation(BRANCH_OR_TAG).install(fork.branch)
    ActionRead(fork.branch, request()).send()
    fork.answer(ActionResult.answering(request(), github()))

    result = ActionRead(fork.branch, request()).wait(timeout=0)

    assert result == committed(ActionResult.answering(request(), github()))


def test_a_result_answering_another_request_is_not_collected(fork):
    ActionInstallation(BRANCH_OR_TAG).install(fork.branch)
    fork.answer(ActionResult("fedcba9876543210"))

    with pytest.raises(ActionResultMissing) as raised:
        ActionRead(fork.branch, request()).wait(timeout=0)

    assert f"{FORK.url}/actions" in str(raised.value)
    assert "disabled" in str(raised.value)


def test_a_result_from_a_release_that_named_no_request_is_not_collected(fork):
    ActionInstallation(BRANCH_OR_TAG).install(fork.branch)
    fork.branch.write(
        {
            ActionFile.RESULT: DatasetLocation.ACTION_RESULT_WITHOUT_REQUEST_IDENTIFIER.value.read_text()
        },
        "Answer",
    )

    with pytest.raises(ActionResultMissing):
        ActionRead(fork.branch, request()).wait(timeout=0)


@dataclass
class ListedRuns(RestClient):
    """
    Answers the fork's run listing with the given runs.
    """

    runs: list[dict[str, Any]] | None
    """
    The listed runs, ``None`` to fail as ``gh`` does without access.
    """

    paths: list[str] = field(default_factory=list)
    """
    Every path asked for.
    """

    def get(self, path: str) -> Any:
        """:return: The listing."""
        self.paths.append(path)
        if self.runs is None:
            raise GitHubRequestFailed(
                status=HTTPStatus.FORBIDDEN, detail="", method="GET", path=path
            )
        return {"workflow_runs": self.runs}

    def get_pages(self, path: str) -> list[Any]:
        """:return: The listing, as its one page."""
        return [self.get(path)]


def run(commit: str, conclusion: str | None) -> dict[str, Any]:
    """:return: One listed run of the fork's workflow."""
    return {"id": 7, "head_sha": commit, "conclusion": conclusion}


def waiting(fork: Fork, runs: ListedRuns) -> tuple[ActionRead, str]:
    """:return: A read whose request was pushed, looking for its run at once."""
    ActionInstallation(BRANCH_OR_TAG).install(fork.branch)
    read = ActionRead(fork.branch, request(), runs, poll_interval=0, start_allowance=0)
    return read, read.send()


def test_a_request_no_run_started_for_says_actions_are_disabled(fork):
    read, commit = waiting(fork, ListedRuns([run("another-commit", "success")]))

    with pytest.raises(ActionRunNotStarted, match=f"{FORK.url}/actions"):
        read.wait(timeout=60, commit=commit)


def test_the_runs_are_listed_on_the_action_branch_alone(fork):
    runs = ListedRuns([])
    read, commit = waiting(fork, runs)

    with pytest.raises(ActionRunNotStarted):
        read.wait(timeout=60, commit=commit)

    assert runs.paths == [WorkflowRunPage.path(FORK)]
    assert f"branch={ActionBranch.NAME}" in runs.paths[0]


def test_a_run_that_failed_without_a_result_is_reported_with_its_page(fork):
    runs = ListedRuns([])
    read, commit = waiting(fork, runs)
    runs.runs = [run(commit, "failure")]

    with pytest.raises(ActionRunFailed, match="actions/runs/7"):
        read.wait(timeout=60, commit=commit)


def test_a_run_still_going_is_waited_for(fork):
    runs = ListedRuns([])
    read, commit = waiting(fork, runs)
    runs.runs = [run(commit, None)]

    with pytest.raises(ActionResultMissing):
        read.wait(timeout=0, commit=commit)


def test_runs_that_cannot_be_listed_leave_the_wait_to_the_result(fork):
    read, commit = waiting(fork, ListedRuns(None))

    with pytest.raises(ActionResultMissing):
        read.wait(timeout=0, commit=commit)


def test_a_result_is_collected_even_if_its_run_failed_afterwards(fork):
    runs = ListedRuns([])
    read, commit = waiting(fork, runs)
    runs.runs = [run(commit, "failure")]
    fork.answer(ActionResult.answering(request(), github()))

    assert read.wait(timeout=60, commit=commit).request_identifier == REQUEST_IDENTIFIER


def test_the_fork_is_named_by_its_remote(fork):
    assert fork.branch.fork == FORK


def test_read_prints_the_report_from_the_committed_result(fork, monkeypatch, capsys):
    ActionInstallation(BRANCH_OR_TAG).install(fork.branch)
    send = ActionRead.send

    def send_and_run(read: ActionRead) -> str:
        commit = send(read)
        fork.answer(ActionResult.answering(read.request, github()))
        return commit

    monkeypatch.setattr(ActionRead, "send", send_and_run)

    status = main(
        [
            "--project-root",
            str(fork.clone),
            "read",
            "--upstream",
            str(UPSTREAM),
            "--fork-owner",
            Example.FORK_OWNER,
            "--pull-request",
            str(RECORDED_PULL_REQUEST_NUMBER),
            "--timeout",
            "0",
        ]
    )

    assert status == 0
    assert (
        capsys.readouterr().out.strip()
        == direct_report(github(), request(), include_resolved=False).strip()
    )


def test_read_without_the_action_fails_with_how_to_install_it(fork, capsys):
    status = main(
        [
            "--project-root",
            str(fork.clone),
            "read",
            "--upstream",
            str(UPSTREAM),
            "--fork-owner",
            Example.FORK_OWNER,
            "--pull-request",
            "1",
        ]
    )

    assert status == ActionExitCode.UNAVAILABLE
    assert "install-upstream-reviews-action" in capsys.readouterr().err


def test_status_prints_the_state_and_the_installed_release(fork, capsys):
    arguments = [
        "--project-root",
        str(fork.clone),
        "status",
        "--branch-or-tag",
        BRANCH_OR_TAG,
    ]
    main(arguments)
    main(
        ["--project-root", str(fork.clone), "install", "--branch-or-tag", BRANCH_OR_TAG]
    )
    capsys.readouterr()

    main(arguments)
    main(["--project-root", str(fork.clone), "status", "--branch-or-tag", "v10.0.0"])

    assert capsys.readouterr().out.split("\n")[:2] == [
        f"{InstallationState.CURRENT} {BRANCH_OR_TAG}",
        f"{InstallationState.STALE} {BRANCH_OR_TAG}",
    ]


def test_the_shell_configuration_names_the_same_branch():
    configuration = (
        RepositoryLocation.HOOKS / "resolve-personal-notes-config.sh"
    ).read_text()

    assert f'UPSTREAM_REVIEWS_ACTION_BRANCH="{ActionBranch.NAME}"' in configuration
