"""
The branch of the fork the upstream-reviews Action lives on, and installing it.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler_domain_model.git.commands import (
    GitCommandRunner,
    ProposedPush,
)
from basstler.render_common import create_template_environment
from basstler_domain_model.repository import (
    Repository,
)
from basstler.upstream_reviews.vocabulary import (
    ActionBranch,
    ActionFile,
    BasstlerSource,
)

# %% the branch


@dataclass(frozen=True)
class RemoteBranch:
    """
    The Action branch on one remote, read and written without touching the working tree.
    """

    git: GitCommandRunner
    """
    The clone to work in.
    """

    remote: str
    """
    The fork's remote.
    """

    @property
    def fork(self) -> Repository | None:
        """
        :return: The repository the remote points at, ``None`` when it names none - a
            remote given as a local path.
        """
        url = self.git.attempt("remote", "get-url", self.remote).output or self.remote
        if not Repository.names_a_repository(url):
            return None
        return Repository.from_remote_url(url)

    @property
    def description(self) -> str:
        """:return: The fork's name, or the remote's where it names none."""
        fork = self.fork
        return fork.full_name if fork else self.remote

    def fetch(self) -> str | None:
        """
        :return: The branch's tip commit, or ``None`` when the remote has no such branch.
        """
        if not self.git.attempt(
            "ls-remote", "--heads", self.remote, ActionBranch.NAME
        ).output:
            return None
        self.git.fetch(
            self.remote,
            "--no-tags",
            "--no-write-fetch-head",
            f"+refs/heads/{ActionBranch.NAME}:{ActionBranch.LOCAL_REFERENCE}",
        )
        return self.git.commit_at(ActionBranch.LOCAL_REFERENCE)

    def read(self, commit: str, path: ActionFile) -> str | None:
        """
        :param commit: The commit to read from.
        :param path: The file.
        :return: Its text without the final newline, or ``None`` when the commit has
            no such file.
        """
        shown = self.git.attempt("show", f"{commit}:{path}")
        return shown.output if shown.succeeded else None

    def commit(
        self, parent: str | None, files: dict[ActionFile, str], message: str
    ) -> str:
        """
        Commit files on top of the branch, keeping every file not given.

        :param parent: The commit to build on, ``None`` for the branch's first.
        :param files: What to write, by path, each without its final newline.
        :param message: The commit message.
        :return: The new commit.
        """
        with tempfile.TemporaryDirectory() as scratch:
            index = {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
            if parent is None:
                self.git.run("read-tree", "--empty", environment=index)
            else:
                self.git.run("read-tree", parent, environment=index)
            for path, text in files.items():
                blob_file = Path(scratch) / "blob"
                blob_file.write_text(text + "\n")
                blob = self.git.run("hash-object", "-w", str(blob_file))
                self.git.run(
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"100644,{blob},{path}",
                    environment=index,
                )
            tree = self.git.run("write-tree", environment=index)
        parents = ["-p", parent] if parent else []
        return self.git.run("commit-tree", tree, *parents, "-m", message)

    def write(
        self, files: dict[ActionFile, str], message: str, attempts: int = 3
    ) -> str:
        """
        Commit files onto the branch's latest tip and push them, again on top of a run's
        own commit when that landed first.

        :param files: What to write, by path, each without its final newline.
        :param message: The commit message.
        :param attempts: How many times to commit and push before giving up.
        :return: The pushed commit.
        :raises GitCommandFailed: If the last push is refused too.
        """
        for _ in range(attempts):
            commit = self.commit(self.fetch(), files, message)
            pushed = self.git.push(
                ProposedPush(self.remote, f"{commit}:refs/heads/{ActionBranch.NAME}")
            )
            if pushed.succeeded:
                return commit
        pushed.raise_if_failed()
        return commit


# %% installing


class ActionTemplate(StrEnum):
    """
    The templates in :attr:`PackageLocation.TEMPLATES` the Action's files are rendered
    from.
    """

    WORKFLOW = "upstream_reviews_action_workflow.yml"
    """
    The caller workflow.
    """

    README = "upstream_reviews_action_readme.md"
    """
    What a reader browsing the fork's branches finds there.
    """

    def render(self, **context: Any) -> str:
        """
        :param context: The values the template substitutes, by name.
        :return: The rendered file, without its final newline.
        """
        return create_template_environment().get_template(self.value).render(**context)


class InstallationState(StrEnum):
    """
    How the fork's Action compares with the release installing it.
    """

    MISSING = "missing"
    STALE = "stale"
    CURRENT = "current"


@dataclass(frozen=True)
class ActionInstallation:
    """
    The Action as one basstler release writes it.
    """

    branch_or_tag: str
    """
    The basstler branch or release tag the workflow runs.
    """

    @classproperty
    def branch_or_tag_line(cls) -> re.Pattern[str]:
        """
        The line of an installed workflow naming the basstler branch or tag it runs.
        """
        return re.compile(
            r"^\s*basstler_branch_or_tag:\s*(?P<branch_or_tag>\S+)\s*$", re.MULTILINE
        )

    @property
    def workflow(self) -> str:
        """:return: The caller workflow."""
        return ActionTemplate.WORKFLOW.render(
            branch=ActionBranch.NAME,
            request=ActionFile.REQUEST,
            repository=BasstlerSource.REPOSITORY,
            reusable_workflow=BasstlerSource.REUSABLE_WORKFLOW,
            branch_or_tag=self.branch_or_tag,
        )

    @property
    def files(self) -> dict[ActionFile, str]:
        """:return: What installing writes."""
        return {
            ActionFile.WORKFLOW: self.workflow,
            ActionFile.README: ActionTemplate.README.render(
                request=ActionFile.REQUEST,
                workflow=ActionFile.WORKFLOW,
                result=ActionFile.RESULT,
            ),
        }

    def state(self, branch: RemoteBranch) -> tuple[InstallationState, str | None]:
        """
        :param branch: The fork's branch.
        :return: How it compares, and the branch or tag it runs when it is installed at
            all.
        """
        tip = branch.fetch()
        workflow = branch.read(tip, ActionFile.WORKFLOW) if tip else None
        if workflow is None:
            return InstallationState.MISSING, None
        match = self.branch_or_tag_line.search(workflow)
        installed = match["branch_or_tag"] if match else None
        if workflow == self.workflow:
            return InstallationState.CURRENT, installed
        return InstallationState.STALE, installed

    def install(self, branch: RemoteBranch) -> bool:
        """
        :param branch: The fork's branch.
        :return: Whether anything had to be pushed.
        """
        if self.state(branch)[0] is InstallationState.CURRENT:
            return False
        branch.write(
            self.files, f"Run basstler {self.branch_or_tag}'s upstream-reviews Action"
        )
        return True
