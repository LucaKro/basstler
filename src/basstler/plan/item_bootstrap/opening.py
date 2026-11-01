"""
Opening an item's work: its branch and its draft pull request.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from basstler_domain_model.environment import (
    GitHubCredentialVariable,
)
from basstler_domain_model.github.pull_request_fields import (
    PullRequestField,
)
from basstler.plan.item_bootstrap.documents import PlanDocuments, ProjectCheckout
from basstler.plan.item_bootstrap.exceptions import (
    BranchAlreadyPublishedError,
    PullRequestDetailsMissingError,
    PullRequestRefusedError,
)
from basstler.plan.item_bootstrap.manifest_text import ManifestText
from basstler.plan.item_bootstrap.report import BootstrapReport
from basstler.plan.model import (
    ItemStatus,
    ManifestKey,
)
from basstler_domain_model.repository import (
    GitHubAddress,
    PullRequestReference,
    Repository,
)

# %% opening the work


class NewPullRequestField(StrEnum):
    """
    The fields GitHub's create-pull-request endpoint reads from its JSON body.
    """

    TITLE = "title"
    """
    The pull request's title.
    """

    BODY = "body"
    """
    The pull request's description.
    """

    HEAD = "head"
    """
    The branch carrying the changes.
    """

    BASE = "base"
    """
    The branch to merge into.
    """

    DRAFT = "draft"
    """
    Whether it opens as a draft.
    """


@dataclass(frozen=True)
class NewPullRequest:
    """
    A pull request that does not exist yet.
    """

    repository: Repository
    """
    The repository to open it on.
    """

    title: str
    """
    The pull request's title.
    """

    body: str
    """
    The pull request's description.
    """

    head: str
    """
    The branch carrying the changes.
    """

    base: str
    """
    The branch to merge into.
    """

    draft: bool = True
    """
    Always a draft: this repository's convention is that a pull request stays a draft
    until its author has reviewed it themselves, and at creation time nobody has.
    """

    def to_json(self) -> dict[NewPullRequestField, Any]:
        """
        :return: The body GitHub's create-pull-request endpoint reads.
        """
        return {
            NewPullRequestField.TITLE: self.title,
            NewPullRequestField.BODY: self.body,
            NewPullRequestField.HEAD: self.head,
            NewPullRequestField.BASE: self.base,
            NewPullRequestField.DRAFT: self.draft,
        }


class PullRequestOpener(ABC):
    """
    Creating a pull request on a forge, so the git work around it is testable without
    network access.
    """

    @abstractmethod
    def open_pull_request(self, pull_request: NewPullRequest) -> PullRequestReference:
        """
        Create *pull_request*.

        :param pull_request: The pull request to create.
        :raises PullRequestRefusedError: If the remote declines it.
        :return: The created pull request.
        """


@dataclass(frozen=True)
class GitHubPullRequestOpener(PullRequestOpener):
    """
    Opens pull requests through GitHub's REST API.

    Sends a bearer token when the environment supplies one and nothing otherwise, since
    inside a Claude Code session the agent proxy supplies the credential.
    """

    api_root: str = GitHubAddress.API
    """
    The API host, overridable for a GitHub Enterprise deployment.
    """

    def open_pull_request(self, pull_request: NewPullRequest) -> PullRequestReference:
        http_request = urllib.request.Request(
            f"{self.api_root}/{pull_request.repository.api_path}/pulls",
            data=json.dumps(pull_request.to_json()).encode(),
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                **self.authorization_headers(),
            },
        )
        try:
            with urllib.request.urlopen(http_request) as response:
                created = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise PullRequestRefusedError(
                detail=f"{error.code} "
                f"{error.read().decode(errors='replace').strip()}"
            ) from error
        return PullRequestReference(
            pull_request.repository, created[PullRequestField.NUMBER.key]
        )

    @staticmethod
    def authorization_headers() -> dict[str, str]:
        """
        The ``Authorization`` header, when the environment carries a token to send.

        :return: The header, or an empty mapping when there is no token.
        """
        token = GitHubCredentialVariable.first_token()
        return {"Authorization": f"Bearer {token}"} if token else {}


@dataclass(frozen=True)
class WorkOpenRequest:
    """
    What opening an item's work needs: the branch to create and the pull request to open
    on it.
    """

    plan_identifier: str
    """
    The plan the item belongs to.
    """

    item_identifier: str
    """
    The item being opened, which must already be tracked.
    """

    branch: str
    """
    The branch to create and publish.
    """

    base_branch: str
    """
    The branch to create it from and target the pull request at.
    """

    session_url: str
    """
    The session doing the work, recorded on the item.

    Required rather than derived: a session's environment cannot be asked which session
    it is, and a script that guessed would record something wrong in silence.
    """

    pull_request_title: str | None = None
    """
    The pull request's title, needed only when this module creates it.
    """

    pull_request_body: str | None = None
    """
    The pull request's description, needed only when this module creates it.
    """

    pull_request_number: int | None = None
    """
    A pull request the caller has already created, recorded instead of creating one.

    A pull request this module creates is attributed to the app the request is proxied
    through rather than to the person whose work it is, so a caller that can create one
    under its own identity should, and hand the number here. Left unset, the module
    creates it - which is what an unattended run with no session has to do.
    """


@dataclass
class WorkOpener:
    """
    The git half of opening an item's work: branch from the base, mark the start, and
    publish.
    """

    project_root: Path
    """
    The repository the branch is created in.
    """

    remote: str = "origin"
    """
    The remote the branch is published to.
    """

    def publish(self, request: WorkOpenRequest) -> None:
        """
        Create the branch from its base and push it, so a pull request has a head.

        The opening commit is empty by construction: the whole point of this tool is
        that the branch exists before any implementation does, and a pull request needs
        a commit its base does not have.

        :param request: The work being opened.
        """
        checkout = ProjectCheckout(self.project_root)
        checkout.run_git("checkout", "-b", request.branch, request.base_branch)
        checkout.run_git(
            "commit",
            "--allow-empty",
            "--quiet",
            "-m",
            f"Bootstrap {request.item_identifier}",
        )
        checkout.run_git("push", "-u", self.remote, request.branch)

    def open(
        self,
        request: WorkOpenRequest,
        pull_request_opener: PullRequestOpener | None = None,
    ) -> BootstrapReport:
        """
        Create the item's branch and draft pull request, then record both on the item.

        Both refusals this can raise before publishing - an untracked item, an already
        published branch - are checked first, so neither leaves anything behind. A pull
        request the remote declines is the one case that does: the branch is already
        published by then and stays, since a session cannot delete a remote branch. The
        manifest is left untouched rather than pointing at a pull request that does not
        exist, and re-running once the refusal is understood is refused by the
        already-published guard rather than silently overwriting those commits.

        :param request: The work to open.
        :param pull_request_opener: What creates the pull request, defaulting to GitHub.
        :raises UnknownItemError: If the plan does not track the item yet.
        :raises PullRequestDetailsMissingError: If it must create a pull request but was
            given nothing to create one from.
        :raises BranchAlreadyPublishedError: If the branch already exists on the remote.
        :raises PullRequestRefusedError: If the pull request could not be created.
        :return: What was opened.
        """
        opener = pull_request_opener or GitHubPullRequestOpener()
        if request.pull_request_number is None and not (
            request.pull_request_title and request.pull_request_body
        ):
            raise PullRequestDetailsMissingError(
                item_identifier=request.item_identifier
            )
        documents = PlanDocuments.load(request.plan_identifier, self.project_root)
        repository = Repository.parse(documents.repository_for(request.item_identifier))

        if not ProjectCheckout(self.project_root).branch_is_published(
            request.branch, self.remote
        ):
            self.publish(request)
        elif request.pull_request_number is None:
            raise BranchAlreadyPublishedError(branch=request.branch, remote=self.remote)

        created = (
            PullRequestReference(repository, request.pull_request_number)
            if request.pull_request_number is not None
            else opener.open_pull_request(
                NewPullRequest(
                    repository=repository,
                    title=request.pull_request_title,
                    body=request.pull_request_body,
                    head=request.branch,
                    base=request.base_branch,
                )
            )
        )

        manifest_text = ManifestText.from_text(
            documents.manifest_text
        ).with_item_fields(
            request.plan_identifier,
            request.item_identifier,
            {
                ManifestKey.BRANCH: request.branch,
                ManifestKey.PULL_REQUEST_NUMBER: created.number,
                ManifestKey.SESSION: request.session_url,
                ManifestKey.STATUS: ItemStatus.IN_PROGRESS.value,
            },
        )
        documents.save(manifest_text, documents.roadmap_text, self.project_root)
        return BootstrapReport(
            plan_identifier=request.plan_identifier,
            item_identifier=request.item_identifier,
            branch=request.branch,
            pull_request_number=created.number,
            pull_request_url=(
                created.url if request.pull_request_number is None else None
            ),
        )
