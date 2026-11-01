"""
The log behind a check a pull request did not pass, and the excerpt of it a report
quotes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, StrEnum

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler_domain_model.repository import (
    Repository,
)
from basstler_domain_model.github.clients import (
    JobLogReader,
)
from basstler_domain_model.github.pull_request_models import (
    CheckResult,
    CheckStatus,
)
from basstler_domain_model.github.vocabulary import (
    CheckOutcome,
)

# %% the log behind a failed check


class LogFormatting(Enum):
    """
    What gets written around a log line's text that does not survive being quoted.
    """

    TERMINAL_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    """
    The colour a test runner writes around its own output.
    """

    TIMESTAMP = re.compile(r"^\S+Z ")
    """
    The timestamp a runner writes in front of every line it records.
    """

    @classmethod
    def remove_from(cls, line: str) -> str:
        """
        :param line: One line of a job log.
        :return: The line's text alone.
        """
        for formatting in cls:
            line = formatting.value.sub("", line)
        return line


class LogMarker(StrEnum):
    """
    The lines in a runner's log that say where a failure is described.
    """

    PYTEST_SUMMARY = "short test summary info"
    """
    Opens pytest's own list of what failed, which is the answer wherever there is one.
    """

    ERROR_ANNOTATION = "##[error]"
    """
    Marks a line the runner itself flagged, which is all a job that died before pytest
    leaves behind.
    """


@dataclass(frozen=True)
class FailedJob:
    """
    The Actions job behind a check that did not pass.
    """

    identifier: int
    """
    The job's own number, which is what a log read asks for.
    """

    @classproperty
    def identifier_group(cls) -> str:
        """
        What the job's number is called inside :attr:`link_pattern`.
        """
        return "identifier"

    @classproperty
    def link_pattern(cls) -> re.Pattern[str]:
        """
        Where a check's own output link carries the job that produced it.
        """
        return re.compile(rf"/actions/runs/\d+/job/(?P<{cls.identifier_group}>\d+)")

    @classmethod
    def behind(cls, result: CheckResult) -> FailedJob | None:
        """
        Find the job a check's output link points at.

        A rollup also carries contexts posted by services that are not Actions at all,
        and those link somewhere with no job behind them.

        :param result: The check to locate.
        :return: The job, or ``None`` where the link names none.
        """
        match = cls.link_pattern.search(result.url)
        if match is None:
            return None
        return cls(int(match.group(cls.identifier_group)))


@dataclass(frozen=True)
class FailureLog:
    """
    The part of a failed job's log that says what went wrong.
    """

    check_name: str
    """
    The check whose job this came from.
    """

    lines: list[str]
    """
    The excerpt, oldest line first, with the runner's timestamps taken off.
    """

    @classproperty
    def line_limit(cls) -> int:
        """
        How many lines of one job's log the report is willing to quote.
        """
        return 40

    @classmethod
    def excerpt(cls, check_name: str, log: str) -> FailureLog:
        """
        Cut one whole job log down to the lines worth reading.

        pytest's own summary is preferred where the job ran that far, the runner's error
        annotations where it did not, and the log's last lines where neither marker
        appears at all. A runner's timestamps and a test runner's colour both come off,
        since neither survives being quoted under the check that produced it.

        :param check_name: The check the job reported for.
        :param log: The job log, exactly as the runner recorded it.
        :return: The excerpt.
        """
        lines = [LogFormatting.remove_from(line) for line in log.splitlines()]
        summary = cls._from_marker(lines, LogMarker.PYTEST_SUMMARY)
        if summary:
            ended = cls._until_marker(summary, LogMarker.ERROR_ANNOTATION)
            return cls(check_name, ended[: cls.line_limit])
        annotations = [line for line in lines if LogMarker.ERROR_ANNOTATION in line]
        if annotations:
            return cls(check_name, annotations[: cls.line_limit])
        return cls(check_name, lines[-cls.line_limit :])

    @staticmethod
    def _from_marker(lines: list[str], marker: LogMarker) -> list[str]:
        """
        :param lines: The log's lines.
        :param marker: What opens the part worth keeping.
        :return: That line and everything after it, empty where it never appears.
        """
        for index, line in enumerate(lines):
            if marker in line:
                return lines[index:]
        return []

    @staticmethod
    def _until_marker(lines: list[str], marker: LogMarker) -> list[str]:
        """
        Stop where the failure has been stated, before the steps that run afterwards.

        :param lines: The lines kept so far.
        :param marker: What the runner writes once the step has failed.
        :return: Everything through that line, or all of them where it never appears.
        """
        for index, line in enumerate(lines):
            if marker in line:
                return lines[: index + 1]
        return lines


@dataclass
class FailureLogReader:
    """
    Reads the log behind every check a pull request did not pass.
    """

    job_logs: JobLogReader
    """
    How one job's log is fetched.
    """

    upstream_repository: Repository
    """
    The repository whose jobs are read.
    """

    def read(self, checks: CheckStatus | None) -> list[FailureLog]:
        """
        Excerpt the log behind each check that failed.

        A check still running has no failure to describe, and one whose link names no
        job has no log to read, so both are left out rather than reported empty.

        :param checks: The head commit's checks, where it reported any.
        :return: One excerpt per failed check with a job behind it.
        """
        if checks is None:
            return []
        excerpts = []
        for result in checks.unsuccessful:
            if result.outcome is CheckOutcome.PENDING:
                continue
            job = FailedJob.behind(result)
            if job is None:
                continue
            excerpts.append(
                FailureLog.excerpt(
                    result.name,
                    self.job_logs.read_job_log(
                        self.upstream_repository, job.identifier
                    ),
                )
            )
        return excerpts
