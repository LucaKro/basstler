"""
Which settings a checkout runs on, and which of its remotes are the fork and the
upstream.

``stack.toml`` carries the shipped defaults (label names, the base branch); the
project's committed ``.claude/basstler/stack.toml`` names its upstream, and a
``.claude/personal/stack.toml`` on the personal-notes branch, if present, layers per-
user overrides on top of both.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from basstler.locations import PackageLocation, ProjectLocation
from basstler_domain_model.git.commands import IntegrationStrategy, ProposedPush
from basstler_domain_model.repository import Repository
from basstler.shell_configuration import (
    FetchedBranchReference,
    ShellConfigurationFunction,
)
from basstler.stack.checkout import CurrentCheckout, GitArgument
from basstler.stack.configuration import (
    CommittedStackConfiguration,
    StackSetting,
)
from basstler.stack.exceptions import AmbiguousForkRemoteError, ForkRemoteNotFoundError
from basstler.standard_streams import StandardStreamHandler

logger = StandardStreamHandler.logger_for(__name__)
"""
This module's logger, which is also what its command prints through.
"""

# %% configuration


@dataclass(frozen=True)
class Remote:
    """
    A git remote, identified by the repository its URL names rather than by its name.
    """

    name: str
    """
    What this checkout calls the remote.
    """

    repository: Repository
    """
    The repository the remote points at.
    """


@dataclass(frozen=True)
class LayeredStackSettings:
    """
    The stack settings this checkout runs on: the shipped defaults and the project's
    settings, with any personal-notes overrides layered on top.
    """

    defaults: Path = PackageLocation.STACK_CONFIGURATION.value
    """
    The shipped defaults file.
    """

    def values(self) -> dict[str, str]:
        """:return: The layered values."""
        values = CommittedStackConfiguration(self.defaults).values()
        values.update(self._personal_overrides())
        return values

    @staticmethod
    def _personal_overrides() -> dict[str, object]:
        """
        Fetch the personal-notes branch and parse its configuration override file, if
        any.

        The shell configuration's own fetch function decides which remote and branch the
        notes are on, so this and the hook scripts can never disagree about it.

        :return: The parsed contents of :attr:`ProjectLocation.PERSONAL_STACK_CONFIGURATION`
            on the personal-notes branch, or an empty mapping if the branch or the file
            doesn't exist (e.g. before it has ever been written).
        """
        if ShellConfigurationFunction.FETCH_PERSONAL_NOTES_BRANCH.run().returncode != 0:
            return {}
        fetched_file = FetchedBranchReference.PERSONAL_NOTES.file_at(
            ProjectLocation.PERSONAL_STACK_CONFIGURATION
        )
        if not CurrentCheckout.succeeds(
            GitArgument.CAT_FILE, GitArgument.EXISTS, fetched_file
        ):
            return {}
        return tomllib.loads(CurrentCheckout.output(GitArgument.SHOW, fetched_file))


@dataclass(frozen=True)
class RemoteResolution:
    """
    Which remote is the fork and which is the upstream, decided without trusting names.
    """

    fork: Remote
    """
    The remote holding the stack.
    """

    upstream: Remote | None
    """
    The remote for the upstream review repository, absent if the checkout has none.
    """

    upstream_repository: Repository
    """
    The upstream, whether or not a remote points at it yet.
    """

    preferred_upstream_name: str
    """
    What the upstream's tracking references are kept under when no remote points at it.
    """

    @property
    def upstream_name(self) -> str:
        """
        :return: The upstream remote's name, or :attr:`preferred_upstream_name` when the
            checkout has no remote for it.
        """
        return self.upstream.name if self.upstream else self.preferred_upstream_name

    @property
    def upstream_location(self) -> str:
        """
        :return: What the upstream is fetched from: its remote's name, or its URL when
            the checkout has no remote for it.
        """
        return (
            self.upstream.name if self.upstream else self.upstream_repository.clone_url
        )

    @classmethod
    def resolve(
        cls,
        remote_urls: Mapping[str, str],
        upstream_repository: Repository,
        preferred_upstream_name: str,
        fork_repository: Repository | None = None,
    ) -> RemoteResolution:
        """
        Decide which remote is the fork and which is the upstream.

        Remotes are matched by the repository their URL names, so a checkout whose
        remotes are called anything at all resolves the same way.

        :param remote_urls: Remote name to URL, as git reports them.
        :param upstream_repository: The repository every fork is forked from.
        :param preferred_upstream_name: What to call the upstream remote if one must be
            added.
        :param fork_repository: The fork, when configuration names it outright.
        :return: The resolved remotes.
        :raises ForkRemoteNotFoundError: If no remote points at a fork.
        :raises AmbiguousForkRemoteError: If several do and configuration does not
            disambiguate.
        """
        remotes = [
            Remote(name, Repository.from_remote_url(url))
            for name, url in remote_urls.items()
            if Repository.names_a_repository(url)
        ]
        upstream = next(
            (remote for remote in remotes if remote.repository == upstream_repository),
            None,
        )
        candidates = tuple(
            remote for remote in remotes if remote.repository != upstream_repository
        )
        return cls(
            fork=cls._select_fork(candidates, fork_repository, upstream_repository),
            upstream=upstream,
            upstream_repository=upstream_repository,
            preferred_upstream_name=preferred_upstream_name,
        )

    @classmethod
    def of_checkout(
        cls,
        path: Path = PackageLocation.STACK_CONFIGURATION.value,
        fork_repository: Repository | None = None,
        upstream_repository: Repository | None = None,
    ) -> RemoteResolution:
        """
        Resolve this checkout's fork and upstream remotes.

        :param path: The committed defaults file.
        :param fork_repository: The fork, when the caller already knows it.
        :param upstream_repository: The upstream, when the caller already knows it.
        :return: The resolved remotes.
        :raises ForkRemoteNotFoundError: If no remote points at a fork.
        :raises AmbiguousForkRemoteError: If several do and nothing names which one it
            is.
        """
        values = LayeredStackSettings(path).values()
        configured_fork = values.get(StackSetting.FORK_REPOSITORY)
        return cls.resolve(
            CurrentCheckout.remote_urls(),
            upstream_repository
            or CommittedStackConfiguration(path).upstream_repository_in(
                values, ProjectLocation.PERSONAL_STACK_CONFIGURATION.value
            ),
            values[StackSetting.UPSTREAM_REMOTE],
            fork_repository
            or (Repository.parse(configured_fork) if configured_fork else None),
        )

    @staticmethod
    def _select_fork(
        candidates: tuple[Remote, ...],
        fork_repository: Repository | None,
        upstream_repository: Repository,
    ) -> Remote:
        """
        Pick the fork from the remotes that are not the upstream.

        :param candidates: The non-upstream remotes.
        :param fork_repository: The fork, when configuration names it outright.
        :param upstream_repository: The upstream, for reporting when nothing qualifies.
        :return: The fork's remote.
        :raises ForkRemoteNotFoundError: If no candidate qualifies.
        :raises AmbiguousForkRemoteError: If several do and configuration does not
            disambiguate.
        """
        if fork_repository:
            named = [
                remote for remote in candidates if remote.repository == fork_repository
            ]
            if not named:
                raise ForkRemoteNotFoundError(upstream_repository)
            return named[0]
        if not candidates:
            raise ForkRemoteNotFoundError(upstream_repository)
        if len(candidates) > 1:
            raise AmbiguousForkRemoteError(candidates)
        return candidates[0]


@dataclass
class Configuration:
    """
    Everything this checkout runs on: the layered settings and the remotes they resolve
    to.
    """

    in_review_label: str
    """
    Fork pull request label marking a branch as promoted to the upstream and under
    review.
    """

    rebase_label: str
    """
    Fork pull request label opting a branch into the rebase strategy instead of the
    default merge.
    """

    needs_resolution_label: str
    """
    Fork pull request label marking a branch withheld from promotion pending conflict
    resolution.
    """

    fork_repository: Repository
    """
    The fork that holds the full stack, as GitHub names it.
    """

    fork_remote: str
    """
    Git remote for the fork that holds the full stack.
    """

    upstream_repository: Repository
    """
    The repository every fork is forked from, and the only one constant across
    contributors.
    """

    upstream_remote: str
    """
    The name the upstream's tracking references are kept under, ``refs/remotes/<it>/``,
    whether or not a remote of that name exists.
    """

    upstream_base: str
    """
    The upstream base branch every stack ultimately targets.
    """

    upstream_location: str
    """
    What the upstream is fetched from: its remote's name, or its URL when the checkout
    has no remote for it.
    """

    @classmethod
    def load(
        cls,
        path: Path = PackageLocation.STACK_CONFIGURATION.value,
        fork_repository: Repository | None = None,
        upstream_repository: Repository | None = None,
    ) -> Configuration:
        """
        Parse the layered configuration.

        Values from the shipped *path* are the defaults; the project's committed
        ``.claude/basstler/stack.toml`` overrides them, and any key present in
        ``.claude/personal/stack.toml`` on the personal-notes branch overrides both, so
        a user's own remotes/labels never have to be hand-edited into a checked-in file.
        A repository passed here outranks all three, so a caller that has been told
        which repository is which never has one inferred for it.

        :param path: The shipped defaults file.
        :param fork_repository: The fork, when the caller already knows it.
        :param upstream_repository: The upstream, when the caller already knows it.
        :return: The layered configuration.
        """
        values = LayeredStackSettings(path).values()
        upstream_repository = upstream_repository or CommittedStackConfiguration(
            path
        ).upstream_repository_in(
            values, ProjectLocation.PERSONAL_STACK_CONFIGURATION.value
        )
        resolution = RemoteResolution.of_checkout(
            path, fork_repository, upstream_repository
        )
        return cls(
            in_review_label=values[StackSetting.IN_REVIEW_LABEL],
            rebase_label=values[StackSetting.REBASE_LABEL],
            needs_resolution_label=values[StackSetting.NEEDS_RESOLUTION_LABEL],
            fork_repository=resolution.fork.repository,
            fork_remote=resolution.fork.name,
            upstream_repository=upstream_repository,
            upstream_remote=resolution.upstream_name,
            upstream_base=values[StackSetting.UPSTREAM_BASE],
            upstream_location=resolution.upstream_location,
        )

    @property
    def upstream_base_reference(self) -> str:
        """
        The upstream's base branch, as the checkout's tracking reference names it.
        """
        return f"{self.upstream_remote}/{self.upstream_base}"

    def reference_on_fork(self, name: str) -> str:
        """:param name: A branch or parent name.
        :return: Its reference on the fork remote."""
        return f"{self.fork_remote}/{name}"

    def propose_push(
        self,
        branch: str,
        strategy: IntegrationStrategy,
        replaces_superseded_merges: bool = False,
    ) -> ProposedPush:
        """
        Build the push that publishes a restacked branch to the fork.

        :param branch: The branch to publish.
        :param strategy: How its parent was integrated, which authorises a rewrite when
            it is a rebase - and which ``Stack.build`` sets to rebase only from the label.
        :param replaces_superseded_merges: Whether the integration replaced merges that
            only brought in an earlier state of the parent, the one rewrite a merge is
            authorised to make.
        :return: The push.
        """
        return ProposedPush(
            remote=self.fork_remote,
            refspec=f"{branch}:{branch}",
            with_lease=strategy is IntegrationStrategy.REBASE
            or replaces_superseded_merges,
        )

    def print_settings(self) -> None:
        """
        Print the resolved configuration as one ``field<TAB>value`` line per setting.

        Keys are this class's own field names, so a caller reading one by name cannot be
        reading a name this module never prints. A setting with no value is omitted
        rather than printed empty.
        """
        for name, value in vars(self).items():
            if value is None:
                continue
            logger.info(f"{name}{PrintedConfiguration.FIELD_SEPARATOR}{value}")


class PrintedConfiguration(StrEnum):
    """
    How :meth:`Configuration.print_settings` lays a setting out on its line.
    """

    FIELD_SEPARATOR = "\t"
    """
    What separates a setting's name from its value.
    """

    @classmethod
    def read(cls, output: str) -> dict[str, str]:
        """
        Read printed configuration back into its settings.

        :param output: What :meth:`Configuration.print_settings` printed.
        :return: Each printed setting's value, by name.
        """
        return dict(line.split(cls.FIELD_SEPARATOR) for line in output.splitlines())
