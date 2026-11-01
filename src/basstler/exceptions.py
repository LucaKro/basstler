"""
The errors raised across this package's subpackages.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from basstler_domain_model.class_property import classproperty
from basstler_domain_model.exceptions import DataclassException

from basstler.locations import (
    BasstlerVariable,
    PluginLocation,
)
from basstler_domain_model.environment import (
    ClaudeCodeVariable,
)


@dataclass
class NotesBranchMissingError(DataclassException, RuntimeError):
    """
    Raised when no remote tried holds the personal-notes branch.
    """

    branch: str
    """
    The personal-notes branch that was looked for.
    """

    attempted_remotes: tuple[str, ...]
    """
    Every remote it was fetched from, in order.
    """

    def error_message(self) -> str:
        return (
            f"branch '{self.branch}' doesn't exist yet (tried: "
            f"{', '.join(self.attempted_remotes)})"
        )

    def suggest_correction(self) -> str:
        return f"run {PluginLocation.PERSONAL_NOTES_BRANCH_CREATION_SCRIPT.path_under_exported_root} first"


@dataclass
class UnsafeNotesPathError(DataclassException, ValueError):
    """
    Raised when a path to write on the personal-notes branch could leave it.
    """

    path: PurePosixPath
    """
    The refused path.
    """

    def error_message(self) -> str:
        return f"{self.path} would be written outside the personal-notes branch's root"

    def suggest_correction(self) -> str:
        return "give a path relative to the branch's root, with no '..' component"


@dataclass
class MissingStatusLineError(DataclassException, ValueError):
    """
    Raised when an item slated for a status correction has no ``status:`` line in its
    plan.yaml block.
    """

    item_identifier: str
    """
    The id of the item whose block has no ``status:`` line.
    """

    def error_message(self) -> str:
        return f"item {self.item_identifier!r} has no status: line to correct"

    def suggest_correction(self) -> str:
        return "give the item a status: line in plan.yaml"


# %% plan validation


@dataclass
class ValidationProblem(DataclassException):
    """A single problem found while validating a plan.yaml - see plan-schema.md.

    One dataclass subclass per validation rule, each carrying the specific
    fields that rule cares about, so a caller can react to one kind of
    problem with ``isinstance()``. :class:`PlanValidator` collects them and
    raises them together as one :class:`PlanValidationError`.
    """

    def suggest_correction(self) -> str:
        """
        No validation rule suggests a correction of its own.
        """
        return ""


@dataclass
class InvalidManifestRoot(ValidationProblem):
    """The manifest didn't parse to a mapping - an empty file (``yaml.safe_load``
    returns ``None``) or a manifest whose top level is a list or scalar."""

    actual_value: Any
    """
    Whatever the manifest actually parsed to.
    """

    def error_message(self) -> str:
        return f"plan.yaml must parse to a mapping, got {type(self.actual_value).__name__}: {self.actual_value!r}"


@dataclass
class InvalidSchemaVersion(ValidationProblem):
    """
    The manifest's ``schema_version`` is missing or not :attr:`supported_version`.
    """

    actual_value: Any
    """
    Whatever ``schema_version`` actually held.
    """

    @classproperty
    def supported_version(cls) -> int:
        """
        The only ``schema_version`` a plan.yaml may declare.
        """
        return 1

    def error_message(self) -> str:
        return (
            f"schema_version must be {self.supported_version}, "
            f"got {self.actual_value!r}"
        )


@dataclass
class DuplicateItemId(ValidationProblem):
    """
    Two or more items resolve to the same effective id.
    """

    duplicate_identifiers: list[str]
    """
    Every identifier that occurred more than once.
    """

    def error_message(self) -> str:
        return f"duplicate item id(s): {sorted(self.duplicate_identifiers)}"


@dataclass
class UnknownTrack(ValidationProblem):
    """
    An item's ``track`` doesn't resolve to a declared track.
    """

    item_identifier: str
    """
    The offending item's effective id.
    """

    track: Any
    """
    Whatever ``track`` actually held.
    """

    def error_message(self) -> str:
        return f"item {self.item_identifier!r} has unknown track {self.track!r}"


@dataclass
class UnknownStatus(ValidationProblem):
    """
    An item's ``status`` isn't one of :class:`ItemStatus`'s values.
    """

    item_identifier: str
    """
    The offending item's effective id.
    """

    status: Any
    """
    Whatever ``status`` actually held.
    """

    def error_message(self) -> str:
        return f"item {self.item_identifier!r} has unknown status {self.status!r}"


@dataclass
class InvalidDependsOn(ValidationProblem):
    """
    An item's ``depends_on`` isn't a list.

    A plain string is iterable character-by-character in Python, so without this check a
    string ``depends_on`` would silently be misread as one dependency per character
    instead of failing loudly.
    """

    item_identifier: str
    """
    The offending item's effective id.
    """

    actual_type: type
    """
    The type ``depends_on`` actually held, instead of ``list``.
    """

    def error_message(self) -> str:
        return f"item {self.item_identifier!r} depends_on must be a list, got {self.actual_type.__name__}"


@dataclass
class InvalidBlockers(ValidationProblem):
    """
    An item's ``blockers`` isn't a list.

    A plain string is iterable character-by-character in Python, so without this check a
    string ``blockers`` would silently be misread as one blocker per character instead
    of failing loudly.
    """

    item_identifier: str
    """
    The offending item's effective id.
    """

    actual_type: type
    """
    The type ``blockers`` actually held, instead of ``list``.
    """

    def error_message(self) -> str:
        return f"item {self.item_identifier!r} blockers must be a list, got {self.actual_type.__name__}"


@dataclass
class UnknownDependency(ValidationProblem):
    """
    An item's ``depends_on`` names an id that doesn't resolve to another item.
    """

    item_identifier: str
    """
    The offending item's effective id.
    """

    dependency_identifier: str
    """
    The unresolvable id named in ``depends_on``.
    """

    def error_message(self) -> str:
        return f"item {self.item_identifier!r} depends_on unknown id {self.dependency_identifier!r}"


@dataclass
class UnknownDependencyPlan(ValidationProblem):
    """
    An item's ``depends_on`` names a plan that isn't in the plans directory.
    """

    item_identifier: str
    """
    The offending item's effective id.
    """

    dependency_identifier: str
    """
    The whole reference, as the manifest wrote it.
    """

    def error_message(self) -> str:
        return f"item {self.item_identifier!r} depends_on {self.dependency_identifier!r}, whose plan is not in the plans directory"


@dataclass
class SelfPlanReference(ValidationProblem):
    """
    An item's ``depends_on`` qualifies an id with this plan's own id.

    The bare id already names that item, so admitting the qualified form would give one
    edge two spellings.
    """

    item_identifier: str
    """
    The offending item's effective id.
    """

    dependency_identifier: str
    """
    The whole reference, as the manifest wrote it.
    """

    def error_message(self) -> str:
        return f"item {self.item_identifier!r} depends_on {self.dependency_identifier!r}, which is this plan - name it by its bare id"


@dataclass
class MalformedDependencyReference(ValidationProblem):
    """
    An item's ``depends_on`` entry is neither a bare id nor ``<plan-id>/<item-id>``.
    """

    item_identifier: str
    """
    The offending item's effective id.
    """

    dependency_identifier: str
    """
    The entry, as the manifest wrote it.
    """

    def error_message(self) -> str:
        return f"item {self.item_identifier!r} depends_on malformed reference {self.dependency_identifier!r}"


@dataclass
class MissingPlanDirectory(ValidationProblem):
    """
    An item's ``depends_on`` reaches into another plan, with no plans directory given to
    resolve it against.

    Accepting it unchecked would let a reference nothing can resolve count as satisfied,
    which is the fault this form exists to remove.
    """

    item_identifier: str
    """
    The offending item's effective id.
    """

    dependency_identifier: str
    """
    The reference that could not be resolved.
    """

    def error_message(self) -> str:
        return f"item {self.item_identifier!r} depends_on {self.dependency_identifier!r} in another plan, but no plans directory was given to resolve it against"


@dataclass
class UnknownWave(ValidationProblem):
    """
    A track's ``wave`` doesn't resolve to a declared wave.
    """

    track_identifier: str
    """
    The offending track's id.
    """

    wave: Any
    """
    Whatever ``wave`` actually held.
    """

    def error_message(self) -> str:
        return f"track {self.track_identifier!r} has unknown wave {self.wave!r}"


@dataclass
class DependencyCycle(ValidationProblem):
    """
    A ``depends_on`` chain loops back on itself.

    Undetected, a cycle causes affected items to silently disappear from the rendered
    dependency stack instead of being reported.
    """

    cycle_identifiers: list[str]
    """
    The item ids forming the cycle, in order, with the first id repeated at the end to
    show where it closes.
    """

    def error_message(self) -> str:
        return f"depends_on cycle: {' -> '.join(self.cycle_identifiers)}"


@dataclass
class PlanValidationError(DataclassException):
    """Raised when a plan.yaml fails schema validation - see plan-schema.md."""

    problems: list[ValidationProblem]
    """
    Every problem found, so a broken manifest is reported in one pass rather than one
    problem at a time.
    """

    def error_message(self) -> str:
        return "; ".join(problem.error_message() for problem in self.problems)

    def suggest_correction(self) -> str:
        return ""


@dataclass
class MalformedEmailAddressError(DataclassException, ValueError):
    """
    Raised when text is not an email address in ``local-part@domain`` form.
    """

    text: str
    """
    The value that could not be parsed.
    """

    def error_message(self) -> str:
        return f"expected an email address as 'local-part@domain', got {self.text!r}"

    def suggest_correction(self) -> str:
        return "give the address your commits should carry, e.g. you@example.com"


@dataclass
class BashNotFoundError(DataclassException, FileNotFoundError):
    """
    Raised when no bash a script can run in is found, the Windows launcher for WSL
    aside.
    """

    passed_over: list[Path]
    """
    The bash programs found in the Windows directory, which start WSL rather than the
    bash the scripts are written for.
    """

    def error_message(self) -> str:
        if not self.passed_over:
            return "found no bash to run the plugin's scripts in"
        launchers = ", ".join(str(path) for path in self.passed_over)
        return (
            f"found no bash to run the plugin's scripts in: {launchers} starts WSL, not "
            f"Git Bash"
        )

    def suggest_correction(self) -> str:
        return (
            f"install Git for Windows, or set {ClaudeCodeVariable.GIT_BASH_PATH} to its "
            f"bash.exe"
        )


@dataclass
class PluginRootUnsetError(DataclassException, LookupError):
    """
    Raised when no plugin root is exported, so no plugin script can be found.
    """

    def error_message(self) -> str:
        return (
            f"{BasstlerVariable.PLUGIN_ROOT} is not set, so the plugin cannot be found"
        )

    def suggest_correction(self) -> str:
        return (
            f"source {PluginLocation.PERSONAL_NOTES_CONFIGURATION_SCRIPT} from the "
            f"plugin's root first, or export {BasstlerVariable.PLUGIN_ROOT} as that root"
        )


@dataclass
class PluginRootNotADirectoryError(DataclassException, NotADirectoryError):
    """
    Raised when the exported plugin root is not a directory.
    """

    root: Path
    """
    The plugin root exported.
    """

    def error_message(self) -> str:
        return f"{BasstlerVariable.PLUGIN_ROOT} names {self.root}, which is not a directory"

    def suggest_correction(self) -> str:
        return (
            f"export {BasstlerVariable.PLUGIN_ROOT} as the directory holding the "
            f"plugin's {PluginLocation.HOOKS} directory"
        )
