"""
Tests for opening an item's branch and draft pull request.
"""

from __future__ import annotations

import pytest
import yaml

from basstler.plan.item_bootstrap.exceptions import (
    BranchAlreadyPublishedError,
    PullRequestDetailsMissingError,
    PullRequestRefusedError,
    UnknownItemError,
)
from basstler.plan.item_bootstrap.opening import WorkOpener
from basstler.plan.item_bootstrap.vocabulary import PlanItemBootstrapExitCode
from basstler.plan.model import (
    ItemStatus,
    ManifestKey,
    PlanDocument,
)
from basstler_domain_model.repository import (
    Repository,
)

from ...constants.notes import ScratchBranch
from ...scratch_repository import ScratchRepository
from .bootstrap_scenario import (
    NEW_BRANCH,
    PLAN_MANIFEST,
    SESSION_URL,
    RecordingPullRequestOpener,
    RefusingPullRequestOpener,
    manifest_line,
    open_request,
    published_plan,
)

# %% opening the work


def test_opening_writes_the_branch_pull_request_and_session_onto_the_item(
    bootstrap_repository: ScratchRepository,
):
    opener = RecordingPullRequestOpener(number=143)

    result = WorkOpener(bootstrap_repository.project_root).open(
        open_request(),
        pull_request_opener=opener,
    )

    assert result.exit_code is PlanItemBootstrapExitCode.SUCCESS
    assert result.pull_request_number == 143
    manifest = published_plan(bootstrap_repository)[PlanDocument.MANIFEST]
    for written_key, value in (
        (ManifestKey.BRANCH, NEW_BRANCH),
        (ManifestKey.PULL_REQUEST_NUMBER, 143),
        (ManifestKey.SESSION, SESSION_URL),
        (ManifestKey.STATUS, ItemStatus.IN_PROGRESS.value),
    ):
        assert manifest_line(written_key, value) in manifest


def test_opening_asks_for_a_draft_pull_request_against_the_plans_repository(
    bootstrap_repository: ScratchRepository,
):
    opener = RecordingPullRequestOpener()

    WorkOpener(bootstrap_repository.project_root).open(
        open_request(),
        pull_request_opener=opener,
    )

    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.draft is True
    assert request.repository == Repository.parse(
        yaml.safe_load(PLAN_MANIFEST)[ManifestKey.DEFAULT_REPOSITORY.key]
    )
    assert request.head == NEW_BRANCH
    assert request.base == ScratchBranch.WORK


def test_opening_publishes_the_branch_to_the_repositorys_own_remote(
    bootstrap_repository: ScratchRepository,
):
    WorkOpener(bootstrap_repository.project_root).open(
        open_request(),
        pull_request_opener=RecordingPullRequestOpener(),
    )

    published = bootstrap_repository.run_git(
        "ls-remote",
        "--heads",
        str(bootstrap_repository.work_remote_path),
        NEW_BRANCH,
    )
    assert NEW_BRANCH in published.stdout


def test_opening_an_already_published_branch_is_refused(
    bootstrap_repository: ScratchRepository,
):
    opener = RecordingPullRequestOpener()
    WorkOpener(bootstrap_repository.project_root).open(
        open_request(),
        pull_request_opener=opener,
    )

    with pytest.raises(BranchAlreadyPublishedError) as refusal:
        WorkOpener(bootstrap_repository.project_root).open(
            open_request(),
            pull_request_opener=opener,
        )
    assert refusal.value.branch == NEW_BRANCH
    assert len(opener.requests) == 1


def test_opening_an_unknown_item_is_refused_before_anything_is_created(
    bootstrap_repository: ScratchRepository,
):
    opener = RecordingPullRequestOpener()

    with pytest.raises(UnknownItemError):
        WorkOpener(bootstrap_repository.project_root).open(
            open_request(item_identifier="no-such-item"),
            pull_request_opener=opener,
        )

    assert opener.requests == []
    published = bootstrap_repository.run_git(
        "ls-remote", "--heads", str(bootstrap_repository.work_remote_path)
    )
    assert NEW_BRANCH not in published.stdout


def test_a_refused_pull_request_leaves_the_manifest_untouched(
    bootstrap_repository: ScratchRepository,
):
    with pytest.raises(PullRequestRefusedError):
        WorkOpener(bootstrap_repository.project_root).open(
            open_request(),
            pull_request_opener=RefusingPullRequestOpener(),
        )

    assert published_plan(bootstrap_repository)[PlanDocument.MANIFEST] == PLAN_MANIFEST


def test_a_refused_pull_request_leaves_the_branch_it_already_published(
    bootstrap_repository: ScratchRepository,
):
    with pytest.raises(PullRequestRefusedError):
        WorkOpener(bootstrap_repository.project_root).open(
            open_request(),
            pull_request_opener=RefusingPullRequestOpener(),
        )

    published = bootstrap_repository.run_git(
        "ls-remote",
        "--heads",
        str(bootstrap_repository.work_remote_path),
        NEW_BRANCH,
    )
    assert NEW_BRANCH in published.stdout


def test_a_supplied_pull_request_number_is_recorded_without_creating_one(
    bootstrap_repository: ScratchRepository,
):
    opener = RecordingPullRequestOpener()

    result = WorkOpener(bootstrap_repository.project_root).open(
        open_request(pull_request_number=57),
        pull_request_opener=opener,
    )

    assert opener.requests == []
    assert result.pull_request_number == 57
    assert (
        manifest_line(ManifestKey.PULL_REQUEST_NUMBER, 57)
        in published_plan(bootstrap_repository)[PlanDocument.MANIFEST]
    )


def test_creating_a_pull_request_without_a_title_or_body_is_refused_before_publishing(
    bootstrap_repository: ScratchRepository,
):
    with pytest.raises(PullRequestDetailsMissingError):
        WorkOpener(bootstrap_repository.project_root).open(
            open_request(pull_request_title=None, pull_request_body=None),
            pull_request_opener=RecordingPullRequestOpener(),
        )

    published = bootstrap_repository.run_git(
        "ls-remote", "--heads", str(bootstrap_repository.work_remote_path)
    )
    assert NEW_BRANCH not in published.stdout


def test_a_supplied_pull_request_number_adopts_the_branch_its_caller_published(
    bootstrap_repository: ScratchRepository,
):
    WorkOpener(bootstrap_repository.project_root).open(
        open_request(),
        pull_request_opener=RecordingPullRequestOpener(number=99),
    )

    result = WorkOpener(bootstrap_repository.project_root).open(
        open_request(pull_request_number=57),
        pull_request_opener=RecordingPullRequestOpener(),
    )

    assert result.pull_request_number == 57
