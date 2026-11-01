"""
The patterns, statuses and report keys the item-bootstrap commands share.
"""

from __future__ import annotations

import re
from enum import Enum, StrEnum

from basstler.exit_codes import ExitCode
from basstler.plan.model import (
    ManifestKey,
)

# %% shared vocabulary


class ManifestLinePattern(Enum):
    """
    What the item-block patching looks for in a raw ``plan.yaml`` line.
    """

    ITEM_START = re.compile(rf"^\s*- {re.escape(ManifestKey.IDENTIFIER.key)}:")
    """
    Matches the first line of an item block, which is always its ``id``.

    Same anchor ``plan/sync_manifest_status.py`` uses to find item boundaries in raw text.
    """

    SEQUENCE_ENTRY = re.compile(r"^\s*-\s")
    """
    Matches one entry of a block sequence, which YAML lets sit either indented under its
    key or flush with it.
    """

    TOP_LEVEL_KEY = re.compile(r"^\S")
    """
    Matches a top-level ``plan.yaml`` key, which is where the last item block ends.
    """

    BLOCK_VALUE = re.compile(
        r"^\s*("
        + "|".join(
            sorted(manifest_key.key for manifest_key in ManifestKey.block_styled())
        )
        + "):"
    )
    """
    Matches a key whose value may run over the following lines, derived from
    :meth:`ManifestKey.block_styled` rather than listing those keys a second time.
    """

    def matches(self, line: str) -> bool:
        """
        :param line: One manifest line.
        :return: Whether this pattern matches at the line's start.
        """
        return self.value.match(line) is not None


class PlanItemBootstrapExitCode(ExitCode):
    """
    The process statuses this tool exits with.

    A distinct status per refusal lets a caller act on *which* failure happened without
    parsing stderr.
    """

    SUCCESS = 0
    """
    The operation ran and printed its report.
    """

    USAGE = 2
    """
    No such command, or the wrong arguments; ``argparse``'s own status for a usage error.
    """

    UNKNOWN_PLAN = 3
    """
    No plan of that id is on the personal-notes branch.
    """

    UNKNOWN_ITEM = 4
    """
    The plan exists but tracks no item of that id, and too little was given to add one.
    """

    INCOMPLETE_NEW_ITEM = 5
    """
    Adding the item was asked for without every key a new entry must carry.
    """

    BRANCH_ALREADY_PUBLISHED = 6
    """
    The branch is already on the remote, so opening the work would adopt someone's.
    """

    PULL_REQUEST_DETAILS_MISSING = 7
    """
    Neither an existing pull request number nor the title and body to create one.
    """

    PULL_REQUEST_REFUSED = 8
    """
    GitHub rejected the creation; its own message says why.
    """

    PLAN_SAVE_REFUSED = 9
    """
    The plan save rejected the edited manifest or roadmap; its own message says why.
    """

    MANIFEST_IS_STALE = 10
    """
    The check found recorded fields that local git contradicts.
    """

    BRANCH_TRACKS_NO_ITEM = 11
    """
    No plan claims the branch, so there was nothing to resolve or to write.

    A finding rather than a failure: every pull request is supposed to belong to a plan,
    so a caller acting on the status alone reports it instead of guessing.
    """

    PLAN_NOT_WRITTEN = 12
    """
    The save reported success, but the notes branch does not carry the edit.
    """

    UNSTACKABLE_ITEM = 13
    """
    The item's dependencies do not form one line of branches below it, so it cannot be
    a layer of a GitHub stack; stderr says why.
    """


class ReportKey(StrEnum):
    """
    The keys of the JSON documents these operations print that this module invents.

    A key naming a manifest field - ``branch``, ``blockers``, an item's own ``status`` -
    is read from :class:`ManifestKey` instead.
    """

    STATUS = "status"
    """
    What the run means, as :attr:`ExitCode.name_for_a_caller` puts it.

    Distinct from an item's own ``status``, which names a point in its lifecycle; the
    two share a spelling and nothing else.
    """

    EXIT_CODE = "exit_code"
    """
    The status the process exits with.
    """

    PLAN = "plan"
    """
    The plan the operation acted on.
    """

    ITEM = "item"
    """
    The item the operation acted on.
    """

    ITEMS = "items"
    """
    Every item a branch carries, for an operation keyed on a branch.
    """

    CREATED_ITEM = "created_item"
    """
    Whether the item's manifest entry was written for the first time.
    """

    DASHBOARD_COMMAND = "dashboard_command"
    """
    The dashboard republish the caller still has to run.
    """

    PULL_REQUEST_URL = "pull_request_url"
    """
    Where an opened pull request lives.
    """

    NOTE_PARAGRAPHS = "note_paragraphs"
    """
    How many paragraphs the item's note reads as once written.
    """

    FINDINGS = "findings"
    """
    Every recorded field local git contradicts.
    """

    FIELD = "field"
    """
    The manifest field one finding is about.
    """

    RECORDED = "recorded"
    """
    What the manifest says for that field.
    """

    OBSERVED = "observed"
    """
    What git shows for it instead.
    """

    PREVIOUS_STATUS = "previous_status"
    """
    The status an item carried before a write.
    """

    BASE = "base"
    """
    The branch an item's branch is cut from and its pull request based on.
    """

    BRANCHES = "branches"
    """
    The branches of an item's stack, bottom first, ending with the item's own.
    """

    PULL_REQUESTS_BELOW = "pull_requests_below"
    """
    The pull requests of the layers below an item, bottom first.
    """

    DEPENDENCIES_IN_OTHER_PLANS = "dependencies_in_other_plans"
    """
    The dependencies naming another plan's items, which a placement does not follow.
    """


class BlockScalarStyle(StrEnum):
    """
    How a block scalar YAML has already read back separates its paragraphs, named by the
    indicator that opens it.
    """

    LITERAL = "|"
    """
    Every line break is kept, so a single newline is a hard-wrapped line and a blank line
    separates paragraphs.
    """

    FOLDED = ">"
    """
    Line breaks fold into spaces and a blank line reads back as one newline, so a single
    newline separates paragraphs.
    """

    @property
    def paragraph_separator(self) -> re.Pattern[str]:
        """
        What separates two paragraphs in a value of this style once it has been read.
        """
        if self is BlockScalarStyle.LITERAL:
            return re.compile(r"\n\s*\n")
        return re.compile(r"\n+")
