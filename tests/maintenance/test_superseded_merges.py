"""
Tests for keeping one merge of its parent per branch, however many passes the branch
waits through.
"""

from __future__ import annotations

from basstler_domain_model.git.commands import (
    IntegrationStrategy,
)
from basstler.stack.model import (
    PullRequest,
)

from basstler.maintenance.restack_procedure import restack
from basstler.maintenance.restack_steps import (
    BranchOutcome,
    RestackOutcome,
)

from ..constants.stack import RemoteName, StackBranch
from .fork import (
    UPSTREAM_BASE,
    StackPullRequestNumber,
    make_configuration,
    ForkCheckout,
    a_board_entry,
    a_stack,
    a_parent_and_child,
    the_board,
)
from .recorded_fork import RecordingPullRequests


# %% one merge per branch, however many passes it waits through


def restack_once(
    fork_checkout: ForkCheckout, board: list[PullRequest] | None = None
) -> dict[str, BranchOutcome]:
    """
    Run one restack over a freshly derived stack, as each pass does.

    :param fork_checkout: The checkout to restack.
    :param board: The board entries, the parent-and-child board when not given.
    :return: Every branch's outcome, by branch name.
    """
    outcomes = restack(
        a_stack(fork_checkout, board or the_board()),
        fork_checkout.git,
        RecordingPullRequests(),
    )
    return {outcome.branch: outcome for outcome in outcomes}


def merges_between(fork_checkout: ForkCheckout, parent: str, branch: str) -> int:
    """
    :param fork_checkout: The checkout to read the fork's branches from.
    :param parent: The branch the merges are counted against.
    :param branch: The branch whose own merges are counted.
    :return: How many merge commits the fork's *branch* holds that *parent* does not.
    """
    return int(
        fork_checkout.run_git(
            "rev-list", "--merges", "--count", f"origin/{parent}..origin/{branch}"
        )
    )


def test_a_second_restack_replaces_the_merge_the_first_one_left(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    child_s_own = fork_checkout.published_commit("origin", "a-child")
    fork_checkout.commit_on("a-parent", "a-parent-file", "the parent moved\n")
    restack_once(fork_checkout)
    first_merge = fork_checkout.published_commit("origin", "a-child")
    parent_tip = fork_checkout.commit_on("a-parent", "a-parent-file", "moved again\n")

    child = restack_once(fork_checkout)["a-child"]

    assert child.outcome == RestackOutcome.PUSHED
    assert merges_between(fork_checkout, "a-parent", "a-child") == 1
    assert not fork_checkout.git.contains(first_merge, "origin/a-child")
    assert fork_checkout.git.contains(child_s_own, "origin/a-child")
    assert fork_checkout.git.contains(parent_tip, "origin/a-child")


def test_a_child_replaces_its_merge_of_a_parent_whose_own_merge_was_replaced(
    fork_checkout: ForkCheckout,
):
    """
    The child's earlier merge brought in a parent tip that the parent's own restack has
    since replaced, so it is no longer contained in the parent at all - yet everything it
    brought in still is.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(UPSTREAM_BASE, "a-base-file", "the base moved\n")
    restack_once(fork_checkout)
    fork_checkout.commit_on(UPSTREAM_BASE, "a-base-file", "the base moved again\n")

    outcomes = restack_once(fork_checkout)

    assert outcomes["a-parent"].outcome == RestackOutcome.PUSHED
    assert outcomes["a-child"].outcome == RestackOutcome.PUSHED
    assert merges_between(fork_checkout, UPSTREAM_BASE, "a-parent") == 1
    assert merges_between(fork_checkout, "a-parent", "a-child") == 1


def test_a_merge_carrying_a_change_of_its_own_is_kept(fork_checkout: ForkCheckout):
    """
    A merge whose content differs from what merging its parents produces holds somebody's
    own work - a conflict resolution, an edit made while merging - which no later merge
    would reproduce.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on("a-parent", "a-parent-file", "the parent moved\n")
    fork_checkout.run_git("checkout", "--quiet", "a-child")
    fork_checkout.run_git("merge", "--quiet", "--no-commit", "a-parent")
    (fork_checkout.project_root / "a-child-file").write_text("edited while merging\n")
    fork_checkout.run_git("commit", "--quiet", "--all", "--no-edit")
    fork_checkout.run_git("push", "--quiet", "origin", "a-child:a-child")
    fork_checkout.run_git("fetch", "--quiet", "origin")
    own_merge = fork_checkout.published_commit("origin", "a-child")
    fork_checkout.commit_on("a-parent", "a-parent-file", "moved again\n")

    child = restack_once(fork_checkout)["a-child"]

    assert child.outcome == RestackOutcome.PUSHED
    assert fork_checkout.git.contains(own_merge, "origin/a-child")


def test_a_merge_of_a_branch_the_parent_does_not_hold_is_kept(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    sibling_s = fork_checkout.branch_from("a-sibling", UPSTREAM_BASE)
    fork_checkout.run_git("checkout", "--quiet", "a-child")
    fork_checkout.run_git("merge", "--quiet", "--no-edit", "a-sibling")
    fork_checkout.run_git("push", "--quiet", "origin", "a-child:a-child")
    fork_checkout.run_git("fetch", "--quiet", "origin")
    fork_checkout.commit_on("a-parent", "a-parent-file", "the parent moved\n")

    child = restack_once(fork_checkout)["a-child"]

    assert child.outcome == RestackOutcome.PUSHED
    assert fork_checkout.git.contains(sibling_s, "origin/a-child")


def test_a_merge_with_work_committed_on_top_of_it_is_kept(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on("a-parent", "a-parent-file", "the parent moved\n")
    restack_once(fork_checkout)
    first_merge = fork_checkout.published_commit("origin", "a-child")
    fork_checkout.commit_on("a-child", "a-later-file", "work after the merge\n")
    fork_checkout.commit_on("a-parent", "a-parent-file", "moved again\n")

    child = restack_once(fork_checkout)["a-child"]

    assert child.outcome == RestackOutcome.PUSHED
    assert fork_checkout.git.contains(first_merge, "origin/a-child")


def test_a_replacement_that_conflicts_falls_back_to_merging_onto_the_tip(
    fork_checkout: ForkCheckout,
):
    """
    The branch and its parent made the same change, so the earlier merge was clean; the
    parent then changed that line again. Merging onto the earlier merge sees only.

    the parent's second change, while merging onto the branch's own commit sees both
    sides change one line differently - replacing the merge must not turn a clean
    restack into a reported conflict.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on("a-parent", "a-shared-file", "the same change\n")
    fork_checkout.commit_on("a-child", "a-shared-file", "the same change\n")
    restack_once(fork_checkout)
    first_merge = fork_checkout.published_commit("origin", "a-child")
    fork_checkout.commit_on("a-parent", "a-shared-file", "the parent changed it\n")
    fork = RecordingPullRequests()

    outcomes = restack(a_stack(fork_checkout, the_board()), fork_checkout.git, fork)

    child = next(outcome for outcome in outcomes if outcome.branch == "a-child")
    assert child.outcome == RestackOutcome.PUSHED
    assert fork_checkout.git.contains(first_merge, "origin/a-child")
    assert fork.label_writes == []
    assert fork.comments == []


def test_a_conflicting_replacement_falls_back_to_the_next_shallower_one(
    fork_checkout: ForkCheckout,
):
    """
    Merging onto the branch's own commit conflicts, as in
    :func:`test_a_replacement_that_conflicts_falls_back_to_merging_onto_the_tip`, but
    merging onto the first of the two superseded merges does not.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(StackBranch.PARENT, "a-shared-file", "the same change\n")
    fork_checkout.commit_on(StackBranch.CHILD, "a-shared-file", "the same change\n")
    restack_once(fork_checkout)
    first_merge = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD)
    fork_checkout.commit_on(
        StackBranch.PARENT, "a-shared-file", "the parent changed it\n"
    )
    restack_once(fork_checkout)
    second_merge = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD)
    fork_checkout.commit_on(StackBranch.PARENT, "a-parent-file", "the parent moved\n")

    child = restack_once(fork_checkout)[StackBranch.CHILD]

    assert child.outcome == RestackOutcome.PUSHED
    assert fork_checkout.git.contains(
        first_merge, f"{RemoteName.ORIGIN}/{StackBranch.CHILD}"
    )
    assert not fork_checkout.git.contains(
        second_merge, f"{RemoteName.ORIGIN}/{StackBranch.CHILD}"
    )


def test_a_reparented_branch_replaces_its_merge_of_the_former_parent(
    fork_checkout: ForkCheckout,
):
    """
    The child was cut from the parent, so once the parent's pull request is gone and the
    child is based on the base instead, its merge of the parent brought in nothing that
    the base and the child's own line do not already hold.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(UPSTREAM_BASE, "a-base-file", "the base moved\n")
    restack_once(fork_checkout)
    merge_of_the_former_parent = fork_checkout.published_commit(
        RemoteName.ORIGIN, StackBranch.CHILD
    )
    fork_checkout.commit_on(UPSTREAM_BASE, "a-base-file", "the base moved again\n")
    reparented = [
        a_board_entry(StackPullRequestNumber.CHILD, StackBranch.CHILD, UPSTREAM_BASE)
    ]

    child = restack_once(fork_checkout, reparented)[StackBranch.CHILD]

    assert child.outcome == RestackOutcome.PUSHED
    assert merges_between(fork_checkout, UPSTREAM_BASE, StackBranch.CHILD) == 1
    assert not fork_checkout.git.contains(
        merge_of_the_former_parent, f"{RemoteName.ORIGIN}/{StackBranch.CHILD}"
    )


def test_a_merge_of_the_parent_made_by_hand_is_kept(fork_checkout: ForkCheckout):
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(StackBranch.PARENT, "a-parent-file", "the parent moved\n")
    fork_checkout.run_git("checkout", "--quiet", StackBranch.CHILD)
    fork_checkout.run_git("merge", "--quiet", "--no-edit", StackBranch.PARENT)
    fork_checkout.run_git(
        "push", "--quiet", RemoteName.ORIGIN, f"{StackBranch.CHILD}:{StackBranch.CHILD}"
    )
    fork_checkout.run_git("fetch", "--quiet", RemoteName.ORIGIN)
    hand_made_merge = fork_checkout.published_commit(
        RemoteName.ORIGIN, StackBranch.CHILD
    )
    fork_checkout.commit_on(StackBranch.PARENT, "a-parent-file", "moved again\n")

    child = restack_once(fork_checkout)[StackBranch.CHILD]

    assert child.outcome == RestackOutcome.PUSHED
    assert fork_checkout.git.contains(
        hand_made_merge, f"{RemoteName.ORIGIN}/{StackBranch.CHILD}"
    )


def test_a_hand_made_merge_reusing_a_restack_message_is_kept(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(StackBranch.PARENT, "a-parent-file", "the parent moved\n")
    restack_once(fork_checkout)
    restack_message = fork_checkout.run_git(
        "log",
        "--max-count=1",
        "--format=%B",
        f"{RemoteName.ORIGIN}/{StackBranch.CHILD}",
    )
    fork_checkout.commit_on(StackBranch.PARENT, "a-parent-file", "moved again\n")
    fork_checkout.run_git(
        "checkout",
        "--quiet",
        "-B",
        StackBranch.CHILD,
        f"{RemoteName.ORIGIN}/{StackBranch.CHILD}",
    )
    fork_checkout.run_git(
        "merge", "--quiet", "--message", restack_message, StackBranch.PARENT
    )
    fork_checkout.run_git(
        "push", "--quiet", RemoteName.ORIGIN, f"{StackBranch.CHILD}:{StackBranch.CHILD}"
    )
    fork_checkout.run_git("fetch", "--quiet", RemoteName.ORIGIN)
    hand_made_merge = fork_checkout.published_commit(
        RemoteName.ORIGIN, StackBranch.CHILD
    )
    fork_checkout.commit_on(StackBranch.PARENT, "a-parent-file", "and again\n")

    child = restack_once(fork_checkout)[StackBranch.CHILD]

    assert child.outcome == RestackOutcome.PUSHED
    assert fork_checkout.git.contains(
        hand_made_merge, f"{RemoteName.ORIGIN}/{StackBranch.CHILD}"
    )


def test_replacing_a_merge_never_overwrites_a_push_the_pass_did_not_see(
    fork_checkout: ForkCheckout,
):
    """
    Staleness is arranged by winding the remote-tracking ref back to the merge, which is
    the state a concurrent push leaves this checkout in.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on("a-parent", "a-parent-file", "the parent moved\n")
    restack_once(fork_checkout)
    stale = fork_checkout.published_commit("origin", "a-child")
    fork_checkout.run_git("checkout", "--quiet", "-B", "a-side-line", stale)
    fork_checkout.commit_on("a-parent", "a-parent-file", "moved again\n")
    fork_checkout.run_git("checkout", "--quiet", "a-side-line")
    somebody_else_s = fork_checkout.commit("a-file-somebody-else-pushed", "not ours\n")
    fork_checkout.run_git("push", "--quiet", "origin", "a-side-line:a-child")
    fork_checkout.run_git("update-ref", "refs/remotes/origin/a-child", stale)

    child = restack_once(fork_checkout)["a-child"]

    assert child.outcome == RestackOutcome.PUSH_REJECTED
    assert fork_checkout.commit_on_the_fork("a-child") == somebody_else_s


def test_replacing_superseded_merges_authorises_rewriting_published_history():
    replacing = make_configuration().propose_push(
        "a-branch",
        IntegrationStrategy.MERGE,
        replaces_superseded_merges=True,
    )

    assert replacing.with_lease
