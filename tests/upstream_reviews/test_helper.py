"""
Tests for reading an upstream pull request through a helper session.

The helper runs the command its prompt carries; its output reaches this session's
transcript, and ``report`` replays it through the same reader. These tests run that very
command against a ``gh`` stub answering the recorded REST responses, and hold the
replayed report to a direct REST read's.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import basstler.upstream_reviews.helper.fetch
from basstler_domain_model.environment import (
    ClaudeCodeVariable,
)
from basstler.upstream_reviews.exceptions import (
    HelperOutputIncomplete,
    HelperOutputMissing,
    HelperOutputUnreadable,
    HelperResponseMissing,
    NoRecordedReadRequest,
)
from basstler_domain_model.github.exceptions import (
    UpstreamPullRequestNotFound,
)
from basstler.upstream_reviews.failure_logs import (
    FailureLogReader,
)
from basstler.upstream_reviews.helper.__main__ import (
    HelperPrompt,
    HelperScript,
    main,
)
from basstler.upstream_reviews.helper.recording import (
    HelperChunk,
    HelperRecording,
    RecordedFields,
    ReplayingClient,
    SessionTranscript,
)
from basstler.upstream_reviews.report import (
    UpstreamPullRequestReport,
)
from basstler.upstream_reviews.request import (
    UpstreamReadRequest,
)
from basstler_domain_model.github.vocabulary import (
    RestJSONKey,
)
from basstler_domain_model.github.rest_models import (
    RestPullRequestSummary,
)
from basstler_domain_model.github.vocabulary import (
    PullRequestJSONKey,
)

from ..constants.paths import DatasetLocation
from .test_rest import (
    NUMBER,
    ROUTE,
    UPSTREAM,
    Example,
    make_reader,
    pull_request_responses,
)
from .replay import RecordedRestResponse as Recorded
from .replay import ReplayingRestClient

FAILED_JOB = 22
"""
The job behind the recorded check run that failed.
"""

FAILED_JOB_LOG = "line one\nFAILED test_something - assert 1 == 2\n"
"""
What the stub answers for that job's log.
"""


def request(
    pull_request: int | None = NUMBER,
    branch: str | None = None,
    failure_logs: bool = False,
    request_identifier: str = "0123456789abcdef",
) -> UpstreamReadRequest:
    """
    :return: A request over the recorded upstream and fork.
    """
    return UpstreamReadRequest(
        request_identifier,
        UPSTREAM,
        Example.FORK_OWNER,
        pull_request,
        branch,
        failure_logs,
    )


@pytest.fixture
def stubbed_gh(tmp_path, monkeypatch) -> Path:
    """
    Put a ``gh`` answering each recorded path first on ``PATH``.

    :return: The file the stub reads its responses from.
    """
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    installed = stub_directory / "gh"
    shutil.copy(DatasetLocation.STUBS / "gh_by_path.py", installed)
    installed.chmod(installed.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{stub_directory}{os.pathsep}{os.environ['PATH']}")
    responses = tmp_path / "responses.json"
    responses.write_text(json.dumps(pull_request_responses()))
    monkeypatch.setenv("STUB_GH_RESPONSES", str(responses))
    logs = tmp_path / "logs.json"
    logs.write_text(json.dumps({UPSTREAM.job_log_path(FAILED_JOB): FAILED_JOB_LOG}))
    monkeypatch.setenv("STUB_GH_JOB_LOGS", str(logs))
    return responses


def add_responses(responses: Path, extra: dict[str, object]) -> None:
    """
    :param responses: The file the stub reads.
    :param extra: More paths for it to answer.
    """
    responses.write_text(json.dumps(json.loads(responses.read_text()) | extra))


def run_fetch(helper_request: UpstreamReadRequest) -> str:
    """
    Run the exact command the prompt hands the helper.

    :param helper_request: The read.
    :return: What it printed.
    """
    completed = subprocess.run(
        ["bash", "-c", HelperScript(helper_request).render()],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def recording_of(helper_request: UpstreamReadRequest, output: str) -> HelperRecording:
    """
    :return: The recording the helper's output carries.
    """
    return HelperRecording.assemble(
        helper_request.identifier,
        HelperChunk.found_in(helper_request.identifier, output),
    )


class StubLogs:
    """
    Answers the failed job's log, as the ``gh`` stub does.
    """

    def read_job_log(self, repository, job_identifier):
        """:return: The recorded log."""
        assert job_identifier == FAILED_JOB
        return FAILED_JOB_LOG


def direct_report(include_resolved: bool = False, failure_logs: bool = False) -> str:
    """
    :return: The report a direct REST read renders for the recorded pull request.
    """
    snapshot = make_reader(pull_request_responses()).read_current_state(NUMBER)
    logs = (
        FailureLogReader(StubLogs(), UPSTREAM).read(snapshot.checks)
        if failure_logs
        else []
    )
    return UpstreamPullRequestReport(
        snapshot, include_resolved=include_resolved, failure_logs=logs
    ).render()


# %% the same answer as a direct read


@pytest.mark.parametrize("include_resolved", [False, True])
def test_the_helper_read_reports_what_a_direct_read_does(
    stubbed_gh, include_resolved: bool
):
    helper_request = request()

    recording = recording_of(helper_request, run_fetch(helper_request))

    assert recording.report(helper_request, include_resolved) == direct_report(
        include_resolved
    )


def test_a_branch_is_chosen_by_the_reader_on_replay(stubbed_gh):
    add_responses(
        stubbed_gh,
        {
            ROUTE.pull_requests_for_head(
                Example.FORK_OWNER, Example.BRANCH
            ): Recorded.PULL_REQUESTS_FOR_BRANCH.load()
        },
    )
    helper_request = request(pull_request=None, branch=Example.BRANCH)

    recording = recording_of(helper_request, run_fetch(helper_request))

    assert recording.report(helper_request, False) == direct_report()


@pytest.mark.parametrize(
    "recorded",
    [
        Recorded.PULL_REQUESTS_FOR_BRANCH,
        Recorded.PULL_REQUESTS_FOR_BRANCH_FOREIGN_OWNER,
        Recorded.PULL_REQUESTS_FOR_BRANCH_NONE,
    ],
)
def test_the_helper_reads_the_pull_request_the_reader_chooses(recorded):
    pages = recorded.load()
    reader = make_reader({})
    fetch = basstler.upstream_reviews.helper.fetch.Fetch(
        HelperScript(request(pull_request=None, branch=Example.BRANCH)).configuration
    )
    fetch.get_pages = lambda path: pages

    try:
        chosen = [
            reader.choose(
                Example.BRANCH,
                [
                    RestPullRequestSummary.from_json(entry).branch_pull_request
                    for page in pages
                    for entry in page
                ],
            )
        ]
    except UpstreamPullRequestNotFound:
        chosen = []

    assert fetch.numbers() == chosen


def test_a_branch_never_promoted_replays_into_the_same_answer(stubbed_gh):
    add_responses(
        stubbed_gh,
        {
            ROUTE.pull_requests_for_head(
                Example.FORK_OWNER, Example.BRANCH
            ): Recorded.PULL_REQUESTS_FOR_BRANCH_NONE.load()
        },
    )
    helper_request = request(pull_request=None, branch=Example.BRANCH)

    recording = recording_of(helper_request, run_fetch(helper_request))

    with pytest.raises(UpstreamPullRequestNotFound):
        recording.report(helper_request, False)


def test_the_log_behind_a_failed_check_is_read_and_quoted(stubbed_gh):
    helper_request = request(failure_logs=True)

    recording = recording_of(helper_request, run_fetch(helper_request))

    assert [log.job for log in recording.job_logs] == [FAILED_JOB]
    assert recording.report(helper_request, False) == direct_report(failure_logs=True)


def test_no_log_is_read_unless_asked_for(stubbed_gh):
    helper_request = request()

    assert recording_of(helper_request, run_fetch(helper_request)).job_logs == []


def test_the_helper_records_exactly_the_paths_a_direct_read_asks_for(stubbed_gh):
    helper_request = request()
    client = ReplayingRestClient(pull_request_responses())
    reader = make_reader(pull_request_responses())
    reader.client = client
    reader.read_current_state(NUMBER)

    recording = recording_of(helper_request, run_fetch(helper_request))

    assert [response.path for response in recording.responses] == client.calls


def test_a_failing_gh_fails_the_fetch_with_its_error(stubbed_gh):
    stubbed_gh.write_text("{}")

    with pytest.raises(subprocess.CalledProcessError) as raised:
        run_fetch(request())

    assert "unexpected invocation" in raised.value.stderr


def test_only_the_fields_the_models_read_are_kept():
    kept = basstler.upstream_reviews.helper.fetch.Fetch(
        HelperScript(request()).configuration
    ).kept({"title": "t", "body": "b", "head": {"sha": "s", "repo": {}}, "_links": {}})

    assert kept == {"title": "t", "body": "b", "head": {"sha": "s"}}


def test_the_kept_fields_are_every_field_a_rest_model_reads():
    assert RecordedFields.names == {key.value for key in RestJSONKey} | {
        PullRequestJSONKey.LOGIN.value
    }


def test_output_too_long_for_one_command_is_split_into_files(stubbed_gh, monkeypatch):
    monkeypatch.setattr(HelperChunk, "size", 100)
    helper_request = request()

    printed = run_fetch(helper_request)
    output = "".join(
        Path(line.removeprefix("cat ")).read_text()
        for line in printed.splitlines()
        if line.startswith("cat ")
    )

    assert "chunks; print each with its own command" in printed
    assert (
        recording_of(helper_request, output).request_identifier
        == helper_request.identifier
    )


# %% what the helper is told


def test_the_shipped_source_is_code_alone_and_runs_without_basstler():
    source = HelperScript.code_only(
        Path(basstler.upstream_reviews.helper.fetch.__file__).read_text()
    )

    assert '"""' not in source
    assert "import basstler" not in source and "from basstler" not in source
    compile(source, "<helper>", "exec")


def test_the_prompt_carries_the_command_and_how_to_answer():
    prompt = HelperPrompt(request()).render()

    assert HelperScript(request()).render() in prompt
    assert "DONE" in prompt


# %% reading the output back


def chunks_of(helper_request: UpstreamReadRequest, count: int) -> list[HelperChunk]:
    """
    :return: A real recording's output, split into ``count`` chunks.
    """
    fetch = basstler.upstream_reviews.helper.fetch.Fetch(
        HelperScript(helper_request).configuration | {"chunk_size": 10**9}
    )
    fetch.responses.append({"path": "p", "body": {"a": 1}})
    [line] = fetch.output_lines()
    encoded = line.split(":", 1)[1]
    size = -(-len(encoded) // count)
    return [
        HelperChunk(index, count, encoded[(index - 1) * size : index * size])
        for index in range(1, count + 1)
    ]


def test_chunks_reassemble_in_order_whatever_order_they_are_found_in():
    helper_request = request()
    chunks = chunks_of(helper_request, 3)

    recording = HelperRecording.assemble(
        helper_request.identifier, chunks[::-1] + chunks
    )

    assert [(entry.path, entry.body) for entry in recording.responses] == [
        ("p", {"a": 1})
    ]


def test_no_chunk_found_is_reported_as_missing_output():
    with pytest.raises(HelperOutputMissing):
        HelperRecording.assemble(request().identifier, [])


def test_a_missing_chunk_is_named():
    helper_request = request()
    chunks = chunks_of(helper_request, 3)

    with pytest.raises(HelperOutputIncomplete) as raised:
        HelperRecording.assemble(helper_request.identifier, [chunks[0], chunks[2]])

    assert raised.value.missing == [2]


def test_output_that_does_not_decode_is_unreadable():
    with pytest.raises(HelperOutputUnreadable):
        HelperRecording.assemble(
            request().identifier, [HelperChunk(1, 1, "bm90IHpsaWI=")]
        )


def test_output_answering_another_request_is_unreadable():
    chunks = chunks_of(request(request_identifier="ffffffffffffffff"), 1)

    with pytest.raises(HelperOutputUnreadable):
        HelperRecording.assemble(request().identifier, chunks)


def test_a_chunk_is_found_however_deeply_the_transcript_quotes_it():
    helper_request = request()
    [chunk] = chunks_of(helper_request, 1)
    line = f"{HelperChunk.marker(helper_request.identifier)}-1-1:{chunk.text}"
    quoted = json.dumps({"content": json.dumps({"stdout": line})})

    assert HelperChunk.found_in(helper_request.identifier, quoted) == [chunk]


def test_the_transcript_is_every_file_named_after_the_session(tmp_path):
    for project in ["-home-user", "-home-user-project"]:
        (tmp_path / "projects" / project).mkdir(parents=True)
        (tmp_path / "projects" / project / "session.jsonl").write_text("")
    (tmp_path / "projects" / "-home-user" / "other.jsonl").write_text("")

    files = SessionTranscript("session", tmp_path).files

    assert [path.parent.name for path in files] == ["-home-user", "-home-user-project"]


def test_a_response_the_helper_did_not_record_is_named():
    with pytest.raises(HelperResponseMissing):
        ReplayingClient(HelperRecording("n", [])).get("repos/a/b/pulls/1")


# %% command line


@pytest.fixture
def session(tmp_path, monkeypatch) -> Path:
    """
    Point the request record and the transcript into a temporary directory.

    :return: The transcript file.
    """
    monkeypatch.setattr(UpstreamReadRequest, "location", tmp_path / "request.json")
    monkeypatch.setenv(ClaudeCodeVariable.CONFIGURATION_DIRECTORY, str(tmp_path))
    monkeypatch.setenv(ClaudeCodeVariable.SESSION_IDENTIFIER, "session")
    transcript = tmp_path / "projects" / "-home-user" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    return transcript


def test_report_before_any_prompt_says_so(session):
    with pytest.raises(NoRecordedReadRequest):
        UpstreamReadRequest.load()


def test_prompt_then_report_reads_the_helper_output_from_the_transcript(
    stubbed_gh, session, capsys
):
    assert (
        main(
            [
                "prompt",
                "--upstream",
                str(UPSTREAM),
                "--fork-owner",
                Example.FORK_OWNER,
                "--pull-request",
                str(NUMBER),
            ]
        )
        == 0
    )
    capsys.readouterr()
    session.write_text(
        json.dumps({"stdout": run_fetch(UpstreamReadRequest.load())}) + "\n"
    )

    assert main(["report"]) == 0
    assert capsys.readouterr().out.strip() == direct_report().strip()


def test_report_without_the_helper_output_fails_with_its_reason(session, capsys):
    request().save()

    assert main(["report"]) == 1
    assert "list_events" in capsys.readouterr().err
