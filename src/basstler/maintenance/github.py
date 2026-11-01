"""
Reading and writing a repository's pull requests and labels.

The reading and the writing halves are declared separately, so a caller that must not
write - the board export - can be handed a reader and provably cannot. Every write here
was probed against the live API first: a pull request's *base branch* is the one the
credential a session carries is refused, which is why retargeting is reported for a
caller to perform rather than performed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any

from basstler.pull_request_state import PullRequestState
from basstler.class_property import classproperty
from basstler.exit_codes import CommandFailure, StackExitCode
from basstler.github_labels import GitHubLabel
from basstler.locations import PathEnumeration
from basstler.maintenance.board import CommitChecks, PullRequestRecord
from basstler.environment import GitHubCredentialVariable
from basstler.maintenance.exceptions import ExternalCallFailed
from basstler.repository import GitHubAddress, Repository
from basstler.stack.pull_request_stacks import PullRequestStacks, RegisteredStack
from basstler.upstream_reviews.pull_request_models import CheckStatus
from basstler.upstream_reviews.rest_models import (
    RestCheckRunPage,
    RestCombinedStatusPage,
    RestJSONKey,
)
from basstler.upstream_reviews.vocabulary import RollupState


@dataclass
class GitHubCredentialUnavailableError(CommandFailure, RuntimeError):
    """
    Raised when no token is available to authenticate the API calls with.
    """

    @classproperty
    def exit_code(cls) -> StackExitCode:
        """:return: The status this kind of error ends a command with."""
        return StackExitCode.CREDENTIAL_UNAVAILABLE

    variables: tuple[str, ...]
    """
    The environment variables that were consulted.
    """

    def __str__(self) -> str:
        """:return: What was looked for, so the caller can supply it."""
        return (
            f"no GitHub token: set one of {', '.join(self.variables)}, or run this "
            f"with a caller that has one"
        )


# %% what a pass reads and writes


@dataclass(frozen=True)
class PullRequestReader(ABC):
    """
    Reading the pull-request state a pass derives from.

    Implementations inherit rather than merely match the shape, so one that omits a read
    is refused when it is constructed rather than when the missing call is first made.
    """

    @abstractmethod
    def open_pull_requests(self) -> list[PullRequestRecord]:
        """:return: Every open pull request on the fork."""

    @abstractmethod
    def pull_request(self, number: int) -> PullRequestRecord:
        """:param number: The pull request to read.
        :return: That pull request."""


@dataclass(frozen=True)
class PullRequestWriter(ABC):
    """
    The three writes a pass makes, each one probed against the live API first.

    Every one of them is available to the credential a session carries; a pull request's
    *base branch* is the single write that is not, which is why reparenting is the
    caller's job and none of this is.
    """

    @abstractmethod
    def replace_labels(self, number: int, labels: Sequence[str]) -> None:
        """:param number: The pull request to write.
        :param labels: The complete label set it must end up with."""

    @abstractmethod
    def add_comment(self, number: int, body: str) -> str:
        """:param number: The pull request to comment on.
        :param body: The comment.
        :return: The comment's URL."""

    @abstractmethod
    def set_description(self, number: int, body: str) -> None:
        """:param number: The pull request to write.
        :param body: The new description."""


@dataclass(frozen=True)
class ForkPullRequests(PullRequestReader, PullRequestWriter, ABC):
    """
    Everything a pass does to the fork's pull requests.

    A pass reads state and writes back to the same fork, so the two halves are named
    together wherever both are needed; the board export takes the reading half alone,
    which is what keeps an export from being able to write.
    """


@dataclass(frozen=True)
class RepositoryLabels(ABC):
    """
    The labels a repository carries, and adding one it lacks.
    """

    @abstractmethod
    def labels(self) -> set[str]:
        """:return: The name of every label the repository carries."""

    @abstractmethod
    def create_label(self, label: GitHubLabel) -> None:
        """:param label: The label to add, described by its purpose."""


@dataclass(frozen=True)
class CredentialHolder(ABC):
    """
    Telling whose credential a client calls GitHub with.
    """

    @abstractmethod
    def authenticated_login(self) -> str:
        """:return: The login of the account the credential belongs to."""


class GitHubPath(PathEnumeration):
    """
    The API paths that are not below a repository.
    """

    AUTHENTICATED_USER = Path("/user")
    """
    The account the request's credential belongs to.
    """


class RepositoryPath(PathEnumeration):
    """
    The API paths below a repository.
    """

    PULL_REQUESTS = Path("/pulls")
    """
    The repository's pull requests.
    """

    LABELS = Path("/labels")
    """
    The labels defined on the repository.
    """

    COMMITS = Path("/commits")
    """
    The repository's commits.
    """

    STACKS = Path("/stacks")
    """
    The stacks the repository's pull requests are grouped in.
    """


class CommitReport(StrEnum):
    """
    The listings reported against one commit, below :attr:`RepositoryPath.COMMITS`.
    """

    CHECK_RUNS = "check-runs"
    """
    The check runs reported against it.
    """

    STATUS = "status"
    """
    The commit statuses posted against it.
    """


class ListingParameter(StrEnum):
    """
    The query parameters a paged listing takes.
    """

    STATE = "state"
    """
    Which pull requests to list, by state.
    """

    PAGE_SIZE = "per_page"
    """
    How many entries one page holds.
    """

    PAGE = "page"
    """
    Which page to read, counting from one.
    """

    PULL_REQUEST = "pull_request"
    """
    Which pull request a stack listing is narrowed to the stack of.
    """


class StacksApi(StrEnum):
    """
    What a call to GitHub's stacks endpoints carries beyond any other call.
    """

    VERSION = "2026-03-10"
    """
    The API version the stacks endpoints are served under.
    """

    PULL_REQUESTS = "pull_requests"
    """
    The key a create or add call names its pull requests under.
    """

    ADD = "add"
    """
    The path below a stack that puts pull requests on top of it.
    """


class LabelField(StrEnum):
    """
    The fields of a label the API reads and writes.
    """

    NAME = "name"
    """
    The label's name.
    """

    DESCRIPTION = "description"
    """
    What the label means.
    """


class AccountField(StrEnum):
    """
    The fields of an account the API describes.
    """

    LOGIN = "login"
    """
    The account's login.
    """


@dataclass(frozen=True)
class PullRequestListingSpecification:
    """
    One filter the pull request list API takes: what it is asked for by, and which
    :class:`PullRequestState` values it lets through.
    """

    query: str
    """
    The value the list API's ``state`` parameter takes for this filter.
    """

    states: tuple[PullRequestState, ...]
    """
    The pull request states this filter lets through.
    """


class PullRequestListState(PullRequestListingSpecification, Enum):
    """
    The pull request states the list API filters on: each :class:`PullRequestState` a
    pull request reports, plus every state at once.

    Each member *is* a :class:`PullRequestListingSpecification`, written as its fields
    in declaration order - ``query``, then ``states`` - which the enum passes straight to
    the dataclass.
    """

    OPEN = PullRequestState.OPEN.value, (PullRequestState.OPEN,)
    """
    Pull requests whose state is :attr:`PullRequestState.OPEN`: drafts and those ready
    for review alike.
    """

    CLOSED = PullRequestState.CLOSED.value, (PullRequestState.CLOSED,)
    """
    Pull requests whose state is :attr:`PullRequestState.CLOSED`: merged and closed
    unmerged alike.
    """

    ALL = "all", tuple(PullRequestState)
    """
    Every pull request, whatever its :class:`PullRequestState`.
    """


@dataclass
class GitHubRequestFailed(CommandFailure, ExternalCallFailed):
    """
    Raised when the API refuses a call this module depends on.
    """

    @classproperty
    def exit_code(cls) -> StackExitCode:
        """:return: The status this kind of error ends a command with."""
        return StackExitCode.GITHUB_REQUEST_FAILED

    method: str = ""
    """
    The HTTP method used.
    """
    path: str = ""
    """
    The API path called, without the host.
    """

    @property
    def call(self) -> str:
        """:return: The request line, as issued."""
        return f"{self.method} {self.path}"


# %% the client that makes the calls


@dataclass(frozen=True)
class GitHubRepository(
    ForkPullRequests,
    CommitChecks,
    RepositoryLabels,
    CredentialHolder,
    PullRequestStacks,
):
    """
    Every pull-request and label call this tooling makes, against one repository.

    ``gh`` is absent from the environment this normally runs in, so the calls are plain
    authenticated requests rather than a CLI wrapper.
    """

    repository: Repository
    """
    The repository to read and write.
    """

    token: str
    """
    The credential the requests authenticate with.
    """

    page_size: int = 100
    """
    How many pull requests or labels to ask for per request.
    """

    api_version: str | None = None
    """
    The API version every call is made under, ``None`` for GitHub's default one.
    """

    @classmethod
    def from_environment(cls, repository: Repository) -> GitHubRepository:
        """
        Build a client from whichever credential the environment carries.

        :param repository: The repository to read and write.
        :return: The client.
        :raises GitHubCredentialUnavailableError: If no token is set.
        """
        token = GitHubCredentialVariable.first_token()
        if token:
            return cls(repository, token)
        raise GitHubCredentialUnavailableError(tuple(GitHubCredentialVariable))

    def open_pull_requests(self) -> list[PullRequestRecord]:
        """:return: Every open pull request on the repository, oldest page first."""
        return self._pull_requests_in_state(PullRequestListState.OPEN)

    def all_pull_requests(self) -> list[PullRequestRecord]:
        """:return: Every pull request on the repository, open or closed."""
        return self._pull_requests_in_state(PullRequestListState.ALL)

    def issue(self, number: int) -> Mapping[str, Any]:
        """:param number: The issue or pull request to read.
        :return: It, as the issues API describes it."""
        return self._call("GET", f"/issues/{number}")

    def _pull_requests_in_state(
        self, state: PullRequestListState
    ) -> list[PullRequestRecord]:
        """
        Read every page of pull requests in one state.

        :param state: Which pull requests to list.
        :return: Them, oldest page first.
        """
        return self._every_page(
            RepositoryPath.PULL_REQUESTS, {ListingParameter.STATE: state.query}
        )

    def labels(self) -> set[str]:
        return {
            label[LabelField.NAME]
            for label in self._every_page(RepositoryPath.LABELS, {})
        }

    def create_label(self, label: GitHubLabel) -> None:
        self._call(
            "POST",
            str(RepositoryPath.LABELS),
            {LabelField.NAME: label, LabelField.DESCRIPTION: label.purpose},
        )

    def check_state(self, commit: str) -> RollupState:
        runs = [
            run.result
            for page in self._every_page_of_entries(
                self._commit_report(commit, CommitReport.CHECK_RUNS),
                RestJSONKey.CHECK_RUNS,
            )
            for run in RestCheckRunPage.from_json(page).check_runs
        ]
        statuses = [
            status.result
            for page in self._every_page_of_entries(
                self._commit_report(commit, CommitReport.STATUS), RestJSONKey.STATUSES
            )
            for status in RestCombinedStatusPage.from_json(page).statuses
        ]
        checks = CheckStatus.over(runs + statuses)
        return RollupState.NOT_REPORTED if checks is None else checks.state

    @staticmethod
    def _commit_report(commit: str, report: CommitReport) -> str:
        """:param commit: The commit reported against.
        :param report: Which of its listings to read.
        :return: The listing's path below the repository."""
        return f"{RepositoryPath.COMMITS}/{commit}/{report}"

    def authenticated_login(self) -> str:
        account = self._request("GET", str(GitHubPath.AUTHENTICATED_USER))
        return str(account[AccountField.LOGIN])

    def _every_page(
        self, path: RepositoryPath, filters: Mapping[ListingParameter, str]
    ) -> list[Any]:
        """
        Read a listing below the repository to its end.

        :param path: The listing's path below the repository.
        :param filters: The query parameters narrowing the listing.
        :return: Every entry, first page first.
        """
        return [
            entry
            for page in self._every_page_of_entries(str(path), None, filters)
            for entry in page
        ]

    def _every_page_of_entries(
        self,
        path: str,
        entries_key: str | None,
        filters: Mapping[ListingParameter, str] | None = None,
    ) -> list[Any]:
        """
        Read a paged listing below the repository to its end.

        :param path: The listing's path below the repository.
        :param entries_key: The key a page holds its entries under, ``None`` when a page
            is the list of entries itself.
        :param filters: The query parameters narrowing the listing.
        :return: Every page, first page first.
        """
        pages: list[Any] = []
        page_number = 1
        while True:
            query = urllib.parse.urlencode(
                {
                    **(filters or {}),
                    ListingParameter.PAGE_SIZE: self.page_size,
                    ListingParameter.PAGE: page_number,
                }
            )
            page = self._call("GET", f"{path}?{query}")
            pages.append(page)
            entries = page if entries_key is None else page[entries_key]
            if len(entries) < self.page_size:
                return pages
            page_number += 1

    def pull_request(self, number: int) -> PullRequestRecord:
        """:param number: The pull request to read.
        :return: That pull request."""
        return self._call("GET", f"{RepositoryPath.PULL_REQUESTS}/{number}")

    def replace_labels(self, number: int, labels: Sequence[str]) -> None:
        """
        Write a pull request's complete label set.

        :param number: The pull request to write.
        :param labels: The complete set it must end up with, computed by
            :meth:`stack.LabelWrite.replacing` - this call replaces rather than adds.
        """
        self._call("PUT", f"/issues/{number}/labels", {"labels": list(labels)})

    def add_comment(self, number: int, body: str) -> str:
        """:param number: The pull request to comment on.
        :param body: The comment.
        :return: The comment's URL."""
        created = self._call("POST", f"/issues/{number}/comments", {"body": body})
        return str(created["html_url"])

    def set_description(self, number: int, body: str) -> None:
        """
        Rewrite a pull request's description and nothing else.

        :param number: The pull request to write.
        :param body: The new description.
        """
        self._call("PATCH", f"{RepositoryPath.PULL_REQUESTS}/{number}", {"body": body})

    @property
    def stacks_endpoints(self) -> GitHubRepository:
        """
        This client, calling under the API version the stacks endpoints are served
        under.
        """
        return replace(self, api_version=StacksApi.VERSION)

    def stack_containing(self, pull_request: int) -> RegisteredStack | None:
        query = urllib.parse.urlencode({ListingParameter.PULL_REQUEST: pull_request})
        found = self.stacks_endpoints._call("GET", f"{RepositoryPath.STACKS}?{query}")
        return RegisteredStack.from_json(found[0]) if found else None

    def create_stack(self, pull_requests: Sequence[int]) -> RegisteredStack:
        return RegisteredStack.from_json(
            self.stacks_endpoints._call(
                "POST",
                str(RepositoryPath.STACKS),
                {StacksApi.PULL_REQUESTS: list(pull_requests)},
            )
        )

    def add_to_stack(
        self, stack_number: int, pull_requests: Sequence[int]
    ) -> RegisteredStack:
        return RegisteredStack.from_json(
            self.stacks_endpoints._call(
                "POST",
                f"{RepositoryPath.STACKS}/{stack_number}/{StacksApi.ADD}",
                {StacksApi.PULL_REQUESTS: list(pull_requests)},
            )
        )

    def _call(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Any:
        """
        Make one authenticated API call below this repository.

        :param method: The HTTP method.
        :param path: The path below the repository, starting with a slash.
        :param payload: The JSON body, absent for a read.
        :return: The decoded response.
        :raises GitHubRequestFailed: If the API answers with an error status.
        """
        return self._request(method, f"/repos/{self.repository}{path}", payload)

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Any:
        """
        Make one authenticated API call.

        :param method: The HTTP method.
        :param path: The path below the API root, starting with a slash.
        :param payload: The JSON body, absent for a read.
        :return: The decoded response.
        :raises GitHubRequestFailed: If the API answers with an error status.
        """
        version = (
            {}
            if self.api_version is None
            else {"X-GitHub-Api-Version": self.api_version}
        )
        request = urllib.request.Request(
            f"{GitHubAddress.API}{path}",
            method=method,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                **version,
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as refused:
            raise GitHubRequestFailed(
                status=refused.code,
                detail=refused.read().decode(errors="replace"),
                method=method,
                path=path,
            ) from refused
