"""
Tests for fast-forwarding the fork's base and restacking its branches, the whole stack
or one subtree, against a real scratch fork, with plain git rebases standing in for ``gh
stack``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from basstler.exit_codes import StackExitCode
from basstler.stack.cascading_rebase import CascadeStep, CascadeStepOutcome
from basstler.stack.model import PullRequest

from basstler.maintenance.commands import RestackCommand
from basstler.maintenance.fast_forward import (
    FastForwardOutcome,
    fast_forward,
)
from basstler.maintenance.git_commands import GitCommandFailed
from basstler.maintenance.restack import (
    ConflictResponse,
    LeaveItToTheCaller,
    RestackOutcome,
    TellTheBranchOwner,
    restack,
)

from ..constants.stack import RemoteName, StackBranch

from .cascade import CascadeOfPlainRebases
from .fork import (
    PullRequestHeadRepository,
    UPSTREAM_BASE,
    FixtureFile,
    make_configuration,
    ForkCheckout,
    a_stack,
    a_parent_and_child,
    a_board_entry,
    the_board,
)
from .recorded_fork import (
    RecordingPullRequests,
    AlreadyResolvedPass,
)

# %% fast-forward


def test_the_fork_base_is_fast_forwarded_to_the_upstream(fork_checkout: ForkCheckout):
    fork_checkout.run_git("checkout", "--quiet", UPSTREAM_BASE)
    advanced = fork_checkout.commit("another-file", "upstream moved\n")
    fork_checkout.run_git("push", "--quiet", RemoteName.UPSTREAM, UPSTREAM_BASE)
    fork_checkout.run_git(
        "push", "--quiet", "--force", RemoteName.ORIGIN, f"HEAD~1:{UPSTREAM_BASE}"
    )
    fork_checkout.run_git("fetch", "--quiet", RemoteName.ORIGIN)

    report = fast_forward(make_configuration(), fork_checkout.git)

    assert report.outcome == FastForwardOutcome.PUSHED
    assert fork_checkout.published_commit(RemoteName.ORIGIN, UPSTREAM_BASE) == advanced


def test_the_fork_base_is_fast_forwarded_from_an_upstream_with_no_remote(
    fork_checkout: ForkCheckout,
):
    """
    A cloud session may not add the upstream as a remote, so a pass reaches it by URL.
    """
    fork_checkout.run_git("checkout", "--quiet", UPSTREAM_BASE)
    advanced = fork_checkout.commit("another-file", "upstream moved\n")
    fork_checkout.run_git("push", "--quiet", RemoteName.UPSTREAM, UPSTREAM_BASE)
    fork_checkout.run_git(
        "push", "--quiet", "--force", RemoteName.ORIGIN, f"HEAD~1:{UPSTREAM_BASE}"
    )
    fork_checkout.run_git("fetch", "--quiet", RemoteName.ORIGIN)
    fork_checkout.run_git("remote", "remove", RemoteName.UPSTREAM)
    configuration = replace(
        make_configuration(), upstream_location=fork_checkout.upstream_path.as_uri()
    )

    report = fast_forward(configuration, fork_checkout.git)

    assert report.outcome == FastForwardOutcome.PUSHED
    assert fork_checkout.published_commit(RemoteName.ORIGIN, UPSTREAM_BASE) == advanced


def test_a_fork_base_already_level_with_the_upstream_is_left_alone(
    fork_checkout: ForkCheckout,
):
    report = fast_forward(make_configuration(), fork_checkout.git)

    assert report.outcome == FastForwardOutcome.ALREADY_CURRENT


def test_a_non_fast_forward_is_refused_and_the_fork_base_is_untouched(
    fork_checkout: ForkCheckout,
):
    """
    The doctrine says stop rather than force; this makes it unable to force, and the
    assertion is on the destination ref rather than on the command having failed.
    """
    fork_checkout.run_git("checkout", "--quiet", UPSTREAM_BASE)
    fork_checkout.commit("a-fork-only-file", "only on the fork\n")
    fork_checkout.run_git("push", "--quiet", RemoteName.ORIGIN, UPSTREAM_BASE)
    fork_checkout.run_git("checkout", "--quiet", "-B", "a-divergent-line", "HEAD~1")
    fork_checkout.commit("an-upstream-only-file", "only upstream\n")
    fork_checkout.run_git(
        "push", "--quiet", "--force", RemoteName.UPSTREAM, f"HEAD:{UPSTREAM_BASE}"
    )
    fork_checkout.run_git("fetch", "--quiet", RemoteName.ORIGIN)
    fork_checkout.run_git("fetch", "--quiet", RemoteName.UPSTREAM)
    before = fork_checkout.published_commit(RemoteName.ORIGIN, UPSTREAM_BASE)

    report = fast_forward(make_configuration(), fork_checkout.git)

    assert report.outcome == FastForwardOutcome.REFUSED_NOT_FAST_FORWARD
    assert fork_checkout.published_commit(RemoteName.ORIGIN, UPSTREAM_BASE) == before


def test_a_refused_push_of_the_fork_base_stops_the_pass(fork_checkout: ForkCheckout):
    fork_checkout.run_git("checkout", "--quiet", UPSTREAM_BASE)
    fork_checkout.commit("another-file", "upstream moved\n")
    fork_checkout.run_git("push", "--quiet", RemoteName.UPSTREAM, UPSTREAM_BASE)
    fork_checkout.run_git(
        "config",
        f"remote.{RemoteName.ORIGIN}.pushurl",
        str(fork_checkout.fork_path.parent / "nowhere.git"),
    )

    with pytest.raises(GitCommandFailed):
        fast_forward(make_configuration(), fork_checkout.git)


# %% restack


@dataclass(frozen=True)
class CascadeThatCannotRebase(CascadeOfPlainRebases):
    """
    A cascade whose every rebase fails without conflicting on anything, as one does
    whose tooling is missing or whose checkout is in a state it refuses.
    """

    detail: str = "the cascade could not run"
    """
    What the failing rebase says.
    """

    def rebase_up_to(self, branch: str) -> CascadeStep:
        return CascadeStep(CascadeStepOutcome.FAILED, self.detail)

    def abort_rebase(self) -> CascadeStep:
        return CascadeStep(CascadeStepOutcome.COMPLETED)


@dataclass(frozen=True)
class CascadeRacedBySomebodyElse(CascadeOfPlainRebases):
    """
    A cascade whose push loses a race: somebody else publishes the top layer between its
    rebase and its push, from elsewhere, so this checkout never sees it.
    """

    fork_checkout: ForkCheckout | None = None
    """
    The checkout the other push is made from.
    """

    racing_commits: list[str] | None = None
    """
    Where the commit the other push published is recorded, for the test to read back.
    """

    def publish(self) -> CascadeStep:
        checkout = self.fork_checkout
        checkout.run_git(
            "checkout", "--quiet", "--detach", f"origin/{StackBranch.CHILD}"
        )
        self.racing_commits.append(
            checkout.commit(FixtureFile.PUSHED_BY_SOMEBODY_ELSE, "not ours\n")
        )
        checkout.run_git(
            "push", "--quiet", checkout.fork_path.as_uri(), f"HEAD:{StackBranch.CHILD}"
        )
        return super().publish()


def the_parent_moved(fork_checkout: ForkCheckout) -> str:
    """
    Publish a new commit on the parent, so everything stacked on it is behind.

    :param fork_checkout: The checkout to commit in.
    :return: The parent's new published commit.
    """
    return fork_checkout.commit_on(
        StackBranch.PARENT,
        fork_checkout.own_file(StackBranch.PARENT),
        "the parent moved\n",
    )


def outcome_of(outcomes: Sequence, branch: str):
    """
    :param outcomes: What a restack returned.
    :param branch: The branch whose outcome is wanted.
    :return: That branch's outcome.
    """
    return next(outcome for outcome in outcomes if outcome.branch == branch)


def test_a_branch_whose_parent_has_not_moved_is_reported_up_to_date(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)

    outcomes = restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
    )

    assert [outcome.outcome for outcome in outcomes] == [
        RestackOutcome.UP_TO_DATE,
        RestackOutcome.UP_TO_DATE,
    ]


def test_a_branch_whose_parent_moved_is_rebased_onto_it_and_pushed(
    fork_checkout: ForkCheckout,
):
    """
    A stack keeps a line of history: the child is replayed onto the parent's new tip
    rather than merged with it.
    """
    a_parent_and_child(fork_checkout)
    moved = the_parent_moved(fork_checkout)
    before = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD)

    outcomes = restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
    )

    child = outcome_of(outcomes, StackBranch.CHILD)
    after = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD)
    assert child.outcome == RestackOutcome.PUSHED
    assert child.pushed_commit == after != before
    assert fork_checkout.git.contains(moved, after)
    merges = fork_checkout.run_git(
        "rev-list", "--merges", "--count", f"{UPSTREAM_BASE}..{after}"
    )
    assert merges == "0"


def test_the_cascade_is_handed_each_line_on_its_trunk(fork_checkout: ForkCheckout):
    a_parent_and_child(fork_checkout)
    cascades: list[CascadeOfPlainRebases] = []

    def recorded_cascade(working_directory: Path) -> CascadeOfPlainRebases:
        cascades.append(CascadeOfPlainRebases(working_directory))
        return cascades[-1]

    restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        recorded_cascade,
    )

    assert cascades[0].adopted == [
        (UPSTREAM_BASE, (StackBranch.PARENT, StackBranch.CHILD))
    ]


def test_a_second_child_of_one_parent_is_restacked_as_a_line_of_its_own(
    fork_checkout: ForkCheckout,
):
    """
    A GitHub stack is a single line, so a parent carrying two children leaves one of
    them to a line based on the parent's branch - which still takes the parent's move.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.branch_from(StackBranch.SIBLING, StackBranch.PARENT)
    moved = the_parent_moved(fork_checkout)
    board = the_board() + [a_board_entry(42, StackBranch.SIBLING, StackBranch.PARENT)]

    outcomes = restack(
        a_stack(fork_checkout, board),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
    )

    sibling = outcome_of(outcomes, StackBranch.SIBLING)
    assert sibling.parent == StackBranch.PARENT
    assert sibling.outcome == RestackOutcome.PUSHED
    assert fork_checkout.git.contains(
        moved, f"{RemoteName.ORIGIN}/{StackBranch.SIBLING}"
    )


def test_a_conflicting_rebase_pushes_nothing_and_names_the_files(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT, FixtureFile.CONTESTED, "the parent's version\n"
    )
    fork_checkout.commit_on(
        StackBranch.CHILD, FixtureFile.CONTESTED, "the child's version\n"
    )
    before = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD)

    outcomes = restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
    )

    child = outcome_of(outcomes, StackBranch.CHILD)
    assert child.outcome == RestackOutcome.CONFLICT
    assert child.conflicting_paths == (FixtureFile.CONTESTED,)
    assert (
        fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD) == before
    )


def test_a_line_based_on_a_conflicted_branch_is_reported_blocked(
    fork_checkout: ForkCheckout,
):
    """
    The sibling's line is based on the child's branch, which did not take the parent's
    move, so rebasing onto it would build on what the pass meant to replace.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.branch_from(StackBranch.GRANDCHILD, StackBranch.CHILD)
    fork_checkout.branch_from(StackBranch.SIBLING, StackBranch.CHILD)
    fork_checkout.commit_on(
        StackBranch.PARENT, FixtureFile.CONTESTED, "the parent's version\n"
    )
    fork_checkout.commit_on(
        StackBranch.CHILD, FixtureFile.CONTESTED, "the child's version\n"
    )
    board = the_board() + [
        a_board_entry(42, StackBranch.GRANDCHILD, StackBranch.CHILD),
        a_board_entry(43, StackBranch.SIBLING, StackBranch.CHILD),
    ]

    outcomes = restack(
        a_stack(fork_checkout, board),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
    )

    sibling = outcome_of(outcomes, StackBranch.SIBLING)
    assert sibling.outcome == RestackOutcome.BLOCKED_BY_ANCESTOR
    assert sibling.blocking_ancestor == StackBranch.CHILD


def test_a_rebase_that_fails_without_a_conflict_is_not_reported_as_one(
    fork_checkout: ForkCheckout,
):
    """
    A cascade fails for reasons that leave nothing conflicted at all.

    Reading any failure as a conflict labels a branch that rebases perfectly well and
    sends its owner a report naming no files.
    """
    a_parent_and_child(fork_checkout)
    the_parent_moved(fork_checkout)
    before = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD)
    fork = RecordingPullRequests()

    outcomes = restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        fork,
        CascadeThatCannotRebase,
    )

    child = outcome_of(outcomes, StackBranch.CHILD)
    assert child.outcome == RestackOutcome.INTEGRATION_FAILED
    assert child.explanation == CascadeThatCannotRebase.detail
    assert fork.label_writes == []
    assert fork.comments == []
    assert (
        fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD) == before
    )


def test_a_branch_that_moved_under_the_pass_is_rebased_rather_than_overwritten(
    fork_checkout: ForkCheckout,
):
    """
    The rebase starts from the branch's published tip, not from whatever this checkout
    last saw, so work pushed by somebody else survives the restack.
    """
    a_parent_and_child(fork_checkout)
    the_parent_moved(fork_checkout)
    fork_checkout.run_git(
        "checkout", "--quiet", "-B", "a-side-line", f"origin/{StackBranch.CHILD}"
    )
    fork_checkout.commit(FixtureFile.PUSHED_BY_SOMEBODY_ELSE, "not ours\n")
    fork_checkout.run_git(
        "push", "--quiet", RemoteName.ORIGIN, f"a-side-line:{StackBranch.CHILD}"
    )
    fork_checkout.run_git("fetch", "--quiet", RemoteName.ORIGIN)

    outcomes = restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
    )

    assert outcome_of(outcomes, StackBranch.CHILD).outcome == RestackOutcome.PUSHED
    assert (
        fork_checkout.published_file(
            RemoteName.ORIGIN, StackBranch.CHILD, FixtureFile.PUSHED_BY_SOMEBODY_ELSE
        )
        == "not ours\n"
    )


def test_a_push_somebody_else_raced_is_rejected_rather_than_forced_through(
    fork_checkout: ForkCheckout,
):
    """
    The lease is what stops a rebase overwriting a push this pass never saw.
    """
    a_parent_and_child(fork_checkout)
    the_parent_moved(fork_checkout)
    racing_commits: list[str] = []

    def raced_cascade(working_directory: Path) -> CascadeRacedBySomebodyElse:
        return CascadeRacedBySomebodyElse(
            working_directory,
            fork_checkout=fork_checkout,
            racing_commits=racing_commits,
        )

    outcomes = restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        raced_cascade,
    )

    assert outcome_of(outcomes, StackBranch.CHILD).outcome == (
        RestackOutcome.PUSH_REJECTED
    )
    assert fork_checkout.commit_on_the_fork(StackBranch.CHILD) == racing_commits[0]


# %% restacking one subtree, for a fix just made below it


def a_stack_three_deep(fork_checkout: ForkCheckout) -> None:
    """
    Publish the parent on the base, the child on it and the grandchild above that,
    beside the branch cut straight from the base.

    :param fork_checkout: The checkout to build the branches in.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.branch_from(StackBranch.GRANDCHILD, StackBranch.CHILD)
    fork_checkout.branch_from(StackBranch.OFF_THE_CHAIN, UPSTREAM_BASE)


def the_deeper_board() -> list[PullRequest]:
    """
    :return: The four-branch board matching :func:`a_stack_three_deep`.
    """
    return the_board() + [
        PullRequest(
            number=42,
            head=StackBranch.GRANDCHILD,
            base=StackBranch.CHILD,
            draft=False,
            head_repository=PullRequestHeadRepository.FORK.value.full_name,
        ),
        PullRequest(
            number=43,
            head=StackBranch.OFF_THE_CHAIN,
            base=UPSTREAM_BASE,
            draft=False,
            head_repository=PullRequestHeadRepository.FORK.value.full_name,
        ),
    ]


def test_naming_a_branch_restacks_everything_stacked_on_it(
    fork_checkout: ForkCheckout,
):
    a_stack_three_deep(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT, fork_checkout.own_file(StackBranch.PARENT), "the fix\n"
    )

    outcomes = restack(
        a_stack(fork_checkout, the_deeper_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
        stacked_on=StackBranch.PARENT,
    )

    assert [outcome.branch for outcome in outcomes] == [
        StackBranch.CHILD,
        StackBranch.GRANDCHILD,
    ]
    assert (
        fork_checkout.published_file(
            "origin", StackBranch.GRANDCHILD, fork_checkout.own_file(StackBranch.PARENT)
        )
        == "the fix\n"
    )


def test_naming_a_branch_leaves_the_rest_of_the_board_where_it_was(
    fork_checkout: ForkCheckout,
):
    a_stack_three_deep(fork_checkout)
    fork_checkout.commit_on(UPSTREAM_BASE, FixtureFile.ON_THE_BASE, "the base moved\n")
    before = fork_checkout.published_commit("origin", StackBranch.OFF_THE_CHAIN)

    restack(
        a_stack(fork_checkout, the_deeper_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
        stacked_on=StackBranch.PARENT,
    )

    assert fork_checkout.published_commit("origin", StackBranch.OFF_THE_CHAIN) == before


def test_a_collision_with_the_fix_is_handed_back_rather_than_reported_to_its_owner(
    fork_checkout: ForkCheckout,
):
    """
    Whoever named the subtree is present and makes the parent-to-descendant judgement
    themselves, so the descendant is left unlabelled and its owner is not asked to
    resolve what the caller is about to.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(StackBranch.PARENT, FixtureFile.CONTESTED, "the fix\n")
    fork_checkout.commit_on(
        StackBranch.CHILD, FixtureFile.CONTESTED, "the child's version\n"
    )
    fork = RecordingPullRequests()

    outcomes = restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        fork,
        CascadeOfPlainRebases,
        stacked_on=StackBranch.PARENT,
    )

    collision = outcomes[0]
    assert collision.branch == StackBranch.CHILD
    assert collision.parent == StackBranch.PARENT
    assert collision.outcome == RestackOutcome.CONFLICT
    assert collision.conflicting_paths == (FixtureFile.CONTESTED,)
    assert collision.reported_at is None
    assert fork.label_writes == []
    assert fork.comments == []


def test_a_branch_above_a_collision_is_reported_blocked_rather_than_up_to_date(
    fork_checkout: ForkCheckout,
):
    """
    The grandchild still holds the child's old tip, but not the parent's fix the child
    could not take, so calling it up to date would hide that it lacks the fix.
    """
    a_stack_three_deep(fork_checkout)
    fork_checkout.commit_on(StackBranch.PARENT, FixtureFile.CONTESTED, "the fix\n")
    fork_checkout.commit_on(
        StackBranch.CHILD, FixtureFile.CONTESTED, "the child's version\n"
    )
    fork_checkout.run_git("checkout", "--quiet", StackBranch.GRANDCHILD)
    fork_checkout.run_git("rebase", "--quiet", StackBranch.CHILD)
    fork_checkout.run_git(
        "push", "--quiet", "--force", "origin", StackBranch.GRANDCHILD
    )
    before = fork_checkout.published_commit("origin", StackBranch.GRANDCHILD)

    outcomes = restack(
        a_stack(fork_checkout, the_deeper_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
        stacked_on=StackBranch.PARENT,
    )

    grandchild = outcomes[1]
    assert grandchild.branch == StackBranch.GRANDCHILD
    assert grandchild.outcome == RestackOutcome.BLOCKED_BY_ANCESTOR
    assert grandchild.blocking_ancestor == StackBranch.CHILD
    assert fork_checkout.published_commit("origin", StackBranch.GRANDCHILD) == before


def test_naming_a_subtree_leaves_its_collisions_to_the_caller():
    assert type(ConflictResponse.chosen_for(StackBranch.PARENT)) is LeaveItToTheCaller


def test_a_whole_board_pass_tells_the_branch_owner_of_its_collisions():
    assert type(ConflictResponse.chosen_for(None)) is TellTheBranchOwner


def test_the_restack_command_passes_the_subtree_it_was_given(
    fork_checkout: ForkCheckout,
):
    """
    The flag is what a session carrying a fix down its own stack invokes, so what it
    names has to reach the pass rather than only the parser.
    """
    a_stack_three_deep(fork_checkout)
    fork_checkout.commit_on(UPSTREAM_BASE, FixtureFile.ON_THE_BASE, "the base moved\n")
    before = fork_checkout.published_commit("origin", StackBranch.OFF_THE_CHAIN)

    status = RestackCommand().run(
        AlreadyResolvedPass.over(fork_checkout, the_deeper_board()),
        argparse.Namespace(stacked_on=StackBranch.PARENT),
    )

    assert status == StackExitCode.SUCCESS
    assert fork_checkout.published_commit("origin", StackBranch.OFF_THE_CHAIN) == before
