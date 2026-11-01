"""
Tests for reading GitHub's stacks of pull requests and putting a pull request on top of
one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from basstler.exit_codes import StackExitCode
from basstler.maintenance.github import (
    GitHubRepository,
    ListingParameter,
    RepositoryPath,
    StacksApi,
)
from basstler.stack.exceptions import LayerTakenError
from basstler.stack.pull_request_stacks import (
    PullRequestStacks,
    RegisteredStack,
    StackKey,
    StackRegistration,
)

from ..constants.paths import DatasetLocation
from .dataset import StackRepository

# %% a stack as GitHub answers with it


def the_recorded_stack() -> dict[str, Any]:
    """
    :return: The recorded stack, as the stacks API answers with it.
    """
    return json.loads(DatasetLocation.GITHUB_STACK.read_text())


def test_a_stack_reads_its_number_and_its_pull_requests_bottom_first():
    recorded = the_recorded_stack()

    stack = RegisteredStack.from_json(recorded)

    assert stack.number == recorded[StackKey.NUMBER]
    assert stack.pull_requests == tuple(
        entry[StackKey.NUMBER] for entry in recorded[StackKey.PULL_REQUESTS]
    )


def test_what_is_above_a_pull_request_is_what_the_stack_carries_on_top_of_it():
    stack = RegisteredStack.from_json(the_recorded_stack())

    assert stack.above(stack.pull_requests[0]) == stack.pull_requests[1:]


# %% putting a pull request on top


@dataclass(frozen=True)
class StacksHeldInMemory(PullRequestStacks):
    """
    A repository's stacks held as a list, recording every stack created or extended.
    """

    stacks: list[RegisteredStack] = field(default_factory=list)
    """
    The stacks the repository holds.
    """

    created: list[tuple[int, ...]] = field(default_factory=list)
    """
    The pull requests of every stack created, in order.
    """

    added: list[tuple[int, tuple[int, ...]]] = field(default_factory=list)
    """
    Every stack extended, and the pull requests put on top of it.
    """

    def stack_containing(self, pull_request: int) -> RegisteredStack | None:
        return next(
            (stack for stack in self.stacks if pull_request in stack.pull_requests),
            None,
        )

    def create_stack(self, pull_requests: Sequence[int]) -> RegisteredStack:
        self.created.append(tuple(pull_requests))
        created = RegisteredStack(len(self.stacks) + 1, tuple(pull_requests))
        self.stacks.append(created)
        return created

    def add_to_stack(
        self, stack_number: int, pull_requests: Sequence[int]
    ) -> RegisteredStack:
        self.added.append((stack_number, tuple(pull_requests)))
        extended = next(stack for stack in self.stacks if stack.number == stack_number)
        replaced = replace(
            extended, pull_requests=extended.pull_requests + tuple(pull_requests)
        )
        self.stacks[self.stacks.index(extended)] = replaced
        return replaced


BOTTOM, MIDDLE, TOP = 21, 22, 23
"""
The pull requests of a line of three, bottom first.
"""


def test_a_pull_request_with_nothing_below_it_is_a_single_layer():
    stacks = StacksHeldInMemory()

    assert StackRegistration(stacks, (), BOTTOM).register() is None
    assert stacks.created == []


def test_a_line_none_of_whose_layers_is_stacked_yet_becomes_a_new_stack():
    stacks = StacksHeldInMemory()

    registered = StackRegistration(stacks, (BOTTOM, MIDDLE), TOP).register()

    assert stacks.created == [(BOTTOM, MIDDLE, TOP)]
    assert registered.pull_requests == (BOTTOM, MIDDLE, TOP)


def test_a_pull_request_goes_on_top_of_the_stack_the_layer_below_it_is_in():
    stacks = StacksHeldInMemory([RegisteredStack(1, (BOTTOM, MIDDLE))])

    registered = StackRegistration(stacks, (BOTTOM, MIDDLE), TOP).register()

    assert stacks.added == [(1, (TOP,))]
    assert registered.pull_requests == (BOTTOM, MIDDLE, TOP)


def test_registering_a_pull_request_already_on_its_stack_changes_nothing():
    existing = RegisteredStack(1, (BOTTOM, MIDDLE, TOP))
    stacks = StacksHeldInMemory([existing])

    registered = StackRegistration(stacks, (BOTTOM, MIDDLE), TOP).register()

    assert registered == existing
    assert stacks.created == []
    assert stacks.added == []


def test_a_layer_another_pull_request_is_already_stacked_on_is_refused():
    """
    A stack is a single line, so the pull request below can carry only one.
    """
    stacks = StacksHeldInMemory([RegisteredStack(1, (BOTTOM, MIDDLE))])

    with pytest.raises(LayerTakenError) as refused:
        StackRegistration(stacks, (BOTTOM,), TOP).register()

    assert refused.value.below == BOTTOM
    assert refused.value.above == (MIDDLE,)
    assert refused.value.exit_code is StackExitCode.LAYER_TAKEN
    assert stacks.added == []


# %% the stacks endpoints


@dataclass(frozen=True)
class StacksEndpointsAnsweringFromMemory(GitHubRepository):
    """
    A repository client recording every call it is asked to make, with the API version
    it was made under, and answering with the recorded stack.
    """

    calls: list[tuple[str, str, Mapping[str, Any] | None, str | None]] = field(
        default_factory=list
    )
    """
    Every call made: its method, path, payload and API version.
    """

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Any:
        self.calls.append((method, path, payload, self.api_version))
        recorded = the_recorded_stack()
        return [recorded] if method == "GET" else recorded


def a_stacks_client() -> StacksEndpointsAnsweringFromMemory:
    """
    :return: A client for the fork answering from the recorded stack.
    """
    return StacksEndpointsAnsweringFromMemory(
        repository=StackRepository.FORK.value, token=""
    )


def test_finding_a_pull_request_s_stack_asks_the_stacks_listing_for_it():
    client = a_stacks_client()

    found = client.stack_containing(BOTTOM)

    assert client.calls == [
        (
            "GET",
            f"/repos/{StackRepository.FORK.value}{RepositoryPath.STACKS}"
            f"?{ListingParameter.PULL_REQUEST}={BOTTOM}",
            None,
            StacksApi.VERSION,
        )
    ]
    assert found == RegisteredStack.from_json(the_recorded_stack())


def test_creating_a_stack_names_its_pull_requests_bottom_first():
    client = a_stacks_client()

    client.create_stack([BOTTOM, MIDDLE])

    assert client.calls == [
        (
            "POST",
            f"/repos/{StackRepository.FORK.value}{RepositoryPath.STACKS}",
            {StacksApi.PULL_REQUESTS: [BOTTOM, MIDDLE]},
            StacksApi.VERSION,
        )
    ]


def test_extending_a_stack_adds_to_the_stack_it_names():
    client = a_stacks_client()
    number = RegisteredStack.from_json(the_recorded_stack()).number

    client.add_to_stack(number, [TOP])

    assert client.calls == [
        (
            "POST",
            f"/repos/{StackRepository.FORK.value}{RepositoryPath.STACKS}/{number}/"
            f"{StacksApi.ADD}",
            {StacksApi.PULL_REQUESTS: [TOP]},
            StacksApi.VERSION,
        )
    ]


def test_every_other_call_is_made_under_github_s_default_version():
    client = a_stacks_client()

    client.pull_request(BOTTOM)

    assert client.calls[0][-1] is None
