"""
The refusals an item-bootstrap command ends with.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass

from basstler.class_property import classproperty
from basstler.exceptions import DataclassException
from basstler.exit_codes import CommandFailure
from basstler.locations import PluginLocation
from basstler.plan.item_bootstrap.vocabulary import PlanItemBootstrapExitCode
from basstler.plan.model import (
    ManifestKey,
    PlanDocument,
)

# %% failures


@dataclass
class BootstrapError(CommandFailure, DataclassException, ABC):
    """
    Base for every refusal this tool reports, each carrying its own exit status.
    """


@dataclass
class UnknownPlanError(BootstrapError):
    """
    Raised when the named plan has no manifest on the personal-notes branch.
    """

    @classproperty
    def exit_code(cls) -> PlanItemBootstrapExitCode:
        """:return: The status this kind of error ends a command with."""
        return PlanItemBootstrapExitCode.UNKNOWN_PLAN

    plan_identifier: str
    """
    The plan that could not be found.
    """

    manifest_path: str
    """
    Where its manifest was looked for.
    """

    def error_message(self) -> str:
        return (
            f"no plan {self.plan_identifier!r} on the personal-notes branch "
            f"({self.manifest_path} is not there)"
        )

    def suggest_correction(self) -> str:
        return "Run /plan-create to bootstrap it, or check the plan id for a typo."


@dataclass
class UnknownItemError(BootstrapError):
    """
    Raised when the named item is absent from an otherwise resolvable plan.
    """

    @classproperty
    def exit_code(cls) -> PlanItemBootstrapExitCode:
        """:return: The status this kind of error ends a command with."""
        return PlanItemBootstrapExitCode.UNKNOWN_ITEM

    plan_identifier: str
    """
    The plan that was searched.
    """

    item_identifier: str
    """
    The item that is not in it.
    """

    def error_message(self) -> str:
        return f"no item {self.item_identifier!r} in plan {self.plan_identifier!r}"

    def suggest_correction(self) -> str:
        return (
            "Record the item first - /add-plan-item decides where new work belongs, and "
            "this tool's record operation writes the entry."
        )


@dataclass
class IncompleteNewItemError(BootstrapError):
    """
    Raised when an item that does not exist yet is recorded without the fields needed to
    write its entry.
    """

    @classproperty
    def exit_code(cls) -> PlanItemBootstrapExitCode:
        """:return: The status this kind of error ends a command with."""
        return PlanItemBootstrapExitCode.INCOMPLETE_NEW_ITEM

    item_identifier: str
    """
    The item that would have been created.
    """

    missing_keys: tuple[ManifestKey, ...]
    """
    The keys a new entry cannot omit that were not supplied.
    """

    def error_message(self) -> str:
        missing = ", ".join(manifest_key.key for manifest_key in self.missing_keys)
        return (
            f"item {self.item_identifier!r} is not in the plan yet, so recording it "
            f"needs {missing}"
        )

    def suggest_correction(self) -> str:
        return "Pass " + " and ".join(
            f"--{manifest_key.key.replace('_', '-')}"
            for manifest_key in self.missing_keys
        )


@dataclass
class BranchAlreadyPublishedError(BootstrapError):
    """
    Raised when the branch to open already exists on the remote, which means work is
    underway and overwriting it would discard someone's commits.
    """

    @classproperty
    def exit_code(cls) -> PlanItemBootstrapExitCode:
        """:return: The status this kind of error ends a command with."""
        return PlanItemBootstrapExitCode.BRANCH_ALREADY_PUBLISHED

    branch: str
    """
    The branch that is already published.
    """
    remote: str
    """
    The remote carrying it.
    """

    def error_message(self) -> str:
        return (
            f"branch {self.branch!r} already exists on {self.remote!r} - it is already "
            "being worked, and republishing it would discard those commits"
        )

    def suggest_correction(self) -> str:
        return (
            "Pass --pull-request-number if its pull request already exists, or choose a "
            "branch name that is not taken."
        )


@dataclass
class PullRequestDetailsMissingError(BootstrapError):
    """
    Raised when opening the work must create a pull request but was given nothing to
    create it from.
    """

    @classproperty
    def exit_code(cls) -> PlanItemBootstrapExitCode:
        """:return: The status this kind of error ends a command with."""
        return PlanItemBootstrapExitCode.PULL_REQUEST_DETAILS_MISSING

    item_identifier: str
    """
    The item whose work was being opened.
    """

    def error_message(self) -> str:
        return (
            f"opening {self.item_identifier!r} has to create a pull request, but was "
            "given neither a title nor a body to create one from"
        )

    def suggest_correction(self) -> str:
        return (
            "Pass --pull-request-number for one you already created - which keeps your "
            "identity on it - or --pull-request-title and --pull-request-body."
        )


@dataclass
class PullRequestRefusedError(BootstrapError):
    """
    Raised when the remote declines to create the pull request.
    """

    @classproperty
    def exit_code(cls) -> PlanItemBootstrapExitCode:
        """:return: The status this kind of error ends a command with."""
        return PlanItemBootstrapExitCode.PULL_REQUEST_REFUSED

    detail: str
    """
    What the remote said.
    """

    def error_message(self) -> str:
        return f"the remote refused to create the pull request: {self.detail}"

    def suggest_correction(self) -> str:
        return (
            "The branch is published, so create the pull request yourself and re-run "
            "with --pull-request-number."
        )


@dataclass
class PlanSaveRefusedError(BootstrapError):
    """
    Raised when the plan save rejects the edited documents, so nothing was published.
    """

    @classproperty
    def exit_code(cls) -> PlanItemBootstrapExitCode:
        """:return: The status this kind of error ends a command with."""
        return PlanItemBootstrapExitCode.PLAN_SAVE_REFUSED

    detail: str
    """
    What the plan save printed on its error stream.
    """

    def error_message(self) -> str:
        return f"the plan save refused the edit: {self.detail}"

    def suggest_correction(self) -> str:
        return (
            "Fix what the plan save names and run the command again; after opening, "
            "pass --pull-request-number for the pull request it already created."
        )


@dataclass
class PlanNotWrittenError(BootstrapError):
    """
    Raised when a save that reported success left the notes branch without the edit.
    """

    @classproperty
    def exit_code(cls) -> PlanItemBootstrapExitCode:
        """:return: The status this kind of error ends a command with."""
        return PlanItemBootstrapExitCode.PLAN_NOT_WRITTEN

    plan_identifier: str
    """
    The plan that was meant to be written.
    """
    documents: tuple[PlanDocument, ...]
    """
    The documents the notes branch does not carry the edit to.
    """

    def error_message(self) -> str:
        missing = ", ".join(document.value for document in self.documents)
        return (
            f"the plan save reported success, but plan {self.plan_identifier!r} on the "
            f"personal-notes branch does not carry the edit to {missing}"
        )

    def suggest_correction(self) -> str:
        return (
            "Read the plan off the notes branch before editing it again - another write "
            "may have landed in between."
        )


@dataclass
class NotesBranchUnavailableError(BootstrapError):
    """
    Raised when the personal-notes branch cannot be fetched, so there is no plan data to
    read or write.
    """

    @classproperty
    def exit_code(cls) -> PlanItemBootstrapExitCode:
        """:return: The status this kind of error ends a command with."""
        return PlanItemBootstrapExitCode.UNKNOWN_PLAN

    detail: str
    """
    Why the fetch failed.
    """

    def error_message(self) -> str:
        return f"could not fetch the personal-notes branch: {self.detail}"

    def suggest_correction(self) -> str:
        return f"Run {PluginLocation.PERSONAL_NOTES_BRANCH_CREATION_SCRIPT.path_under_exported_root} first."


# %% placing an item in its stack


@dataclass
class UnstackableItemError(BootstrapError, ABC):
    """
    Base for the reasons an item's dependencies do not form one line of branches below
    it.
    """

    @classproperty
    def exit_code(cls) -> PlanItemBootstrapExitCode:
        """:return: The status this kind of error ends a command with."""
        return PlanItemBootstrapExitCode.UNSTACKABLE_ITEM


@dataclass
class ForkingDependenciesError(UnstackableItemError):
    """
    Raised when an item of the line rests on more than one unlanded item at once.
    """

    item_identifier: str
    """
    The item whose dependencies fork.
    """

    dependencies: tuple[str, ...]
    """
    Its unlanded dependencies.
    """

    def error_message(self) -> str:
        return (
            f"item {self.item_identifier!r} rests on {len(self.dependencies)} unlanded "
            f"items at once ({', '.join(self.dependencies)}), and a stack is a single "
            f"line"
        )

    def suggest_correction(self) -> str:
        return (
            "Raise it with the user: land one of them first, or change the plan's "
            "depends_on so the items build on each other in one line."
        )


@dataclass
class DependencyInAnotherRepositoryError(UnstackableItemError):
    """
    Raised when an item of the line lives in another repository than the item placed.
    """

    item_identifier: str
    """
    The item in the other repository.
    """

    repository: str
    """
    The repository it lives in.
    """

    def error_message(self) -> str:
        return (
            f"dependency {self.item_identifier!r} lives in {self.repository}, and a "
            f"stack cannot span repositories"
        )

    def suggest_correction(self) -> str:
        return "Land it first, then base the item on its repository's trunk."


@dataclass
class UnstartedDependencyError(UnstackableItemError):
    """
    Raised when an unlanded item of the line has no branch to build on yet.
    """

    item_identifier: str
    """
    The item without a branch.
    """

    def error_message(self) -> str:
        return f"dependency {self.item_identifier!r} has no branch to build on yet"

    def suggest_correction(self) -> str:
        return "Start that item first, with /plan-item-kickoff."
