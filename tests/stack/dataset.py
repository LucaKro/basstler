"""
The repositories, sample text, configuration and in-memory stack the stack suites build
their cases from.
"""

from __future__ import annotations

from collections.abc import Container
from enum import Enum, IntEnum, StrEnum
from urllib.parse import parse_qs as parse_query_string
from urllib.parse import urlsplit as split_url

from basstler.repository import Repository
from basstler.stack.model import (
    PullRequest,
    Stack,
)
from basstler.stack.promotion_link import PromotionLinkParameter
from basstler.stack.settings import Configuration

from ..constants.stack import RemoteName, StackBranch, StackLabel


# %% shared stack inputs


class StackRepository(Enum):
    """
    The repositories the stack suites' checkouts and configurations name.
    """

    FORK = Repository("a-fork-owner", "a-fork")
    """
    The fork a scratch checkout's remote points at.
    """

    SOMEBODY_ELSES_FORK = Repository("someone-else", "their-fork")
    """
    A fork other than the one the configuration names.
    """

    SAME_NAMED_FORK = Repository("a-fork-owner", "a-project")
    """
    A fork named like the upstream it was forked from.
    """

    ANOTHER_OWNERS_FORK = Repository("another-owner", "a-project")
    """
    A second fork of the upstream, beside :attr:`SAME_NAMED_FORK`.
    """

    UPSTREAM = Repository("an-upstream-owner", "a-project")
    """
    The upstream every fork here is forked from.
    """

    DIFFERENT_UPSTREAM = Repository("a-different-upstream", "a-project")
    """
    An upstream named on the command line in place of the committed one.
    """


class UpstreamPullRequestNumber(IntEnum):
    """
    The upstream pull requests the stack suites open from a fork branch.
    """

    OPEN = 101
    """
    An upstream pull request still open for review.
    """


class StackSampleText(StrEnum):
    """
    The text the stack suites feed in where only its shape matters.
    """

    SESSION_LINK = "https://claude.ai/code/session_x"
    """
    The session a pull request was opened from.
    """

    TITLE = "A title"
    """
    A promotion link's title.
    """

    BODY = "A body"
    """
    A promotion link's description.
    """

    SHORT_BODY = "short"
    """
    A description well within the link's length limit.
    """

    TITLE_WITH_SEPARATORS = "Fix A & B"
    """
    A title holding a character that would end a query parameter unencoded.
    """

    BODY_WITH_FRAGMENT = "see #106"
    """
    A description holding a character that would start a URL fragment unencoded.
    """

    FILLER = "x"
    """
    One character, repeated to make a text too long for a link.
    """

    LOCAL_PATH_REMOTE = "/srv/git/mirror"
    """
    A remote URL that is a local path rather than a repository.
    """

    UNRELATED_FILE = "README.md"
    """
    A notes-branch file that is not the stack configuration.
    """

    UNRELATED_CONTENT = "unrelated\n"
    """
    Its content.
    """

    UNKNOWN_COMMAND = "not-a-command"
    """
    A command the tool does not answer.
    """

    NOT_A_REPOSITORY = "not-a-repository"
    """
    A repository reference missing its ``owner/`` part.
    """


def make_configuration() -> Configuration:
    return Configuration(
        in_review_label=StackLabel.IN_REVIEW,
        needs_resolution_label=StackLabel.NEEDS_RESOLUTION,
        fork_repository=StackRepository.FORK.value,
        fork_remote=RemoteName.ORIGIN,
        upstream_repository=StackRepository.UPSTREAM.value,
        upstream_remote=RemoteName.UPSTREAM,
        upstream_base=StackBranch.BASE,
        upstream_location=RemoteName.UPSTREAM,
    )


def build(
    pull_requests: list[PullRequest], merged: Container[str] = frozenset()
) -> Stack:
    """
    :param pull_requests: The board entries to derive the stack from.
    :param merged: The branches that have already landed upstream.
    :return: The stack those entries describe.
    """
    return Stack.build(make_configuration(), pull_requests, lambda name: name in merged)


# %% reading a compare-and-create link


def prefilled(url: str, parameter: PromotionLinkParameter) -> str:
    """
    :param url: A compare-and-create link.
    :param parameter: The prefill to read out of it.
    :return: That prefill, decoded.
    """
    return parse_query_string(split_url(url).query)[parameter][0]


# %% a whole stack through its lifecycle


def a_deep_stack_beside_an_independent_branch(
    approved: Container[str] = frozenset(),
    promoted: Container[str] = frozenset(),
    withheld: Container[str] = frozenset(),
    landed: Container[str] = frozenset(),
):
    """
    Two independent stacks off `main`, the first three deep and the second a single
    branch.

    :param approved: Branches the developer has un-drafted.
    :param promoted: Branches with an open upstream pull request.
    :param withheld: Branches delegated for conflict resolution.
    :param landed: Branches that are ancestors of the upstream base.
    :return: The stack as the tooling derives it.
    """
    deep = [
        (StackBranch.ENGINE, StackBranch.BASE),
        (StackBranch.ENGINE_UI, StackBranch.ENGINE),
        (StackBranch.ENGINE_DOCS, StackBranch.ENGINE_UI),
    ]
    aside = [(StackBranch.PARSER, StackBranch.BASE)]
    pull_requests = [
        PullRequest(
            number,
            name,
            parent,
            draft=name not in approved,
            labels=[StackLabel.NEEDS_RESOLUTION] if name in withheld else [],
            upstream_pull_request=(
                UpstreamPullRequestNumber.OPEN if name in promoted else None
            ),
            head_repository=StackRepository.FORK.value.full_name,
        )
        for number, (name, parent) in enumerate([*deep, *aside], start=1)
    ]
    return build(pull_requests, merged=landed)
