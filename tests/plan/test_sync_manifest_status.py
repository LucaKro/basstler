"""
Tests for plan/sync_manifest_status.py: auto-correcting a plan.yaml's item statuses to
"done" wherever GitHub confirms the item's pull request is merged.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from basstler.dashboard.plan import Plan
from basstler.exceptions import (
    InvalidSchemaVersion,
    MissingPlanDirectory,
    MissingStatusLineError,
    PlanValidationError,
)
from basstler.plan.model import ItemStatus, ManifestKey
from basstler.plan.validation import PlanValidator
from basstler.pull_request_state import (
    PullRequestRecord,
)
from basstler_domain_model.github.vocabulary import (
    PullRequestState,
)
from basstler.plan.model import PlanDocument
from basstler.plan.sync_manifest_status import (
    ManifestText,
    StatusCorrection,
    main,
)

from ..constants.paths import DatasetLocation
from ..fixture_plans import (
    FixturePlanId,
    ForeignItemId,
    PLAN_UNDER_TEST_ID,
    foreign_reference,
)


def plan(**overrides: Any) -> dict[str, Any]:
    """
    Build one raw, plan.yaml-shaped ``dict`` for a test - the same shape
    ``yaml.safe_load`` would produce, since :meth:`StatusCorrection.items_to_correct`
    operates directly on that raw structure, never on a parsed :class:`Plan`.
    """
    data = {
        ManifestKey.SCHEMA_VERSION.key: InvalidSchemaVersion.supported_version,
        ManifestKey.IDENTIFIER.key: PLAN_UNDER_TEST_ID,
        ManifestKey.TITLE.key: "Test Plan",
        ManifestKey.DESCRIPTION.key: "A plan.",
        ManifestKey.DEFAULT_REPOSITORY.key: "owner/repo",
        ManifestKey.WAVES.key: [
            {ManifestKey.IDENTIFIER.key: "wave-1", ManifestKey.NAME.key: "Wave 1"}
        ],
        ManifestKey.TRACKS.key: [
            {
                ManifestKey.IDENTIFIER.key: "track-1",
                ManifestKey.NAME.key: "Track 1",
                ManifestKey.WAVE.key: "wave-1",
            }
        ],
        ManifestKey.ITEMS.key: [],
    }
    data.update(overrides)
    return data


def item(
    identifier: str,
    status: str,
    pull_request_number: int | None = None,
    repository: str | None = None,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    """
    Build one raw, plan.yaml-shaped item ``dict`` for a test.

    ``status`` is a plain ``str``, not :class:`ItemStatus`: this mirrors exactly what
    ``yaml.safe_load`` hands back before any parsing into typed dataclasses happens,
    since :meth:`StatusCorrection.items_to_correct` and
    :meth:`ManifestText.correct_statuses` both work directly on that raw structure.
    """
    entry = {
        ManifestKey.IDENTIFIER.key: identifier,
        ManifestKey.TITLE.key: identifier,
        ManifestKey.BRANCH.key: identifier,
        ManifestKey.TRACK.key: "track-1",
        ManifestKey.STATUS.key: status,
        ManifestKey.PULL_REQUEST_NUMBER.key: pull_request_number,
    }
    if repository is not None:
        entry[ManifestKey.REPOSITORY.key] = repository
    if depends_on is not None:
        entry[ManifestKey.DEPENDS_ON.key] = depends_on
    return entry


# %% finding items to correct


def test_finds_an_in_progress_item_whose_pull_request_is_merged():
    pull_requests_by_repository = {
        "owner/repo": {
            "1": PullRequestRecord(
                state=PullRequestState.CLOSED, merged_at=datetime(2026, 1, 1)
            )
        }
    }
    items = [item("a", "in_progress", pull_request_number=1)]
    corrections = StatusCorrection.items_to_correct(
        plan(items=items), pull_requests_by_repository
    )
    assert [entry[ManifestKey.IDENTIFIER.key] for entry in corrections] == ["a"]


def test_ignores_an_item_already_marked_done():
    pull_requests_by_repository = {
        "owner/repo": {
            "1": PullRequestRecord(
                state=PullRequestState.CLOSED, merged_at=datetime(2026, 1, 1)
            )
        }
    }
    items = [item("a", "done", pull_request_number=1)]
    assert (
        StatusCorrection.items_to_correct(
            plan(items=items), pull_requests_by_repository
        )
        == []
    )


def test_ignores_an_item_whose_pull_request_is_still_open():
    pull_requests_by_repository = {
        "owner/repo": {"1": PullRequestRecord(state=PullRequestState.OPEN, draft=False)}
    }
    items = [item("a", "in_progress", pull_request_number=1)]
    assert (
        StatusCorrection.items_to_correct(
            plan(items=items), pull_requests_by_repository
        )
        == []
    )


def test_ignores_an_item_with_no_pull_request_yet():
    items = [item("a", "not_started")]
    assert StatusCorrection.items_to_correct(plan(items=items), {}) == []


def test_merged_via_out_of_band_label_is_also_corrected():
    pull_requests_by_repository = {
        "owner/repo": {
            "1": PullRequestRecord(state=PullRequestState.CLOSED, labels=["merged"])
        }
    }
    items = [item("a", "blocked", pull_request_number=1)]
    corrections = StatusCorrection.items_to_correct(
        plan(items=items), pull_requests_by_repository
    )
    assert [entry[ManifestKey.IDENTIFIER.key] for entry in corrections] == ["a"]


def test_uses_the_item_repository_override_over_the_plan_default():
    pull_requests_by_repository = {
        "owner/other-repo": {
            "1": PullRequestRecord(
                state=PullRequestState.CLOSED, merged_at=datetime(2026, 1, 1)
            )
        }
    }
    items = [
        item("a", "in_progress", pull_request_number=1, repository="owner/other-repo")
    ]
    corrections = StatusCorrection.items_to_correct(
        plan(items=items), pull_requests_by_repository
    )
    assert [entry[ManifestKey.IDENTIFIER.key] for entry in corrections] == ["a"]


# %% applying corrections - real manifest text

# A real plan.yaml's raw text (two items, one due for correction) - loaded
# once and shared read-only by every test below, since each test patches its own
# ManifestText parsed from it.
MANIFEST_TEXT = (DatasetLocation.DIRECTORY / "manifest.yaml").read_text()


def test_patches_only_the_targeted_items_status_line():
    data = yaml.safe_load(MANIFEST_TEXT)
    manifest_text = ManifestText.parse(MANIFEST_TEXT)
    corrections = manifest_text.correct_statuses([data[ManifestKey.ITEMS.key][0]])
    patched_text = manifest_text.text
    assert "    status: done" in patched_text
    assert "    status: not_started" in patched_text  # item b untouched
    assert [c.item_identifier for c in corrections] == ["a"]
    assert [c.previous_status for c in corrections] == [ItemStatus.IN_PROGRESS]


def test_patching_leaves_every_other_line_byte_for_byte_identical():
    data = yaml.safe_load(MANIFEST_TEXT)
    manifest_text = ManifestText.parse(MANIFEST_TEXT)
    manifest_text.correct_statuses([data[ManifestKey.ITEMS.key][0]])
    patched_text = manifest_text.text
    original_lines = MANIFEST_TEXT.split("\n")
    patched_lines = patched_text.split("\n")
    changed_line_pairs = [
        (before, after)
        for before, after in zip(original_lines, patched_lines)
        if before != after
    ]
    assert changed_line_pairs == [("    status: in_progress", "    status: done")]


def test_patched_text_still_parses_and_validates():
    data = yaml.safe_load(MANIFEST_TEXT)
    original = Plan.from_mapping(data)
    manifest_text = ManifestText.parse(MANIFEST_TEXT)
    manifest_text.correct_statuses([data[ManifestKey.ITEMS.key][0]])
    patched_text = manifest_text.text
    reparsed = yaml.safe_load(patched_text)
    PlanValidator(reparsed).validate()  # must not raise
    patched = Plan.from_mapping(reparsed)
    assert patched.items[0].status is ItemStatus.DONE
    assert patched.items[0].notes == original.items[0].notes


def test_patches_a_status_line_spelled_with_spaces():
    text = (
        DatasetLocation.DIRECTORY / "manifest-with-spaced-statuses.yaml"
    ).read_text()
    data = yaml.safe_load(text)
    manifest_text = ManifestText.parse(text)
    corrections = manifest_text.correct_statuses([data[ManifestKey.ITEMS.key][0]])
    patched_text = manifest_text.text
    patched = Plan.from_mapping(yaml.safe_load(patched_text))
    assert patched.items[0].status is ItemStatus.DONE
    assert [c.previous_status for c in corrections] == [ItemStatus.IN_PROGRESS]


def test_no_items_to_correct_returns_original_text_unchanged():
    manifest_text = ManifestText.parse(MANIFEST_TEXT)
    corrections = manifest_text.correct_statuses([])
    patched_text = manifest_text.text
    assert patched_text == MANIFEST_TEXT
    assert corrections == []


def test_raises_if_an_item_has_no_status_line():
    text = "- id: a\n  title: A\n  branch: a\n"
    with pytest.raises(MissingStatusLineError):
        ManifestText.parse(text).correct_statuses(
            [{ManifestKey.IDENTIFIER.key: "a", ManifestKey.BRANCH.key: "a"}]
        )


# %% main - manifest validation


def test_main_rejects_an_invalid_manifest_instead_of_crashing(
    tmp_path, monkeypatch, capsys
):
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text("")  # yaml.safe_load("") -> None, not a mapping
    pr_data_path = tmp_path / "pr_data.json"
    pr_data_path.write_text("{}")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "plan/sync_manifest_status.py",
            "--plan",
            str(plan_path),
            "--pr-data",
            str(pr_data_path),
        ],
    )
    exit_code = main()
    assert exit_code == 1
    with pytest.raises(PlanValidationError) as expected_error:
        PlanValidator(None).validate()
    assert capsys.readouterr().err == (
        f"plan.yaml failed validation: {expected_error.value}\n"
    )


# %% main - file read/write round trip


def _write_merged_pull_request_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """
    Write the shared ``MANIFEST_TEXT`` fixture (item "a" in progress, pull request #1)
    plus a ``pr_data.json`` reporting #1 merged, to *tmp_path*.

    :param tmp_path: pytest's per-test temporary directory.
    :return:``(plan_path, pr_data_path)``.
    """
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(MANIFEST_TEXT)
    pr_data_path = tmp_path / "pr_data.json"
    pr_data_path.write_text(
        json.dumps(
            {
                "owner/repo": {
                    "1": {
                        "state": "closed",
                        "draft": False,
                        "merged_at": "2026-01-01T00:00:00+00:00",
                        "labels": [],
                    }
                }
            }
        )
    )
    return plan_path, pr_data_path


def test_main_corrects_the_plan_file_in_place_when_no_output_is_given(
    tmp_path, monkeypatch, capsys
):
    plan_path, pr_data_path = _write_merged_pull_request_fixture(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "plan/sync_manifest_status.py",
            "--plan",
            str(plan_path),
            "--pr-data",
            str(pr_data_path),
        ],
    )
    exit_code = main()
    assert exit_code == 0
    assert "    status: done" in plan_path.read_text()
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "corrected": [{"id": "a", "previous_status": ItemStatus.IN_PROGRESS.value}]
    }


def test_main_writes_the_corrected_manifest_to_output_leaving_the_plan_file_untouched(
    tmp_path, monkeypatch
):
    plan_path, pr_data_path = _write_merged_pull_request_fixture(tmp_path)
    output_path = tmp_path / "corrected-plan.yaml"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "plan/sync_manifest_status.py",
            "--plan",
            str(plan_path),
            "--pr-data",
            str(pr_data_path),
            "--output",
            str(output_path),
        ],
    )
    exit_code = main()
    assert exit_code == 0
    assert plan_path.read_text() == MANIFEST_TEXT
    assert "    status: done" in output_path.read_text()


# %% main - cross-plan references


def _write_plan_depending_on_another_plan(tmp_path: Path) -> tuple[Path, Path]:
    """
    Write a manifest whose one item depends on an item of another plan.

    :param tmp_path: pytest's per-test temporary directory.
    :return: The manifest's path and an empty pull request data file's path.
    """
    plan_path = tmp_path / PlanDocument.MANIFEST
    plan_path.write_text(
        yaml.dump(
            plan(
                items=[
                    item(
                        "a",
                        ItemStatus.NOT_STARTED.value,
                        depends_on=[
                            foreign_reference(ForeignItemId.DONE, FixturePlanId.OTHER)
                        ],
                    )
                ]
            )
        )
    )
    pull_request_data_path = tmp_path / "pr_data.json"
    pull_request_data_path.write_text("{}")
    return plan_path, pull_request_data_path


def _run_main(plan_path: Path, pull_request_data_path: Path, monkeypatch, *extra: str):
    """
    Run plan/sync_manifest_status.py's ``main`` over the given files.

    :param plan_path: The manifest to sync.
    :param pull_request_data_path: The live pull request data to sync against.
    :param monkeypatch: pytest's argv patcher.
    :param extra: Further command line arguments.
    :return:``main``'s exit code.
    """
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "plan/sync_manifest_status.py",
            "--plan",
            str(plan_path),
            "--pr-data",
            str(pull_request_data_path),
            *extra,
        ],
    )
    return main()


def test_main_accepts_a_cross_plan_reference_given_the_plans_directory(
    tmp_path, monkeypatch, capsys
):
    # This script validates the manifest before correcting it, so without the
    # directory the whole refresh would stop before rendering.
    plan_path, pull_request_data_path = _write_plan_depending_on_another_plan(tmp_path)
    exit_code = _run_main(
        plan_path,
        pull_request_data_path,
        monkeypatch,
        "--plans-dir",
        str(DatasetLocation.PLANS),
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"corrected": []}


def test_main_rejects_a_cross_plan_reference_without_the_plans_directory(
    tmp_path, monkeypatch, capsys
):
    plan_path, pull_request_data_path = _write_plan_depending_on_another_plan(tmp_path)
    exit_code = _run_main(plan_path, pull_request_data_path, monkeypatch)
    assert exit_code == 1
    with pytest.raises(PlanValidationError) as expected_error:
        PlanValidator(yaml.safe_load(plan_path.read_text())).validate()
    assert [type(problem) for problem in expected_error.value.problems] == [
        MissingPlanDirectory
    ]
    assert capsys.readouterr().err == (
        f"plan.yaml failed validation: {expected_error.value}\n"
    )
