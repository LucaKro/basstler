"""
Tests for the stack helper's command line, whose exit status a caller acts on, and the
status table it prints.
"""

from __future__ import annotations

import subprocess

import pytest

import basstler.stack.__main__
from basstler.exit_codes import StackExitCode
from basstler.stack.configuration import StackSetting
from basstler.stack.__main__ import Command, CommandOption
from basstler.stack.promotion_link import PromotionLink
from basstler.stack.model import PullRequest
from basstler.stack.report import StackReport, StatusColumn
from basstler.stack.settings import PrintedConfiguration

from basstler.locations import ProjectLocation
from basstler_domain_model.github.vocabulary import (
    RollupState,
)

from ..constants.paths import DatasetLocation
from ..constants.stack import RemoteName, StackBranch, StackLabel
from ..scratch_repository import ScratchRepository
from ..script_runner import PythonModuleRunner

from .dataset import (
    StackRepository,
    StackSampleText,
    build,
    make_configuration,
)


# %% the command line a caller acts on the exit status of


@pytest.fixture
def offline_checkout(scratch_repository: ScratchRepository) -> ScratchRepository:
    """
    A checkout naming its upstream, whose personal-notes remote is a local bare
    repository.

    Resolving configuration fetches that branch, so pointing it at a local path is what
    keeps these tests off the network.

    :param scratch_repository: The scratch repository to point.
    :return: The same repository, now resolving its notes remote locally.
    """
    scratch_repository.resolve_notes_remote_to()
    scratch_repository.write(
        ProjectLocation.PROJECT_STACK_CONFIGURATION,
        DatasetLocation.SCRATCH_FORK_STACK_CONFIGURATION.read_text(),
    )
    return scratch_repository


def run_stack(
    checkout: ScratchRepository, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """
    Invoke the tool as a caller does, so its exit status is exercised rather than
    assumed.

    :param checkout: The repository to run in.
    :param arguments: The command and its flags.
    :return: The finished subprocess.
    """
    return PythonModuleRunner(
        project_root=checkout.project_root, module_name=basstler.stack.__main__.__name__
    ).run(*arguments)


def test_an_unknown_command_is_a_usage_error(offline_checkout: ScratchRepository):
    assert (
        run_stack(offline_checkout, StackSampleText.UNKNOWN_COMMAND).returncode
        == StackExitCode.USAGE
    )


def test_a_label_write_prints_the_complete_set_one_label_per_line(
    offline_checkout: ScratchRepository,
):
    """
    One per line rather than a joined list: a label may contain whatever a separator
    would have been.
    """
    result = run_stack(
        offline_checkout,
        Command.LABELS,
        CommandOption.CURRENT,
        StackLabel.IN_REVIEW,
        CommandOption.CURRENT,
        StackLabel.BUG,
        CommandOption.ADD,
        StackLabel.REBASE,
    )

    assert result.returncode == StackExitCode.SUCCESS
    assert result.stdout.splitlines() == [
        StackLabel.IN_REVIEW,
        StackLabel.BUG,
        StackLabel.REBASE,
    ]


def test_a_contradictory_label_write_is_refused_rather_than_guessed_at(
    offline_checkout: ScratchRepository,
):
    result = run_stack(
        offline_checkout,
        Command.LABELS,
        CommandOption.ADD,
        StackLabel.REBASE,
        CommandOption.REMOVE,
        StackLabel.REBASE,
    )

    assert result.returncode == StackExitCode.USAGE
    assert StackLabel.REBASE in result.stderr


def test_a_checkout_whose_fork_cannot_be_identified_says_so_by_status(
    offline_checkout: ScratchRepository,
):
    """
    A caller has to tell "I cannot know which remote is yours" from every other failure
    without reading stderr, because that one is the question it can put to the user.
    """
    result = run_stack(offline_checkout, Command.CONFIGURATION)

    assert result.returncode == StackExitCode.REMOTES_UNRESOLVED


def test_a_project_naming_no_upstream_says_so_by_status(
    offline_checkout: ScratchRepository,
):
    """
    The upstream is the one setting only the project knows, and its absence is a setup
    step a caller can name, not a failure to read the checkout.
    """
    (
        offline_checkout.project_root / ProjectLocation.PROJECT_STACK_CONFIGURATION
    ).unlink()
    offline_checkout.add_remote(RemoteName.MINE, StackRepository.FORK.value.clone_url)

    result = run_stack(offline_checkout, Command.CONFIGURATION)

    assert result.returncode == StackExitCode.UPSTREAM_UNCONFIGURED
    assert str(ProjectLocation.PROJECT_STACK_CONFIGURATION) in result.stderr
    assert str(ProjectLocation.PERSONAL_STACK_CONFIGURATION) in result.stderr


def test_a_readable_checkout_with_no_board_reports_the_missing_board_instead(
    offline_checkout: ScratchRepository,
):
    offline_checkout.add_remote(RemoteName.MINE, StackRepository.FORK.value.clone_url)

    result = run_stack(offline_checkout, Command.STATUS)

    assert result.returncode == StackExitCode.BOARD_UNAVAILABLE


def test_a_named_fork_and_upstream_are_used_where_inference_would_have_refused(
    offline_checkout: ScratchRepository,
):
    """
    Two candidate remotes are ambiguous, so a caller that already knows the answer must
    be able to say so rather than be blocked by the guess it does not need.
    """
    offline_checkout.add_remote(RemoteName.MINE, StackRepository.FORK.value.clone_url)
    offline_checkout.add_remote(
        RemoteName.THEIRS, StackRepository.SOMEBODY_ELSES_FORK.value.clone_url
    )

    assert (
        run_stack(offline_checkout, Command.CONFIGURATION).returncode
        == StackExitCode.REMOTES_UNRESOLVED
    )

    result = run_stack(
        offline_checkout,
        Command.CONFIGURATION,
        CommandOption.FORK,
        StackRepository.FORK.value.full_name,
        CommandOption.UPSTREAM,
        StackRepository.UPSTREAM.value.full_name,
    )

    assert result.returncode == StackExitCode.SUCCESS
    printed = PrintedConfiguration.read(result.stdout)
    assert printed[StackSetting.FORK_REPOSITORY] == StackRepository.FORK.value.full_name
    assert printed[StackSetting.FORK_REMOTE] == RemoteName.MINE
    assert (
        printed[StackSetting.UPSTREAM_REPOSITORY]
        == StackRepository.UPSTREAM.value.full_name
    )


def test_a_fork_that_is_not_owner_and_name_is_a_usage_error(
    offline_checkout: ScratchRepository,
):
    result = run_stack(
        offline_checkout,
        Command.CONFIGURATION,
        CommandOption.FORK,
        StackSampleText.NOT_A_REPOSITORY,
    )

    assert result.returncode == StackExitCode.USAGE


def test_a_promotion_link_is_built_from_the_resolved_repositories(
    offline_checkout: ScratchRepository,
):
    offline_checkout.add_remote(RemoteName.MINE, StackRepository.FORK.value.clone_url)

    result = run_stack(
        offline_checkout,
        Command.PROMOTION_LINK,
        CommandOption.BRANCH,
        StackBranch.ENGINE,
        CommandOption.TITLE,
        StackSampleText.TITLE,
    )

    assert result.returncode == StackExitCode.SUCCESS
    assert result.stdout.strip() == (
        PromotionLink.build(
            make_configuration(),
            StackBranch.ENGINE,
            StackSampleText.TITLE,
            "",
        ).url
    )


# %% status table


def test_status_rows_start_each_cell_where_its_heading_starts():
    columns = list(StatusColumn)
    heading = StatusColumn.line(*columns)
    row = StatusColumn.line(*range(len(columns)))
    assert [heading.index(column) for column in columns] == [
        row.index(str(position)) for position in range(len(columns))
    ]


def a_ready_branch_whose_checks_failed() -> PullRequest:
    """
    :return: An approved branch on the base whose checks failed.
    """
    return PullRequest(
        1,
        StackBranch.FEATURE,
        StackBranch.BASE,
        draft=False,
        check_state=RollupState.FAILURE,
        head_repository=StackRepository.FORK.value.full_name,
    )


def test_the_status_table_shows_the_verdict_over_each_branch_s_checks(
    capsys: pytest.CaptureFixture[str],
):
    StackReport(build([a_ready_branch_whose_checks_failed()])).print_status()

    heading = StatusColumn.line(*StatusColumn)
    row = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith(StackBranch.FEATURE)
    )
    assert row[heading.index(StatusColumn.CHECKS) :].split()[0] == RollupState.FAILURE


# %% the next branches to promote


def test_a_branch_withheld_for_its_checks_is_named_with_their_verdict(
    capsys: pytest.CaptureFixture[str],
):
    StackReport(build([a_ready_branch_whose_checks_failed()])).print_next()

    printed = capsys.readouterr().out
    assert f"{StackBranch.FEATURE} ({RollupState.FAILURE})" in printed
