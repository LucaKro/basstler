"""
A pull request's live GitHub state, as ``pr_data.json`` records it, and the state an
item is classified into from it.

pr_data.json shape: {"<owner>/<repo>": {"<pr_number>": {"state": "open"| "closed",
"draft": bool, "merged_at": str|null, "labels": [str, ...], "body": str|null}}} - one entry per pull
request number referenced by any item. A closed entry must carry merged_at explicitly,
null included; omitting it is rejected rather than read as unmerged. See pr-data-
fetching.md for how a session should gather it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from basstler.workflow_labels import WorkflowLabel
from basstler_domain_model.github.pull_request_fields import (
    PullRequestField,
    PullRequestPayload,
)
from basstler_domain_model.github.vocabulary import PullRequestState
from basstler.maintenance.description import PullRequestDescription
from basstler_domain_model.repository import (
    Repository,
)


# %% classified state


class LiveState(StrEnum):
    """
    An item's live GitHub pull request state, classified fresh on every run.

    :attr:`NO_PULL_REQUEST` is a real member, not represented as ``None``: "this item
    has no pull request yet" is itself a meaningful, displayable state (with its own
    label and CSS class) rather than an absence value every caller would otherwise have
    to special-case around the enum.
    """

    NO_PULL_REQUEST = "none"
    MERGED = "merged"
    OPEN_DRAFT = "open_draft"
    OPEN_READY = "open_ready"
    CLOSED_UNMERGED = "closed_unmerged"
    NOT_FOUND = "not_found"

    @property
    def display_label(self) -> str:
        """
        The human-readable label shown in the dashboard UI for this state.
        """
        match self:
            case LiveState.NO_PULL_REQUEST:
                return "No pull request yet"
            case LiveState.MERGED:
                return "Merged"
            case LiveState.OPEN_DRAFT:
                return "Open · Draft"
            case LiveState.OPEN_READY:
                return "Open · Ready"
            case LiveState.CLOSED_UNMERGED:
                return "Closed (unmerged)"
            case LiveState.NOT_FOUND:
                return "Not found on GitHub"

    @classmethod
    def classify(
        cls,
        pull_request_number: int | None,
        repository: str,
        pull_requests_by_repository: PullRequestsByRepository,
    ) -> LiveState:
        """
        Classify one item's live GitHub state from its pull request number and
        repository.

        :param pull_request_number: The item's tracked pull request number, or ``None``
            if it has no pull request yet.
        :param repository: The ``"owner/repo"`` to look the pull request up under.
        :param pull_requests_by_repository: Live pull request state for every repository
            referenced by the plan's items.
        :return: The classified state.
        """
        if pull_request_number is None:
            return LiveState.NO_PULL_REQUEST
        repository_pull_requests = pull_requests_by_repository.get(repository, {})
        pull_request = repository_pull_requests.get(str(pull_request_number))
        if pull_request is None:
            return LiveState.NOT_FOUND
        if pull_request.was_merged:
            return LiveState.MERGED
        if pull_request.state is PullRequestState.CLOSED:
            return LiveState.CLOSED_UNMERGED
        if pull_request.draft:
            return LiveState.OPEN_DRAFT
        return LiveState.OPEN_READY


# %% pr_data.json records


class MissingMergeTimestampError(ValueError):
    """
    Raised when a closed pull request's ``pr_data.json`` entry omits the ``merged_at``
    key altogether, which leaves no way to tell a merged pull request from one closed
    unmerged.
    """


@dataclass
class PullRequestRecord:
    """
    The live GitHub state of one pull request, as gathered by the skill.
    """

    state: PullRequestState
    """
    GitHub's own coarse-grained state.
    """

    draft: bool = False
    """
    Whether the pull request is currently a draft.
    """

    merged_at: datetime | None = None
    """
    The pull request's merge timestamp, or ``None`` if GitHub never recorded a merge
    through its own merge API.
    """

    labels: list[str] = field(default_factory=list)
    """
    The pull request's raw GitHub labels, exactly as GitHub reports them.

    Kept as ``list[str]`` rather than ``list[WorkflowLabel]``: a real pull request can carry
    labels this codebase has no reason to know about (other.

    automation's own labels, GitHub's defaults), so this field must tolerate
    anything - see :attr:`identified_labels` for the subset this codebase
    actually recognizes. Needed because :attr:`merged_at` alone misses a real
    case in this repo's history: a pull request merged out-of-band (its
    branch pushed directly, then the pull request closed by hand rather than
    through GitHub's merge button) never gets ``merged_at`` set, so the
    closer manually adds the :attr:`WorkflowLabel.MERGED` label to record
    what actually happened - see :meth:`was_merged`.
    """

    upstream_link: str | None = None
    """
    The URL of the upstream pull request a maintenance pass linked in the pull request's
    description, ``None`` while none is linked.
    """

    @property
    def identified_labels(self) -> frozenset[WorkflowLabel]:
        """The subset of :attr:`labels` that match a known
        :class:`WorkflowLabel` - any other raw label this pull request
        happens to carry is silently excluded, not an error, since
        :attr:`labels` is deliberately not limited to labels this codebase
        recognizes."""
        known_values = {member.value for member in WorkflowLabel}
        return frozenset(
            WorkflowLabel(label) for label in self.labels if label in known_values
        )

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> PullRequestRecord:
        """
        Build a record from one entry of ``pr_data.json``.

        A closed entry must carry ``merged_at`` explicitly, ``null`` included: a
        gatherer that never requested the field would otherwise be indistinguishable
        from GitHub reporting no merge, silently turning every merged pull request into
        a closed-unmerged one.

        :raises MissingMergeTimestampError: If a closed entry omits ``merged_at``.
        """
        state = PullRequestState(data[PullRequestField.STATE.key])
        if (
            state is PullRequestState.CLOSED
            and PullRequestField.MERGED_AT.key not in data
        ):
            raise MissingMergeTimestampError(
                "a closed pull request entry must carry merged_at (null included)"
            )
        merged_at = data.get(PullRequestField.MERGED_AT.key)
        return cls(
            state=state,
            draft=data.get(PullRequestField.DRAFT.key, False),
            merged_at=datetime.fromisoformat(merged_at) if merged_at else None,
            labels=list(data.get(PullRequestField.LABELS.key) or []),
            upstream_link=PullRequestDescription(
                data.get(PullRequestField.BODY.key) or ""
            ).upstream_link,
        )

    @classmethod
    def from_api(cls, api_record: PullRequestPayload) -> PullRequestRecord:
        """
        Read one pull request object as the REST pulls API answers it.

        :param api_record: The pull request, as the API describes it.
        :return: The fields a dashboard classifies it by.
        """
        number = PullRequestField.NUMBER.read(api_record)
        return cls.from_mapping(
            {
                field.key: field.read(api_record, number)
                for field in (
                    PullRequestField.STATE,
                    PullRequestField.DRAFT,
                    PullRequestField.MERGED_AT,
                    PullRequestField.LABELS,
                    PullRequestField.BODY,
                )
            }
        )

    @property
    def was_merged(self) -> bool:
        """Whether this pull request's changes actually landed - GitHub's own
        :attr:`merged_at`, or (for an out-of-band merge GitHub never
        recorded) the manually-applied :attr:`WorkflowLabel.MERGED`
        label."""
        return (
            self.merged_at is not None or WorkflowLabel.MERGED in self.identified_labels
        )

    @classmethod
    def parse_by_repository(
        cls,
        raw_pull_request_data: dict[str, Any],
    ) -> PullRequestsByRepository:
        """
        Parse ``pr_data.json``'s raw JSON into :class:`PullRequestRecord`\\ s.

        :param raw_pull_request_data: The parsed JSON, keyed by ``"owner/repo"``
            then by pull request number as a string - see the module docstring for the
            exact shape.
        :raises MalformedPullRequestDataError: If the data is not keyed by repository
            then by number, or any entry can't be parsed into a
            :class:`PullRequestRecord`.
        :return: The same structure, with each leaf mapping parsed into a
            :class:`PullRequestRecord`.
        """
        pull_requests_by_repository: PullRequestsByRepository = {}
        for repository, pull_requests in raw_pull_request_data.items():
            if not Repository.is_full_name(repository) or not isinstance(
                pull_requests, dict
            ):
                raise MalformedPullRequestDataError(
                    f"top-level key {repository!r} does not map an 'owner/repo' to its "
                    'pull requests: pr_data.json is {"<owner>/<repo>": '
                    '{"<pr_number>": {...}}}'
                )
            pull_requests_by_repository[repository] = {}
            for pull_request_number, record in pull_requests.items():
                if not isinstance(record, dict):
                    raise MalformedPullRequestDataError(
                        f"{repository}#{pull_request_number}: expected the pull "
                        f"request's fields, got {record!r}"
                    )
                try:
                    pull_requests_by_repository[repository][pull_request_number] = (
                        PullRequestRecord.from_mapping(record)
                    )
                except (KeyError, ValueError) as error:
                    raise MalformedPullRequestDataError(
                        f"{repository}#{pull_request_number}: {error}"
                    ) from error
        return pull_requests_by_repository


class MalformedPullRequestDataError(ValueError):
    """Raised when one entry of ``pr_data.json`` cannot be parsed into a
    :class:`PullRequestRecord` - a missing or invalid ``state`` field, or an
    unparsable ``merged_at`` timestamp."""


PullRequestsByRepository = dict[str, dict[str, PullRequestRecord]]
