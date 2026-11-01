"""
What the release workflow tags a merge commit as, and its tag step against a scratch remote.
"""

from __future__ import annotations

from basstler_domain_model.github.vocabulary import RestJSONKey

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest

from basstler_domain_model.environment import (
    GitHubActionsVariable,
)
from basstler_domain_model.locations import (
    PathEnumeration,
)
from basstler_domain_model.git.commands import (
    GitCommandRunner,
)
from basstler.release_version import (
    MalformedVersion,
    MergedPullRequest,
    PlannedRelease,
    PullRequestLabel,
    Release,
    ReleaseVersion,
    VersionPart,
)

from .constants.environment import SearchPathVariable
from .constants.paths import RepositoryLocation
from .script_runner import BashCommandRunner
from .scratch_repository import ScratchRepository, initialize_bare_repository
from .workflow_definition import Workflow


class ReleaseSampleVersion(StrEnum):
    """
    The versions the release-version tests tag or expect.
    """

    RELEASED = "1.0.0"
    """
    The version the scratch checkout is tagged at.
    """

    NEXT_PATCH = "1.0.1"
    """
    :attr:`RELEASED` with its patch part bumped: what a merge releases unless labelled.
    """

    NEXT_MINOR = "1.1.0"
    """
    :attr:`RELEASED` with its minor part bumped.
    """

    NEXT_MAJOR = "2.0.0"
    """
    :attr:`RELEASED` with its major part bumped.
    """

    UNMERGED = "3.0.0"
    """
    A version tagged on a branch the checked-out commit does not contain.
    """

    FIRST = "0.1.0"
    """
    What a repository with no release yet releases.
    """


class ReleaseSampleTag(StrEnum):
    """
    Tags the release-version tests put on a scratch checkout that release nothing.
    """

    NOT_A_RELEASE = "nightly"
    """
    A tag that names no version.
    """

    MALFORMED = "v1.x"
    """
    A tag shaped like a release whose version is not one.
    """


class ReleaseSampleLabel(StrEnum):
    """
    A label that chooses no part.
    """

    UNRELATED = "bug"
    """
    A label every repository has, which releases a patch.
    """


class ReleaseStepCommand(StrEnum):
    """
    The commands that pick out the release workflow's tag step.
    """

    PUSH = "git push"
    """
    Only the tag step pushes.
    """


class ReleaseSampleFile(PathEnumeration):
    """
    The files of a scratch checkout the release-version tests change, relative to its
    root.
    """

    PACKAGE_MODULE = Path("src") / "sample" / "__init__.py"
    """
    A module of the scratch checkout's package.
    """

    SKILL = Path("plugin") / "skills" / "sample" / "SKILL.md"
    """
    A skill of the scratch checkout's plugin.
    """

    DOCUMENTATION = Path("docs") / "guide.md"
    """
    Documentation, which no install runs but which still makes a new release.
    """


CHANGE = "changed = True\n"
"""
What a test writes to change a file.
"""


def version(sample: ReleaseSampleVersion) -> ReleaseVersion:
    """
    :param sample: A sample version.
    :return: It, parsed.
    """
    return ReleaseVersion.parse(sample)


def pull_request_json(number: int, *labels: str) -> dict[str, Any]:
    """
    :param number: The pull request's number.
    :param labels: The names of its labels.
    :return: The pull request as GitHub's ``commits/<sha>/pulls`` returns one.
    """
    return {
        RestJSONKey.NUMBER: number,
        RestJSONKey.LABELS: [{RestJSONKey.NAME: label} for label in labels],
    }


def merged(number: int, *labels: str) -> MergedPullRequest:
    """
    :param number: The pull request's number.
    :param labels: The names of its labels.
    :return: The pull request, parsed.
    """
    return MergedPullRequest.from_json(pull_request_json(number, *labels))


def change_and_commit(repository: ScratchRepository, path: Path) -> None:
    """
    :param repository: The scratch checkout.
    :param path: The file to change, relative to its root.
    """
    repository.write(path, CHANGE)
    repository.commit_everything(f"Change {path}")


@pytest.fixture
def released_checkout(tmp_path) -> ScratchRepository:
    """
    :return: A scratch checkout with a package, a plugin and some documentation, tagged
        at :attr:`ReleaseSampleVersion.RELEASED`.
    """
    repository = ScratchRepository.create(tmp_path)
    for path in ReleaseSampleFile:
        repository.write(path, "")
    repository.commit_everything("Release")
    repository.git.run("tag", version(ReleaseSampleVersion.RELEASED).tag)
    return repository


def release_of(repository: ScratchRepository) -> Release:
    """
    :param repository: The scratch checkout.
    :return: Its release.
    """
    return Release(repository.project_root)


# %% the latest release


def test_the_latest_release_is_the_highest_tag_the_commit_contains(released_checkout):
    """
    Tags that name no version, and releases on a branch never merged, are not releases
    of this commit.
    """
    released_checkout.git.run("tag", ReleaseSampleTag.NOT_A_RELEASE)
    released_checkout.git.run("tag", ReleaseSampleTag.MALFORMED)
    released_checkout.git.run("checkout", "--quiet", "-b", "side")
    change_and_commit(released_checkout, ReleaseSampleFile.DOCUMENTATION)
    released_checkout.git.run("tag", version(ReleaseSampleVersion.UNMERGED).tag)
    released_checkout.git.run("checkout", "--quiet", "-")

    assert release_of(released_checkout).latest_release() == version(
        ReleaseSampleVersion.RELEASED
    )


def test_there_is_no_latest_release_before_the_first(tmp_path):
    repository = ScratchRepository.create(tmp_path)
    repository.write(ReleaseSampleFile.PACKAGE_MODULE, "")
    repository.commit_everything("First")

    assert release_of(repository).latest_release() is None


def test_the_unreleased_commits_are_the_first_parent_line_since_the_latest_release(
    released_checkout,
):
    """
    A merge commit stands for its whole pull request, so the commits merged in are not
    listed: each listed commit is one GitHub associates a pull request with.
    """
    released_checkout.git.run("checkout", "--quiet", "-b", "feature")
    change_and_commit(released_checkout, ReleaseSampleFile.SKILL)
    released_checkout.git.run("checkout", "--quiet", "-")
    change_and_commit(released_checkout, ReleaseSampleFile.DOCUMENTATION)
    direct_push = released_checkout.git.commit_at("HEAD")
    released_checkout.git.run("merge", "--quiet", "--no-ff", "--no-edit", "feature")
    merge = released_checkout.git.commit_at("HEAD")

    assert release_of(released_checkout).unreleased_commits() == (direct_push, merge)


# %% what a merge releases


def test_nothing_changed_since_the_latest_release_releases_nothing(released_checkout):
    assert release_of(released_checkout).plan(()) is None


def test_a_commit_a_release_already_contains_releases_nothing(released_checkout):
    """
    The case is a run started for a merge that a later merge's run, or a re-run, has
    already released.
    """
    change_and_commit(released_checkout, ReleaseSampleFile.PACKAGE_MODULE)
    change_and_commit(released_checkout, ReleaseSampleFile.SKILL)
    released_checkout.git.run("tag", version(ReleaseSampleVersion.NEXT_PATCH).tag)
    released_checkout.git.run("checkout", "--quiet", "HEAD~1")

    assert release_of(released_checkout).plan(()) is None


@pytest.mark.parametrize(
    "changed_file", list(ReleaseSampleFile), ids=lambda p: p._name_
)
def test_any_change_is_released_as_the_next_patch(released_checkout, changed_file):
    """
    The reported case was a hook gaining a call into the package while the plugin kept
    installing the release from before it.

    Any other change counts the same, so no list of shipped paths can leave one out.
    """
    change_and_commit(released_checkout, changed_file)

    planned = release_of(released_checkout).plan(
        (merged(1, ReleaseSampleLabel.UNRELATED),)
    )

    assert planned == PlannedRelease(version(ReleaseSampleVersion.NEXT_PATCH))


@pytest.mark.parametrize(
    "labels, expected",
    [
        ((VersionPart.MINOR.label,), ReleaseSampleVersion.NEXT_MINOR),
        ((VersionPart.MAJOR.label,), ReleaseSampleVersion.NEXT_MAJOR),
        (
            (VersionPart.MINOR.label, VersionPart.MAJOR.label),
            ReleaseSampleVersion.NEXT_MAJOR,
        ),
    ],
)
def test_a_labelled_pull_request_releases_its_part(released_checkout, labels, expected):
    change_and_commit(released_checkout, ReleaseSampleFile.PACKAGE_MODULE)

    planned = release_of(released_checkout).plan((merged(1, *labels),))

    assert planned == PlannedRelease(version(expected))


def test_several_merged_pull_requests_release_the_most_significant_part(
    released_checkout,
):
    """
    A run a later merge cancelled while it waited is released by that merge's run, so
    a minor label on the earlier pull request still counts.
    """
    change_and_commit(released_checkout, ReleaseSampleFile.PACKAGE_MODULE)

    planned = release_of(released_checkout).plan(
        (merged(1, VersionPart.MINOR.label), merged(2))
    )

    assert planned == PlannedRelease(version(ReleaseSampleVersion.NEXT_MINOR))


def test_the_first_release_is_the_first_version(tmp_path):
    repository = ScratchRepository.create(tmp_path)
    repository.write(ReleaseSampleFile.PACKAGE_MODULE, "")
    repository.commit_everything("First")

    planned = release_of(repository).plan(())

    assert planned == PlannedRelease(version(ReleaseSampleVersion.FIRST))


def test_a_planned_release_prints_its_tag():
    planned = PlannedRelease(version(ReleaseSampleVersion.NEXT_PATCH))

    assert planned.output_lines() == f"tag=v{ReleaseSampleVersion.NEXT_PATCH}"


# %% reading the merged pull requests


def test_a_pull_request_reached_by_several_commits_is_read_once(tmp_path):
    """
    A rebase merge puts each of a pull request's commits on the first-parent line, and
    GitHub returns the pull request for each.
    """
    listed = tmp_path / "pull-requests.json"
    listed.write_text(
        json.dumps(
            [
                pull_request_json(1, VersionPart.MINOR.label),
                pull_request_json(1, VersionPart.MINOR.label),
                pull_request_json(2),
            ]
        )
    )

    assert MergedPullRequest.read_all(listed) == (
        merged(1, VersionPart.MINOR.label),
        merged(2),
    )


@pytest.mark.parametrize("part", list(VersionPart), ids=str)
def test_a_part_s_own_label_releases_it(part):
    assert PullRequestLabel(part.label).part is part


def test_any_other_label_releases_a_patch():
    assert PullRequestLabel(ReleaseSampleLabel.UNRELATED).part is VersionPart.PATCH


# %% the workflow's tag step


def release_step_script() -> str:
    """
    :return: The shell the release workflow runs to tag and push a release.
    """
    (job,) = Workflow.read(RepositoryLocation.RELEASE_WORKFLOW).jobs.values()
    return next(
        step.command
        for step in job.steps
        if step.command and ReleaseStepCommand.PUSH in step.command
    )


@dataclass(frozen=True)
class ReleaseStepFiles:
    """
    The files the workflow hands its tag step, standing in for a runner's.
    """

    directory: Path
    """
    Where they are, standing in for ``$RUNNER_TEMP``.
    """

    @property
    def summary(self) -> Path:
        """:return: The file standing in for ``$GITHUB_STEP_SUMMARY``."""
        return self.directory / "summary.md"

    @property
    def output(self) -> Path:
        """:return: The file standing in for ``$GITHUB_OUTPUT``."""
        return self.directory / "output"

    @property
    def pull_requests(self) -> Path:
        """:return: What the step before it read from GitHub."""
        return self.directory / "pull-requests.json"


def run_release_step(
    checkout: Path, files: ReleaseStepFiles, *labels: str
) -> subprocess.CompletedProcess[str]:
    """
    Run the tag step in *checkout*, as the workflow does, with this interpreter's
    package on the path.

    :param checkout: A clone whose ``origin`` is the scratch remote.
    :param files: The files the step reads and writes.
    :param labels: The labels of the one pull request merged since the latest release.
    :return: The finished step.
    """
    files.pull_requests.write_text(json.dumps([pull_request_json(1, *labels)]))
    return BashCommandRunner(project_root=checkout, script=release_step_script()).run(
        **{
            SearchPathVariable.EXECUTABLE_SEARCH_PATH: os.pathsep.join(
                [
                    str(Path(sys.executable).parent),
                    os.environ[SearchPathVariable.EXECUTABLE_SEARCH_PATH],
                ]
            ),
            GitHubActionsVariable.STEP_SUMMARY: str(files.summary),
            GitHubActionsVariable.OUTPUT: str(files.output),
            GitHubActionsVariable.RUNNER_TEMPORARY_DIRECTORY: str(files.directory),
        }
    )


@dataclass(frozen=True)
class PublishedCheckout:
    """
    A released checkout pushed to a scratch remote, and a clone of that remote standing in
    for the runner's checkout.
    """

    repository: ScratchRepository
    """
    The released checkout, with the scratch remote as its ``origin``.
    """

    runner: GitCommandRunner
    """
    Git in the runner's clone of the scratch remote.
    """

    def remote_commit(self, reference: str) -> str:
        """
        :param reference: A branch or tag on the scratch remote.
        :return: The commit it names there, or an empty string when there is none.
        """
        return self.runner.run("ls-remote", "origin", reference).split("\t", 1)[0]

    def merge(self) -> str:
        """
        Push a change to the remote's ``main``, and check it out in the runner as the
        workflow's checkout does.

        :return: The merged commit.
        """
        change_and_commit(self.repository, ReleaseSampleFile.PACKAGE_MODULE)
        self.repository.git.run("push", "--quiet", "origin", "HEAD:main")
        self.runner.run("fetch", "--quiet", "--tags", "origin")
        self.runner.run("checkout", "--quiet", "--detach", "origin/main")
        return self.repository.git.commit_at("HEAD")


@pytest.fixture
def published_checkout(released_checkout, tmp_path) -> PublishedCheckout:
    """
    :return: The released checkout, published to a scratch remote with its tag.
    """
    remote = initialize_bare_repository(tmp_path / "origin.git")
    released_checkout.git.run("push", "--quiet", "--tags", str(remote), "HEAD:main")
    runner = tmp_path / "runner"
    GitCommandRunner(tmp_path).run(
        "clone", "--quiet", "--branch", "main", str(remote), str(runner)
    )
    released_checkout.add_remote("origin", str(remote))
    return PublishedCheckout(released_checkout, GitCommandRunner(runner))


@pytest.fixture
def release_step_files(tmp_path) -> ReleaseStepFiles:
    """
    :return: An empty directory for the step's files.
    """
    directory = tmp_path / "runner-temp"
    directory.mkdir()
    return ReleaseStepFiles(directory)


def test_the_release_step_tags_the_merge_commit_and_pushes_nothing_else(
    published_checkout, release_step_files
):
    """
    The merge commit is the release, so a plugin synced from the merge already has it:
    ``main`` is left where the merge put it.
    """
    merge_commit = published_checkout.merge()

    result = run_release_step(
        published_checkout.runner.working_directory,
        release_step_files,
        VersionPart.MINOR.label,
    )

    assert result.returncode == 0, result.stderr
    tag = version(ReleaseSampleVersion.NEXT_MINOR).tag
    assert published_checkout.remote_commit("refs/heads/main") == merge_commit
    assert published_checkout.remote_commit(f"refs/tags/{tag}") == merge_commit
    assert release_step_files.output.read_text() == f"tag={tag}\n"


def test_the_release_step_does_nothing_when_nothing_changed(
    published_checkout, release_step_files
):
    result = run_release_step(
        published_checkout.runner.working_directory, release_step_files
    )

    assert result.returncode == 0, result.stderr
    assert "Nothing changed" in release_step_files.summary.read_text()
    assert not release_step_files.output.exists()


# %% versions


@pytest.mark.parametrize(
    "part, expected",
    [
        (VersionPart.PATCH, ReleaseSampleVersion.NEXT_PATCH),
        (VersionPart.MINOR, ReleaseSampleVersion.NEXT_MINOR),
        (VersionPart.MAJOR, ReleaseSampleVersion.NEXT_MAJOR),
    ],
)
def test_a_version_bumps_by_the_part_named(part, expected):
    assert version(ReleaseSampleVersion.RELEASED).bumped(part) == version(expected)


def test_a_version_that_is_not_three_numbers_is_refused_with_a_correction():
    with pytest.raises(MalformedVersion, match="Suggestion: "):
        ReleaseVersion.parse(f"v{ReleaseSampleVersion.RELEASED}")
