"""
Tests for reporting a branch the pass could not publish back to its owner, through
labels, comments and its description.
"""

from __future__ import annotations

from basstler_domain_model.github.vocabulary import (
    MergeableState,
)
from basstler.maintenance.restack_procedure import restack
from basstler.maintenance.restack_steps import RestackOutcome

from ..constants.stack import StackBranch, StackLabel

from .fork import (
    StackPullRequestNumber,
    PromotionSampleText,
    FixtureFile,
    A_SESSION_LINK,
    make_configuration,
    ForkCheckout,
    a_stack,
    a_parent_and_child,
    the_board,
)
from .recorded_fork import (
    RecordedLabelWrite,
    RecordingPullRequests,
)


# %% reporting a branch back to its owner


def test_a_conflict_labels_the_branch_and_tells_its_owner(
    fork_checkout: ForkCheckout,
):
    """
    A conflict this pass cannot resolve is somebody else's to fix, and a report that
    only reaches a run summary reaches nobody.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT, FixtureFile.CONTESTED, "the parent's version\n"
    )
    fork_checkout.commit_on(
        StackBranch.CHILD, FixtureFile.CONTESTED, "the child's version\n"
    )
    board = the_board()
    board[1].session = A_SESSION_LINK
    fork = RecordingPullRequests()

    outcomes = restack(a_stack(fork_checkout, board), fork_checkout.git, fork)

    child = next(outcome for outcome in outcomes if outcome.branch == StackBranch.CHILD)
    assert child.outcome == RestackOutcome.CONFLICT
    assert fork.label_writes == [
        RecordedLabelWrite(
            StackPullRequestNumber.CHILD, (make_configuration().needs_resolution_label,)
        )
    ]
    comment = fork.comments[0]
    assert comment.pull_request_number == StackPullRequestNumber.CHILD
    assert FixtureFile.CONTESTED in comment.body
    assert A_SESSION_LINK in comment.body
    assert child.reported_at == f"{PromotionSampleText.COMMENT_LINK_PREFIX}1"


def test_a_label_write_keeps_every_label_the_branch_already_carried(
    fork_checkout: ForkCheckout,
):
    """
    The write replaces the whole set, so a label this pass knows nothing about has to be
    sent back with it.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT, FixtureFile.CONTESTED, "the parent's version\n"
    )
    fork_checkout.commit_on(
        StackBranch.CHILD, FixtureFile.CONTESTED, "the child's version\n"
    )
    fork = RecordingPullRequests()

    restack(
        a_stack(fork_checkout, the_board(labels=[StackLabel.SOMEBODY_ELSES])),
        fork_checkout.git,
        fork,
    )

    assert fork.label_writes == [
        RecordedLabelWrite(
            StackPullRequestNumber.CHILD,
            (
                StackLabel.SOMEBODY_ELSES,
                make_configuration().needs_resolution_label,
            ),
        )
    ]


def test_a_branch_still_conflicting_is_withheld_without_being_relabelled(
    fork_checkout: ForkCheckout,
):
    """
    Re-reporting the same conflict every run is what the label exists to prevent, so a
    branch already carrying it is left entirely alone while it is still dirty.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT,
        fork_checkout.own_file(StackBranch.PARENT),
        "the parent moved\n",
    )
    fork = RecordingPullRequests(
        states={StackPullRequestNumber.CHILD: MergeableState.DIRTY}
    )

    outcomes = restack(
        a_stack(
            fork_checkout,
            the_board(labels=[make_configuration().needs_resolution_label]),
        ),
        fork_checkout.git,
        fork,
    )

    child = next(outcome for outcome in outcomes if outcome.branch == StackBranch.CHILD)
    assert child.outcome == RestackOutcome.WITHHELD
    assert fork.label_writes == []
    assert fork.comments == []


def test_a_branch_that_no_longer_conflicts_has_its_label_cleared_and_is_restacked(
    fork_checkout: ForkCheckout,
):
    """
    GitHub reports ``dirty`` only while there are conflicts, so anything else means the
    owner has resolved it and the branch rejoins the pass.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT,
        fork_checkout.own_file(StackBranch.PARENT),
        "the parent moved\n",
    )
    fork = RecordingPullRequests(
        states={StackPullRequestNumber.CHILD: MergeableState.CLEAN}
    )

    outcomes = restack(
        a_stack(
            fork_checkout,
            the_board(labels=[make_configuration().needs_resolution_label]),
        ),
        fork_checkout.git,
        fork,
    )

    child = next(outcome for outcome in outcomes if outcome.branch == StackBranch.CHILD)
    assert child.outcome == RestackOutcome.PUSHED
    assert fork.label_writes == [RecordedLabelWrite(StackPullRequestNumber.CHILD, ())]
