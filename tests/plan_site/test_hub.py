"""
Tests for plan_site/hub.py: the hub file, and reading a listed repository's notes
branch.
"""

from __future__ import annotations

from pathlib import Path

from basstler.locations import ProjectLocation
from basstler_domain_model.git.commands import (
    GitCommandRunner,
)
from basstler.plan_site.hub import NotesBranch, PlanHub
from basstler.plan.model import PlanDocument
from basstler_domain_model.repository import (
    Repository,
)
from basstler.setup.steps import PersonalNotesSetting

from ..constants.site import SitePlan, SiteRepository, SiteSampleText

REGISTRY = SiteRepository.NOTES.value
"""
The repository whose notes hold the hub file under test.
"""

PLAN_MANIFEST = SitePlan.FIRST.directory / PlanDocument.MANIFEST
"""
The manifest a published notes branch carries.
"""


def publish_notes(remote_root: Path, repository: Repository, files: dict[Path, str]):
    """
    Create a bare repository standing in for one on GitHub, whose notes branch holds
    ``files``.

    :param remote_root: The directory standing in for GitHub, as
        :meth:`Repository.clone_url_under` takes it.
    :param repository: The repository to create under it.
    :param files: File contents, keyed by their path inside the plans directory.
    """
    remote = Path(repository.clone_url_under(str(remote_root)))
    work = remote.with_suffix(".work")
    for directory in (remote, work):
        directory.mkdir(parents=True)
        GitCommandRunner(directory).run("init", "--quiet")
    GitCommandRunner(remote).run("config", "core.bare", "true")
    for relative_path, content in files.items():
        path = work / ProjectLocation.PLANS / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git = GitCommandRunner(work)
    git.run("add", "--all")
    git.run(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "n",
    )
    git.run(
        "push", "--quiet", str(remote), f"HEAD:{PersonalNotesSetting.BRANCH.default}"
    )


# %% the hub file


def test_register_lists_a_repository_once():
    hub = PlanHub(registry=str(REGISTRY))

    assert hub.register(str(REGISTRY))
    assert not hub.register(str(REGISTRY))
    assert hub.repositories == [str(REGISTRY)]


def test_the_hub_file_round_trips(tmp_path: Path):
    hub = PlanHub(
        registry=str(REGISTRY),
        repositories=[str(REGISTRY)],
        site_url=SiteSampleText.SITE_URL.value,
    )
    path = tmp_path / ProjectLocation.PLAN_HUB.name
    path.write_text(hub.to_text())

    assert PlanHub.load(path) == hub


def test_a_missing_hub_file_loads_as_none(tmp_path: Path):
    assert PlanHub.load(tmp_path / ProjectLocation.PLAN_HUB.name) is None


def test_the_hub_file_is_read_off_the_registry_notes_branch(tmp_path: Path):
    hub = PlanHub(registry=str(REGISTRY), repositories=[str(REGISTRY)])
    publish_notes(
        tmp_path, REGISTRY, {Path(ProjectLocation.PLAN_HUB.name): hub.to_text()}
    )

    assert PlanHub.from_registry(REGISTRY, str(tmp_path)) == hub


def test_a_registry_without_a_hub_file_reads_as_none(tmp_path: Path):
    publish_notes(tmp_path, REGISTRY, {})

    assert PlanHub.from_registry(REGISTRY, str(tmp_path)) is None


# %% a listed repository's notes


def test_a_fetched_notes_branch_reads_and_checks_out_its_plans(tmp_path: Path):
    manifest_path = Path(SitePlan.FIRST) / PlanDocument.MANIFEST
    publish_notes(tmp_path, REGISTRY, {manifest_path: PLAN_MANIFEST.read_text()})

    notes = NotesBranch.fetch(
        REGISTRY,
        PersonalNotesSetting.BRANCH.default,
        str(tmp_path),
        tmp_path / "scratch",
    )
    plans_directory = notes.check_out_plans()

    assert notes.read(ProjectLocation.PLANS / manifest_path) == (
        PLAN_MANIFEST.read_text().strip()
    )
    assert (plans_directory / manifest_path).read_text() == PLAN_MANIFEST.read_text()


def test_a_repository_that_cannot_be_fetched_reads_as_none(tmp_path: Path):
    notes = NotesBranch.fetch(
        SiteRepository.MISSING.value,
        PersonalNotesSetting.BRANCH.default,
        str(tmp_path),
        tmp_path / "scratch",
    )

    assert notes is None
