#!/usr/bin/env python3
"""
Read an upstream pull request through a helper session, for a Claude Code cloud session
that cannot attach the upstream itself.

A cloud session checks every attached repository out at a directory named after it, so
a session working in a fork can never attach the fork's same-named upstream beside it.
The read then happens in a helper session that has only the upstream attached. A helper
runs no code from any repository outside its own scope, so its prompt carries the
program: :mod:`basstler.upstream_reviews.helper.fetch`, code alone, configured with the paths
of :class:`RepositoryRoute` and the fields the REST models read. Its output reaches
this session's transcript once the helper's events are read, and the report replays it
through :class:`RestPullRequestReader`, so it renders exactly what a direct read does.

Two commands, run around the helper session::

    # record the request and print the helper session's first message
    python -m basstler.upstream_reviews.helper prompt --upstream O/N --fork-owner F \\
        (--pull-request N | --branch B) [--failure-logs]
    # once the helper's events are read: render the report
    python -m basstler.upstream_reviews.helper report [--include-resolved]
"""

from __future__ import annotations

from basstler_domain_model.github.exceptions import (
    GitHubCredentialUnavailableError,
    GitHubRequestFailed,
    PullRequestReadError,
)

import argparse
import ast
import json
import shlex
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from basstler.upstream_reviews.helper import fetch
from basstler_domain_model.class_property import (
    classproperty,
)
from basstler_domain_model.repository import (
    Repository,
)
from basstler.standard_streams import StandardStreamHandler
from basstler.upstream_reviews.failure_logs import (
    FailedJob,
)
from basstler.upstream_reviews.helper.recording import (
    HelperChunk,
    HelperRecording,
    RecordedFields,
    RecordedJobLog,
    SessionTranscript,
)
from basstler_domain_model.github.routes import RepositoryRoute
from basstler.upstream_reviews.request import (
    UpstreamReadRequest,
)
from basstler_domain_model.github.vocabulary import (
    CheckOutcome,
)

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its commands print through.
"""


# %% what the helper is told


class HelperPlaceholder(StrEnum):
    """
    Stand-ins in the script's path templates, which the script fills in once it knows
    them.
    """

    NUMBER = "{number}"
    SHA = "{sha}"
    JOB = "{job}"


@dataclass(frozen=True)
class HelperScript:
    """
    What the helper session runs: :mod:`basstler.upstream_reviews.helper.fetch`, configured.

    Its paths come from :class:`RepositoryRoute` and its fields from the REST models,
    so the helper fetches exactly what :class:`RestPullRequestReader` asks the
    recording for, and nothing the models do not read.
    """

    request: UpstreamReadRequest
    """
    The read to perform.
    """

    @property
    def configuration(self) -> dict[str, Any]:
        """:return: Everything the script reads, passed to it as JSON."""
        route = RepositoryRoute(self.request.upstream)
        # The route is asked for its paths with placeholders standing in for values the
        # script only learns as it reads, so it fills in the reader's own paths.
        number, sha = HelperPlaceholder.NUMBER.value, HelperPlaceholder.SHA.value
        return {
            "marker": HelperChunk.marker(self.request.identifier),
            "request_identifier": self.request.identifier,
            "fork_owner": self.request.fork_owner,
            "pull_request": self.request.pull_request,
            "failure_logs": self.request.failure_logs,
            "chunk_size": HelperChunk.size,
            "log_line_limit": RecordedJobLog.line_limit,
            "job_link": FailedJob.link_pattern.pattern,
            "fields": sorted(RecordedFields.names),
            "log_outcomes": [
                outcome.name.lower()
                for outcome in CheckOutcome
                if outcome not in {CheckOutcome.SUCCESS, CheckOutcome.PENDING}
            ],
            "paths": {
                "pull_requests_for_head": (
                    route.pull_requests_for_head(
                        self.request.fork_owner, self.request.branch
                    )
                    if self.request.branch
                    else None
                ),
                "pull_request": route.pull_request(number),  # type: ignore[arg-type]
                "reviews": route.reviews(number),  # type: ignore[arg-type]
                "review_comments": route.review_comments(number),  # type: ignore[arg-type]
                "review_threads": route.review_threads(number),  # type: ignore[arg-type]
                "check_runs": route.check_runs(sha),
                "commit_status": route.commit_status(sha),
                "job_log": self.request.upstream.job_log_path(
                    HelperPlaceholder.JOB.value  # type: ignore[arg-type]
                ),
            },
        }

    @staticmethod
    def code_only(source: str) -> str:
        """
        Drop a module's docstrings, comments and parameter and return annotations,
        which the helper would only copy back out, token for token, when it runs the
        command.

        :param source: The module's source.
        :return: The same program, code alone.
        """
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.arg):
                node.annotation = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.returns = None
            body = getattr(node, "body", None)
            if (
                isinstance(body, list)
                and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]  # type: ignore[attr-defined]
        return ast.unparse(tree)

    @classproperty
    def delimiter(cls) -> str:
        """
        Ends the here-document carrying the source, a word the source never holds.
        """
        return "UPSTREAM_REVIEWS_FETCH"

    def render(self) -> str:
        """
        :return: The shell command running the fetch module from its own source.
        """
        source = self.code_only(Path(fetch.__file__).read_text())
        configuration = json.dumps(self.configuration, separators=(",", ":"))
        return "\n".join(
            [
                f"python3 - {shlex.quote(configuration)} <<'{self.delimiter}'",
                source,
                self.delimiter,
            ]
        )


@dataclass(frozen=True)
class HelperPrompt:
    """
    The whole first message a helper session is started with.
    """

    request: UpstreamReadRequest
    """
    The read to perform.
    """

    def render(self) -> str:
        """:return: The message."""
        return "\n".join(
            [
                "Read-only helper for another session: change nothing on GitHub. Run "
                "this in one Bash call, exactly as written; it only reads through "
                "`gh api`:",
                "",
                "```bash",
                HelperScript(self.request).render(),
                "```",
                "",
                "If it prints `cat` commands, run each as its own Bash call. Never "
                "repeat or summarize output; the other session reads it from your "
                "transcript. Then reply with exactly DONE, or with the error text if "
                "a command failed.",
            ]
        )


# %% command line


class HelperCommand(StrEnum):
    """
    The module's two commands.
    """

    PROMPT = "prompt"
    """
    Record a request and print the helper session's first message.
    """

    REPORT = "report"
    """
    Build the report from the helper's output in this session's transcript.
    """


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """
    Parse the command line.

    :param argv: The arguments to parse, defaulting to the process's own.
    :return: The parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prompt = commands.add_parser(HelperCommand.PROMPT)
    target = prompt.add_mutually_exclusive_group(required=True)
    target.add_argument("--pull-request", type=int)
    target.add_argument("--branch")
    prompt.add_argument("--upstream", required=True, type=Repository.parse)
    prompt.add_argument("--fork-owner", required=True)
    prompt.add_argument("--failure-logs", action="store_true")
    report = commands.add_parser(HelperCommand.REPORT)
    report.add_argument("--include-resolved", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """
    Run one command.

    :param argv: The arguments to parse, defaulting to the process's own.
    :return: The process exit status.
    """
    arguments = _parse_arguments(argv)
    try:
        if arguments.command == HelperCommand.PROMPT:
            request = UpstreamReadRequest.from_arguments(arguments, arguments.upstream)
            request.save()
            logger.info(HelperPrompt(request).render())
            return 0
        request = UpstreamReadRequest.load()
        recording = HelperRecording.assemble(
            request.identifier, SessionTranscript.current().chunks(request.identifier)
        )
        logger.info(recording.report(request, arguments.include_resolved))
    except (
        PullRequestReadError,
        GitHubRequestFailed,
        GitHubCredentialUnavailableError,
    ) as failure:
        logger.error(failure)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
