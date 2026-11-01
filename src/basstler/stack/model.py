"""
The stack itself: the fork's pull requests, the branches they describe, and what the
stack derives from them.

The dependency tree is each fork pull request's base branch (base = parent). A branch is
``draft`` or ``ready`` by its pull request's draft flag, ``in-review`` while an upstream
pull request opened from it is open, and ``merged`` once git finds it contained in
``<upstream_remote>/<upstream_base>``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from basstler.github_labels import GitHubLabel
from basstler_domain_model.git.commands import IntegrationStrategy
from basstler_domain_model.repository import Repository
from basstler.stack.checkout import CurrentCheckout, LandedBranches
from basstler.stack.exceptions import BoardUnavailable
from basstler.stack.settings import Configuration
from basstler_domain_model.github.vocabulary import (
    RollupState,
)

# %% domain model


class BranchStatus(StrEnum):
    """A stack node's lifecycle position."""

    DRAFT = "draft"
    """Its author has not yet reviewed it themselves, so it may not be promoted."""

    READY = "ready"
    """Out of draft - reviewed by its author, and promotable once its parent is."""

    IN_REVIEW = "in-review"
    """Promoted to the upstream, where it waits on that queue rather than on us."""

    MERGED = "merged"
    """Its own commits are already contained in the upstream base."""

    @classmethod
    def derive(cls, draft: bool, merged: bool, in_review: bool) -> BranchStatus:
        """Map a pull request's raw facts to a lifecycle status.

        Precedence: a merged branch is ``merged``; a branch under upstream review is
        ``in-review``; an un-drafted branch is ``ready`` (self-approved for promotion);
        otherwise ``draft``.

        :param draft: Whether the pull request is still a draft.
        :param merged: Whether the branch has landed upstream (git ancestry).
        :param in_review: Whether an upstream pull request opened from the branch is
            open.
        :return: The derived status.
        """
        if merged:
            return cls.MERGED
        if in_review:
            return cls.IN_REVIEW
        return cls.DRAFT if draft else cls.READY


class SkipReason(StrEnum):
    """Why a pull request open on the fork is left out of the pass that maintains it."""

    HEAD_ON_ANOTHER_FORK = "head-on-another-fork"
    """Its head branch lives on another contributor's fork, which the pass cannot push
    to."""

    HEAD_REPOSITORY_DELETED = "head-repository-deleted"
    """The repository its head branch lived in has been deleted, so there is no branch
    left to maintain."""


class BoardKey(StrEnum):
    """
    The keys of ``board.json`` that are not a :class:`PullRequest` field.
    """

    PULL_REQUESTS = "pull_requests"
    """
    The exported pull requests, one object per :class:`PullRequest`.
    """


@dataclass
class PullRequest:
    """One fork pull request as exported into ``board.json``."""

    number: int
    """The pull request number on the fork."""

    head: str
    """The pull request's head branch - the branch this stack node names."""

    base: str
    """The pull request's base branch - its parent in the stack (``base = parent``)."""

    draft: bool
    """Whether the pull request is a draft (not yet approved for review)."""

    head_repository: str | None
    """The ``owner/name`` of the repository the head branch lives in, ``None`` once that
    repository is deleted."""

    labels: list[str] = field(default_factory=list)
    """Labels currently on the pull request."""

    check_state: RollupState = RollupState.NOT_REPORTED
    """The verdict over every check and commit status reported on the pull request's
    head."""

    upstream_pull_request: int | None = None
    """The number of the open upstream pull request opened from the head branch, ``None``
    while none is open."""

    session: str | None = None
    """URL of the Claude session working this pull request, parsed from the pull request body (None if none)."""

    title: str | None = None
    """The pull request's title, ``None`` when the export did not carry one."""

    description: str | None = None
    """The pull request's description without the sections a pass writes,
    ``None`` when the export did not carry one."""

    upstream_link: str | None = None
    """The URL of the upstream pull request a pass linked in the pull request body,
    ``None`` while none is linked."""

    def skip_reason(self, fork: Repository) -> SkipReason | None:
        """:param fork: The fork the pass maintains.
        :return: Why the pass has to leave this pull request out, ``None`` when its head
            is a branch of *fork*."""
        if self.head_repository is None:
            return SkipReason.HEAD_REPOSITORY_DELETED
        if self.head_repository != fork.full_name:
            return SkipReason.HEAD_ON_ANOTHER_FORK
        return None

    @classmethod
    def read_board(cls, path: Path) -> list[PullRequest]:
        """Parse ``board.json`` into the list of fork pull requests.

        :param path: The board export file.
        :return: The exported pull requests.
        :raises BoardUnavailable: If *path* does not exist.
        """
        if not path.exists():
            raise BoardUnavailable(path)
        board = json.loads(path.read_text())
        return [cls.from_board_entry(entry) for entry in board[BoardKey.PULL_REQUESTS]]

    @classmethod
    def from_board_entry(cls, entry: dict[str, Any]) -> PullRequest:
        """:param entry: One pull request as ``board.json`` holds it.
        :return: The pull request, its check state read back as its verdict."""
        pull_request = cls(**entry)
        return replace(pull_request, check_state=RollupState(pull_request.check_state))


@dataclass
class Branch:
    """A stack node derived from a fork pull request plus git state."""

    name: str
    """The branch name (the pull request head)."""

    parent: str
    """The parent branch (the pull request base)."""

    pull_request_number: int
    """The fork pull request number."""

    status: BranchStatus
    """Lifecycle status."""

    strategy: IntegrationStrategy
    """
    How the branch takes in its parent's moved tip: a merge replaces the merges earlier
    restacks left that only brought in an older state of the parent, so the branch
    carries one such merge; a rebase rewrites published history, and is authorised only
    by the branch's own rebase label.
    """

    labels: list[str]
    """Labels carried by the pull request."""

    skipped_pull_request: SkippedPullRequest | None
    """The pull request in its role as one the pass leaves out, ``None`` when the branch
    is on the fork."""

    check_state: RollupState = RollupState.NOT_REPORTED
    """The verdict over the checks reported on the pull request head."""

    upstream_pull_request: int | None = None
    """The open upstream pull request opened from this branch, if any."""

    session: str | None = None
    """URL of the Claude session working this pull request, if any."""

    title: str | None = None
    """The pull request's title, if known."""

    description: str | None = None
    """The pull request's description without the sections a pass writes, if known."""

    upstream_link: str | None = None
    """The URL of the upstream pull request linked on the pull request, if any."""

    @property
    def checks_failed(self) -> bool:
        """:return: Whether the checks on the pull request head have failed."""
        return self.check_state.failed

    @classmethod
    def from_pull_request(
        cls,
        pull_request: PullRequest,
        configuration: Configuration,
        is_merged: Callable[[str], bool],
    ) -> Branch:
        """:param pull_request: The exported pull request the node names.
        :param configuration: The static configuration naming the labels.
        :param is_merged: Maps a branch name to whether it has landed upstream.
        :return: The stack node the pull request describes."""
        skip_reason = pull_request.skip_reason(configuration.fork_repository)
        return cls(
            name=pull_request.head,
            parent=pull_request.base,
            pull_request_number=pull_request.number,
            status=BranchStatus.derive(
                pull_request.draft,
                is_merged(pull_request.head),
                pull_request.upstream_pull_request is not None,
            ),
            strategy=(
                IntegrationStrategy.REBASE
                if configuration.rebase_label in pull_request.labels
                else IntegrationStrategy.MERGE
            ),
            labels=pull_request.labels,
            skipped_pull_request=(
                None
                if skip_reason is None
                else SkippedPullRequest(pull_request, skip_reason)
            ),
            check_state=pull_request.check_state,
            upstream_pull_request=pull_request.upstream_pull_request,
            session=pull_request.session,
            title=pull_request.title,
            description=pull_request.description,
            upstream_link=pull_request.upstream_link,
        )


@dataclass(frozen=True)
class RestackPlanEntry:
    """One entry of :meth:`Stack.restack_plan`: a branch, and what and how it is brought up to date with."""

    branch: str
    """The branch to bring up to date."""

    parent: str
    """The branch it is brought up to date with."""

    strategy: IntegrationStrategy
    """How it is brought up to date."""


@dataclass(frozen=True)
class SkippedPullRequest:
    """The role of a pull request open on the fork that the pass leaves alone.

    It is neither restacked, promoted nor reported as landed, and nothing is written to
    it; only its base can still be retargeted, through :meth:`Stack.reparents`.
    """

    pull_request: PullRequest
    """The pull request taking the role."""

    reason: SkipReason
    """Why it is left alone."""


@dataclass(frozen=True)
class Reparent:
    """A pull request whose base has landed and must be retargeted at the upstream base.

    A child left on a landed base cannot reach the upstream, and is closed outright the
    moment that base branch is deleted.
    """

    branch: str
    """The child branch that has to move."""

    pull_request_number: int
    """The child's fork pull request."""

    current_base: str
    """The landed branch it is still targeting."""

    target_base: str
    """The base it must be retargeted at."""


@dataclass
class Stack:
    """The whole stack: configuration plus the branches derived from GitHub and git."""

    configuration: Configuration
    """The static configuration."""

    branches: list[Branch]
    """The derived stack nodes."""

    is_merged: Callable[[str], bool]
    """Maps any branch name - tracked by this stack or not - to whether it has landed
    upstream."""

    @classmethod
    def build(
        cls,
        configuration: Configuration,
        pull_requests: list[PullRequest],
        is_merged: Callable[[str], bool],
    ) -> Stack:
        """Assemble the stack from the pull request export and a merged-branch predicate.

        :param configuration: The static configuration.
        :param pull_requests: The exported pull requests.
        :param is_merged: Maps a branch name to whether it has landed upstream; injected so
            the pure assembly logic can be tested without git.
        :return: The assembled stack.
        """
        return cls(
            configuration=configuration,
            branches=[
                Branch.from_pull_request(pull_request, configuration, is_merged)
                for pull_request in pull_requests
            ],
            is_merged=is_merged,
        )

    @classmethod
    def load(cls, board: Path) -> Stack:
        """:param board: The exported board to derive the stack from.
        :return: The full live stack: configuration + board export + git merged-detection.
        """
        configuration = Configuration.load()
        pull_requests = PullRequest.read_board(board)
        heads_on_fork = {
            pull_request.head
            for pull_request in pull_requests
            if pull_request.skip_reason(configuration.fork_repository) is None
        }
        bases = {pull_request.base for pull_request in pull_requests}
        CurrentCheckout.fetch(configuration, sorted(heads_on_fork | bases))
        return cls.build(
            configuration,
            pull_requests,
            LandedBranches.from_checkout(configuration).contains,
        )

    def maintained_branches(self) -> list[Branch]:
        """The branches this pass can restack, promote and write to, parent before child.

        :return: Every branch whose head is on the fork.
        """
        return [
            branch for branch in self.ordered() if branch.skipped_pull_request is None
        ]

    def skipped_pull_requests(self) -> list[SkippedPullRequest]:
        """:return: Every pull request the pass leaves alone, parent before child."""
        return [
            branch.skipped_pull_request
            for branch in self.ordered()
            if branch.skipped_pull_request is not None
        ]

    def needs_resolution(self, branch: Branch) -> bool:
        """:param branch: The branch to check.
        :return: Whether the branch is withheld from promotion pending conflict resolution.
        """
        return self.configuration.needs_resolution_label in branch.labels

    def has_landed_upstream(self, branch_name: str) -> bool:
        """Whether a branch's commits are already in the upstream base.

        Answered from git ancestry, so it holds for any branch name - including one no open
        pull request describes, which the board therefore never mentions.

        :param branch_name: The branch to check.
        :return: Whether its commits are in the upstream base.
        """
        return branch_name == self.configuration.upstream_base or self.is_merged(
            branch_name
        )

    def ordered(self) -> list[Branch]:
        """:return: The branches, topologically ordered so a parent always precedes its
        children."""
        by_name = {branch.name: branch for branch in self.branches}
        ordered: list[Branch] = []
        seen: set[str] = set()

        def visit(branch: Branch) -> None:
            if branch.name in seen:
                return
            seen.add(branch.name)
            parent = by_name.get(branch.parent)
            if parent is not None:
                visit(parent)
            ordered.append(branch)

        for branch in self.branches:
            visit(branch)
        return ordered

    def branches_stacked_on(self, branch_name: str) -> list[Branch]:
        """Every not-yet-merged branch whose parent chain reaches *branch_name*.

        A branch is stacked on another when its pull request's base is that branch,
        directly or through the branches between them - the same parent relation
        :meth:`restack_plan` works from. A merged branch is left out along with everything
        only it carries, since its commits are already in the upstream base.

        :param branch_name: The branch whose subtree is wanted; it is never in the result.
        :return: The branches stacked on it, parent before child.
        """
        stacked: set[str] = set()
        found = []
        for branch in self.maintained_branches():
            if branch.status == BranchStatus.MERGED:
                continue
            if branch.parent != branch_name and branch.parent not in stacked:
                continue
            stacked.add(branch.name)
            found.append(branch)
        return found

    def parent_landed(self, branch: Branch) -> bool:
        """Whether a branch's parent has reached the upstream (merged or in-review), so it
        can promote.

        A parent no open fork pull request describes is not on the board, so git ancestry
        is the only evidence available for it - and absence from the board is not itself
        evidence of a root branch.

        :param branch: The branch to check.
        :return: Whether the branch's parent has landed.
        """
        parent = next(
            (
                candidate
                for candidate in self.branches
                if candidate.name == branch.parent
            ),
            None,
        )
        if parent is None:
            return self.has_landed_upstream(branch.parent)
        return parent.status in {
            BranchStatus.IN_REVIEW,
            BranchStatus.MERGED,
        }

    def integration_parent(self, branch: Branch) -> str:
        """
        :param branch: A branch of the stack.
        :return: What it is brought up to date with: its parent, or the upstream base
            once that parent has landed.
        """
        if self.has_landed_upstream(branch.parent):
            return self.configuration.upstream_base
        return branch.parent

    def promotion_order(self) -> list[Branch]:
        """The branches to submit to the upstream next, in dependency order.

        A branch is a candidate when it is approved (``ready``), its parent has landed, it
        has not been withheld pending conflict resolution, and its checks have not failed.
        Every such branch promotes together - there is no admission cap or per-stack slot
        limit.

        :return: The promotable branches, in dependency order.
        """
        return [
            branch
            for branch in self.maintained_branches()
            if branch.status == BranchStatus.READY
            and self.parent_landed(branch)
            and not self.needs_resolution(branch)
            and not branch.checks_failed
        ]

    def withheld_by_failing_checks(self) -> list[Branch]:
        """
        :return: Every approved branch whose parent has landed but whose checks failed,
            in dependency order.
        """
        return [
            branch
            for branch in self.maintained_branches()
            if branch.status == BranchStatus.READY
            and self.parent_landed(branch)
            and branch.checks_failed
        ]

    def awaiting_promotion_link(self) -> list[Branch]:
        """
        :return: The branches of :meth:`promotion_order` whose upstream link no pass has
            recorded yet, in dependency order.
        """
        return [
            branch
            for branch in self.promotion_order()
            if GitHubLabel.PROMOTION_LINK_SENT not in branch.labels
        ]

    def restack_plan(self, stacked_on: str | None = None) -> list[RestackPlanEntry]:
        """The bottom-up restack plan the ``restack`` workflow consumes as its arguments.

        One entry per branch not yet ``merged``, in parent-before-child order. In-review
        branches are included so they pick up a moved parent; their ``merge`` strategy never
        rewrites their own commits, only replaces the merges earlier restacks left, so an
        open review keeps its history.

        When a branch's parent has **merged** into the upstream, its commits are already in
        the base, so the child is reparented onto the upstream base: the restack rebases it
        there and it stops depending on a landed (and about-to-be-closed) branch. The
        maintenance skill mirrors this by retargeting the child pull request's base to the upstream base on
        GitHub. This holds however the parent landed - including when its own pull request
        was closed rather than merged, leaving the board with no entry for it at all.

        Naming a branch in *stacked_on* limits the plan to that branch's own subtree.

        :param stacked_on: The branch whose subtree to plan, or ``None`` for every branch.
        :return: The restack plan, one entry per planned branch.
        """
        planned = (
            self.maintained_branches()
            if stacked_on is None
            else self.branches_stacked_on(stacked_on)
        )
        return [
            RestackPlanEntry(
                branch=branch.name,
                parent=self.integration_parent(branch),
                strategy=branch.strategy,
            )
            for branch in planned
            if branch.status != BranchStatus.MERGED
        ]

    def reparents(self) -> list[Reparent]:
        """Every open pull request whose base has already landed upstream.

        Decided by git ancestry rather than by the base's own pull-request state, so it also
        covers a parent whose pull request was closed rather than merged - which leaves the
        board with no entry for it at all.

        :return: The children to retarget, parent before child.
        """
        base = self.configuration.upstream_base
        return [
            Reparent(
                branch=branch.name,
                pull_request_number=branch.pull_request_number,
                current_base=branch.parent,
                target_base=base,
            )
            for branch in self.ordered()
            if branch.status != BranchStatus.MERGED
            and branch.parent != base
            and self.has_landed_upstream(branch.parent)
        ]

    def landed_branches(self) -> list[Branch]:
        """Every open fork pull request whose own branch is already in the upstream base.

        These are the ones to label as landed and close; their children are named
        separately by :meth:`reparents`, which must be acted on first so none is ever
        orphaned.

        :return: The landed branches, parent before child.
        """
        return [
            branch
            for branch in self.maintained_branches()
            if branch.status == BranchStatus.MERGED
        ]
