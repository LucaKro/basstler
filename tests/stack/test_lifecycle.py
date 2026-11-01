"""
Tests for a whole stack taken through its lifecycle, from drafts to landed branches,
against in-memory board exports.
"""

from __future__ import annotations

from basstler_domain_model.git.commands import (
    IntegrationStrategy,
)
from basstler.stack.model import (
    PullRequest,
    RestackPlanEntry,
)

from ..constants.stack import StackBranch

from .dataset import (
    StackRepository,
    build,
    a_deep_stack_beside_an_independent_branch,
)


# %% a whole stack through its lifecycle

# The other stack suites each exercise one function against one or two pull requests.
# These walk a single realistic stack through the transitions the README describes -
# approve, promote, land, restack - asserting the derived state at each step, because
# the interactions between those stages are what a per-function test cannot reach.


def test_nothing_promotes_while_the_whole_stack_is_still_draft():
    assert a_deep_stack_beside_an_independent_branch().promotion_order() == []


def test_approving_a_root_promotes_it_and_nothing_above_it():
    """
    Un-drafting is the approval gate, and it approves one branch rather than a whole
    stack.
    """
    stack = a_deep_stack_beside_an_independent_branch(
        approved={StackBranch.ENGINE, StackBranch.ENGINE_UI}
    )

    assert [branch.name for branch in stack.promotion_order()] == [StackBranch.ENGINE]


def test_promoting_a_parent_unblocks_the_child_behind_it():
    """
    A child may follow its parent upstream once the parent is in review - it does not
    wait for the parent to merge.
    """
    stack = a_deep_stack_beside_an_independent_branch(
        approved={StackBranch.ENGINE, StackBranch.ENGINE_UI},
        promoted={StackBranch.ENGINE},
    )

    assert [branch.name for branch in stack.promotion_order()] == [
        StackBranch.ENGINE_UI
    ]


def test_both_stacks_promote_together_since_they_do_not_depend_on_each_other():
    stack = a_deep_stack_beside_an_independent_branch(
        approved={StackBranch.ENGINE, StackBranch.PARSER}
    )

    assert [branch.name for branch in stack.promotion_order()] == [
        StackBranch.ENGINE,
        StackBranch.PARSER,
    ]


def test_a_branch_delegated_for_conflict_resolution_is_held_back_alone():
    """
    Withholding one branch must not withhold an unrelated stack.
    """
    stack = a_deep_stack_beside_an_independent_branch(
        approved={StackBranch.ENGINE, StackBranch.PARSER}, withheld={StackBranch.ENGINE}
    )

    assert [branch.name for branch in stack.promotion_order()] == [StackBranch.PARSER]


def test_landing_a_root_reparents_only_its_own_child():
    """
    The landed branch drops out of the plan and its child moves onto the base, while the
    branch above keeps the parent it still has and the untouched stack keeps its own.
    """
    stack = a_deep_stack_beside_an_independent_branch(landed={StackBranch.ENGINE})

    assert stack.restack_plan() == [
        RestackPlanEntry(
            branch=StackBranch.ENGINE_UI,
            parent=StackBranch.BASE,
            strategy=IntegrationStrategy.MERGE,
        ),
        RestackPlanEntry(
            branch=StackBranch.ENGINE_DOCS,
            parent=StackBranch.ENGINE_UI,
            strategy=IntegrationStrategy.MERGE,
        ),
        RestackPlanEntry(
            branch=StackBranch.PARSER,
            parent=StackBranch.BASE,
            strategy=IntegrationStrategy.MERGE,
        ),
    ]


def test_landing_a_root_that_no_open_pull_request_describes_still_reparents():
    """
    The case that motivated deciding `merged` by git ancestry: a parent whose own pull
    request was closed rather than merged is absent from the board entirely, and the
    child must still be moved off it.
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

    assert orphaned.restack_plan() == [
        RestackPlanEntry(
            branch=StackBranch.ENGINE_UI,
            parent=StackBranch.BASE,
            strategy=IntegrationStrategy.MERGE,
        )
    ]
    assert orphaned.has_landed_upstream(StackBranch.ENGINE)


def test_a_stack_lands_bottom_up_over_successive_runs():
    """
    Each branch reaches the base only after the one below it has, so the plan shortens
    from the bottom as the stack drains.
    """
    after_first = a_deep_stack_beside_an_independent_branch(landed={StackBranch.ENGINE})
    after_second = a_deep_stack_beside_an_independent_branch(
        landed={StackBranch.ENGINE, StackBranch.ENGINE_UI}
    )

    assert [entry.branch for entry in after_first.restack_plan()] == [
        StackBranch.ENGINE_UI,
        StackBranch.ENGINE_DOCS,
        StackBranch.PARSER,
    ]
    assert [entry.branch for entry in after_second.restack_plan()] == [
        StackBranch.ENGINE_DOCS,
        StackBranch.PARSER,
    ]
    assert after_second.restack_plan()[0] == RestackPlanEntry(
        branch=StackBranch.ENGINE_DOCS,
        parent=StackBranch.BASE,
        strategy=IntegrationStrategy.MERGE,
    )
