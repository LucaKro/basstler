"""
Every error a command reports to its caller ends that command with a status of its own,
and every status is an :class:`~basstler.exit_codes.ExitCode`.

The errors are discovered by importing every module of the package, so a new one cannot
be left out of this check by not being listed here.
"""

from __future__ import annotations

import inspect

import pytest

from basstler_domain_model.git.exceptions import (
    GitCommandFailed,
    UnresolvableReferenceError,
)
from basstler_domain_model.github.exceptions import (
    ContradictoryLabelWriteError,
    GitHubCredentialUnavailableError,
    GitHubRequestFailed,
    MissingPullRequestFieldError,
)
from basstler_domain_model.github.pull_request_fields import PullRequestField

from basstler.exit_codes import (
    CommandFailure,
    DomainModelFailure,
    ExitCode,
    StackExitCode,
)

from .command_failures import command_failure_kinds, command_failures


@pytest.mark.parametrize(
    "failure", command_failure_kinds(), ids=lambda failure: failure.__qualname__
)
def test_every_error_nothing_extends_can_be_raised(failure: type[CommandFailure]):
    """
    An error with no status of its own stays abstract, and raising it would fail with a
    ``TypeError`` instead of reporting what went wrong.

    Only a kind other errors extend may leave the status to them.
    """
    assert failure.__subclasses__() or not inspect.isabstract(failure)


@pytest.mark.parametrize(
    "failure", command_failures(), ids=lambda failure: failure.__qualname__
)
def test_every_command_failure_declares_its_status(failure: type[CommandFailure]):
    assert isinstance(failure.exit_code, ExitCode)


def test_no_status_is_a_failure():
    """
    Ending a command on an error with status 0 would tell its caller it succeeded.
    """
    assert all(failure.exit_code != 0 for failure in command_failures())


# %% the domain model's errors


@pytest.mark.parametrize(
    "failure, error",
    [
        (DomainModelFailure.GIT_COMMAND_FAILED, GitCommandFailed(status=1, detail="")),
        (
            DomainModelFailure.UNRESOLVABLE_REFERENCE,
            UnresolvableReferenceError(reference="a-branch"),
        ),
        (
            DomainModelFailure.GITHUB_REQUEST_REFUSED,
            GitHubRequestFailed(status=403, detail=""),
        ),
        (
            DomainModelFailure.CREDENTIAL_UNAVAILABLE,
            GitHubCredentialUnavailableError(variables=()),
        ),
        (
            DomainModelFailure.PULL_REQUEST_FIELD_MISSING,
            MissingPullRequestFieldError(PullRequestField.HEAD, 1),
        ),
        (
            DomainModelFailure.CONTRADICTORY_LABEL_WRITE,
            ContradictoryLabelWriteError(labels=("a-label",)),
        ),
    ],
    ids=lambda value: value._name_ if isinstance(value, DomainModelFailure) else "",
)
def test_a_domain_model_error_ends_a_command_with_its_own_status(
    failure: DomainModelFailure, error: Exception
):
    assert DomainModelFailure.exit_code_of(error) is failure.exit_code


@pytest.mark.parametrize("failure", list(DomainModelFailure), ids=lambda f: f._name_)
def test_no_domain_model_error_ends_a_command_as_a_success(
    failure: DomainModelFailure,
):
    assert failure.exit_code is not StackExitCode.SUCCESS
