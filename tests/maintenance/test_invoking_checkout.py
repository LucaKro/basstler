"""
Tests for leaving the checkout the pass was invoked in, and the tooling it runs from, as
they were.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import basstler.maintenance.__main__
from basstler.maintenance.restack import RestackOutcome, restack

from ..constants.paths import RepositoryLocation
from ..constants.stack import RemoteName, StackBranch

from .cascade import CascadeOfPlainRebases
from .fork import (
    UPSTREAM_BASE,
    FixtureFile,
    ForkCheckout,
    a_stack,
    a_parent_and_child,
    the_board,
)
from .recorded_fork import (
    RecordingPullRequests,
    PullRequestsWatchingTheCaller,
    ReportingRefused,
    PullRequestsRefusingToReport,
)


# %% the checkout the pass was invoked in


TOOLING_PATH = str(
    Path(basstler.maintenance.__main__.__file__).relative_to(
        RepositoryLocation.ROOT.value
    )
)
"""
Where the pass's own tooling sits: tracked content, so a branch cut before it landed
does not carry it, and checking that branch out deletes it from the working tree.
"""

TOOLING_CONTENT = "the pass's own tooling\n"
"""
What the stand-in tooling file holds, so its survival can be asserted by value.
"""


def a_stack_cut_before_the_tooling_landed(fork_checkout: ForkCheckout) -> Path:
    """
    Publish the two-branch stack, add the tooling to the base alone, and move the parent
    so the child has something to restack.

    Branching first is what most of a real stack looks like - the branches were cut
    before the tooling landed, so their trees do not carry it - and it leaves the
    checkout on the base, holding a file neither branch has.

    :param fork_checkout: The checkout to build the stack in.
    :return: The tooling file in the working tree.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.run_git("checkout", "--quiet", UPSTREAM_BASE)
    tooling = fork_checkout.project_root / TOOLING_PATH
    tooling.parent.mkdir(parents=True)
    fork_checkout.commit(TOOLING_PATH, TOOLING_CONTENT)
    fork_checkout.commit_on(
        StackBranch.PARENT,
        fork_checkout.own_file(StackBranch.PARENT),
        "the parent moved\n",
    )
    fork_checkout.run_git("checkout", "--quiet", UPSTREAM_BASE)
    return tooling


def test_a_restack_leaves_the_invoking_checkout_on_its_own_branch(
    fork_checkout: ForkCheckout,
):
    a_stack_cut_before_the_tooling_landed(fork_checkout)

    restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
    )

    assert fork_checkout.run_git("branch", "--show-current") == UPSTREAM_BASE


def test_a_restack_keeps_what_the_branches_it_switches_to_do_not_have(
    fork_checkout: ForkCheckout,
):
    """
    The tooling every step of the pass shells out to is tracked content in the checkout
    the pass runs in, so a restack that switched branches there would delete it.
    """
    tooling = a_stack_cut_before_the_tooling_landed(fork_checkout)

    restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
    )

    assert tooling.read_text() == TOOLING_CONTENT


def test_a_restack_publishes_a_branch_the_caller_is_sitting_on(
    fork_checkout: ForkCheckout,
):
    """
    git refuses to check one branch out in two worktrees at once, and the caller of a
    pass is normally sitting on a branch of the stack - so the restack has to be lent it
    rather than blocked by it.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT,
        fork_checkout.own_file(StackBranch.PARENT),
        "the parent moved\n",
    )
    fork_checkout.run_git("checkout", "--quiet", StackBranch.CHILD)
    before = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD)

    outcomes = restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
    )

    child = next(outcome for outcome in outcomes if outcome.branch == StackBranch.CHILD)
    assert child.outcome == RestackOutcome.PUSHED
    assert (
        fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD) != before
    )


def test_the_caller_holds_no_branch_while_a_restack_runs(fork_checkout: ForkCheckout):
    """
    Lending the branch is what makes the restack possible at all, so the caller has to
    be off it for as long as the restack has it - not merely put back afterwards.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT, FixtureFile.CONTESTED, "the parent's version\n"
    )
    fork_checkout.commit_on(
        StackBranch.CHILD, FixtureFile.CONTESTED, "the child's version\n"
    )
    fork = PullRequestsWatchingTheCaller(caller=fork_checkout.git)

    restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        fork,
        CascadeOfPlainRebases,
    )

    assert fork.branches_held_while_restacking == [""]


def test_a_restack_gives_back_the_branch_the_caller_lent_it(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT,
        fork_checkout.own_file(StackBranch.PARENT),
        "the parent moved\n",
    )
    fork_checkout.run_git("checkout", "--quiet", StackBranch.CHILD)

    restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
    )

    assert fork_checkout.run_git("branch", "--show-current") == StackBranch.CHILD


def test_a_restack_leaves_no_worktree_of_its_own_behind(fork_checkout: ForkCheckout):
    a_stack_cut_before_the_tooling_landed(fork_checkout)

    restack(
        a_stack(fork_checkout, the_board()),
        fork_checkout.git,
        RecordingPullRequests(),
        CascadeOfPlainRebases,
    )

    listed = fork_checkout.run_git("worktree", "list", "--porcelain")
    assert [
        line.removeprefix("worktree ")
        for line in listed.splitlines()
        if line.startswith("worktree ")
    ] == [str(fork_checkout.project_root.resolve())]


def test_a_restack_that_raises_still_takes_its_worktree_with_it(
    fork_checkout: ForkCheckout,
):
    """
    A pass abandoned part-way must not leave a worktree behind holding a checked-out
    branch, so the cleanup cannot sit on the success path.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT, FixtureFile.CONTESTED, "the parent's version\n"
    )
    fork_checkout.commit_on(
        StackBranch.CHILD, FixtureFile.CONTESTED, "the child's version\n"
    )

    with pytest.raises(ReportingRefused):
        restack(
            a_stack(fork_checkout, the_board()),
            fork_checkout.git,
            PullRequestsRefusingToReport(),
            CascadeOfPlainRebases,
        )

    listed = fork_checkout.run_git("worktree", "list", "--porcelain")
    assert [
        line.removeprefix("worktree ")
        for line in listed.splitlines()
        if line.startswith("worktree ")
    ] == [str(fork_checkout.project_root.resolve())]
