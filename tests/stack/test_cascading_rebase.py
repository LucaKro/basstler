"""
Tests for driving GitHub's ``gh stack`` extension: the command lines it is run with, and
what each exit status comes to.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import pytest

from basstler.environment import GitHubCommandLineVariable, OperatingSystemVariable
from basstler.stack.cascading_rebase import (
    CascadeStepOutcome,
    GitHubStackExtension,
    StackExtensionArgument,
    StackExtensionExitCode,
)

from ..constants.stack import RemoteName, StackBranch
from ..executable_stubs import ExecutableStubDirectory
from .dataset import StackRepository

# %% the stub standing in for gh


class StackStubVariable(StrEnum):
    """
    The knobs the ``gh`` stub reads for a ``gh stack`` call.
    """

    EXIT_CODE = "STUB_GH_STACK_EXIT_CODE"
    ERROR = "STUB_GH_STACK_ERROR"
    CALL_LOG = "STUB_GH_CALL_LOG"


@pytest.fixture
def call_log(
    stub_bin: ExecutableStubDirectory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """
    Put the ``gh`` stub first on ``PATH`` and have it log every call.

    :return: The file each call is logged to.
    """
    stub_bin.install(StackExtensionArgument.EXECUTABLE)
    monkeypatch.setenv(
        OperatingSystemVariable.EXECUTABLE_SEARCH_PATH,
        stub_bin.ahead_of_inherited_path(),
    )
    log = tmp_path / "calls.txt"
    monkeypatch.setenv(StackStubVariable.CALL_LOG, str(log))
    return log


def the_extension(tmp_path: Path) -> GitHubStackExtension:
    """
    :return: The extension, run in a scratch directory against the fork.
    """
    return GitHubStackExtension(tmp_path, StackRepository.FORK.value, RemoteName.ORIGIN)


def logged_call(*arguments: str) -> str:
    """
    :param arguments: A ``gh stack`` subcommand and its arguments.
    :return: The line the stub logs for that call against the fork.
    """
    return (
        f"{GitHubCommandLineVariable.REPOSITORY}={StackRepository.FORK.value.full_name} "
        f"{StackExtensionArgument.STACK} {' '.join(arguments)}"
    )


# %% the command lines


def test_adopting_a_line_names_its_trunk_and_its_layers_bottom_first(
    call_log: Path, tmp_path: Path
):
    the_extension(tmp_path).adopt(
        StackBranch.BASE, [StackBranch.PARENT, StackBranch.CHILD]
    )

    assert call_log.read_text().splitlines() == [
        logged_call(
            StackExtensionArgument.INITIALIZE,
            StackExtensionArgument.BASE,
            StackBranch.BASE,
            StackBranch.PARENT,
            StackBranch.CHILD,
        )
    ]


def test_rebasing_up_to_a_layer_fetches_the_trunk_from_the_fork(
    call_log: Path, tmp_path: Path
):
    the_extension(tmp_path).rebase_up_to(StackBranch.CHILD)

    assert call_log.read_text().splitlines() == [
        logged_call(
            StackExtensionArgument.REBASE,
            StackExtensionArgument.DOWNSTACK,
            StackExtensionArgument.REMOTE,
            RemoteName.ORIGIN,
            StackBranch.CHILD,
        )
    ]


def test_publishing_pushes_to_the_fork(call_log: Path, tmp_path: Path):
    the_extension(tmp_path).publish()

    assert call_log.read_text().splitlines() == [
        logged_call(
            StackExtensionArgument.PUSH,
            StackExtensionArgument.REMOTE,
            RemoteName.ORIGIN,
        )
    ]


# %% what an exit status comes to


def test_a_rebase_stopping_on_a_conflict_is_told_apart_from_a_failure(
    call_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(
        StackStubVariable.EXIT_CODE, str(StackExtensionExitCode.REBASE_CONFLICT)
    )

    step = the_extension(tmp_path).rebase_up_to(StackBranch.CHILD)

    assert step.outcome is CascadeStepOutcome.CONFLICTED


def test_a_branch_already_in_a_local_stack_is_adopted_as_it_is(
    call_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(
        StackStubVariable.EXIT_CODE, str(StackExtensionExitCode.INVALID_ARGUMENTS)
    )

    step = the_extension(tmp_path).adopt(StackBranch.BASE, [StackBranch.PARENT])

    assert step.outcome is CascadeStepOutcome.ALREADY_ADOPTED


def test_any_other_failure_carries_what_the_extension_said(
    call_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    said = "a rebase is already in progress"
    monkeypatch.setenv(
        StackStubVariable.EXIT_CODE, str(StackExtensionExitCode.INVALID_ARGUMENTS)
    )
    monkeypatch.setenv(StackStubVariable.ERROR, said)

    step = the_extension(tmp_path).rebase_up_to(StackBranch.CHILD)

    assert step.outcome is CascadeStepOutcome.FAILED
    assert step.detail == said


def test_a_completed_step_carries_no_detail(call_log: Path, tmp_path: Path):
    step = the_extension(tmp_path).abort_rebase()

    assert step.outcome is CascadeStepOutcome.COMPLETED
    assert step.detail == ""
