"""
Tests for the stack derived from an in-memory board export: branch status, ordering,
promotion policy and the restack plan.

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
    RestackPlanEntry,
)
from basstler_domain_model.git.commands import (
    IntegrationStrategy,
)
from basstler_domain_model.github.vocabulary import (
    RollupState,
)

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


def test_rebase_label_sets_strategy():
    stack = build(
        [
            PullRequest(
                1,
                StackBranch.FEATURE,
                StackBranch.BASE,
                draft=True,
                labels=[StackLabel.REBASE],
                head_repository=StackRepository.FORK.value.full_name,
            )
        ]
    )
    assert stack.branches[0].strategy == IntegrationStrategy.REBASE
    stack = build(
        [
            PullRequest(
                1,
                StackBranch.FEATURE,
                StackBranch.BASE,
                draft=True,
                labels=[],
                head_repository=StackRepository.FORK.value.full_name,
            )
        ]
    )
    assert stack.branches[0].strategy == IntegrationStrategy.MERGE


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


# %% restack plan


def test_restack_plan_excludes_merged_only():
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
    plan = build(pull_requests, merged={StackBranch.LANDED}).restack_plan()
    names = [entry.branch for entry in plan]
    assert StackBranch.LANDED not in names
    assert names == [
        StackBranch.UNDER_REVIEW,
        StackBranch.WORK_IN_PROGRESS,
    ]  # in-review included, parent before child


def test_restack_plan_carries_parent_and_strategy():
    pull_requests = [
        PullRequest(
            2,
            StackBranch.WORK_IN_PROGRESS,
            StackBranch.OFF_BOARD_PARENT,
            draft=True,
            labels=[StackLabel.REBASE],
            head_repository=StackRepository.FORK.value.full_name,
        )
    ]
    plan = build(pull_requests).restack_plan()
    assert plan == [
        RestackPlanEntry(
            branch=StackBranch.WORK_IN_PROGRESS,
            parent=StackBranch.OFF_BOARD_PARENT,
            strategy=IntegrationStrategy.REBASE,
        )
    ]


def test_restack_plan_reparents_child_of_merged_parent_onto_base():
    # parent merged into main -> its commits are in the base, so the child is reparented
    # onto main (the maintenance skill mirrors this by retargeting the child PR's base on GitHub).
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
    plan = build(pull_requests, merged={StackBranch.PARENT}).restack_plan()
    assert plan == [
        RestackPlanEntry(
            branch=StackBranch.CHILD,
            parent=StackBranch.BASE,
            strategy=IntegrationStrategy.MERGE,
        )
    ]


# %% what is stacked on one branch


def a_three_deep_stack(
    merged: Container[str] = frozenset(), rebasing: Container[str] = frozenset()
):
    """
    :param merged: Which of its branches have already landed upstream.
    :param rebasing: Which of them carry the label authorising a rebase.
    :return: The chain of three, beside the branch cut from the base.
    """
    configuration = make_configuration()

    def pull_request(number: int, branch: StackBranch, parent: str) -> PullRequest:
        return PullRequest(
            number,
            branch,
            parent,
            draft=True,
            labels=[configuration.rebase_label] if branch in rebasing else [],
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


# %% the restack plan for one subtree


def test_a_named_subtree_plans_only_what_is_stacked_on_it():
    plan = a_three_deep_stack().restack_plan(stacked_on=StackBranch.PARENT)
    assert plan == [
        RestackPlanEntry(
            branch=StackBranch.CHILD,
            parent=StackBranch.PARENT,
            strategy=IntegrationStrategy.MERGE,
        ),
        RestackPlanEntry(
            branch=StackBranch.GRANDCHILD,
            parent=StackBranch.CHILD,
            strategy=IntegrationStrategy.MERGE,
        ),
    ]


def test_naming_no_subtree_plans_the_whole_board():
    plan = a_three_deep_stack().restack_plan()
    assert [entry.branch for entry in plan] == [
        StackBranch.PARENT,
        StackBranch.CHILD,
        StackBranch.GRANDCHILD,
        StackBranch.OFF_THE_CHAIN,
    ]


def test_a_subtree_carries_each_branch_own_integration_strategy():
    stack = a_three_deep_stack(rebasing={StackBranch.CHILD})

    plan = stack.restack_plan(stacked_on=StackBranch.PARENT)

    assert [entry.strategy for entry in plan] == [
        IntegrationStrategy.REBASE,
        IntegrationStrategy.MERGE,
    ]
