#!/usr/bin/env python3
"""
Build the plan site: the hub, each repository's plans, and each plan's dashboard.

Reads the hub file, fetches every repository it lists, and renders that repository's
plans with the live state of every pull request they reference. Nothing here needs a
Claude session or belongs to an account, so the site is the same for everyone:

    <site>/                          the hub, one card per repository
    <site>/<owner>/<repo>/           that repository's plans
    <site>/<owner>/<repo>/<plan>/    a plan's dashboard

Usage:
    python3 -m basstler.plan_site --registry <owner/repo> --output _site \\
        [--site-url <url>] [--remote-root <url or directory>]

The hub file is read off the registry's notes branch. A plan whose manifest fails
validation, or a repository whose notes cannot be fetched, is reported and left out;
everything else is still published.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from typing import Any, Generic, TypeVar

import yaml

from basstler.dashboard.plan_page import DashboardRenderer, RenderedDashboard
from basstler.dashboard.plan import Plan
from basstler.exceptions import PlanValidationError
from basstler.plan.dependencies import DependencyResolver, PlanDirectory
from basstler.plan.validation import PlanValidator
from basstler.pull_request_state import PullRequestRecord, PullRequestsByRepository
from basstler.plan_site.hub_page import HubPage, RepositorySummary, SiteSummary
from basstler.dashboard.index_page import IndexPage, PlanSummary, ProgressCard
from basstler_domain_model.environment import (
    GitHubCredentialVariable,
)
from basstler_domain_model.github.pull_request_fields import (
    PullRequestField,
)
from basstler_domain_model.github.exceptions import (
    GitHubCredentialUnavailableError,
    GitHubRequestFailed,
)
from basstler_domain_model.github.repository_client import (
    GitHubRepository,
)
from basstler.plan_site.hub import NotesBranch, PlanHub
from basstler.plan.model import PlanDocument
from basstler.plan.model import ItemStatus
from basstler_domain_model.repository import (
    GitHubAddress,
    PullRequestReference,
    Repository,
)
from basstler.standard_streams import StandardStreamHandler

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command prints through.
"""


class SiteFile(StrEnum):
    """
    The files the site writes beside its pages.
    """

    PAGE = "index.html"
    """
    The page a directory serves, for the hub, every index and every plan alike.
    """

    NO_JEKYLL = ".nojekyll"
    """
    The marker that stops GitHub Pages from running Jekyll over already-rendered HTML.
    """

    DIGEST = "site-digest.txt"
    """
    The site's :class:`SiteDigest`, which a deploy compares with the published one.
    """


@dataclass(frozen=True)
class SiteDigest:
    """
    A fingerprint of a built site's pages: two builds have the same digest exactly when
    they publish the same files with the same content.
    """

    site_directory: Path
    """
    The root directory the site was built into.
    """

    @property
    def value(self) -> str:
        """
        The fingerprint, as a hexadecimal SHA-256, leaving out :attr:`SiteFile.DIGEST`.
        """
        digest = hashlib.sha256()
        for path in sorted(self.site_directory.rglob("*")):
            relative_path = path.relative_to(self.site_directory)
            if not path.is_file() or relative_path == Path(SiteFile.DIGEST):
                continue
            digest.update(relative_path.as_posix().encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    def write(self) -> None:
        """
        Write :attr:`value` to the site's :attr:`SiteFile.DIGEST`.
        """
        (self.site_directory / SiteFile.DIGEST).write_text(f"{self.value}\n")


# %% a plan as the notes branch stores it


@dataclass
class PlanSource:
    """
    One plan's manifest and roadmap, as read from its directory on the notes branch.
    """

    directory_name: str
    """
    The plan's directory name, which is also the path its page is published under.
    """

    manifest: dict[str, Any]
    """
    The parsed ``plan.yaml``, not yet validated.
    """

    roadmap_text: str
    """
    The plan's ``roadmap.md``, or an empty string if it has none.
    """

    @classmethod
    def from_directory(cls, directory: Path) -> PlanSource:
        """
        Read one plan's directory.

        :param directory: The directory holding the plan's manifest.
        :return: The plan's source.
        """
        roadmap = directory / PlanDocument.ROADMAP
        return cls(
            directory_name=directory.name,
            manifest=yaml.safe_load((directory / PlanDocument.MANIFEST).read_text()),
            roadmap_text=roadmap.read_text() if roadmap.exists() else "",
        )

    @classmethod
    def every_plan_in(cls, plans_directory: Path) -> list[PlanSource]:
        """
        Read every plan under a ``plans`` directory, in directory-name order.

        :param plans_directory: The directory holding one subdirectory per plan.
        :return: Each subdirectory that carries a manifest.
        """
        return [
            cls.from_directory(manifest.parent)
            for manifest in sorted(plans_directory.glob(f"*/{PlanDocument.MANIFEST}"))
        ]


# %% live pull request state


class PullRequestSource(ABC):
    """
    Where the live state of the pull requests a site shows comes from.
    """

    @abstractmethod
    def pull_request(self, reference: PullRequestReference) -> PullRequestRecord | None:
        """
        :param reference: The pull request to look up.
        :return: Its live state, or ``None`` if the repository has no such pull request.
        """

    @abstractmethod
    def issue_url(self, repository: Repository, number: int) -> str | None:
        """
        :param repository: The repository the issue or pull request is opened on.
        :param number: Its number.
        :return: Its web page, or ``None`` if the repository has no such number.
        """


@dataclass
class GitHubPullRequestSource(PullRequestSource):
    """
    Pull request state read from the GitHub REST API, once per repository.

    A repository's pull requests are listed in bulk on first use and kept, so a site
    covering many plans costs a handful of requests rather than one per item. A number
    the listing missed is read on its own.
    """

    token: str
    """
    The credential the requests authenticate with.
    """

    listed_repositories: set[Repository] = field(default_factory=set)
    """
    Every repository whose pull requests have been listed into :attr:`pull_requests`.
    """

    pull_requests: dict[PullRequestReference, PullRequestRecord] = field(
        default_factory=dict
    )
    """
    Every pull request read so far, listed or read on its own.
    """

    def client_for(self, repository: Repository) -> GitHubRepository:
        """
        :param repository: The repository to call.
        :return: A client for it.
        """
        return GitHubRepository(repository, self.token)

    def pull_request(self, reference: PullRequestReference) -> PullRequestRecord | None:
        self.list_pull_requests_of(reference.repository)
        if reference not in self.pull_requests:
            api_record = self.read_if_present(
                lambda client: client.pull_request(reference.number),
                reference.repository,
            )
            if api_record is None:
                return None
            self.pull_requests[reference] = PullRequestRecord.from_api(api_record)
        return self.pull_requests[reference]

    def issue_url(self, repository: Repository, number: int) -> str | None:
        api_record = self.read_if_present(
            lambda client: client.issue(number), repository
        )
        if api_record is None:
            return None
        return PullRequestField.HTML_URL.read(api_record, number)

    def list_pull_requests_of(self, repository: Repository) -> None:
        """
        Read every pull request on a repository into :attr:`pull_requests`, once.

        :param repository: The repository to list.
        """
        if repository in self.listed_repositories:
            return
        for api_record in self.client_for(repository).all_pull_requests():
            number = PullRequestField.NUMBER.read(api_record)
            self.pull_requests[PullRequestReference(repository, number)] = (
                PullRequestRecord.from_api(api_record)
            )
        self.listed_repositories.add(repository)

    def read_if_present(
        self,
        read: Callable[[GitHubRepository], Mapping[str, Any]],
        repository: Repository,
    ) -> Mapping[str, Any] | None:
        """
        Make one read, treating an absent number as an answer rather than a failure.

        :param read: The call to make, given the repository's client.
        :param repository: The repository to call.
        :return: The response, or ``None`` if the API answered that it does not exist.
        :raises GitHubRequestFailed: If the API refused the read for any other reason.
        """
        try:
            return read(self.client_for(repository))
        except GitHubRequestFailed as refused:
            if refused.status != HTTPStatus.NOT_FOUND:
                raise
            return None


# %% the site

SectionCard = TypeVar("SectionCard", bound=ProgressCard)
"""
The card a site section is summed up by: a repository's on the hub, or the whole
site's.
"""


@dataclass
class PublishedPlan:
    """
    One plan rendered to its dashboard, with the entry the index lists it under.
    """

    dashboard: RenderedDashboard
    """
    The plan's rendered dashboard page.
    """

    summary: PlanSummary
    """
    The plan's card on its repository's index.
    """


@dataclass
class SiteSection(ABC, Generic[SectionCard]):
    """
    One part of the site: the pages it writes under the site's root, and the card that
    stands for it on the page above.
    """

    site_url: str
    """
    The address the whole site is served from, which every link is built on.
    """

    pull_request_source: PullRequestSource
    """
    Where the live pull request state comes from.
    """

    @property
    def hub_url(self) -> str:
        """
        The hub's address, which every page links back up to.
        """
        return f"{self.site_url.rstrip('/')}/"

    @abstractmethod
    def build(self, output: Path) -> SectionCard:
        """
        Render this part of the site.

        :param output: The site's root directory.
        :return: The card summing this part up.
        """

    @staticmethod
    def write_page(directory: Path, html: str) -> None:
        """
        :param directory: The directory the page is served from.
        :param html: The page's rendered HTML.
        """
        directory.mkdir(parents=True, exist_ok=True)
        (directory / SiteFile.PAGE).write_text(html)


@dataclass
class RepositorySite(SiteSection[RepositorySummary]):
    """
    One repository's plans: their dashboards plus the index linking them.
    """

    repository: Repository
    """
    The repository whose notes the plans come from.
    """

    plans_directory: Path
    """
    That repository's ``plans`` directory, holding one subdirectory per plan.
    """

    plan_directory: PlanDirectory = field(init=False)
    """
    The same plans, as the ``<plan-id>/<item-id>`` entries of any one of them resolve
    against.
    """

    def __post_init__(self) -> None:
        self.plan_directory = PlanDirectory(self.plans_directory)

    @property
    def index_url(self) -> str:
        """
        The address this repository's index is served from.
        """
        return f"{self.hub_url}{self.repository}/"

    def plan_url(self, directory_name: str) -> str:
        """
        :param directory_name: The plan's directory name.
        :return: The address its page is served from.
        """
        return f"{self.index_url}{directory_name}/"

    def build(self, output: Path) -> RepositorySummary:
        """
        Render this repository's index and plans into the site.

        :param output: The site's root directory.
        :return: The repository's card on the hub.
        """
        published = {
            source.directory_name: self.publish(source)
            for source in PlanSource.every_plan_in(self.plans_directory)
            if self.is_publishable(source)
        }
        repository_directory = output / str(self.repository)
        for directory_name, plan in published.items():
            self.write_page(repository_directory / directory_name, plan.dashboard.html)
        summaries = [plan.summary for plan in published.values()]
        index = IndexPage(summaries, str(self.repository), self.hub_url)
        self.write_page(repository_directory, index.render())
        return RepositorySummary.over_plans(
            str(self.repository), self.index_url, summaries
        )

    def is_publishable(self, source: PlanSource) -> bool:
        """
        :param source: The plan to check.
        :return: Whether its manifest passes validation, logging why if it does not.
        """
        try:
            PlanValidator(source.manifest, self.plan_directory).validate()
        except PlanValidationError as error:
            logger.warning(f"{source.directory_name} is not published: {error}")
            return False
        return True

    def publish(self, source: PlanSource) -> PublishedPlan:
        """
        Render one valid plan.

        :param source: The plan to render.
        :return: Its dashboard and its index entry.
        """
        plan = Plan.from_mapping(source.manifest)
        dependencies = DependencyResolver(plan, self.plan_directory)
        renderer = DashboardRenderer(
            plan=plan,
            roadmap_text=source.roadmap_text,
            pull_requests_by_repository=self.pull_requests_of(dependencies),
            tracking_url=self.tracking_url_of(plan),
            hub_url=self.hub_url,
            index_url=self.index_url,
            plan_directory=self.plan_directory,
            locate_plan_page=self.plan_url,
        )
        dashboard = renderer.render()
        summary = PlanSummary(
            id=plan.id,
            title=plan.title,
            description=plan.description,
            done=dashboard.summary.status_counts.get(ItemStatus.DONE, 0),
            total=len(plan.items),
            open_pull_requests=dashboard.summary.open_pull_requests,
            reviewed_pull_requests=dashboard.summary.reviewed_pull_requests,
            dashboard_url=self.plan_url(source.directory_name),
        )
        return PublishedPlan(dashboard=dashboard, summary=summary)

    def pull_requests_of(
        self, dependencies: DependencyResolver
    ) -> PullRequestsByRepository:
        """
        :param dependencies: A plan's items, plus the items of other plans it depends
            on.
        :return: The live state of every pull request those items name, in the shape
            the dashboard renders from; a number the repository does not have is left
            out.
        """
        pull_requests: PullRequestsByRepository = {}
        for dependency in dependencies.resolved_dependencies:
            if dependency.item.pull_request_number is None:
                continue
            reference = PullRequestReference(
                Repository.parse(dependency.repository),
                dependency.item.pull_request_number,
            )
            record = self.pull_request_source.pull_request(reference)
            if record is not None:
                pull_requests.setdefault(str(reference.repository), {})[
                    str(reference.number)
                ] = record
        return pull_requests

    def tracking_url_of(self, plan: Plan) -> str | None:
        """
        :param plan: The plan whose tracking issue to link.
        :return: The tracking issue's page, or ``None`` if the plan has none.
        """
        if plan.tracking_issue is None:
            return None
        return self.pull_request_source.issue_url(
            Repository.parse(plan.default_repository), plan.tracking_issue
        )


@dataclass
class HubSite(SiteSection[SiteSummary]):
    """
    The whole site: the hub page over every repository the hub file lists.
    """

    hub: PlanHub
    """
    The hub file, naming the repositories to publish.
    """

    remote_root: str = GitHubAddress.WEB.value
    """
    Where the listed repositories are fetched from, as
    :meth:`Repository.clone_url_under` takes it.
    """

    def build(self, output: Path) -> SiteSummary:
        """
        Render every repository, then the hub linking them, then the site's digest.

        :param output: The directory to write the site into.
        :return: The site's summary, holding every repository's card in the hub file's
            order.
        """
        with tempfile.TemporaryDirectory() as scratch:
            repositories = [
                self.build_repository(
                    Repository.parse(repository), Path(scratch) / str(position), output
                )
                for position, repository in enumerate(self.hub.repositories)
            ]
        summary = SiteSummary.over_repositories(repositories)
        self.write_page(output, HubPage(summary).render())
        (output / SiteFile.NO_JEKYLL).touch()
        SiteDigest(output).write()
        return summary

    def build_repository(
        self, repository: Repository, scratch: Path, output: Path
    ) -> RepositorySummary:
        """
        :param repository: The repository to publish.
        :param scratch: A directory of its own to fetch the notes into.
        :param output: The site's root directory.
        :return: The repository's card, with an error when its notes cannot be read.
        """
        notes = NotesBranch.fetch(
            repository, self.hub.notes_branch, self.remote_root, scratch
        )
        if notes is None:
            return RepositorySummary.unreadable(
                str(repository), f"cannot fetch {self.hub.notes_branch}"
            )
        site = RepositorySite(
            site_url=self.site_url,
            pull_request_source=self.pull_request_source,
            repository=repository,
            plans_directory=notes.check_out_plans(),
        )
        return site.build(output)


def main() -> int:
    """
    Parse arguments and build the site. See the module docstring for the CLI contract.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--registry", required=True, help="The repository whose notes hold the hub file"
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="The directory to write the site to"
    )
    parser.add_argument(
        "--site-url", default=None, help="The site's address, if not the hub file's"
    )
    parser.add_argument(
        "--remote-root",
        default=GitHubAddress.WEB.value,
        help="Where repositories are fetched from, as <root>/<owner>/<repo>.git",
    )
    arguments = parser.parse_args()

    hub = PlanHub.from_registry(
        Repository.parse(arguments.registry), arguments.remote_root
    )
    if hub is None:
        logger.error(f"{arguments.registry} has no hub file on its notes branch.")
        return 1
    site_url = arguments.site_url or hub.site_url
    if not site_url:
        logger.error("The hub file names no site_url; pass --site-url.")
        return 1
    token = GitHubCredentialVariable.first_token()
    if not token:
        raise GitHubCredentialUnavailableError(tuple(GitHubCredentialVariable))
    site = HubSite(
        site_url=site_url,
        pull_request_source=GitHubPullRequestSource(token=token),
        hub=hub,
        remote_root=arguments.remote_root,
    )
    summary = site.build(arguments.output)
    logger.info(f"published {summary.progress_label} to {arguments.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
