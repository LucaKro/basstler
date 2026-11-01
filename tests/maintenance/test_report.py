"""
Tests for the report a pass leaves behind - including the pull requests it skipped -
and the exit status every command derives from it.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

import pytest

from basstler.exit_codes import StackExitCode
from basstler.repository import Repository
from basstler.stack.model import (
    PullRequest,
    SkippedPullRequest,
    SkipReason,
    Stack,
)
from basstler.stack.settings import Configuration

from basstler.locations import RepositoryStateLocation
from basstler.maintenance.board import BoardExport
from basstler.maintenance.commands import (
    BoardCommand,
    RestackCommand,
    RunReportCommand,
)
from basstler.maintenance.fast_forward import (
    FastForwardOutcome,
    FastForwardReport,
    fast_forward,
)
from basstler.maintenance.promotion import PromotionSummaries
from basstler.maintenance.report import MaintenanceReport, MaintenanceReportKey
from basstler.maintenance.restack import (
    restack,
    BranchOutcome,
    RestackOutcome,
)

from ..constants.stack import RemoteName, StackBranch

from .cascade import CascadeOfPlainRebases
from .fork import (
    StackPullRequestNumber,
    PullRequestHeadRepository,
    UPSTREAM_BASE,
    make_configuration,
    ForkCheckout,
    a_stack,
    an_api_record,
    an_export,
    a_parent_and_child,
    the_board,
)
from .recorded_fork import (
    RecordingPullRequests,
    AlreadyResolvedPass,
    run_maintenance,
)


# %% the report


def test_the_report_serialises_every_command_s_outcome(fork_checkout: ForkCheckout):
    a_parent_and_child(fork_checkout)
    fork_checkout.commit_on(
        StackBranch.PARENT,
        fork_checkout.own_file(StackBranch.PARENT),
        "the parent moved\n",
    )
    stack = a_stack(fork_checkout, the_board())

    report = MaintenanceReport.of_pass(
        stack,
        fast_forward(make_configuration(), fork_checkout.git),
        restack(
            stack, fork_checkout.git, RecordingPullRequests(), CascadeOfPlainRebases
        ),
    )
    document = json.loads(report.as_json())

    assert document["fast_forward"]["outcome"] == FastForwardOutcome.ALREADY_CURRENT
    assert {entry["branch"] for entry in document["restacked"]} == {
        StackBranch.PARENT,
        StackBranch.CHILD,
    }
    assert document["promotable"] == [StackBranch.PARENT]
    assert document["landed"] == []
    assert document["reparents"] == []


def test_a_whole_pass_leaves_no_board_behind(fork_checkout: ForkCheckout):
    """
    A board is a snapshot of one moment's open pull requests, and a later run reading a
    stale one is worse than one finding none - so a pass that has finished with it
    removes it, and the next pass starts by exporting a fresh one.

    The board is the one the pass was given, in the scratch checkout, so the run touches
    no board outside it.
    """
    a_parent_and_child(fork_checkout)
    maintenance = AlreadyResolvedPass.over(fork_checkout, the_board())
    maintenance.board.write_text("{}")

    RunReportCommand().run(maintenance, argparse.Namespace(json=True, summaries=None))

    assert not maintenance.board.exists()


# %% pull requests whose head is not on the fork


def a_board_with_a_head_outside_the_fork(
    head_repository: Repository | None,
    base: str = StackBranch.PARENT,
) -> list[PullRequest]:
    """
    :param head_repository: Where the extra pull request's head lives, ``None`` once
        deleted.
    :param base: The fork branch the extra pull request is based on.
    :return: The two-branch board plus a ready pull request whose head the fork remote
        has no copy of, read through the export as a pass reads it.
    """
    record = an_api_record(
        number=StackPullRequestNumber.ON_ANOTHER_FORK,
        head=StackBranch.ON_ANOTHER_FORK,
        base=base,
        head_repository=head_repository,
    )
    return [*the_board(), *an_export([record]).pull_requests]


def test_loading_the_stack_still_refreshes_the_fork_s_own_branches(
    fork_checkout: ForkCheckout, monkeypatch: pytest.MonkeyPatch
):
    """
    One fetch names every head, and git refuses the whole fetch over a single branch the
    fork remote does not have, which would leave every other branch stale.
    """
    a_parent_and_child(fork_checkout)
    stale = fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.PARENT)
    moved = fork_checkout.commit_on(
        StackBranch.PARENT, fork_checkout.own_file(StackBranch.PARENT), "moved\n"
    )
    fork_checkout.run_git(
        "update-ref", f"refs/remotes/{RemoteName.ORIGIN}/{StackBranch.PARENT}", stale
    )
    board = BoardExport(
        tuple(
            a_board_with_a_head_outside_the_fork(
                PullRequestHeadRepository.ANOTHER_CONTRIBUTORS_FORK.value
            )
        )
    ).write(fork_checkout.project_root / RepositoryStateLocation.BOARD.name)
    monkeypatch.setattr(Configuration, "load", make_configuration)
    monkeypatch.chdir(fork_checkout.project_root)

    Stack.load(board)

    assert (
        fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.PARENT) == moved
    )


def test_a_pass_over_a_head_on_another_fork_finishes_rather_than_failing_on_git(
    fork_checkout: ForkCheckout,
):
    """
    Observed exiting with ``git-command-failed`` against a fork carrying a pull request
    from another contributor's fork: the restack asked git for the parents of
    ``origin/<head>``, a reference the fork remote never has.
    """
    a_parent_and_child(fork_checkout)
    maintenance = AlreadyResolvedPass.over(
        fork_checkout,
        a_board_with_a_head_outside_the_fork(
            PullRequestHeadRepository.ANOTHER_CONTRIBUTORS_FORK.value
        ),
    )

    status = RunReportCommand().run(
        maintenance, argparse.Namespace(json=True, summaries=None)
    )

    assert status == StackExitCode.SUCCESS


# %% bases no open pull request has as its head


def test_loading_the_stack_fetches_a_base_no_open_pull_request_has_as_its_head(
    fork_checkout: ForkCheckout, monkeypatch: pytest.MonkeyPatch
):
    """
    Observed exiting with ``git-command-failed`` against a fork whose pull request was
    based on a long-lived branch: the restack asked git about ``origin/<base>``, which no
    fetch had brought in.
    """
    published = fork_checkout.branch_from(StackBranch.LONG_LIVED_BASE, UPSTREAM_BASE)
    fork_checkout.branch_from(StackBranch.PARENT, StackBranch.LONG_LIVED_BASE)
    fork_checkout.run_git(
        "update-ref",
        "-d",
        f"refs/remotes/{RemoteName.ORIGIN}/{StackBranch.LONG_LIVED_BASE}",
    )
    board = BoardExport(
        (
            PullRequest(
                number=StackPullRequestNumber.PARENT,
                head=StackBranch.PARENT,
                base=StackBranch.LONG_LIVED_BASE,
                draft=False,
                head_repository=PullRequestHeadRepository.FORK.value.full_name,
            ),
        )
    ).write(fork_checkout.project_root / RepositoryStateLocation.BOARD.name)
    monkeypatch.setattr(Configuration, "load", make_configuration)
    monkeypatch.chdir(fork_checkout.project_root)

    Stack.load(board)

    assert (
        fork_checkout.published_commit(RemoteName.ORIGIN, StackBranch.LONG_LIVED_BASE)
        == published
    )


@pytest.mark.parametrize(
    "head_repository, reason",
    [
        (
            PullRequestHeadRepository.ANOTHER_CONTRIBUTORS_FORK.value,
            SkipReason.HEAD_ON_ANOTHER_FORK,
        ),
        (None, SkipReason.HEAD_REPOSITORY_DELETED),
    ],
)
def test_a_head_outside_the_fork_is_reported_as_skipped_with_its_reason(
    fork_checkout: ForkCheckout, head_repository: Repository | None, reason: SkipReason
):
    a_parent_and_child(fork_checkout)
    board = a_board_with_a_head_outside_the_fork(head_repository)
    maintenance = AlreadyResolvedPass.over(fork_checkout, board)

    report = RunReportCommand().perform(
        maintenance, as_json=True, summaries=PromotionSummaries()
    )
    document = json.loads(report.as_json())

    assert document["skipped"] == [asdict(SkippedPullRequest(board[-1], reason))]
    assert StackBranch.ON_ANOTHER_FORK not in {
        entry["branch"] for entry in document["restacked"]
    }


@pytest.mark.parametrize(
    "head_repository",
    [PullRequestHeadRepository.ANOTHER_CONTRIBUTORS_FORK.value, None],
)
def test_a_head_outside_the_fork_is_never_promoted_labelled_or_commented_on(
    fork_checkout: ForkCheckout, head_repository: Repository | None
):
    """
    Based on the upstream base and out of draft, it would be promoted if it were on the
    fork, so nothing but its head's repository keeps the pass from writing to it.
    """
    a_parent_and_child(fork_checkout)
    maintenance = AlreadyResolvedPass.over(
        fork_checkout,
        a_board_with_a_head_outside_the_fork(head_repository, base=UPSTREAM_BASE),
    )

    report = RunReportCommand().perform(
        maintenance, as_json=True, summaries=PromotionSummaries()
    )

    fork = maintenance.recorded_fork
    written_to = {
        write.pull_request_number
        for write in [*fork.label_writes, *fork.comments, *fork.description_writes]
    }
    assert StackPullRequestNumber.ON_ANOTHER_FORK not in written_to
    assert StackBranch.ON_ANOTHER_FORK not in report.promotable


def test_a_head_outside_the_fork_is_summarised_as_skipped_with_its_reason(
    fork_checkout: ForkCheckout, capsys: pytest.CaptureFixture[str]
):
    a_parent_and_child(fork_checkout)
    maintenance = AlreadyResolvedPass.over(
        fork_checkout,
        a_board_with_a_head_outside_the_fork(
            PullRequestHeadRepository.ANOTHER_CONTRIBUTORS_FORK.value
        ),
    )

    RunReportCommand().perform(
        maintenance, as_json=False, summaries=PromotionSummaries()
    )

    summary = capsys.readouterr().out.splitlines()
    assert any(
        StackBranch.ON_ANOTHER_FORK in line and SkipReason.HEAD_ON_ANOTHER_FORK in line
        for line in summary
    )


# %% the exit status every command derives from what it left behind


def a_report(
    fast_forward_outcome: FastForwardOutcome = FastForwardOutcome.ALREADY_CURRENT,
    restack_outcome: RestackOutcome = RestackOutcome.PUSHED,
) -> MaintenanceReport:
    """
    :param fast_forward_outcome: What became of the fork's base branch.
    :param restack_outcome: What became of the one branch in the pass.
    :return: A report carrying exactly those two outcomes.
    """
    return MaintenanceReport(
        fast_forward=FastForwardReport(
            fast_forward_outcome, "upstream/main", "origin/main", "a-commit"
        ),
        restacked=(BranchOutcome("a-branch", "main", restack_outcome),),
        promoted=(),
        promotion_labels_cleared=(),
        reparents=(),
        landed=(),
        promotable=(),
    )


def test_a_pass_that_published_everything_is_a_success():
    assert a_report().exit_code == StackExitCode.SUCCESS
    assert (
        a_report(restack_outcome=RestackOutcome.UP_TO_DATE).exit_code
        == StackExitCode.SUCCESS
    )


def test_a_refused_fast_forward_is_never_reported_as_a_clean_pass():
    """
    Observed exiting zero against the live fork: the fork's base was left behind the
    upstream and the run said nothing was wrong, which is the one thing a status is for.
    """
    refused = a_report(fast_forward_outcome=FastForwardOutcome.REFUSED_NOT_FAST_FORWARD)

    assert refused.exit_code == StackExitCode.NOT_FAST_FORWARD


@pytest.mark.parametrize(
    "left_behind",
    [
        RestackOutcome.CONFLICT,
        RestackOutcome.WITHHELD,
        RestackOutcome.PUSH_REJECTED,
        RestackOutcome.INTEGRATION_FAILED,
    ],
)
def test_a_branch_left_unpublished_is_never_reported_as_a_clean_pass(
    left_behind: RestackOutcome,
):
    """
    Also observed against the live fork: a conflict exited zero, so a caller acting on
    the status alone would have read the pass as having nothing outstanding.
    """
    assert (
        a_report(restack_outcome=left_behind).exit_code
        == StackExitCode.BRANCH_NEEDS_ATTENTION
    )


def test_every_status_names_itself_distinctly():
    """
    The name is derived from the member rather than written beside it, so a status
    cannot end up with a name that belongs to a different one.
    """
    named = {code: code.name_for_a_caller for code in StackExitCode}

    assert named[StackExitCode.BRANCH_NEEDS_ATTENTION] == "branch-needs-attention"
    assert named[StackExitCode.SUCCESS] == "success"
    assert len(set(named.values())) == len(StackExitCode)


def test_the_report_carries_what_its_status_means(fork_checkout: ForkCheckout):
    """
    A scheduled run reads this document rather than the process status, so the meaning
    has to be in it - mapping an integer back to a meaning is a step it should not have.
    """
    document = json.loads(a_report(restack_outcome=RestackOutcome.CONFLICT).as_json())

    assert (
        document[MaintenanceReportKey.STATUS]
        == StackExitCode.BRANCH_NEEDS_ATTENTION.name_for_a_caller
    )
    assert (
        document[MaintenanceReportKey.EXIT_CODE] == StackExitCode.BRANCH_NEEDS_ATTENTION
    )


def test_a_non_zero_status_says_what_it_means_on_the_way_out(
    fork_checkout: ForkCheckout,
):
    """
    A caller reading a bare number has to look it up; the executor already knows.
    """
    fork_checkout.run_git("remote", "remove", RemoteName.UPSTREAM)

    result = run_maintenance(fork_checkout, RestackCommand())

    assert result.returncode == StackExitCode.BOARD_UNAVAILABLE
    assert "board-unavailable" in result.stderr


def test_a_clean_run_says_nothing_about_its_status(fork_checkout: ForkCheckout):
    """
    Success is the absence of news; announcing it would make every run noisy.
    """
    result = run_maintenance(fork_checkout, BoardCommand(), "--help")

    assert result.returncode == StackExitCode.SUCCESS
    assert "success" not in result.stderr
