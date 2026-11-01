"""
Tests for the stack derived from an in-memory board export: branch status, ordering,
promotion policy and the lines a restack cascades.

The data layer is injected - :meth:`Stack.build` takes a merged-branch predicate - so
none of these touch git or the network.
"""

from __future__ import annotations

from collections.abc import Container

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from basstler.locations import RepositoryStateLocation
from basstler.stack.model import (
    BoardKey,
    BranchStatus,
    PullRequest,
)
from basstler.upstream_reviews.vocabulary import RollupState

from ..constants.stack import StackBranch, StackLabel

from .dataset import (
    StackRepository,
    StackSampleText,
    UpstreamPullRequestNumber,
    make_configuration,
    build,
)

# %% deriving a branch status


def test_merged_wins_over_everything():
    assert (
        BranchStatus.derive(draft=True, merged=True, in_review=True)
        == BranchStatus.MERGED
    )


def test_in_review_beats_draft_flag():
    assert (
        BranchStatus.derive(draft=True, merged=False, in_review=True)
        == BranchStatus.IN_REVIEW
    )


def test_undrafted_is_ready():
    assert (
        BranchStatus.derive(draft=False, merged=False, in_review=False)
        == BranchStatus.READY
    )


def test_drafted_is_draft():
    assert (
        BranchStatus.derive(draft=True, merged=False, in_review=False)
        == BranchStatus.DRAFT
    )


# %% status from the export


def test_in_review_derived_from_the_open_upstream_pull_request():
    stack = build(
        [
            PullRequest(
                3,
                StackBranch.FEATURE,
                StackBranch.BASE,
                draft=False,
                head_repository=StackRepository.FORK.value.full_name,
                upstream_pull_request=UpstreamPullRequestNumber.OPEN,
            )
        ]
    )
    assert stack.branches[0].status == BranchStatus.IN_REVIEW


def test_a_stale_in_review_label_without_an_upstream_pull_request_is_not_in_review():
    stack = build(
        [
            PullRequest(
                3,
                StackBranch.FEATURE,
                StackBranch.BASE,
                draft=False,
                labels=[StackLabel.IN_REVIEW],
                head_repository=StackRepository.FORK.value.full_name,
            )
        ]
    )
    assert stack.branches[0].status == BranchStatus.READY


def test_merged_derived_from_predicate_not_labels():
    stack = build(
        [
            PullRequest(
                9,
                StackBranch.LANDED,
                StackBranch.BASE,
                draft=False,
                labels=[],
                head_repository=StackRepository.FORK.value.full_name,
            )
        ],
        merged={StackBranch.LANDED},
    )
    assert stack.branches[0].status == BranchStatus.MERGED


# %% ordering: parent before child, even when declared out of order


def test_order_places_parent_before_child():
    pull_requests = [
        PullRequest(
            3,
            StackBranch.CHILD,
            StackBranch.PARENT,
            draft=True,
            head_repository=StackRepository.FORK.value.full_name,
        ),
        PullRequest(
            2,
            StackBranch.PARENT,
            StackBranch.BASE,
            draft=True,
            head_repository=StackRepository.FORK.value.full_name,
        ),
    ]
    names = [branch.name for branch in build(pull_requests).ordered()]
    assert names.index(StackBranch.PARENT) < names.index(StackBranch.CHILD)


# %% promotion policy - simplified: every ready, unblocked branch, no cap or turn order


def test_promotes_ready_root():
    pull_requests = [
        PullRequest(
            11,
            StackBranch.FEATURE,
            StackBranch.BASE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        )
    ]
    assert [branch.name for branch in build(pull_requests).promotion_order()] == [
        StackBranch.FEATURE
    ]


def test_nothing_promotes_when_all_draft():
    pull_requests = [
        PullRequest(
            5,
            StackBranch.WORK_IN_PROGRESS,
            StackBranch.BASE,
            draft=True,
            head_repository=StackRepository.FORK.value.full_name,
        )
    ]
    assert build(pull_requests).promotion_order() == []


def test_ready_child_blocked_until_parent_lands():
    pull_requests = [
        PullRequest(
            1,
            StackBranch.PARENT,
            StackBranch.BASE,
            draft=True,
            head_repository=StackRepository.FORK.value.full_name,
        ),  # still draft, not upstream
        PullRequest(
            2,
            StackBranch.CHILD,
            StackBranch.PARENT,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),  # approved but parent hasn't landed
    ]
    assert build(pull_requests).promotion_order() == []


def test_child_promotable_once_parent_reaches_review():
    # there is no per-stack review-slot limit anymore, so a child may promote alongside
    # its own parent the moment the parent has reached in-review - it does not have to
    # wait for the parent to fully merge.
    pull_requests = [
        PullRequest(
            1,
            StackBranch.PARENT,
            StackBranch.BASE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
            upstream_pull_request=UpstreamPullRequestNumber.OPEN,
        ),
        PullRequest(
            2,
            StackBranch.CHILD,
            StackBranch.PARENT,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),
    ]
    assert [branch.name for branch in build(pull_requests).promotion_order()] == [
        StackBranch.CHILD
    ]


def test_child_promotable_once_parent_merged():
    pull_requests = [
        PullRequest(
            1,
            StackBranch.PARENT,
            StackBranch.BASE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),
        PullRequest(
            2,
            StackBranch.CHILD,
            StackBranch.PARENT,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),
    ]
    promotable = build(pull_requests, merged={StackBranch.PARENT}).promotion_order()
    assert [branch.name for branch in promotable] == [StackBranch.CHILD]


def test_promotion_order_includes_every_ready_unblocked_branch_in_dependency_order():
    # two independent ready roots plus one ready child whose own parent has not yet
    # reached review - all ready roots promote together (no cap), the blocked child does
    # not.
    pull_requests = [
        PullRequest(
            1,
            StackBranch.FIRST_ROOT,
            StackBranch.BASE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),
        PullRequest(
            2,
            StackBranch.CHILD_OF_FIRST_ROOT,
            StackBranch.FIRST_ROOT,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),  # its parent is only ready
        PullRequest(
            3,
            StackBranch.SECOND_ROOT,
            StackBranch.BASE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),
    ]
    names = [branch.name for branch in build(pull_requests).promotion_order()]
    assert names == [StackBranch.FIRST_ROOT, StackBranch.SECOND_ROOT]


def test_promotion_order_withholds_a_branch_delegated_for_conflict_resolution():
    # a branch a maintenance pass delegated (needs-resolution) is stuck mid-restack, so it must
    # not be promoted even though it is otherwise ready and unblocked.
    pull_requests = [
        PullRequest(
            1,
            StackBranch.STUCK,
            StackBranch.BASE,
            draft=False,
            labels=[StackLabel.NEEDS_RESOLUTION],
            head_repository=StackRepository.FORK.value.full_name,
        ),
        PullRequest(
            2,
            StackBranch.FINE,
            StackBranch.BASE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),
    ]
    names = [branch.name for branch in build(pull_requests).promotion_order()]
    assert names == [StackBranch.FINE]


def test_check_state_and_session_carried_onto_branch():
    stack = build(
        [
            PullRequest(
                11,
                StackBranch.FEATURE,
                StackBranch.BASE,
                draft=False,
                check_state=RollupState.FAILURE,
                session=StackSampleText.SESSION_LINK,
                head_repository=StackRepository.FORK.value.full_name,
            )
        ]
    )
    assert stack.branches[0].check_state is RollupState.FAILURE
    assert stack.branches[0].session == StackSampleText.SESSION_LINK


def a_ready_branch_whose_checks_reached(check_state: RollupState) -> PullRequest:
    """
    :param check_state: The verdict over the branch's checks.
    :return: An approved branch on the base, otherwise promotable.
    """
    return PullRequest(
        1,
        StackBranch.FEATURE,
        StackBranch.BASE,
        draft=False,
        check_state=check_state,
        head_repository=StackRepository.FORK.value.full_name,
    )


def test_failing_checks_withhold_a_branch_from_promotion():
    stack = build(
        [
            a_ready_branch_whose_checks_reached(state)
            for state in (RollupState.FAILURE, RollupState.ERROR)
        ]
    )
    assert stack.promotion_order() == []


def test_checks_that_have_not_failed_leave_a_branch_promotable():
    for state in (
        RollupState.NOT_REPORTED,
        RollupState.SUCCESS,
        RollupState.PENDING,
        RollupState.EXPECTED,
    ):
        stack = build([a_ready_branch_whose_checks_reached(state)])
        assert [branch.name for branch in stack.promotion_order()] == [
            StackBranch.FEATURE
        ], state


def test_only_branches_promotion_would_otherwise_take_count_as_held_by_their_checks():
    stack = build(
        [
            a_ready_branch_whose_checks_reached(RollupState.FAILURE),
            PullRequest(
                2,
                StackBranch.CHILD,
                StackBranch.FEATURE,
                draft=False,
                check_state=RollupState.FAILURE,
                head_repository=StackRepository.FORK.value.full_name,
            ),
            PullRequest(
                3,
                StackBranch.PARSER,
                StackBranch.BASE,
                draft=True,
                check_state=RollupState.FAILURE,
                head_repository=StackRepository.FORK.value.full_name,
            ),
        ]
    )

    assert [branch.name for branch in stack.withheld_by_failing_checks()] == [
        StackBranch.FEATURE
    ]


@pytest.mark.parametrize("state", [RollupState.PENDING, RollupState.NOT_REPORTED])
def test_the_board_reads_the_check_state_back_as_its_verdict(
    tmp_path: Path, state: RollupState
):
    written = a_ready_branch_whose_checks_reached(state)
    board = tmp_path / RepositoryStateLocation.BOARD.name
    board.write_text(json.dumps({BoardKey.PULL_REQUESTS: [asdict(written)]}))

    assert PullRequest.read_board(board) == [written]


# %% the lines a restack cascades


def layers_of(stack, stacked_on: str | None = None) -> list[tuple[str, list[str]]]:
    """
    :param stack: A derived stack.
    :param stacked_on: The branch whose subtree to cover, or ``None`` for every branch.
    :return: Each line's trunk and layer names, bottom first.
    """
    return [
        (line.trunk, [branch.name for branch in line.layers])
        for line in stack.linear_stacks(stacked_on)
    ]


def test_a_line_leaves_out_what_has_landed_and_keeps_what_is_under_review():
    pull_requests = [
        PullRequest(
            1,
            StackBranch.LANDED,
            StackBranch.BASE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),
        PullRequest(
            2,
            StackBranch.UNDER_REVIEW,
            StackBranch.BASE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
            upstream_pull_request=UpstreamPullRequestNumber.OPEN,
        ),
        PullRequest(
            3,
            StackBranch.WORK_IN_PROGRESS,
            StackBranch.UNDER_REVIEW,
            draft=True,
            head_repository=StackRepository.FORK.value.full_name,
        ),
    ]
    stack = build(pull_requests, merged={StackBranch.LANDED})

    assert layers_of(stack) == [
        (StackBranch.BASE, [StackBranch.UNDER_REVIEW, StackBranch.WORK_IN_PROGRESS])
    ]


def test_a_branch_whose_parent_has_no_pull_request_is_based_on_that_parent():
    pull_requests = [
        PullRequest(
            2,
            StackBranch.WORK_IN_PROGRESS,
            StackBranch.OFF_BOARD_PARENT,
            draft=True,
            head_repository=StackRepository.FORK.value.full_name,
        )
    ]

    assert layers_of(build(pull_requests)) == [
        (StackBranch.OFF_BOARD_PARENT, [StackBranch.WORK_IN_PROGRESS])
    ]


def test_a_child_of_a_landed_parent_starts_a_line_on_the_base():
    # Its parent's commits are in the base, so the child is rebased onto the base (the
    # maintenance skill mirrors this by retargeting the child pull request's base).
    pull_requests = [
        PullRequest(
            1,
            StackBranch.PARENT,
            StackBranch.BASE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),
        PullRequest(
            2,
            StackBranch.CHILD,
            StackBranch.PARENT,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),
    ]
    stack = build(pull_requests, merged={StackBranch.PARENT})

    assert layers_of(stack) == [(StackBranch.BASE, [StackBranch.CHILD])]


def test_a_parent_with_two_children_continues_its_line_with_the_first():
    # A GitHub stack is a single line, so the second child is a line of its own based on
    # the parent's branch.
    pull_requests = [
        PullRequest(
            1,
            StackBranch.PARENT,
            StackBranch.BASE,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),
        PullRequest(
            2,
            StackBranch.CHILD,
            StackBranch.PARENT,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),
        PullRequest(
            3,
            StackBranch.SIBLING,
            StackBranch.PARENT,
            draft=False,
            head_repository=StackRepository.FORK.value.full_name,
        ),
    ]

    assert layers_of(build(pull_requests)) == [
        (StackBranch.BASE, [StackBranch.PARENT, StackBranch.CHILD]),
        (StackBranch.PARENT, [StackBranch.SIBLING]),
    ]


# %% what is stacked on one branch


def a_three_deep_stack(merged: Container[str] = frozenset()):
    """
    :param merged: Which of its branches have already landed upstream.
    :return: The chain of three, beside the branch cut from the base.
    """
    configuration = make_configuration()

    def pull_request(number: int, branch: StackBranch, parent: str) -> PullRequest:
        return PullRequest(
            number,
            branch,
            parent,
            draft=True,
            head_repository=StackRepository.FORK.value.full_name,
        )

    return build(
        [
            pull_request(1, StackBranch.PARENT, configuration.upstream_base),
            pull_request(2, StackBranch.CHILD, StackBranch.PARENT),
            pull_request(3, StackBranch.GRANDCHILD, StackBranch.CHILD),
            pull_request(4, StackBranch.OFF_THE_CHAIN, configuration.upstream_base),
        ],
        merged,
    )


def test_every_branch_along_the_chain_is_stacked_on_the_named_one():
    stacked = a_three_deep_stack().branches_stacked_on(StackBranch.PARENT)
    assert [branch.name for branch in stacked] == [
        StackBranch.CHILD,
        StackBranch.GRANDCHILD,
    ]


def test_the_named_branch_is_not_stacked_on_itself():
    stacked = a_three_deep_stack().branches_stacked_on(StackBranch.PARENT)
    assert StackBranch.PARENT not in [branch.name for branch in stacked]


def test_a_branch_on_another_parent_is_not_stacked_on_the_named_one():
    stacked = a_three_deep_stack().branches_stacked_on(StackBranch.PARENT)
    assert StackBranch.OFF_THE_CHAIN not in [branch.name for branch in stacked]


def test_a_branch_with_nothing_above_it_has_nothing_stacked_on_it():
    assert a_three_deep_stack().branches_stacked_on(StackBranch.GRANDCHILD) == []


def test_a_chain_is_stacked_only_up_to_the_branch_that_has_landed():
    # A merged branch has nothing to take from the branch below it, and whatever sits on
    # it is no longer reached from below either: its parent has landed, so what it needs
    # is a reparent onto the upstream base, which a whole-board pass owns.
    stack = a_three_deep_stack(merged={StackBranch.CHILD})

    assert stack.branches_stacked_on(StackBranch.PARENT) == []


# %% the lines of one subtree


def test_a_named_subtree_is_one_line_based_on_the_named_branch():
    assert layers_of(a_three_deep_stack(), stacked_on=StackBranch.PARENT) == [
        (StackBranch.PARENT, [StackBranch.CHILD, StackBranch.GRANDCHILD])
    ]


def test_naming_no_subtree_covers_the_whole_board():
    assert layers_of(a_three_deep_stack()) == [
        (
            StackBranch.BASE,
            [StackBranch.PARENT, StackBranch.CHILD, StackBranch.GRANDCHILD],
        ),
        (StackBranch.BASE, [StackBranch.OFF_THE_CHAIN]),
    ]
