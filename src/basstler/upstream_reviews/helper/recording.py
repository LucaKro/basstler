"""
What a helper session recorded of the upstream, and replaying it in place of GitHub.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler_domain_model.environment import (
    ClaudeCodeVariable,
)
from basstler_domain_model.repository import (
    Repository,
)
from basstler_domain_model.github.clients import (
    JobLogReader,
    RestClient,
)
from basstler.upstream_reviews.exceptions import (
    HelperOutputIncomplete,
    HelperOutputMissing,
    HelperOutputUnreadable,
    HelperResponseMissing,
)
from basstler_domain_model.github.readers import (
    RestPullRequestReader,
)
from basstler.upstream_reviews.request import (
    HelperJSONKey,
    UpstreamReadRequest,
)
from basstler_domain_model.github.vocabulary import (
    RestJSONKey,
)
from basstler_domain_model.github.vocabulary import (
    JSONModel,
    PullRequestJSONKey,
)

# %% the recording


class RecordedFields:
    """
    The REST fields the models read, which are all a helper keeps of a response.
    """

    @classproperty
    def names(cls) -> frozenset[str]:
        """
        Every field name a REST model reads, at any depth.
        """
        return frozenset(key.value for key in RestJSONKey) | {
            PullRequestJSONKey.LOGIN.value
        }


@dataclass(frozen=True)
class RecordedRestResponse(JSONModel):
    """
    One REST response the helper read.
    """

    path: str
    """
    The path it answers, exactly as the reader asked for it.
    """

    body: Any
    """
    The response, or every page of it for a paged path, with only the read fields.
    """

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RecordedRestResponse:
        """
        :param data: One recorded response.
        :return: The parsed response.
        """
        return cls(data[HelperJSONKey.PATH], data[HelperJSONKey.BODY])


@dataclass(frozen=True)
class RecordedJobLog(JSONModel):
    """
    The end of one job's log, as the helper read it.
    """

    job: int
    """
    The Actions job.
    """

    log: str
    """
    Its last lines.
    """

    @classproperty
    def line_limit(cls) -> int:
        """
        How many of a log's last lines are kept: where a failure's summary sits, without
        a whole log swelling the output.
        """
        return 2000

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RecordedJobLog:
        """
        :param data: One recorded log.
        :return: The parsed log.
        """
        return cls(data[HelperJSONKey.JOB], data[HelperJSONKey.LOG])


@dataclass(frozen=True)
class HelperRecording(JSONModel):
    """
    Everything the helper read for one request.
    """

    request_identifier: str
    """
    The request it answers.
    """

    responses: list[RecordedRestResponse]
    """
    The REST responses, in the order read.
    """

    job_logs: list[RecordedJobLog] = field(default_factory=list)
    """
    The logs read behind the checks that did not pass.
    """

    @classproperty
    def size_limit(cls) -> int:
        """
        The most bytes a decoded recording may take, so a corrupt one cannot exhaust
        memory.
        """
        return 64 * 1024 * 1024

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HelperRecording:
        """
        :param data: The decoded recording.
        :return: The parsed recording.
        """
        return cls(
            request_identifier=data[HelperJSONKey.REQUEST_IDENTIFIER],
            responses=[
                RecordedRestResponse.from_json(entry)
                for entry in data[HelperJSONKey.RESPONSES]
            ],
            job_logs=[
                RecordedJobLog.from_json(entry)
                for entry in data[HelperJSONKey.JOB_LOGS]
            ],
        )

    @classmethod
    def assemble(
        cls, request_identifier: str, chunks: list[HelperChunk]
    ) -> HelperRecording:
        """
        Join a request's chunks back into its recording.

        :param request_identifier: The request's identifier.
        :param chunks: Every chunk found, repeats included.
        :return: The recording.
        :raises HelperOutputMissing: If no chunk was found.
        :raises HelperOutputIncomplete: If some were not.
        :raises HelperOutputUnreadable: If they do not decode into this request's
            recording.
        """
        if not chunks:
            raise HelperOutputMissing(request_identifier)
        count = chunks[0].count
        by_index = {chunk.index: chunk.text for chunk in chunks}
        missing = [index for index in range(1, count + 1) if index not in by_index]
        if missing:
            raise HelperOutputIncomplete(request_identifier, missing)
        encoded = "".join(by_index[index] for index in range(1, count + 1))
        try:
            decompressor = zlib.decompressobj()
            decoded = decompressor.decompress(
                base64.b64decode(encoded, validate=True), cls.size_limit
            )
            if decompressor.unconsumed_tail:
                raise HelperOutputUnreadable(
                    request_identifier, "larger than the size limit"
                )
            recording = cls.from_json(json.loads(decoded))
        except (binascii.Error, zlib.error, ValueError, KeyError, TypeError) as failure:
            raise HelperOutputUnreadable(request_identifier, str(failure)) from failure
        if recording.request_identifier != request_identifier:
            raise HelperOutputUnreadable(
                request_identifier, f"it answers request {recording.request_identifier}"
            )
        return recording

    def report(self, request: UpstreamReadRequest, include_resolved: bool) -> str:
        """
        Render the report a direct read would, from this recording.

        :param request: The read the helper performed.
        :param include_resolved: Whether to list resolved threads too.
        :return: The rendered markdown.
        """
        client = ReplayingClient(self)
        return request.report(
            RestPullRequestReader(client, request.upstream, request.fork_owner),
            client,
            include_resolved,
        )


@dataclass(frozen=True)
class HelperChunk:
    """
    One piece of the helper's encoded output.
    """

    index: int
    """
    Its position, from 1.
    """

    count: int
    """
    How many pieces the output was split into.
    """

    text: str
    """
    Its share of the encoded output.
    """

    size = 20000
    """
    How many characters each chunk carries, under what a session keeps of one command's
    output.
    """

    @classmethod
    def marker(cls, request_identifier: str) -> str:
        """
        :param request_identifier: The request's identifier.
        :return: What each chunk's line starts with, before its index and count.
        """
        return f"BASSTLER-UPSTREAM-REVIEWS-{request_identifier}"

    @classmethod
    def pattern(cls, request_identifier: str) -> re.Pattern[str]:
        """
        :param request_identifier: The request's identifier.
        :return: What one chunk looks like, wherever it is quoted.
        """
        return re.compile(
            rf"{re.escape(cls.marker(request_identifier))}-(?P<index>\d+)-(?P<count>\d+)"
            r":(?P<text>[A-Za-z0-9+/=]+)"
        )

    @classmethod
    def found_in(cls, request_identifier: str, text: str) -> list[HelperChunk]:
        """
        :param request_identifier: The request's identifier.
        :param text: Where to look.
        :return: Every chunk quoted there, repeats included.
        """
        return [
            cls(int(match["index"]), int(match["count"]), match["text"])
            for match in cls.pattern(request_identifier).finditer(text)
        ]


# %% replaying the recording


@dataclass
class ReplayingClient(RestClient, JobLogReader):
    """
    Answers the reader from what the helper recorded, instead of GitHub.
    """

    recording: HelperRecording
    """
    The helper's output.
    """

    def _response(self, path: str) -> Any:
        """
        :param path: The REST path asked for.
        :return: The response recorded for it.
        :raises HelperResponseMissing: If the helper recorded no such path.
        """
        for response in self.recording.responses:
            if response.path == path:
                return response.body
        raise HelperResponseMissing(path)

    def get(self, path: str) -> Any:
        """
        See :meth:`RestClient.get`.
        """
        return self._response(path)

    def get_pages(self, path: str) -> list[Any]:
        """
        See :meth:`RestClient.get_pages`.
        """
        return self._response(path)

    def read_job_log(self, repository: Repository, job_identifier: int) -> str:
        """
        See :meth:`JobLogReader.read_job_log`.
        """
        for log in self.recording.job_logs:
            if log.job == job_identifier:
                return log.log
        raise HelperResponseMissing(repository.job_log_path(job_identifier))


# %% this session's transcript


@dataclass(frozen=True)
class SessionTranscript:
    """
    This session's own transcript, where every tool result it received is kept whole.
    """

    session_identifier: str
    """
    The session whose transcript to read.
    """

    configuration_directory: Path
    """
    Where Claude Code keeps its state.
    """

    @classmethod
    def current(cls) -> SessionTranscript:
        """:return: The transcript of the session running this."""
        configured = ClaudeCodeVariable.CONFIGURATION_DIRECTORY.read()
        return cls(
            ClaudeCodeVariable.SESSION_IDENTIFIER.read() or "",
            Path(configured) if configured else Path.home() / ".claude",
        )

    @property
    def files(self) -> list[Path]:
        """
        :return: Every file of it: one per project directory the session has run in.
        """
        if not self.session_identifier:
            return []
        return sorted(
            (self.configuration_directory / "projects").glob(
                f"*/{self.session_identifier}.jsonl"
            )
        )

    def chunks(self, request_identifier: str) -> list[HelperChunk]:
        """
        :param request_identifier: The request's identifier.
        :return: Every chunk of its output the transcript quotes.
        """
        return [
            chunk
            for path in self.files
            for chunk in HelperChunk.found_in(
                request_identifier, path.read_text(errors="replace")
            )
        ]
