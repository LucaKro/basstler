"""
Tests for the branches a stack derived from an in-memory board export names as landed,
as needing a new base, or as skipped because their head is not on the fork.
"""

from __future__ import annotations

import pytest

from basstler_domain_model.git.commands import (
    IntegrationStrategy,
)
from basstler.stack.model import (
    PullRequest,
    Reparent,
    RestackPlanEntry,
    SkippedPullRequest,
    SkipReason,
)

from ..constants.stack import StackBranch

from .dataset import (
    StackRepository,
    build,
    a_deep_stack_beside_an_independent_branch,
)


# %% landed parents that no open pull request describes


def test_restack_plan_reparents_child_of_a_landed_parent_with_no_open_pull_request():
    # the board only carries OPEN pull requests, so a parent whose own PR was closed is
    # absent from it entirely - yet its commits are in the upstream base, so its child
    # must still be reparented onto that base rather than left on the landed branch.
    pull_requests = [
        PullRequest(
            2,
            StackBranch.CHILD,
            StackBranch.LANDED_ELSEWHERE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        )
    ]
    plan = build(pull_requests, merged={StackBranch.LANDED_ELSEWHERE}).restack_plan()
    assert plan == [
        RestackPlanEntry(
            branch=StackBranch.CHILD,
            parent=StackBranch.BASE,
            strategy=IntegrationStrategy.MERGE,
        )
    ]


def test_ready_child_blocked_when_its_only_parent_is_an_unlanded_off_board_branch():
    # an off-board parent is not evidence of a root branch: this one has not landed, so
    # the child is not promotable even though no PR describes the parent.
    pull_requests = [
        PullRequest(
            2,
            StackBranch.CHILD,
            StackBranch.UNLANDED_ELSEWHERE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        )
    ]
    assert build(pull_requests).promotion_order() == []


def test_ready_child_promotable_when_its_off_board_parent_has_landed():
    pull_requests = [
        PullRequest(
            2,
            StackBranch.CHILD,
            StackBranch.LANDED_ELSEWHERE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        )
    ]
    promotable = build(
        pull_requests, merged={StackBranch.LANDED_ELSEWHERE}
    ).promotion_order()
    assert [branch.name for branch in promotable] == [StackBranch.CHILD]


# %% pull requests whose head is not on the fork


def a_pull_request_with_its_head_in(
    head_repository: str | None, base: str = StackBranch.LANDED_ELSEWHERE
) -> PullRequest:
    """
    :param head_repository: Where its head lives, ``None`` once that repository is
        deleted.
    :param base: The fork branch it is based on.
    :return: A ready pull request open on the fork.
    """
    return PullRequest(
        2,
        StackBranch.CHILD,
        base,
        draft=False,
        head_repository=head_repository,
    )


def test_a_branch_whose_head_is_on_the_fork_is_not_skipped():
    stack = build(
        [a_pull_request_with_its_head_in(StackRepository.FORK.value.full_name)]
    )

    assert stack.branches[0].skipped_pull_request is None


@pytest.mark.parametrize(
    "head_repository, reason",
    [
        (
            StackRepository.SOMEBODY_ELSES_FORK.value.full_name,
            SkipReason.HEAD_ON_ANOTHER_FORK,
        ),
        (None, SkipReason.HEAD_REPOSITORY_DELETED),
    ],
)
def test_a_branch_whose_head_is_not_on_the_fork_is_skipped_with_its_reason(
    head_repository: str | None, reason: SkipReason
):
    pull_request = a_pull_request_with_its_head_in(head_repository)

    stack = build([pull_request])

    assert stack.branches[0].skipped_pull_request == SkippedPullRequest(
        pull_request, reason
    )


@pytest.mark.parametrize(
    "head_repository", [StackRepository.SOMEBODY_ELSES_FORK.value.full_name, None]
)
def test_a_head_outside_the_fork_is_neither_restacked_promoted_nor_landed(
    head_repository: str | None,
):
    """
    Its base has landed, so on the fork it would be restacked and promoted; and its own
    head counts as landed, so it would be reported for closing.
    """
    stack = build(
        [a_pull_request_with_its_head_in(head_repository)],
        merged={StackBranch.LANDED_ELSEWHERE, StackBranch.CHILD},
    )

    assert stack.restack_plan() == []
    assert stack.promotion_order() == []
    assert stack.landed_branches() == []


@pytest.mark.parametrize(
    "head_repository", [StackRepository.SOMEBODY_ELSES_FORK.value.full_name, None]
)
def test_a_head_outside_the_fork_is_still_reparented_once_its_base_has_landed(
    head_repository: str | None,
):
    """
    Its base is a fork branch, and retargeting it changes only the base.
    """
    stack = build(
        [a_pull_request_with_its_head_in(head_repository)],
        merged={StackBranch.LANDED_ELSEWHERE},
    )

    assert stack.reparents() == [
        Reparent(
            branch=StackBranch.CHILD,
            pull_request_number=2,
            current_base=StackBranch.LANDED_ELSEWHERE,
            target_base=StackBranch.BASE,
        )
    ]


def test_only_heads_outside_the_fork_are_listed_as_skipped():
    on_the_fork = PullRequest(
        1,
        StackBranch.PARENT,
        StackBranch.BASE,
        draft=False,
        head_repository=StackRepository.FORK.value.full_name,
    )
    elsewhere = a_pull_request_with_its_head_in(
        StackRepository.SOMEBODY_ELSES_FORK.value.full_name, base=StackBranch.PARENT
    )

    skipped = build([on_the_fork, elsewhere]).skipped_pull_requests()

    assert skipped == [SkippedPullRequest(elsewhere, SkipReason.HEAD_ON_ANOTHER_FORK)]


# %% landed parents


def test_a_child_of_a_landed_parent_is_named_with_the_base_it_should_get():
    stack = a_deep_stack_beside_an_independent_branch(landed={StackBranch.ENGINE})

    assert stack.reparents() == [
        Reparent(
            branch=StackBranch.ENGINE_UI,
            pull_request_number=2,
            current_base=StackBranch.ENGINE,
            target_base=StackBranch.BASE,
        )
    ]


def test_a_child_already_based_on_the_upstream_base_needs_no_reparent():
    assert a_deep_stack_beside_an_independent_branch().reparents() == []


def test_a_child_of_a_landed_parent_with_no_open_pull_request_is_still_named():
    """
    The board carries only open pull requests, so a parent whose own was closed is absent
    from it entirely - the case that orphaned a real pull request for six days.
    """
    orphaned = build(
        [
            PullRequest(
                2,
                StackBranch.ENGINE_UI,
                StackBranch.ENGINE,
                draft=True,
                head_repository=StackRepository.FORK.value.full_name,
            )
        ],
        merged={StackBranch.ENGINE},
    )

    assert [reparent.branch for reparent in orphaned.reparents()] == [
        StackBranch.ENGINE_UI
    ]


def test_a_branch_that_has_landed_is_named_for_labelling_and_closing():
    stack = a_deep_stack_beside_an_independent_branch(landed={StackBranch.ENGINE})

    assert [branch.name for branch in stack.landed_branches()] == [StackBranch.ENGINE]


def test_nothing_has_landed_while_the_whole_stack_is_still_open():
    assert a_deep_stack_beside_an_independent_branch().landed_branches() == []
