"""
What a session writes each upstream summary from, read for it in one go.

A summary is a reading of the change, but not of every line of it: the fork pull
request's own title and description, its commit subjects and which files it changes by
how much are what an upstream reviewer's first paragraph is written from. Each is
capped, so the context for a branch stays the same size however large its change is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler_domain_model.github.pull_request_fields import (
    PullRequestField,
)
from basstler_domain_model.git.commands import (
    GitCommandRunner,
)
from basstler_domain_model.github.repository_client import (
    PullRequestReader,
)
from basstler.stack.model import Branch, Stack

# %% how much a branch changes


@dataclass(frozen=True)
class FileChange:
    """
    One file a branch changes, and by how many lines.
    """

    path: str
    """
    The file, relative to the repository root.
    """

    insertions: int
    """
    Lines added, zero for a binary file.
    """

    deletions: int
    """
    Lines removed, zero for a binary file.
    """

    @classmethod
    def from_numstat_line(cls, line: str) -> FileChange:
        """
        :param line: One line of ``git diff --numstat``: added, removed, path, tab
            separated, with ``-`` for the counts of a binary file.
        :return: The change it describes.
        """
        insertions, deletions, path = line.split("\t", 2)
        return cls(
            path,
            int(insertions) if insertions.isdigit() else 0,
            int(deletions) if deletions.isdigit() else 0,
        )

    @property
    def changed_lines(self) -> int:
        """
        Lines added and removed together.
        """
        return self.insertions + self.deletions


@dataclass(frozen=True)
class ChangeSize:
    """
    How much a branch changes, with only its largest files named.
    """

    files_changed: int
    """
    How many files it changes.
    """

    insertions: int
    """
    Lines added across every file.
    """

    deletions: int
    """
    Lines removed across every file.
    """

    largest_files: tuple[FileChange, ...]
    """
    The files with the most changed lines, largest first.
    """

    @classmethod
    def of(cls, changes: Sequence[FileChange], file_limit: int) -> ChangeSize:
        """
        :param changes: Every file the branch changes.
        :param file_limit: How many of the largest to name.
        :return: The size of the change.
        """
        largest = sorted(changes, key=lambda change: -change.changed_lines)
        return cls(
            files_changed=len(changes),
            insertions=sum(change.insertions for change in changes),
            deletions=sum(change.deletions for change in changes),
            largest_files=tuple(largest[:file_limit]),
        )


# %% one branch's context


@dataclass(frozen=True)
class PromotionContext:
    """
    Everything one upstream summary is written from.
    """

    pull_request_number: int
    """
    The fork pull request the summary is keyed by.
    """

    branch: str
    """
    The branch to be promoted.
    """

    compared_with: str
    """
    The reference its change is measured against: its parent on the fork, or the
    upstream's base once that parent has landed.
    """

    title: str
    """
    The fork pull request's title.
    """

    description: str
    """
    The fork pull request's description, cut short at the reader's limit.
    """

    commit_subjects: tuple[str, ...]
    """
    The subjects of the branch's own commits, oldest first, merges left out.
    """

    commits_not_listed: int
    """
    How many more commits there are than :attr:`commit_subjects` lists.
    """

    change_size: ChangeSize
    """
    How much the branch changes.
    """


@dataclass(frozen=True)
class PromotionContextReader:
    """
    Reads the :class:`PromotionContext` of every branch awaiting its upstream link.
    """

    stack: Stack
    """
    The derived stack the branches belong to.
    """

    git: GitCommandRunner
    """
    The checkout whose fork and upstream references the branches are compared in.
    """

    fork: PullRequestReader
    """
    The fork, read for each pull request's title and description.
    """

    file_limit: int = 8
    """
    How many of a branch's largest files to name.
    """

    commit_limit: int = 12
    """
    How many of a branch's commit subjects to list.
    """

    description_limit: int = 1200
    """
    How many characters of a pull request's description to keep.
    """

    @classproperty
    def truncation_marker(cls) -> str:
        """
        What a shortened description ends with.
        """
        return " [...]"

    def read(self) -> list[PromotionContext]:
        """
        :return: The context of every branch awaiting its upstream link, in dependency
            order.
        """
        return [
            self.context_of(branch) for branch in self.stack.awaiting_promotion_link()
        ]

    def compared_with(self, branch: Branch) -> str:
        """
        :param branch: A branch to be promoted.
        :return: The reference its change is measured against.
        """
        configuration = self.stack.configuration
        if self.stack.has_landed_upstream(branch.parent):
            return configuration.upstream_base_reference
        return configuration.reference_on_fork(branch.parent)

    def context_of(self, branch: Branch) -> PromotionContext:
        """
        :param branch: A branch to be promoted.
        :return: What its upstream summary is written from.
        """
        number = branch.pull_request_number
        pull_request = self.fork.pull_request(number)
        compared_with = self.compared_with(branch)
        tip = self.stack.configuration.reference_on_fork(branch.name)
        subjects = self.git.run(
            "log", "--no-merges", "--reverse", "--format=%s", f"{compared_with}..{tip}"
        ).splitlines()
        changes_per_file = self.git.run("diff", "--numstat", f"{compared_with}...{tip}")
        return PromotionContext(
            pull_request_number=number,
            branch=branch.name,
            compared_with=compared_with,
            title=str(PullRequestField.TITLE.read(pull_request, number) or branch.name),
            description=self.shortened(
                str(PullRequestField.BODY.read(pull_request, number) or "")
            ),
            commit_subjects=tuple(subjects[: self.commit_limit]),
            commits_not_listed=max(len(subjects) - self.commit_limit, 0),
            change_size=ChangeSize.of(
                [
                    FileChange.from_numstat_line(line)
                    for line in changes_per_file.splitlines()
                ],
                self.file_limit,
            ),
        )

    def shortened(self, description: str) -> str:
        """
        :param description: A pull request's description.
        :return: It, cut at the last whole word within :attr:`description_limit` and
            marked as cut when it was.
        """
        if len(description) <= self.description_limit:
            return description
        kept = description[: self.description_limit].rsplit(maxsplit=1)[0]
        return f"{kept}{self.truncation_marker}"
