"""
Tests for running git and for the board export the maintenance pass reads, both pure
except for the one git call.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from http import HTTPStatus
from pathlib import Path

import pytest

from basstler.repository import Repository
from basstler.stack.model import PullRequest

from basstler.locations import RepositoryStateLocation
from basstler.maintenance.board import (
    MissingPullRequestFieldError,
    PullRequestField,
    PullRequestHeadKey,
)
from basstler.upstream_reviews.vocabulary import RollupState
from basstler.maintenance.commands import BoardCommand, MaintenancePass
from basstler.maintenance.git_commands import GitCommandFailed, GitCommandRunner
from basstler.maintenance.github import GitHubRequestFailed, PullRequestReader
from basstler.maintenance.description import (
    PullRequestDescription,
    UpstreamPullRequestLink,
)

from ..constants.stack import StackBranch, StackLabel

from .fork import (
    StackPullRequestNumber,
    PullRequestHeadRepository,
    A_SESSION_LINK,
    ChecksReportedOn,
    ForkCheckout,
    HeadCommit,
    PromotionSampleText,
    an_api_record,
    an_export,
    make_configuration,
)

# %% running git


def test_a_failing_git_command_raises_rather_than_returning_nothing(
    fork_checkout: ForkCheckout,
):
    """
    ``basstler.stack``'s helper returns an empty string on failure, which here would
    make a push that did nothing indistinguishable from one that worked.
    """
    with pytest.raises(GitCommandFailed) as raised:
        fork_checkout.git.run("rev-parse", "a-ref-that-does-not-exist")

    assert raised.value.arguments == ("rev-parse", "a-ref-that-does-not-exist")
    assert raised.value.status != 0


# %% the board export


def test_every_field_is_named_by_the_key_the_api_answers_under():
    """
    A member's value is the specification's argument tuple; passing a built
    specification instead lands the whole instance in the key, where every read then
    silently finds nothing.
    """
    for field in PullRequestField:
        assert isinstance(field.key, str), field
    assert len({field.key for field in PullRequestField}) == len(list(PullRequestField))


def test_the_export_reads_each_field_out_of_the_shape_the_api_returns_it_in():
    """
    ``head`` and ``base`` arrive nested and ``labels`` as objects, so a field read the
    wrong way produces a board that is wrong rather than empty.
    """
    export = an_export(
        [
            an_api_record(
                number=StackPullRequestNumber.CHILD,
                head=StackBranch.CHILD,
                base=StackBranch.PARENT,
                draft=True,
                labels=[StackLabel.SOMEBODY_ELSES],
                body=f"see {A_SESSION_LINK}",
            )
        ]
    )

    exported = export.pull_requests[0]
    assert exported.number == StackPullRequestNumber.CHILD
    assert exported.head == StackBranch.CHILD
    assert exported.base == StackBranch.PARENT
    assert exported.draft is True
    assert exported.labels == [StackLabel.SOMEBODY_ELSES]
    assert exported.session == A_SESSION_LINK


def test_the_written_board_parses_back_into_the_records_it_was_built_from(
    tmp_path: Path,
):
    """
    The export's only contract is that ``PullRequest.read_board`` reads it, so the
    expected value is derived by reading it rather than by hand-writing the shape twice.
    """
    export = an_export(
        [
            an_api_record(
                number=StackPullRequestNumber.CHILD,
                head=StackBranch.CHILD,
                base=StackBranch.PARENT,
                draft=True,
            ),
            an_api_record(
                number=StackPullRequestNumber.PARENT,
                head=StackBranch.PARENT,
                labels=[StackLabel.SOMEBODY_ELSES],
            ),
        ]
    )
    destination = tmp_path / RepositoryStateLocation.BOARD.name

    export.write(destination)

    assert PullRequest.read_board(destination) == list(export.pull_requests)


def test_a_pull_request_missing_a_required_field_is_rejected_rather_than_defaulted():
    """
    A dropped field is what made #119's bad data indistinguishable from good data, so
    the parser refuses it at the point it enters rather than substituting a default.
    """
    record = an_api_record(number=StackPullRequestNumber.CHILD)
    del record["draft"]

    with pytest.raises(MissingPullRequestFieldError) as raised:
        an_export([record])

    assert raised.value.field_name == PullRequestField.DRAFT
    assert raised.value.pull_request_number == StackPullRequestNumber.CHILD


@pytest.mark.parametrize(
    "head_repository",
    [
        PullRequestHeadRepository.FORK.value,
        PullRequestHeadRepository.ANOTHER_CONTRIBUTORS_FORK.value,
    ],
)
def test_the_export_records_the_repository_the_head_lives_in(
    head_repository: Repository,
):
    export = an_export([an_api_record(head_repository=head_repository)])

    assert export.pull_requests[0].head_repository == head_repository.full_name


def test_a_deleted_head_repository_is_exported_as_none():
    """
    GitHub answers a deleted head repository with ``null``, which is a fact about the
    pull request rather than a dropped field.
    """
    export = an_export([an_api_record(head_repository=None)])

    assert export.pull_requests[0].head_repository is None


def test_a_head_that_does_not_say_where_it_lives_is_rejected():
    record = an_api_record(number=StackPullRequestNumber.CHILD)
    del record[PullRequestField.HEAD.key][PullRequestHeadKey.HEAD_REPOSITORY]

    with pytest.raises(MissingPullRequestFieldError) as raised:
        an_export([record])

    assert raised.value.field_name == PullRequestField.HEAD
    assert raised.value.pull_request_number == StackPullRequestNumber.CHILD


def test_every_worktree_of_a_repository_shares_one_board_outside_its_work_trees(
    fork_checkout: ForkCheckout, tmp_path: Path
):
    """
    ``board --write`` from one worktree and ``status`` from another read one file, which
    no checkout can commit and no install of the package holds.
    """
    worktree = tmp_path / "another-worktree"
    fork_checkout.git.run("worktree", "add", "--quiet", "--detach", str(worktree))

    from_the_checkout = fork_checkout.git.repository_state_path(
        RepositoryStateLocation.BOARD
    )
    from_the_worktree = GitCommandRunner(worktree).repository_state_path(
        RepositoryStateLocation.BOARD
    )

    assert from_the_checkout == fork_checkout.project_root / ".git" / (
        RepositoryStateLocation.BOARD
    )
    assert from_the_worktree == from_the_checkout


def test_the_export_reads_the_title():
    export = an_export([an_api_record(title=PromotionSampleText.TITLE)])

    assert export.pull_requests[0].title == PromotionSampleText.TITLE


def test_the_export_carries_the_description_without_the_sections_a_pass_writes():
    promoted = PullRequestDescription(PromotionSampleText.PROSE).with_promotion_link(
        PromotionSampleText.FIRST_LINK
    )
    body = PullRequestDescription(promoted).with_upstream_link(
        UpstreamPullRequestLink(
            make_configuration().upstream_repository, StackPullRequestNumber.UPSTREAM
        )
    )

    export = an_export([an_api_record(body=body)])

    assert export.pull_requests[0].description == PromotionSampleText.PROSE


def test_the_export_reads_the_linked_upstream_pull_request():
    link = UpstreamPullRequestLink(
        make_configuration().upstream_repository, StackPullRequestNumber.UPSTREAM
    )
    body = PullRequestDescription(PromotionSampleText.PROSE).with_upstream_link(link)

    export = an_export([an_api_record(body=body)])

    assert export.pull_requests[0].upstream_link == link.url


# %% what the export reads beside the fork


def test_the_export_records_the_open_upstream_pull_request_opened_from_the_branch():
    upstream_pull_request = an_api_record(
        number=StackPullRequestNumber.UPSTREAM, head=StackBranch.PARENT
    )
    export = an_export(
        [
            an_api_record(
                number=StackPullRequestNumber.PARENT, head=StackBranch.PARENT
            ),
            an_api_record(number=StackPullRequestNumber.CHILD, head=StackBranch.CHILD),
        ],
        upstream_records=[upstream_pull_request],
    )

    assert [entry.upstream_pull_request for entry in export.pull_requests] == [
        StackPullRequestNumber.UPSTREAM,
        None,
    ]


def test_an_upstream_pull_request_from_another_fork_s_same_named_branch_is_not_ours():
    export = an_export(
        [an_api_record(head=StackBranch.PARENT)],
        upstream_records=[
            an_api_record(
                number=StackPullRequestNumber.UPSTREAM,
                head=StackBranch.PARENT,
                head_repository=PullRequestHeadRepository.ANOTHER_CONTRIBUTORS_FORK.value,
            )
        ],
    )

    assert export.pull_requests[0].upstream_pull_request is None


def test_a_head_on_another_fork_is_not_matched_to_the_fork_s_upstream_pull_request():
    export = an_export(
        [
            an_api_record(
                head=StackBranch.PARENT,
                head_repository=PullRequestHeadRepository.ANOTHER_CONTRIBUTORS_FORK.value,
            )
        ],
        upstream_records=[
            an_api_record(
                number=StackPullRequestNumber.UPSTREAM, head=StackBranch.PARENT
            )
        ],
    )

    assert export.pull_requests[0].upstream_pull_request is None


def test_the_export_records_the_verdict_over_the_head_commit_s_checks():
    export = an_export(
        [
            an_api_record(head_commit=HeadCommit.CHECKED),
            an_api_record(head_commit=HeadCommit.UNCHECKED),
        ],
        checks=ChecksReportedOn({HeadCommit.CHECKED: RollupState.FAILURE}),
    )

    assert [entry.check_state for entry in export.pull_requests] == [
        RollupState.FAILURE,
        RollupState.NOT_REPORTED,
    ]


def test_a_head_that_does_not_name_its_commit_is_rejected():
    record = an_api_record(number=StackPullRequestNumber.CHILD)
    del record[PullRequestField.HEAD.key][PullRequestHeadKey.COMMIT]

    with pytest.raises(MissingPullRequestFieldError) as raised:
        an_export([record])

    assert raised.value.field_name == PullRequestField.HEAD
    assert raised.value.pull_request_number == StackPullRequestNumber.CHILD


# %% the board command


@dataclass(frozen=True)
class ListedRepository(ChecksReportedOn, PullRequestReader):
    """
    A repository answering its open pull requests and its commits' checks from fixed
    records.
    """

    records: tuple[dict, ...] = ()
    """
    Its open pull requests, as the REST API returns them.
    """

    def open_pull_requests(self) -> list[dict]:
        return list(self.records)

    def pull_request(self, number: int) -> dict:
        return next(
            record
            for record in self.records
            if record[PullRequestField.NUMBER.key] == number
        )


@dataclass(frozen=True)
class UnreadableRepository(PullRequestReader):
    """
    A repository the credential is refused reading.
    """

    def open_pull_requests(self) -> list[dict]:
        raise GitHubRequestFailed(
            status=HTTPStatus.FORBIDDEN, detail="", method="GET", path="/pulls"
        )

    def pull_request(self, number: int) -> dict:
        raise GitHubRequestFailed(
            status=HTTPStatus.FORBIDDEN, detail="", method="GET", path="/pulls"
        )


@dataclass(frozen=True)
class PassOverListedRepositories(MaintenancePass):
    """
    A pass reading the fork and the upstream from stand-ins rather than from GitHub.
    """

    listed_fork: ListedRepository = dataclasses_field(kw_only=True)
    """
    The fork the board is exported from.
    """

    listed_upstream: PullRequestReader = dataclasses_field(kw_only=True)
    """
    The upstream the fork's branches are reviewed on.
    """

    def fork(self) -> ListedRepository:
        return self.listed_fork

    def upstream(self) -> PullRequestReader:
        return self.listed_upstream


def a_pass_over(
    fork_checkout: ForkCheckout, upstream: PullRequestReader
) -> PassOverListedRepositories:
    """
    :param fork_checkout: The checkout the board is written into.
    :param upstream: The upstream to read reviews from.
    :return: A pass over one fork pull request whose head commit failed its checks.
    """
    return PassOverListedRepositories(
        configuration=make_configuration(),
        git=fork_checkout.git,
        board=fork_checkout.project_root / RepositoryStateLocation.BOARD.name,
        listed_fork=ListedRepository(
            {HeadCommit.CHECKED: RollupState.FAILURE},
            records=(
                an_api_record(
                    number=StackPullRequestNumber.PARENT,
                    head=StackBranch.PARENT,
                    head_commit=HeadCommit.CHECKED,
                ),
            ),
        ),
        listed_upstream=upstream,
    )


def test_the_written_board_carries_each_branch_s_upstream_review_and_checks(
    fork_checkout: ForkCheckout,
):
    upstream = ListedRepository(
        records=(
            an_api_record(
                number=StackPullRequestNumber.UPSTREAM, head=StackBranch.PARENT
            ),
        )
    )
    maintenance = a_pass_over(fork_checkout, upstream)

    BoardCommand().run(maintenance, argparse.Namespace(write=True))

    [exported] = PullRequest.read_board(maintenance.board)
    assert exported.upstream_pull_request == StackPullRequestNumber.UPSTREAM
    assert exported.check_state is RollupState.FAILURE


def test_an_upstream_that_cannot_be_read_stops_the_export_instead_of_guessing(
    fork_checkout: ForkCheckout,
):
    maintenance = a_pass_over(fork_checkout, UnreadableRepository())

    with pytest.raises(GitHubRequestFailed):
        BoardCommand().run(maintenance, argparse.Namespace(write=True))

    assert not maintenance.board.exists()
