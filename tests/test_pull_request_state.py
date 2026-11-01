"""
Tests for :mod:`basstler.pull_request_state`: reading ``pr_data.json`` and classifying a
pull request's live state.
"""

import json
from datetime import datetime

import pytest

from basstler.pull_request_state import (
    LiveState,
    MalformedPullRequestDataError,
    MissingMergeTimestampError,
    PullRequestRecord,
)
from basstler_domain_model.github.vocabulary import (
    PullRequestState,
)
from basstler.workflow_labels import WorkflowLabel
from basstler_domain_model.github.pull_request_fields import (
    PullRequestField,
)
from basstler.maintenance.description import (
    PullRequestDescription,
    UpstreamPullRequestLink,
)
from basstler_domain_model.repository import (
    Repository,
)

from .constants.paths import DatasetLocation
from .constants.stack import StackLabel


# %% item-status and live-state labels


def test_live_state_display_labels_including_no_pull_request():
    assert LiveState.NO_PULL_REQUEST.display_label == "No pull request yet"
    assert LiveState.MERGED.display_label == "Merged"


# %% live-state classification


def test_classified_live_state_is_closed_unmerged_for_a_closed_unlabeled_pull_request():
    pull_requests_by_repository = {
        "owner/repo": {"1": PullRequestRecord(state=PullRequestState.CLOSED)}
    }
    assert (
        LiveState.classify(1, "owner/repo", pull_requests_by_repository)
        is LiveState.CLOSED_UNMERGED
    )


# %% PullRequestRecord.was_merged


def test_was_merged_true_when_github_recorded_a_merge():
    record = PullRequestRecord(
        state=PullRequestState.CLOSED, merged_at=datetime(2026, 1, 1)
    )
    assert record.was_merged


def test_was_merged_true_for_an_out_of_band_merge_marked_by_label():
    # merged_at is never set for a pull request merged by pushing its branch directly
    # and closing by hand - this repo's convention is a "merged" label instead.
    record = PullRequestRecord(
        state=PullRequestState.CLOSED, labels=["in-review", "merged"]
    )
    assert record.was_merged


def test_was_merged_false_for_a_plain_closed_pull_request():
    record = PullRequestRecord(state=PullRequestState.CLOSED, labels=["in-review"])
    assert not record.was_merged


def test_was_merged_false_for_an_open_pull_request():
    record = PullRequestRecord(state=PullRequestState.OPEN)
    assert not record.was_merged


# %% PullRequestRecord.from_mapping - the linked upstream pull request


def test_from_mapping_reads_the_upstream_pull_request_its_description_links():
    link = UpstreamPullRequestLink(Repository("an-upstream-owner", "a-project"), 7)
    body = PullRequestDescription("Prose.").with_upstream_link(link)

    record = PullRequestRecord.from_mapping(
        {
            PullRequestField.STATE.key: PullRequestState.OPEN,
            PullRequestField.BODY.key: body,
        }
    )

    assert record.upstream_link == link.url


def test_from_mapping_links_no_upstream_pull_request_without_a_description():
    assert (
        PullRequestRecord.from_mapping(
            {PullRequestField.STATE.key: PullRequestState.OPEN}
        ).upstream_link
        is None
    )


# %% PullRequestRecord.from_mapping - merge signal


def test_from_mapping_rejects_a_closed_entry_without_a_merge_timestamp():
    # An omitted key is a gatherer that never asked GitHub for merged_at, not a
    # statement that the pull request went unmerged - accepting it silently
    # reports every merged pull request as closed-unmerged.
    with pytest.raises(MissingMergeTimestampError):
        PullRequestRecord.from_mapping(
            {"state": "closed", "draft": False, "labels": ["bug"]}
        )


def test_from_mapping_accepts_a_closed_entry_whose_merge_timestamp_is_null():
    record = PullRequestRecord.from_mapping(
        {"state": "closed", "draft": False, "merged_at": None, "labels": ["bug"]}
    )
    assert record == PullRequestRecord(state=PullRequestState.CLOSED, labels=["bug"])


def test_from_mapping_accepts_an_open_entry_without_a_merge_timestamp():
    record = PullRequestRecord.from_mapping({"state": "open", "draft": True})
    assert record == PullRequestRecord(state=PullRequestState.OPEN, draft=True)


# %% PullRequestRecord.identified_labels


def test_identified_labels_recognizes_known_labels():
    record = PullRequestRecord(
        state=PullRequestState.OPEN, labels=["in-review", "merged"]
    )
    assert record.identified_labels == {
        WorkflowLabel.IN_REVIEW,
        WorkflowLabel.MERGED,
    }


def test_identified_labels_silently_excludes_an_unrecognized_label():
    record = PullRequestRecord(
        state=PullRequestState.OPEN, labels=[StackLabel.SOMEBODY_ELSES]
    )
    assert record.identified_labels == frozenset()


def test_identified_labels_empty_when_no_labels():
    record = PullRequestRecord(state=PullRequestState.OPEN)
    assert record.identified_labels == frozenset()


# %% pull request data parsing


def test_pull_request_data_parsing_parses_every_repository_and_number():
    pull_requests_by_repository = PullRequestRecord.parse_by_repository(
        {"owner/repo": {"1": {"state": "open", "draft": True}}}
    )
    assert pull_requests_by_repository["owner/repo"]["1"] == PullRequestRecord(
        state=PullRequestState.OPEN, draft=True
    )


def test_pull_request_data_parsing_reports_repository_and_number_for_a_missing_state():
    with pytest.raises(MalformedPullRequestDataError, match="owner/repo#1"):
        PullRequestRecord.parse_by_repository({"owner/repo": {"1": {"draft": True}}})


def test_pull_request_data_parsing_reports_repository_and_number_for_an_invalid_state():
    with pytest.raises(MalformedPullRequestDataError, match="owner/repo#7"):
        PullRequestRecord.parse_by_repository(
            {"owner/repo": {"7": {"state": "sideways"}}}
        )


def test_pull_request_data_parsing_reports_repository_and_number_for_a_closed_entry_without_a_merge_timestamp():
    with pytest.raises(MalformedPullRequestDataError, match="owner/repo#103"):
        PullRequestRecord.parse_by_repository(
            {"owner/repo": {"103": {"state": "closed", "draft": False}}}
        )


def test_pull_request_data_parsing_rejects_data_without_its_repository_level():
    with pytest.raises(MalformedPullRequestDataError):
        PullRequestRecord.parse_by_repository(
            json.loads(
                DatasetLocation.PULL_REQUEST_DATA_KEYED_BY_NUMBER.value.read_text()
            )
        )


def test_pull_request_data_parsing_rejects_an_entry_that_is_not_a_mapping():
    with pytest.raises(MalformedPullRequestDataError, match="owner/repo#1"):
        PullRequestRecord.parse_by_repository({"owner/repo": {"1": "open"}})
