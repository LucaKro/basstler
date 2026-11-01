"""
Every error reading an upstream pull request through the fork's Action or a helper
session can end in.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from pathlib import Path

from basstler_domain_model.github.exceptions import PullRequestReadError
from basstler_domain_model.repository import (
    Repository,
)
from basstler.upstream_reviews.vocabulary import ActionBranch, ActionFile

# %% errors


class UpstreamReadError(PullRequestReadError, ABC):
    """
    Base class for every failure reading an upstream pull request through the fork's
    Action or a helper session.
    """


# %% reading through a helper session


@dataclass
class HelperOutputMissing(UpstreamReadError):
    """
    Raised when this session's transcript holds none of the helper's output.
    """

    request_identifier: str
    """
    The request whose output was looked for.
    """

    def error_message(self) -> str:
        return (
            f"no output of helper request {self.request_identifier} in this "
            "session's transcript"
        )

    def suggest_correction(self) -> str:
        return 'Read the helper session\'s events (list_events, kinds ["user"]) first'


@dataclass
class HelperOutputIncomplete(UpstreamReadError):
    """
    Raised when only some of the helper's output chunks reached the transcript.
    """

    request_identifier: str
    """
    The request whose output was looked for.
    """

    missing: list[int]
    """
    The chunks not found.
    """

    def error_message(self) -> str:
        return (
            f"helper request {self.request_identifier} is missing chunks "
            f"{self.missing}"
        )

    def suggest_correction(self) -> str:
        return "Read the helper session's earlier events"


@dataclass
class HelperOutputUnreadable(UpstreamReadError):
    """
    Raised when the helper's output does not decode into a recording.
    """

    request_identifier: str
    """
    The request whose output was read.
    """

    reason: str
    """
    What failed.
    """

    def error_message(self) -> str:
        return (
            f"helper request {self.request_identifier}'s output is unreadable: "
            f"{self.reason}"
        )

    def suggest_correction(self) -> str:
        return "Run 'prompt' again and start a new helper session with its message"


@dataclass
class HelperResponseMissing(UpstreamReadError):
    """
    Raised when the report asks for a response the helper did not record.
    """

    path: str
    """
    The REST path asked for.
    """

    def error_message(self) -> str:
        return f"the helper's recording holds no response for {self.path}"

    def suggest_correction(self) -> str:
        return (
            "Run 'prompt' again with this basstler release and start a new helper "
            "session with its message"
        )


@dataclass
class NoRecordedReadRequest(UpstreamReadError):
    """
    Raised when ``report`` runs before any ``prompt`` recorded a request.
    """

    location: Path
    """
    Where the request was looked for.
    """

    def error_message(self) -> str:
        return f"no helper request recorded at {self.location}"

    def suggest_correction(self) -> str:
        return "Run 'prompt' first"


# %% reading through the fork's Action


class ActionUnavailable(UpstreamReadError, ABC):
    """
    Raised when the fork's Action cannot answer at all, as opposed to answering that the
    read failed.
    """


@dataclass
class ActionNotInstalled(ActionUnavailable):
    """
    Raised when a read is requested from a fork whose Action branch does not exist.
    """

    remote: str
    """
    The remote that was looked at.
    """

    def error_message(self) -> str:
        return f"'{self.remote}' has no {ActionBranch.NAME} branch"

    def suggest_correction(self) -> str:
        return f"Install it with {ActionBranch.install_command}"


@dataclass
class ActionRunNotStarted(ActionUnavailable):
    """
    Raised when GitHub started no run for the pushed request.
    """

    fork: Repository | None
    """
    The fork whose Actions were to run it, when its remote names one.
    """

    waited: int
    """
    How many seconds passed without one.
    """

    def error_message(self) -> str:
        return f"no run started within {self.waited} s of the request"

    def suggest_correction(self) -> str:
        actions = f"{self.fork.url}/actions" if self.fork else "the fork's Actions page"
        return (
            "Actions are most likely disabled on the fork, as GitHub starts every "
            f"fork - enable them at {actions}"
        )


@dataclass
class ActionResultMissing(ActionUnavailable):
    """
    Raised when no run answered the request in time.
    """

    request_identifier: str
    """
    The request no result answered.
    """

    fork: Repository | None
    """
    The fork whose Actions were to run it, when its remote names one.
    """

    waited: int
    """
    How many seconds were spent waiting.
    """

    def error_message(self) -> str:
        return f"no result for request {self.request_identifier} after {self.waited} s"

    def suggest_correction(self) -> str:
        actions = f"{self.fork.url}/actions" if self.fork else "the fork's Actions page"
        return (
            "Either Actions are disabled on the fork (a fork starts with them off - "
            f"enable them at {actions}), or the run failed: see "
            f"{actions}/workflows/{ActionFile.WORKFLOW.file_name}"
        )


@dataclass
class ActionRunFailed(UpstreamReadError):
    """
    Raised when the run answered the request with an error rather than a recording.
    """

    error: str
    """
    What the run reported.
    """

    def error_message(self) -> str:
        return f"the Action could not read the upstream: {self.error}"

    def suggest_correction(self) -> str:
        return ""


@dataclass
class ActionResponseMissing(UpstreamReadError):
    """
    Raised when the replay asks for a response the run did not record - the run used a
    different basstler release from this one.
    """

    query: str
    """
    The digest of the query asked for.
    """

    def error_message(self) -> str:
        return (
            f"the Action's recording holds no response for query {self.query} - its "
            "basstler release differs from this one"
        )

    def suggest_correction(self) -> str:
        return f"Re-run {ActionBranch.install_command}"
