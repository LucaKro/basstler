"""
Tests for what a session writes each upstream summary from, and the command that prints
it, against a real scratch fork.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from enum import StrEnum

import pytest

from basstler.workflow_labels import WorkflowLabel
from basstler.maintenance.commands import (
    PrepareCommand,
    PreparationKey,
    RunReportCommand,
)
from basstler.maintenance.promotion import PromotionSummaries, PromotionSummary
from basstler.maintenance.promotion_context import (
    ChangeSize,
    FileChange,
    PromotionContextReader,
)
from basstler.stack.promotion_link import PromotionLink

from ..constants.stack import RemoteName, StackBranch

from .fork import (
    PromotionSampleText,
    StackPullRequestNumber,
    ForkCheckout,
    a_parent_and_child,
    a_stack,
    the_board,
)
from .large_board import OutputBudget
from .recorded_fork import AlreadyResolvedPass, RecordingPullRequests

# %% vocabulary


class PromotionContextKey(StrEnum):
    """
    The keys of one :attr:`PreparationKey.TO_SUMMARIZE` entry the tests read back from
    the prepared document.
    """

    PULL_REQUEST_NUMBER = "pull_request_number"
    """
    The fork pull request the entry's summary is keyed by.
    """

    BRANCH = "branch"
    """
    The branch to be promoted.
    """


# %% building a reader


def a_context_reader(
    fork_checkout: ForkCheckout, fork: RecordingPullRequests, board=None
) -> PromotionContextReader:
    """
    :param fork_checkout: The checkout the stack is built in.
    :param fork: The fork to read titles and descriptions from.
    :param board: The board entries, the two-branch board when none is given.
    :return: The reader over that stack.
    """
    stack = a_stack(fork_checkout, board or the_board())
    return PromotionContextReader(stack, fork_checkout.git, fork)


# %% one branch's context


def test_a_branch_awaiting_its_link_is_described_from_its_pull_request_and_commits(
    fork_checkout: ForkCheckout,
):
    a_parent_and_child(fork_checkout)
    second = fork_checkout.commit_on(StackBranch.PARENT, "a-second-file", "more\n")
    fork = RecordingPullRequests(
        titles={StackPullRequestNumber.PARENT: PromotionSampleText.TITLE},
        descriptions={StackPullRequestNumber.PARENT: PromotionSampleText.SUMMARY},
    )
    reader = a_context_reader(fork_checkout, fork)

    (context,) = reader.read()

    subjects = fork_checkout.run_git(
        "log", "--reverse", "--format=%s", f"{RemoteName.UPSTREAM}/main..{second}"
    ).splitlines()
    assert context.pull_request_number == StackPullRequestNumber.PARENT
    assert context.title == PromotionSampleText.TITLE
    assert context.description == PromotionSampleText.SUMMARY
    assert context.commit_subjects == tuple(subjects)
    assert context.compared_with == reader.stack.configuration.upstream_base_reference
    assert context.change_size == ChangeSize.of(
        [
            FileChange(ForkCheckout.own_file(StackBranch.PARENT), 1, 0),
            FileChange("a-second-file", 1, 0),
        ],
        reader.file_limit,
    )


def test_a_branch_whose_link_a_pass_recorded_is_not_described_again(
    fork_checkout: ForkCheckout,
):
    """
    The link label is what makes a branch's summary written once: a later pass finds it
    and leaves the branch out.
    """
    a_parent_and_child(fork_checkout)
    board = the_board()
    board[0].labels.append(WorkflowLabel.PROMOTION_LINK_SENT)

    assert a_context_reader(fork_checkout, RecordingPullRequests(), board).read() == []


def test_a_large_change_is_described_within_its_budget(fork_checkout: ForkCheckout):
    a_parent_and_child(fork_checkout)
    fork_checkout.run_git("checkout", "--quiet", StackBranch.PARENT)
    commits = 40
    for commit in range(commits):
        for number in range(8):
            path = fork_checkout.project_root / f"module_{commit:02d}_{number}.py"
            path.write_text("a line\n" * (commit + 1))
        fork_checkout.run_git("add", ".")
        fork_checkout.run_git("commit", "--quiet", "-m", f"change number {commit}")
    fork_checkout.run_git(
        "push",
        "--quiet",
        RemoteName.ORIGIN,
        f"{StackBranch.PARENT}:{StackBranch.PARENT}",
    )
    fork_checkout.run_git("fetch", "--quiet", RemoteName.ORIGIN)
    fork = RecordingPullRequests(
        descriptions={
            StackPullRequestNumber.PARENT: f"{PromotionSampleText.DETAIL} " * 2000
        }
    )
    reader = a_context_reader(fork_checkout, fork)

    (context,) = reader.read()

    assert len(json.dumps(asdict(context)).encode()) <= OutputBudget.PROMOTION_CONTEXT
    assert len(context.change_size.largest_files) == reader.file_limit
    assert context.commits_not_listed == commits + 1 - reader.commit_limit
    assert context.description.endswith(reader.truncation_marker)


# %% the command


def a_prepared_pass(
    fork_checkout: ForkCheckout, capsys: pytest.CaptureFixture[str]
) -> tuple[AlreadyResolvedPass, dict]:
    """
    :param fork_checkout: The checkout holding the two-branch stack.
    :param capsys: What the command prints is read through.
    :return: The pass, and the document ``prepare`` printed for it.
    """
    maintenance = AlreadyResolvedPass.over(fork_checkout, the_board())
    PrepareCommand().run(maintenance, argparse.Namespace())
    return maintenance, json.loads(capsys.readouterr().out)


def test_prepare_lists_the_reparents_and_every_summary_to_write(
    fork_checkout: ForkCheckout, capsys: pytest.CaptureFixture[str]
):
    a_parent_and_child(fork_checkout)

    maintenance, document = a_prepared_pass(fork_checkout, capsys)

    stack = maintenance.stack()
    assert document[PreparationKey.REPARENTS] == [
        asdict(reparent) for reparent in stack.reparents()
    ]
    assert [
        context[PromotionContextKey.PULL_REQUEST_NUMBER]
        for context in document[PreparationKey.TO_SUMMARIZE]
    ] == [branch.pull_request_number for branch in stack.awaiting_promotion_link()]


def test_a_summary_written_for_every_listed_pull_request_is_what_each_opens_with(
    fork_checkout: ForkCheckout, capsys: pytest.CaptureFixture[str]
):
    a_parent_and_child(fork_checkout)
    maintenance, document = a_prepared_pass(fork_checkout, capsys)
    summaries = PromotionSummaries(
        {
            context[PromotionContextKey.PULL_REQUEST_NUMBER]: PromotionSummary(
                points=(PromotionSampleText.SUMMARY,),
                title=f"{PromotionSampleText.TITLE} {context[PromotionContextKey.PULL_REQUEST_NUMBER]}",
            )
            for context in document[PreparationKey.TO_SUMMARIZE]
        }
    )

    report = RunReportCommand().perform(maintenance, as_json=True, summaries=summaries)

    configuration = maintenance.configuration
    assert {
        promotion.pull_request_number: promotion.url for promotion in report.promoted
    } == {
        number: PromotionLink.build(
            configuration,
            next(
                context[PromotionContextKey.BRANCH]
                for context in document[PreparationKey.TO_SUMMARIZE]
                if context[PromotionContextKey.PULL_REQUEST_NUMBER] == number
            ),
            summary.title,
            summary.upstream_description(
                configuration.fork_repository.pull_request_url(number)
            ),
        ).url
        for number, summary in summaries.by_pull_request_number.items()
    }
