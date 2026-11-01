"""
Tests for what the stack helper asks to have written: label writes, the check a commit
move has to pass, and promotion links.
"""

from __future__ import annotations

from collections.abc import Container

import pytest

from basstler.stack.commit_move import (
    CommitMoveAction,
    CommitMoveChecks,
    ProposedCommitMove,
    RefusalReason,
)
from basstler_domain_model.github.exceptions import (
    ContradictoryLabelWriteError,
)
from basstler.stack.exceptions import (
    PromotionLinkTooLongError,
)
from basstler_domain_model.github.labels import (
    LabelWrite,
)
from basstler.stack.promotion_link import PromotionLink, PromotionLinkParameter

from ..constants.stack import RemoteName, StackBranch, StackLabel

from .dataset import (
    StackRepository,
    StackSampleText,
    make_configuration,
    prefilled,
    a_deep_stack_beside_an_independent_branch,
)


# %% label writes


def test_adding_a_label_keeps_every_label_the_pull_request_already_carries():
    """
    The label write replaces the whole set, so computing it from the addition alone
    silently wipes the rest - which has happened, stripping `in-review` off branches that
    were already promoted.
    """
    configuration = make_configuration()

    write = LabelWrite.replacing(
        [configuration.in_review_label, StackLabel.SOMEBODY_ELSES],
        added=[configuration.needs_resolution_label],
    )

    assert write.labels == (
        configuration.in_review_label,
        StackLabel.SOMEBODY_ELSES,
        configuration.needs_resolution_label,
    )


def test_removing_a_label_keeps_every_other_label():
    configuration = make_configuration()

    write = LabelWrite.replacing(
        [configuration.in_review_label, StackLabel.SOMEBODY_ELSES],
        removed=[StackLabel.SOMEBODY_ELSES],
    )

    assert write.labels == (configuration.in_review_label,)


def test_adding_a_label_already_carried_leaves_the_set_unchanged():
    carried = make_configuration().in_review_label

    write = LabelWrite.replacing([carried], added=[carried])

    assert write.labels == (carried,)


def test_removing_a_label_that_is_not_there_leaves_the_set_unchanged():
    configuration = make_configuration()

    write = LabelWrite.replacing(
        [configuration.in_review_label], removed=[configuration.rebase_label]
    )

    assert write.labels == (configuration.in_review_label,)


def test_asking_to_add_and_remove_one_label_at_once_is_refused():
    """
    Either outcome would be a guess at what the caller meant, and the wrong guess is a
    label silently kept or silently dropped.
    """
    configuration = make_configuration()

    with pytest.raises(ContradictoryLabelWriteError):
        LabelWrite.replacing(
            [configuration.in_review_label],
            added=[configuration.rebase_label],
            removed=[configuration.rebase_label],
        )


# %% move check


def a_proposed_push(
    source: str = StackBranch.ENGINE,
    destination: str = StackBranch.ENGINE,
    destination_remote: str = RemoteName.ORIGIN,
) -> ProposedCommitMove:
    """
    A proposed push, defaulting to the one shape that is always allowed.

    :param source: The branch whose commits would move.
    :param destination: The branch they would land on.
    :param destination_remote: The remote holding the destination.
    :return: The proposed move.
    """
    return ProposedCommitMove(
        action=CommitMoveAction.PUSH,
        source=source,
        destination=destination,
        destination_remote=destination_remote,
    )


def checks_over_that_stack(
    checked_out_branch: str = StackBranch.ENGINE,
    ancestors: Container[str] = frozenset(),
) -> CommitMoveChecks:
    """
    A move check over :func:`a_deep_stack_beside_an_independent_branch`.

    :param checked_out_branch: What ``git branch --show-current`` would report.
    :param ancestors: Branches already contained in the move's source.
    :return: The move check to ask for refusals.
    """
    return CommitMoveChecks(
        stack=a_deep_stack_beside_an_independent_branch(),
        checked_out_branch=checked_out_branch,
        is_ancestor=lambda candidate, _descendant: candidate in ancestors,
    )


def test_pushing_the_checked_out_branch_onto_itself_is_allowed():
    assert checks_over_that_stack().refusals(a_proposed_push()) == []


def test_pushing_while_another_branch_is_checked_out_is_refused():
    """
    The checked-out branch is the one whose content actually moves, so a mismatch moves
    something other than what was intended.
    """
    refusals = checks_over_that_stack(checked_out_branch=StackBranch.PARSER).refusals(
        a_proposed_push()
    )

    assert [refusal.reason for refusal in refusals] == [RefusalReason.NOT_CHECKED_OUT]
    assert StackBranch.PARSER in refusals[0].explanation


def test_a_push_naming_a_different_branch_on_each_side_is_refused():
    refusals = checks_over_that_stack().refusals(
        a_proposed_push(destination=StackBranch.ENGINE_UI)
    )

    assert [refusal.reason for refusal in refusals] == [
        RefusalReason.MISMATCHED_BRANCH_NAMES
    ]


def test_a_destination_on_the_upstream_remote_is_refused():
    """
    Every push in this workflow goes to the fork; the upstream is written only by
    opening a pull request against it.
    """
    refusals = checks_over_that_stack().refusals(
        a_proposed_push(destination_remote=RemoteName.UPSTREAM)
    )

    assert [refusal.reason for refusal in refusals] == [RefusalReason.NOT_THE_FORK]
    assert RemoteName.UPSTREAM in refusals[0].explanation


def test_a_push_that_would_make_a_child_an_ancestor_of_its_parent_is_refused():
    """
    GitHub reads a pull request whose head is contained in its base as merged, so this
    push would falsely close the child.
    """
    refusals = checks_over_that_stack(ancestors={StackBranch.ENGINE_UI}).refusals(
        a_proposed_push()
    )

    assert [refusal.reason for refusal in refusals] == [RefusalReason.FALSE_MERGE]
    assert StackBranch.ENGINE_UI in refusals[0].explanation


def test_an_unrelated_branch_contained_in_the_source_is_not_a_false_merge():
    """
    Only a child of the destination can be falsely merged by pushing to it.
    """
    assert (
        checks_over_that_stack(ancestors={StackBranch.PARSER}).refusals(
            a_proposed_push()
        )
        == []
    )


def test_every_reason_to_refuse_is_reported_rather_than_only_the_first():
    """
    Fixing one problem and re-running to discover the next is how a half-applied move
    gets made.
    """
    refusals = checks_over_that_stack(checked_out_branch=StackBranch.PARSER).refusals(
        a_proposed_push(
            destination=StackBranch.ENGINE_UI, destination_remote=RemoteName.UPSTREAM
        )
    )

    assert [refusal.reason for refusal in refusals] == [
        RefusalReason.NOT_CHECKED_OUT,
        RefusalReason.MISMATCHED_BRANCH_NAMES,
        RefusalReason.NOT_THE_FORK,
    ]


# %% promotion links


def a_promotion_link(
    title: str = StackSampleText.TITLE, body: str = StackSampleText.BODY
) -> PromotionLink:
    """
    The compare-and-create link for that stack's root branch.

    :param title: The title to prefill.
    :param body: The body to prefill.
    :return: The built link.
    """
    return PromotionLink.build(make_configuration(), StackBranch.ENGINE, title, body)


def test_the_link_compares_the_upstream_base_against_the_fork_branch():
    assert a_promotion_link().url.startswith(
        f"{StackRepository.UPSTREAM.value.url}/compare/{StackBranch.BASE}..."
        f"{StackRepository.FORK.value.owner}:{StackBranch.ENGINE}"
        f"?{PromotionLinkParameter.EXPAND}=1"
    )


def test_the_title_and_body_are_encoded_rather_than_pasted_in_raw():
    """
    An unencoded ``&`` or ``#`` truncates the query string at that character, so the
    prefill silently loses everything after it.
    """
    link = a_promotion_link(
        title=StackSampleText.TITLE_WITH_SEPARATORS,
        body=StackSampleText.BODY_WITH_FRAGMENT,
    )

    assert prefilled(link.url, PromotionLinkParameter.TITLE) == (
        StackSampleText.TITLE_WITH_SEPARATORS
    )
    assert prefilled(link.url, PromotionLinkParameter.BODY) == (
        StackSampleText.BODY_WITH_FRAGMENT
    )


def test_a_body_within_the_limit_is_left_whole():
    link = a_promotion_link(body=StackSampleText.SHORT_BODY)

    assert not link.body_was_truncated
    assert (
        prefilled(link.url, PromotionLinkParameter.BODY) == StackSampleText.SHORT_BODY
    )


def test_an_oversized_body_is_truncated_rather_than_silently_dropped():
    """
    Over the limit the whole prefill is discarded by the server, so the link arrives
    with an empty description and nobody is told.
    """
    link = a_promotion_link(
        body=StackSampleText.FILLER * (PromotionLink.url_character_limit + 1)
    )

    assert link.body_was_truncated
    assert link.url.endswith(PromotionLink.truncation_marker)
    assert len(link.url) <= PromotionLink.url_character_limit


def test_a_link_that_cannot_fit_even_without_a_description_is_refused():
    """
    Returning a link known to be over the limit would be the silent failure this class
    exists to prevent, so the one input that cannot be fixed by shortening the body says
    so.
    """
    with pytest.raises(PromotionLinkTooLongError):
        a_promotion_link(
            title=StackSampleText.FILLER * (PromotionLink.url_character_limit + 1),
            body="",
        )
