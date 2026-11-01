"""
The fixture plan's items, and the requests and stand-ins the item-bootstrap tests build.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Any

import yaml

import basstler.plan.item_bootstrap
from basstler.plan.item_bootstrap.exceptions import PullRequestRefusedError
from basstler.plan.item_bootstrap.manifest_text import ManifestText
from basstler.plan.item_bootstrap.opening import (
    NewPullRequest,
    PullRequestOpener,
    WorkOpenRequest,
)
from basstler.plan.item_bootstrap.record import ItemRecordRequest
from basstler.plan.item_bootstrap.update import ItemUpdateRequest
from basstler.plan.model import (
    ItemLayout,
    ItemStatus,
    ManifestKey,
    PlanDocument,
    Prose,
)
from basstler.plan.size_limits import (
    Measure,
    Roadmap,
    RoadmapEntryLocation,
    SizeViolation,
)
from basstler_domain_model.repository import (
    PullRequestReference,
)

from ...constants.notes import ScratchBranch
from ...constants.paths import DatasetLocation
from ...scratch_repository import ScratchRepository

# %% the scenario


BOOTSTRAP_MODULE = basstler.plan.item_bootstrap.__name__
"""
The import path the scratch layout's copy of the module under test is run by.

Read off the module rather than spelled out, so a rename cannot leave the tests running
something else - and ``-m`` rather than a file path, since a module run by path would put
the package's own directory on ``sys.path`` in place of the project root.
"""


PLAN_IDENTIFIER = "test-plan"


PLAN_MANIFEST = (DatasetLocation.DIRECTORY / "bootstrap-plan.yaml").read_text()
"""
The manifest every test starts from.
"""


PLAN_ROADMAP = (DatasetLocation.DIRECTORY / "bootstrap-roadmap.md").read_text()
"""
The roadmap every test starts from.
"""


EXISTING_ITEM = "an-existing-item"
"""
The fixture item the plan already tracks, with no branch of its own yet.
"""


NEW_ITEM = "a-brand-new-item"
"""
An item the fixture plan does not track, for the entry-creating path.
"""


NEW_BRANCH = "claude/a-new-branch"
"""
The branch opening the work publishes.
"""


SESSION_URL = "https://example.invalid/session_first"
"""
The session recorded on an item whose work was opened.
"""


INDENTLESS_PLAN_MANIFEST = (
    DatasetLocation.DIRECTORY / "bootstrap-plan-indentless-items.yaml"
).read_text()
"""
The fixture plan written the way a YAML dumper writes it: every item flush with
``items:``, and the fixture's further items - written-out dependencies, a literal note -
beside them.
"""


SECOND_ITEM = "a-second-item"
"""
The fixture item already underway, on a branch of its own.
"""


STACKED_ITEM = "a-stacked-item"
"""
The indentless fixture's item whose dependencies are written out beneath their key.
"""


LITERALLY_NOTED_ITEM = "a-literally-noted-item"
"""
The indentless fixture's item whose note is a literal block.
"""


PULL_REQUEST_NUMBER = 143
"""
The pull request number opening the work records.
"""


OWNER = "a-maintenance-pass"
"""
The automated caller whose blockers the blocking tests write and withdraw.
"""


CONFLICT_REASON = "conflicts with its base in a_file.py"
"""
Why that caller blocks a branch.
"""


HAND_WRITTEN_BLOCKER = "waiting on a decision nobody automated"
"""
A blocker no automated caller owns, which none of them may touch.
"""


NO_PLANS_BRANCH = "claude/belongs-to-nothing"
"""
A branch no plan tracks.
"""


def second_item_branch() -> str:
    """
    :return: The branch the fixture's underway item records, as the fixture states it.
    """
    return item_named(PLAN_MANIFEST, SECOND_ITEM)[ManifestKey.BRANCH.key]


def manifest_line(manifest_key: ManifestKey, value: Any) -> str:
    """
    One manifest line as the key that owns it writes it.

    :param manifest_key: The key the line sets.
    :param value: The value it carries.
    :return: The rendered line.
    """
    return manifest_key.render(value)


# %% mimics


@dataclass
class RecordingPullRequestOpener(PullRequestOpener):
    """
    Stands in for the GitHub pull request endpoint, recording what it was asked to
    create instead of calling it.
    """

    number: int = 99
    """
    The pull request number handed back to the caller.
    """

    requests: list[NewPullRequest] = field(default_factory=list)
    """
    Every request this opener was given, in call order.
    """

    def open_pull_request(self, pull_request: NewPullRequest) -> PullRequestReference:
        """
        Record *pull_request* and hand back a pull request as GitHub would.
        """
        self.requests.append(pull_request)
        return PullRequestReference(pull_request.repository, self.number)


@dataclass
class RefusingPullRequestOpener(PullRequestOpener):
    """
    Stands in for a GitHub endpoint that refuses the creation.
    """

    def open_pull_request(self, pull_request: NewPullRequest) -> PullRequestReference:
        """
        Refuse the creation the way the real opener does on a non-success response.

        :raises PullRequestRefusedError: Always.
        """
        raise PullRequestRefusedError(detail="422 refused")


# %% the scenario


def published_plan(repository: ScratchRepository) -> dict[PlanDocument, str]:
    """
    Read the plan's documents as they actually are on the notes branch, rather than what
    a run reported.

    Asks each document where it lives, so this never states a plan's layout
    independently of the code that owns it.

    :param repository: The scratch repository whose notes remote to read.
    :return: Each document's content.
    """
    checkout = repository.project_root.parent / "published-plan-checkout"
    shutil.rmtree(checkout, ignore_errors=True)
    repository.clone_notes_branch(checkout)
    return {
        document: (
            checkout / document.path_within_notes_branch(PLAN_IDENTIFIER)
        ).read_text()
        for document in PlanDocument
    }


def record_request(repository: ScratchRepository, **overrides: object):
    """
    Build a record request, overriding only what a test cares about.

    :param repository: The scratch repository the request is recorded against.
    :param overrides: Fields to replace on the default request.
    :return: The request.
    """
    defaults = dict(
        plan_identifier=PLAN_IDENTIFIER,
        item_identifier=EXISTING_ITEM,
        status=ItemStatus.IN_PROGRESS,
    )
    defaults.update(overrides)
    return ItemRecordRequest(**defaults)


def published_items(repository: ScratchRepository) -> dict[str, dict[str, Any]]:
    """
    Every item as YAML actually parses it off the notes branch, keyed by id.

    Parsing rather than matching text is what catches a write that leaves the manifest
    valid but means something other than it says.

    :param repository: The scratch repository whose notes remote to read.
    :return: Each item's parsed mapping.
    """
    manifest = yaml.safe_load(published_plan(repository)[PlanDocument.MANIFEST])
    return {
        item[ManifestKey.IDENTIFIER.key]: item
        for item in manifest[ManifestKey.ITEMS.key]
    }


def item_named(manifest_text: str, item_identifier: str) -> dict[str, Any]:
    """
    :param manifest_text: The manifest to parse.
    :param item_identifier: The item's id.
    :return: The item's mapping, as a YAML reader sees it.
    """
    items = yaml.safe_load(manifest_text)[ManifestKey.ITEMS.key]
    return next(
        item for item in items if item[ManifestKey.IDENTIFIER.key] == item_identifier
    )


def update_request(**overrides: object) -> ItemUpdateRequest:
    """
    Build an update request, overriding only what a test cares about.

    :param overrides: Fields to replace on the default request.
    :return: The request.
    """
    defaults = dict(
        plan_identifier=PLAN_IDENTIFIER,
        item_identifier=EXISTING_ITEM,
        values_by_key={},
    )
    defaults.update(overrides)
    return ItemUpdateRequest(**defaults)


def opened_item_fields() -> dict[ManifestKey, Any]:
    """
    :return: The value opening an item's work writes to each key.
    """
    return {
        ManifestKey.BRANCH: NEW_BRANCH,
        ManifestKey.PULL_REQUEST_NUMBER: PULL_REQUEST_NUMBER,
        ManifestKey.SESSION: SESSION_URL,
        ManifestKey.STATUS: ItemStatus.IN_PROGRESS,
    }


def publish_the_branch_index(repository: ScratchRepository) -> None:
    """
    Make the generated branch index exist, by running the save path that writes it.

    Only ``save-plan.sh`` writes that index, so a test needing one asks for a real save
    rather than assembling a second copy of its format.

    :param repository: The scratch repository to save within.
    """
    update_request().update(repository.project_root)


def put_both_items_on_one_branch(repository: ScratchRepository) -> None:
    """
    Move the fixture's unstarted item onto the branch its underway one already rides on.

    :param repository: The scratch repository to save within.
    """
    update_request(values_by_key={ManifestKey.BRANCH: second_item_branch()}).update(
        repository.project_root
    )


def publish_new_branch(repository: ScratchRepository) -> None:
    """
    Create :data:`NEW_BRANCH` and push it to the work remote.

    :param repository: The scratch repository to publish from.
    """
    repository.run_git("checkout", "-b", NEW_BRANCH)
    repository.run_git("push", "--quiet", "origin", NEW_BRANCH)


def entry_wrapping_across(unbreakable: str) -> str:
    """
    A blocker long enough that wrapping has to break somewhere inside *unbreakable*.

    Positioned from the writer's own wrap column, so this keeps reproducing the hazard
    if that column ever moves.

    :param unbreakable: The token that has to survive the wrap intact.
    :return: The blocker's text.
    """
    indentation = ManifestText.from_text(PLAN_MANIFEST).item_indentation()
    body_width = Prose.line_width - len(indentation.nested + ItemLayout.NESTING_STEP)
    lead = "word "
    return (
        lead * ((body_width - len(unbreakable) // 2) // len(lead))
        + unbreakable
        + " and there is more text after it."
    )


def open_request(**overrides: object) -> WorkOpenRequest:
    """
    Build a work-open request, overriding only what a test cares about.

    :param overrides: Fields to replace on the default request.
    :return: The request.
    """
    defaults = dict(
        plan_identifier=PLAN_IDENTIFIER,
        item_identifier=EXISTING_ITEM,
        branch=NEW_BRANCH,
        base_branch=ScratchBranch.WORK,
        session_url=SESSION_URL,
        pull_request_title="An item that has not been started",
        pull_request_body="What it does.",
    )
    defaults.update(overrides)
    return WorkOpenRequest(**defaults)


def an_entry_over_the_length_limit() -> str:
    """
    :return: A roadmap entry one character longer than the plan's limits allow.
    """
    return "x" * (Measure.ROADMAP_ENTRY_LENGTH.maximum + 1)


def the_length_violation_of(entry: str) -> SizeViolation:
    """
    :param entry: An entry appended to the fixture roadmap.
    :return: The violation the plan save names for it.
    """
    return SizeViolation(
        measure=Measure.ROADMAP_ENTRY_LENGTH,
        location=RoadmapEntryLocation(len(Roadmap.from_text(PLAN_ROADMAP).entries) + 1),
        measured=len(entry),
    )
