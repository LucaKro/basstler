"""
The item-bootstrap commands, one per operation.
"""

from __future__ import annotations

import argparse
from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from basstler.class_property import classproperty
from basstler.command_line import Command
from basstler.plan.item_bootstrap.blockers import BranchReport, OwnedBlocker
from basstler.plan.item_bootstrap.check import ItemCheckRequest, StalenessReport
from basstler.plan.item_bootstrap.opening import WorkOpener, WorkOpenRequest
from basstler.plan.item_bootstrap.placement import (
    ItemPlacementRequest,
    StackPlacement,
)
from basstler.plan.item_bootstrap.record import ItemRecordRequest
from basstler.plan.item_bootstrap.report import BootstrapReport, OperationReport
from basstler.plan.item_bootstrap.update import ItemUpdateRequest
from basstler.plan.model import (
    ItemStatus,
    ManifestKey,
)

# %% commands


@dataclass(frozen=True)
class BootstrapCommand(Command):
    """
    One operation this module's command line offers.
    """

    @abstractmethod
    def run(self, arguments: argparse.Namespace, project_root: Path) -> OperationReport:
        """
        Do the work the parsed command line asks for.

        :param arguments: The parsed command line.
        :param project_root: The repository to run within.
        :return: What the operation found or wrote.
        """

    @staticmethod
    def parser() -> argparse.ArgumentParser:
        """
        :return: The argument parser, built from every command.
        """
        parser = argparse.ArgumentParser(
            description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
        )
        subcommands = parser.add_subparsers(dest="subcommand", required=True)
        for command in BootstrapCommand.every_command():
            command.declare_arguments(
                subcommands.add_parser(command.invoked_as, help=command.description)
            )
        return parser


@dataclass(frozen=True)
class RecordCommand(BootstrapCommand):
    """
    Writes an item's manifest entry, and a standing rule it settled.
    """

    @classproperty
    def description(cls) -> str:
        return "Write an item's manifest entry and its roadmap entry, if any"

    def declare_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--plan", required=True)
        parser.add_argument("--item", required=True)
        parser.add_argument("--status", required=True, type=ItemStatus)
        parser.add_argument("--roadmap-entry")
        parser.add_argument("--title")
        parser.add_argument("--track")

    def run(self, arguments: argparse.Namespace, project_root: Path) -> BootstrapReport:
        return ItemRecordRequest(
            plan_identifier=arguments.plan,
            item_identifier=arguments.item,
            status=arguments.status,
            roadmap_entry=arguments.roadmap_entry,
            title=arguments.title,
            track=arguments.track,
        ).record(project_root)


@dataclass(frozen=True)
class PlaceCommand(BootstrapCommand):
    """
    Names the branch an item's branch goes on: the top of the line of unlanded items it
    builds on.
    """

    @classproperty
    def description(cls) -> str:
        return "Name the branch the item's branch goes on, and the stack below it"

    def declare_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--plan", required=True)
        parser.add_argument("--item", required=True)

    def run(self, arguments: argparse.Namespace, project_root: Path) -> StackPlacement:
        return ItemPlacementRequest(
            plan_identifier=arguments.plan, item_identifier=arguments.item
        ).place(project_root)


@dataclass(frozen=True)
class OpenCommand(BootstrapCommand):
    """
    Creates the item's branch and draft pull request, and records them.
    """

    @classproperty
    def description(cls) -> str:
        return "Create the item's branch and draft pull request"

    def declare_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--plan", required=True)
        parser.add_argument("--item", required=True)
        parser.add_argument("--branch", required=True)
        parser.add_argument("--base", required=True)
        parser.add_argument("--session", required=True)
        parser.add_argument(
            "--pull-request-title",
            help="Required unless --pull-request-number is given",
        )
        parser.add_argument(
            "--pull-request-body",
            type=Path,
            help="Required unless --pull-request-number is given",
        )
        parser.add_argument(
            "--pull-request-number",
            type=int,
            help="Record a pull request the caller already created, instead of creating one",
        )
        parser.add_argument("--remote", default="origin")

    def run(self, arguments: argparse.Namespace, project_root: Path) -> BootstrapReport:
        return WorkOpener(project_root=project_root, remote=arguments.remote).open(
            WorkOpenRequest(
                plan_identifier=arguments.plan,
                item_identifier=arguments.item,
                branch=arguments.branch,
                base_branch=arguments.base,
                session_url=arguments.session,
                pull_request_title=arguments.pull_request_title,
                pull_request_body=(
                    arguments.pull_request_body.read_text()
                    if arguments.pull_request_body
                    else None
                ),
                pull_request_number=arguments.pull_request_number,
            )
        )


@dataclass(frozen=True)
class UpdateCommand(BootstrapCommand):
    """
    Sets an item's recorded fields, reading prose from files.
    """

    @classproperty
    def description(cls) -> str:
        return "Set an item's recorded fields"

    def declare_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--plan", required=True)
        parser.add_argument("--item", required=True)
        parser.add_argument("--status", type=ItemStatus)
        parser.add_argument("--branch")
        parser.add_argument("--pull-request-number", type=int)
        parser.add_argument("--session")
        notes = parser.add_mutually_exclusive_group()
        notes.add_argument(
            "--notes", type=Path, help="A file whose text becomes the item's notes"
        )
        notes.add_argument(
            "--append-notes",
            type=Path,
            help="A file whose text is added to the item's notes as a new paragraph",
        )
        parser.add_argument(
            "--blockers",
            type=Path,
            action="append",
            help="A file whose text becomes one blocker; repeat for each",
        )

    def run(self, arguments: argparse.Namespace, project_root: Path) -> BootstrapReport:
        values_by_key: dict[ManifestKey, Any] = {
            manifest_key: value
            for manifest_key, value in (
                (ManifestKey.STATUS, arguments.status),
                (ManifestKey.BRANCH, arguments.branch),
                (ManifestKey.PULL_REQUEST_NUMBER, arguments.pull_request_number),
                (ManifestKey.SESSION, arguments.session),
            )
            if value is not None
        }
        if arguments.notes:
            values_by_key[ManifestKey.NOTES] = arguments.notes.read_text()
        if arguments.blockers:
            values_by_key[ManifestKey.BLOCKERS] = [
                blocker.read_text().strip() for blocker in arguments.blockers
            ]
        return ItemUpdateRequest(
            plan_identifier=arguments.plan,
            item_identifier=arguments.item,
            values_by_key=values_by_key,
            notes_to_append=(
                arguments.append_notes.read_text() if arguments.append_notes else None
            ),
        ).update(project_root)


@dataclass(frozen=True)
class CheckCommand(BootstrapCommand):
    """
    Reports which of an item's recorded fields local git contradicts.
    """

    @classproperty
    def description(cls) -> str:
        return "Report which recorded fields local git contradicts"

    def declare_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--plan", required=True)
        parser.add_argument("--item", required=True)
        parser.add_argument("--remote", default="origin")

    def run(self, arguments: argparse.Namespace, project_root: Path) -> StalenessReport:
        return ItemCheckRequest(
            plan_identifier=arguments.plan,
            item_identifier=arguments.item,
            remote=arguments.remote,
        ).check(project_root)


@dataclass(frozen=True)
class ResolveCommand(BootstrapCommand):
    """
    Names the plan and the items a branch belongs to.
    """

    @classproperty
    def description(cls) -> str:
        return "Name the plan and items a branch belongs to"

    def declare_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--branch", required=True)

    def run(self, arguments: argparse.Namespace, project_root: Path) -> BranchReport:
        return BranchReport.of_branch(arguments.branch, project_root)


@dataclass(frozen=True)
class BlockCommand(BootstrapCommand):
    """
    Records an owner's blocker on every item a branch carries.
    """

    @classproperty
    def description(cls) -> str:
        return "Record your own blocker on every item a branch carries"

    def declare_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--branch", required=True)
        parser.add_argument("--owner", required=True)
        parser.add_argument(
            "--reason",
            required=True,
            type=Path,
            help="A file whose text is the blocker",
        )

    def run(self, arguments: argparse.Namespace, project_root: Path) -> BranchReport:
        return OwnedBlocker(
            branch=arguments.branch,
            owner=arguments.owner,
            reason=arguments.reason.read_text().strip(),
        ).write(project_root)


@dataclass(frozen=True)
class UnblockCommand(BootstrapCommand):
    """
    Withdraws an owner's blocker from every item a branch carries.
    """

    @classproperty
    def description(cls) -> str:
        return "Clear your own blocker from every item a branch carries"

    def declare_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--branch", required=True)
        parser.add_argument("--owner", required=True)

    def run(self, arguments: argparse.Namespace, project_root: Path) -> BranchReport:
        return OwnedBlocker(branch=arguments.branch, owner=arguments.owner).write(
            project_root
        )
