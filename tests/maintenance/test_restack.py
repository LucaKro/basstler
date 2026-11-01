"""
Tests for fast-forwarding the fork's base and restacking its branches, the whole stack
or one subtree, against a real scratch fork.
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import pytest

from basstler.exit_codes import StackExitCode
from basstler.stack.commit_move import RefusalReason
from basstler_domain_model.git.commands import (
    IntegrationStrategy,
)
from basstler.stack.model import (
    PullRequest,
)

from basstler.maintenance.commands import RestackCommand
from basstler.maintenance.fast_forward import (
    FastForwardOutcome,
    fast_forward,
)
from basstler_domain_model.git.exceptions import (
    GitCommandFailed,
)
from basstler.maintenance.restack_procedure import restack
from basstler.maintenance.restack_steps import (
    ConflictResponse,
    LeaveItToTheCaller,
    RestackOutcome,
    TellTheBranchOwner,
)

from ..constants.stack import RemoteName, StackBranch, StackLabel

from .fork import (
    PullRequestHeadRepository,
    UPSTREAM_BASE,
    FixtureFile,
    make_configuration,
    ForkCheckout,
    a_stack,
    a_parent_and_child,
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


def an_unrelated_history_on(fork_checkout: ForkCheckout, branch: str) -> None:
    """
    Republish a branch on a root commit of its own, sharing no history with the stack.

    A merge refuses this outright, so it is a way to stop an integration before it
    begins that depends on nothing but what the fork carries.

    :param fork_checkout: The checkout to republish the branch from.
    :param branch: The branch to republish.
    """
    fork_checkout.run_git("checkout", "--quiet", "--orphan", "an-unrelated-root")
    fork_checkout.run_git("rm", "--quiet", "-rf", ".")
    fork_checkout.commit("an-unrelated-file", "a history of its own\n")
    fork_checkout.run_git("branch", "--force", branch, "HEAD")
    fork_checkout.run_git(
        "push", "--quiet", "--force", RemoteName.ORIGIN, f"{branch}:{branch}"
    )
    fork_checkout.run_git("fetch", "--quiet", RemoteName.ORIGIN)


def test_a_branch_whose_parent_has_not_moved_is_reported_up_to_date(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)

    outcomes = restack(
        a_stack(fork_checkout, the_board()), fork_checkout.git, RecordingPullRequests()
    )

    assert [outcome.outcome for outcome in outcomes] == [
        RestackOutcome.UP_TO_DATE,
        RestackOutcome.UP_TO_DATE,
    ]


def test_a_branch_whose_parent_moved_is_integrated_and_pushed(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT,
        fork_checkout.own_file(StackBranch.PARENT),
        "the parent moved\n",
    )
    before = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD)

    outcomes = restack(
        a_stack(fork_checkout, the_board()), fork_checkout.git, RecordingPullRequests()
    )

    child = next(outcome for outcome in outcomes if outcome.branch == StackBranch.CHILD)
    assert child.outcome == RestackOutcome.PUSHED
    after = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD)
    assert after != before
    assert child.pushed_commit == after


def test_a_conflicting_integration_pushes_nothing_and_names_the_files(
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
        a_stack(fork_checkout, the_board()), fork_checkout.git, RecordingPullRequests()
    )

    child = next(outcome for outcome in outcomes if outcome.branch == StackBranch.CHILD)
    assert child.outcome == RestackOutcome.CONFLICT
    assert child.conflicting_paths == (FixtureFile.CONTESTED,)
    assert (
        fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD) == before
    )


def test_an_integration_stopped_before_it_began_is_not_reported_as_a_conflict(
    fork_checkout: ForkCheckout,
):
    """
    A merge exits non-zero for reasons that leave nothing conflicted at all - here a
    parent whose history shares no root with the branch. Reading any failure as a
    conflict labels a branch that merges perfectly well and sends its owner a report
    naming no files.
    """
    a_parent_and_child(fork_checkout)
    an_unrelated_history_on(fork_checkout, StackBranch.PARENT)
    before = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD)
    fork = RecordingPullRequests()

    outcomes = restack(a_stack(fork_checkout, the_board()), fork_checkout.git, fork)

    child = next(outcome for outcome in outcomes if outcome.branch == StackBranch.CHILD)
    assert child.outcome == RestackOutcome.INTEGRATION_FAILED
    assert child.conflicting_paths == ()
    assert "refusing to merge unrelated histories" in child.explanation
    assert fork.label_writes == []
    assert fork.comments == []
    assert child.reported_at is None
    assert (
        fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD) == before
    )


def test_a_rebase_labelled_branch_is_rebased_rather_than_merged(
    fork_checkout: ForkCheckout,
):
    """
    The strategy is the only thing that authorises a force-push, so it has to come from
    the label rather than from the executor's own judgement.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT,
        fork_checkout.own_file(StackBranch.PARENT),
        "the parent moved\n",
    )

    outcomes = restack(
        a_stack(fork_checkout, the_board(labels=[StackLabel.REBASE])),
        fork_checkout.git,
        RecordingPullRequests(),
    )

    child = next(outcome for outcome in outcomes if outcome.branch == StackBranch.CHILD)
    assert child.strategy == IntegrationStrategy.REBASE
    assert child.outcome == RestackOutcome.PUSHED
    merges = fork_checkout.run_git(
        "rev-list",
        "--merges",
        "--count",
        f"origin/{UPSTREAM_BASE}..origin/{StackBranch.CHILD}",
    )
    assert merges == "0"


def test_only_the_rebase_strategy_authorises_rewriting_published_history():
    """
    Forcing is decided in exactly one place, so that is where it is pinned - a test that
    a push happened cannot tell a fast-forward from an overwrite.
    """
    configuration = make_configuration()

    merging = configuration.propose_push("a-branch", IntegrationStrategy.MERGE)
    rebasing = configuration.propose_push("a-branch", IntegrationStrategy.REBASE)

    assert not merging.with_lease
    assert rebasing.with_lease


def test_a_branch_that_moved_under_the_pass_is_incorporated_rather_than_overwritten(
    fork_checkout: ForkCheckout,
):
    """
    The integration starts from the branch's published tip, not from whatever this
    checkout last saw, so work pushed by somebody else survives the restack.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT,
        fork_checkout.own_file(StackBranch.PARENT),
        "the parent moved\n",
    )
    fork_checkout.run_git(
        "checkout", "--quiet", "-B", "a-side-line", f"origin/{StackBranch.CHILD}"
    )
    somebody_else_s = fork_checkout.commit(
        FixtureFile.PUSHED_BY_SOMEBODY_ELSE, "not ours\n"
    )
    fork_checkout.run_git(
        "push", "--quiet", RemoteName.ORIGIN, f"a-side-line:{StackBranch.CHILD}"
    )
    fork_checkout.run_git("fetch", "--quiet", RemoteName.ORIGIN)

    outcomes = restack(
        a_stack(fork_checkout, the_board()), fork_checkout.git, RecordingPullRequests()
    )

    child = next(outcome for outcome in outcomes if outcome.branch == StackBranch.CHILD)
    assert child.outcome == RestackOutcome.PUSHED
    assert fork_checkout.git.contains(somebody_else_s, f"origin/{StackBranch.CHILD}")


def test_a_rebase_whose_lease_has_expired_is_rejected_rather_than_forced_through(
    fork_checkout: ForkCheckout,
):
    """
    The lease is what stops a rebase overwriting a push this pass never saw.

    Staleness is arranged by winding the remote-tracking ref back, which is the state a
    concurrent push leaves this checkout in.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT,
        fork_checkout.own_file(StackBranch.PARENT),
        "the parent moved\n",
    )
    stale = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.CHILD)
    somebody_else_s = fork_checkout.commit_on(
        StackBranch.CHILD, FixtureFile.PUSHED_BY_SOMEBODY_ELSE, "not ours\n"
    )
    fork_checkout.run_git(
        "update-ref", f"refs/remotes/origin/{StackBranch.CHILD}", stale
    )

    outcomes = restack(
        a_stack(fork_checkout, the_board(labels=[StackLabel.REBASE])),
        fork_checkout.git,
        RecordingPullRequests(),
    )

    child = next(outcome for outcome in outcomes if outcome.branch == StackBranch.CHILD)
    assert child.outcome == RestackOutcome.PUSH_REJECTED
    assert fork_checkout.commit_on_the_fork(StackBranch.CHILD) == somebody_else_s


def test_a_push_the_move_checks_refuse_is_not_made(fork_checkout: ForkCheckout):
    """
    A parent that has swallowed its own child would, once pushed, make the child an
    ancestor of its own base - which GitHub reads as the child having merged and closes
    its pull request. The refusal is asserted as its reason rather than as the sentence
    explaining it.
    """
    a_parent_and_child(fork_checkout)
    fork_checkout.run_git("checkout", "--quiet", StackBranch.PARENT)
    fork_checkout.run_git("merge", "--quiet", "--no-edit", StackBranch.CHILD)
    fork_checkout.run_git(
        "push",
        "--quiet",
        RemoteName.ORIGIN,
        f"{StackBranch.PARENT}:{StackBranch.PARENT}",
    )
    fork_checkout.commit_on(UPSTREAM_BASE, FixtureFile.ON_THE_BASE, "the base moved\n")
    before = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.PARENT)

    outcomes = restack(
        a_stack(fork_checkout, the_board()), fork_checkout.git, RecordingPullRequests()
    )

    parent = next(
        outcome for outcome in outcomes if outcome.branch == StackBranch.PARENT
    )
    assert parent.outcome == RestackOutcome.REFUSED
    assert RefusalReason.FALSE_MERGE in parent.refusals
    assert (
        fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.PARENT) == before
    )


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
    fork_checkout.run_git("merge", "--quiet", "--no-edit", StackBranch.CHILD)
    fork_checkout.run_git("push", "--quiet", "origin", StackBranch.GRANDCHILD)
    before = fork_checkout.published_commit("origin", StackBranch.GRANDCHILD)

    outcomes = restack(
        a_stack(fork_checkout, the_deeper_board()),
        fork_checkout.git,
        RecordingPullRequests(),
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
