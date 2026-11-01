"""
Tests for promoting approved, unblocked branches to the upstream review queue.
"""

from __future__ import annotations

import argparse

import pytest

import basstler.maintenance.__main__
import basstler.maintenance.restack_procedure
from basstler.workflow_labels import WorkflowLabel
from basstler.maintenance.commands import RunReportCommand
from basstler.maintenance.constants import MaintenanceText
from basstler.maintenance.description import PullRequestDescription
from basstler.maintenance.promotion import (
    PromotionSummaries,
    PromotionSummary,
    clear_spent_promotion_labels,
    promote,
)
from basstler.maintenance.restack_procedure import restack
from basstler.stack.promotion_link import PromotionLinkParameter

from ..constants.paths import DatasetLocation
from ..constants.stack import StackBranch, StackLabel
from ..stack.dataset import prefilled

from .fork import (
    StackPullRequestNumber,
    PromotionSampleText,
    make_configuration,
    ForkCheckout,
    a_stack,
    a_parent_and_child,
    the_board,
)
from .recorded_fork import (
    AlreadyResolvedPass,
    RecordedLabelWrite,
    RecordingPullRequests,
)


# %% promotion


def test_the_promotion_link_goes_into_the_description_and_the_branch_is_labelled(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    fork = RecordingPullRequests(
        descriptions={
            StackPullRequestNumber.PARENT: (
                f"{PromotionSampleText.SUMMARY}\n\n{PromotionSampleText.DETAIL}\n"
            )
        },
        titles={StackPullRequestNumber.PARENT: PromotionSampleText.TITLE},
    )

    promoted = promote(a_stack(fork_checkout, the_board()), fork, PromotionSummaries())

    assert [entry.branch for entry in promoted] == [StackBranch.PARENT]
    written = fork.description_writes[0]
    assert written.pull_request_number == StackPullRequestNumber.PARENT
    assert MaintenanceText.PROMOTION_HEADING in written.body
    assert promoted[0].url in written.body
    assert PromotionSampleText.SUMMARY in written.body
    assert fork.label_writes == [
        RecordedLabelWrite(
            StackPullRequestNumber.PARENT, (WorkflowLabel.PROMOTION_LINK_SENT,)
        )
    ]


def test_a_branch_already_carrying_the_link_label_is_not_promoted_again(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    fork = RecordingPullRequests(
        labels={StackPullRequestNumber.PARENT: [WorkflowLabel.PROMOTION_LINK_SENT]}
    )

    promoted = promote(a_stack(fork_checkout, the_board()), fork, PromotionSummaries())

    assert promoted == []
    assert fork.description_writes == []


def test_a_branch_labelled_needs_resolution_during_this_pass_is_not_promoted(
    fork_checkout: ForkCheckout,
):
    """
    The board is a snapshot taken before the restack runs, so a branch the restack has
    just withheld still looks promotable in it.

    Promotion has to ask the branch, not the snapshot, or a pass promotes what the same
    pass just conflicted on.
    """
    a_parent_and_child(fork_checkout)
    fork = RecordingPullRequests(
        labels={
            StackPullRequestNumber.PARENT: [make_configuration().needs_resolution_label]
        }
    )

    promoted = promote(a_stack(fork_checkout, the_board()), fork, PromotionSummaries())

    assert promoted == []
    assert fork.description_writes == []
    assert fork.label_writes == []


def test_the_promotion_label_write_keeps_a_label_added_since_the_board_was_taken(
    fork_checkout: ForkCheckout,
):
    """
    A label write replaces the whole set, so computing it from the snapshot silently
    strips anything applied since - including the label the restack applies mid-pass.
    """
    a_parent_and_child(fork_checkout)
    fork = RecordingPullRequests(
        labels={StackPullRequestNumber.PARENT: [StackLabel.SOMEBODY_ELSES]}
    )

    promote(a_stack(fork_checkout, the_board()), fork, PromotionSummaries())

    assert fork.label_writes == [
        RecordedLabelWrite(
            StackPullRequestNumber.PARENT,
            (StackLabel.SOMEBODY_ELSES, WorkflowLabel.PROMOTION_LINK_SENT),
        )
    ]


def test_a_promoted_branch_that_reached_review_has_its_link_label_removed(
    fork_checkout: ForkCheckout,
):
    """
    The label exists to stop a link being rebuilt; once the branch is in review the link
    has been acted on, so leaving it would misreport the branch forever.
    """
    a_parent_and_child(fork_checkout)
    board = the_board()
    board[0].labels = [
        WorkflowLabel.PROMOTION_LINK_SENT,
        make_configuration().in_review_label,
    ]
    board[0].upstream_pull_request = StackPullRequestNumber.UPSTREAM
    fork = RecordingPullRequests()

    cleared = clear_spent_promotion_labels(a_stack(fork_checkout, board), fork)

    assert cleared == (StackBranch.PARENT,)
    assert fork.label_writes == [
        RecordedLabelWrite(
            StackPullRequestNumber.PARENT, (make_configuration().in_review_label,)
        )
    ]


# %% what the upstream pull request opens with


def link_back_to(pull_request_number: int) -> str:
    """
    :param pull_request_number: A fork pull request.
    :return: The line every prefilled description ends with.
    """
    fork_url = make_configuration().fork_repository.pull_request_url(
        pull_request_number
    )
    return f"{MaintenanceText.FULL_DETAIL} {fork_url}"


def a_parent_with_a_description() -> RecordingPullRequests:
    """
    :return: A fork whose parent pull request has a title and a two-paragraph
        description.
    """
    return RecordingPullRequests(
        descriptions={
            StackPullRequestNumber.PARENT: (
                f"{PromotionSampleText.SUMMARY}\n\n{PromotionSampleText.DETAIL}\n"
            )
        },
        titles={StackPullRequestNumber.PARENT: PromotionSampleText.TITLE},
    )


def test_the_upstream_pull_request_opens_with_the_written_points_and_the_link_back(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    summaries = PromotionSummaries.read_from(DatasetLocation.PROMOTION_SUMMARIES.value)
    summary = summaries.for_pull_request(StackPullRequestNumber.PARENT)

    promoted = promote(
        a_stack(fork_checkout, the_board()), a_parent_with_a_description(), summaries
    )

    assert prefilled(promoted[0].url, PromotionLinkParameter.BODY).splitlines() == [
        *(f"- {point}" for point in summary.points),
        "",
        link_back_to(StackPullRequestNumber.PARENT),
    ]


def test_a_written_title_replaces_the_fork_title_upstream(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    summaries = PromotionSummaries.read_from(DatasetLocation.PROMOTION_SUMMARIES.value)

    promoted = promote(
        a_stack(fork_checkout, the_board()), a_parent_with_a_description(), summaries
    )

    assert (
        prefilled(promoted[0].url, PromotionLinkParameter.TITLE)
        == summaries.for_pull_request(StackPullRequestNumber.PARENT).title
    )


def test_a_branch_nobody_wrote_for_opens_with_only_its_title_and_the_link_back(
    fork_checkout: ForkCheckout,
):
    """
    The fork description was written for the fork's reviewer, so none of it is carried
    upstream in place of a summary nobody wrote.
    """
    a_parent_and_child(fork_checkout)

    promoted = promote(
        a_stack(fork_checkout, the_board()),
        a_parent_with_a_description(),
        PromotionSummaries(),
    )

    assert prefilled(promoted[0].url, PromotionLinkParameter.BODY) == link_back_to(
        StackPullRequestNumber.PARENT
    )
    assert (
        prefilled(promoted[0].url, PromotionLinkParameter.TITLE)
        == PromotionSampleText.TITLE
    )


def test_an_empty_entry_reads_as_no_summary():
    summaries = PromotionSummaries.read_from(DatasetLocation.PROMOTION_SUMMARIES.value)

    assert (
        summaries.for_pull_request(StackPullRequestNumber.CHILD) == PromotionSummary()
    )


def test_no_summaries_document_reads_as_none_written():
    assert PromotionSummaries.read_from(None) == PromotionSummaries()


def test_a_whole_pass_promotes_with_the_summaries_it_was_given(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    maintenance = AlreadyResolvedPass.over(fork_checkout, the_board())
    summaries_document = DatasetLocation.PROMOTION_SUMMARIES.value

    RunReportCommand().run(
        maintenance, argparse.Namespace(json=True, summaries=summaries_document)
    )

    written = maintenance.recorded_fork.description_writes[0]
    recorded_link = PullRequestDescription(written.body).section(
        MaintenanceText.PROMOTION_HEADING
    )
    assert (
        prefilled(recorded_link, PromotionLinkParameter.TITLE)
        == PromotionSummaries.read_from(summaries_document)
        .for_pull_request(written.pull_request_number)
        .title
    )


def test_a_branch_no_step_concludes_is_an_error_rather_than_a_silent_pass(
    fork_checkout: ForkCheckout, monkeypatch: pytest.MonkeyPatch
):
    """
    The last step always concludes a branch, so a branch reaching the end of the steps
    means the procedure lost it - which must not read as a branch nothing happened to.
    """
    a_parent_and_child(fork_checkout)
    monkeypatch.setattr(
        basstler.maintenance.restack_procedure, "restack_steps", lambda: ()
    )

    with pytest.raises(
        basstler.maintenance.restack_procedure.RestackConcludedNothingError
    ) as raised:
        restack(
            a_stack(fork_checkout, the_board()),
            fork_checkout.git,
            RecordingPullRequests(),
        )

    assert raised.value.branch in {StackBranch.PARENT, StackBranch.CHILD}
