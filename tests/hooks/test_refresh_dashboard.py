"""
Tests for refresh_dashboard.sh's own orchestration logic: argument parsing, the
correction-triggers-a-push gate, and the optional --tracking-url passthrough.

The two modules it calls (basstler.plan.sync_manifest_status, basstler.dashboard.plan_page) and
the personal-notes push (write-personal-notes-file.sh) are replaced with stubs in a
scratch project-root layout, so these tests exercise only refresh_dashboard.sh's own
shell logic - no real git remote, network access, or GitHub data is involved.
basstler.dashboard.refresh_support has no such dependencies, so the real module is reused
unchanged, with the logging module it prints through.
"""

import json
import shutil
import subprocess
from enum import StrEnum
from pathlib import Path
from types import ModuleType

import pytest

import basstler.dashboard.exceptions
import basstler.dashboard.plan_page
import basstler.dashboard.publication
import basstler.dashboard.refresh_support
import basstler.environment
import basstler.exceptions
import basstler.exit_codes
import basstler.locations
import basstler.plan.sync_manifest_status
import basstler.standard_streams
from basstler.dashboard.publication import PublicationCheck, PublicationField
from basstler.locations import PackageLocation, PluginLocation, ProjectLocation
from basstler.plan.model import PlanDocument

from ..constants.environment import FileMode, ScrubbedEnvironmentPrefix
from ..constants.paths import DatasetLocation, RepositoryLocation, SkillDirectory
from ..scratch_repository import PackageSourceFile, install_hook_scripts_into
from ..script_runner import BashScriptRunner


def path_within_package(module: ModuleType) -> Path:
    """
    :param module: A module of this package.
    :return: Its file, relative to the package directory.
    """
    return Path(module.__file__).relative_to(PackageLocation.DIRECTORY.value)


class StubbedScript(StrEnum):
    """
    The package modules refresh_dashboard.sh calls out to, each stood in for by a stub
    that records how it was called.
    """

    SYNC_MANIFEST_STATUS = str(path_within_package(basstler.plan.sync_manifest_status))
    """
    Reads the manifest and corrects statuses that drifted.
    """

    BUILD_DASHBOARD = str(path_within_package(basstler.dashboard.plan_page))
    """
    Renders the page.
    """

    @property
    def invocation_record(self) -> str:
        """
        The file this script's stub records its own arguments to.
        """
        return f"{Path(self).stem}_invocation.json"


class RefreshDashboardOption(StrEnum):
    """
    The options refresh_dashboard.sh takes that only the script defines.
    """

    PLANS_DIRECTORY = "--plans-dir"
    """
    The directory holding every plan, forwarded to both scripts it calls.
    """

    URL_CACHE = "--url-cache"
    """
    The dashboard-URL cache the render is checked against.
    """


@pytest.fixture
def scratch_project_root(tmp_path: Path) -> Path:
    """
    Build a scratch layout refresh_dashboard.sh can run against unmodified: a scratch
    basstler checkout holding the real script plus resolve-personal-notes-config.sh, and
    stand-ins for every script/hook it calls out to, beside the directory it runs in.

    :param tmp_path: pytest's per-test temporary directory.
    :return: The directory the script runs in.
    """
    checkout_root = scratch_checkout_root(tmp_path)
    plugin_root = checkout_root / RepositoryLocation.PLUGIN.value.name
    plan_dashboard_directory = plugin_root / SkillDirectory.PLAN_DASHBOARD
    hooks_directory = plugin_root / PluginLocation.HOOKS.value.name
    package_directory = (
        checkout_root / PackageSourceFile.SOURCE / PackageLocation.DIRECTORY.value.name
    )
    plan_dashboard_directory.mkdir(parents=True)
    package_directory.mkdir(parents=True)
    shutil.copy(
        DatasetLocation.SET_UP_CHECKOUT / PackageSourceFile.METADATA,
        checkout_root / PackageSourceFile.METADATA,
    )

    shutil.copy(
        RepositoryLocation.PLUGIN / SkillDirectory.DASHBOARD_REFRESH_SCRIPT,
        plan_dashboard_directory / SkillDirectory.DASHBOARD_REFRESH_SCRIPT.name,
    )
    install_hook_scripts_into(
        plugin_root, PluginLocation.PERSONAL_NOTES_CONFIGURATION_SCRIPT.name
    )

    # The real support module and the modules it imports, whose own dependencies are
    # the standard library and PyYAML only, in a scratch package the script's
    # `python3 -m basstler.<module>` calls resolve against.
    copies = {
        Path(module.__file__): package_directory / path_within_package(module)
        for module in (
            basstler.dashboard.refresh_support,
            basstler.dashboard.publication,
            basstler.dashboard.exceptions,
            basstler.exceptions,
            basstler.exit_codes,
            basstler.environment,
            basstler.locations,
            basstler.standard_streams,
        )
    } | {
        DatasetLocation.STUBS
        / "sync_manifest_status_stub.py": package_directory
        / StubbedScript.SYNC_MANIFEST_STATUS,
        DatasetLocation.STUBS
        / "build_dashboard_stub.py": package_directory
        / StubbedScript.BUILD_DASHBOARD,
    }
    initializer = "__init__.py"
    for source, destination in copies.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        (destination.parent / initializer).touch()
        shutil.copy(source, destination)
    (package_directory / initializer).touch()
    shutil.copy(
        DatasetLocation.STUBS / "write_personal_notes_file_stub.sh",
        hooks_directory / PluginLocation.PERSONAL_NOTES_WRITER_SCRIPT.name,
    )
    (hooks_directory / PluginLocation.PERSONAL_NOTES_WRITER_SCRIPT.name).chmod(
        FileMode.EXECUTABLE
    )

    return tmp_path


def _refresh_dashboard_script_path(scratch_project_root: Path) -> Path:
    """
    The scratch layout's copy of refresh_dashboard.sh.

    :param scratch_project_root: A fixture-built scratch project root.
    """
    return (
        scratch_checkout_root(scratch_project_root)
        / RepositoryLocation.PLUGIN.value.name
        / SkillDirectory.DASHBOARD_REFRESH_SCRIPT
    )


def scratch_checkout_root(scratch_project_root: Path) -> Path:
    """
    :param scratch_project_root: The directory the script runs in.
    :return: The scratch basstler checkout the script sits in, inside it.
    """
    return scratch_project_root / "basstler-checkout"


def scrubbed_environment() -> dict[str, str]:
    """
    :return: This process's environment without anything that would point the script
        at another project, plugin or personal-notes setting.
    """
    return BashScriptRunner(
        project_root=Path(),
        removed_variable_prefixes=tuple(ScrubbedEnvironmentPrefix),
        script_path=Path(),
    ).environment({})


def run_refresh_dashboard(
    scratch_project_root: Path, corrected_ids: list[str], extra_arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    """
    Run the scratch layout's refresh_dashboard.sh, working directory set to the scratch
    root so any relative file it writes lands there.

    :param scratch_project_root: A fixture-built scratch project root.
    :param corrected_ids: Item ids the plan/sync_manifest_status.py stub should report as
        corrected. Written into --pr-data's file, which the stub echoes back verbatim.
    :param extra_arguments: Additional CLI arguments to pass through.
    :return: The finished subprocess.
    """
    plan_path = scratch_project_root / "plan.yaml"
    plan_path.write_text("schema_version: 1\n")
    pull_request_data_path = scratch_project_root / "pr_data.json"
    pull_request_data_path.write_text(
        json.dumps([{"id": identifier} for identifier in corrected_ids])
    )
    artifact_directory = scratch_project_root / "dashboard"

    return subprocess.run(
        [
            "bash",
            str(_refresh_dashboard_script_path(scratch_project_root)),
            "--plan-id",
            "test-plan",
            "--plan",
            str(plan_path),
            "--roadmap",
            str(scratch_project_root / PlanDocument.ROADMAP),
            "--pr-data",
            str(pull_request_data_path),
            "--artifact-directory",
            str(artifact_directory),
            *extra_arguments,
        ],
        cwd=scratch_project_root,
        capture_output=True,
        text=True,
        env=scrubbed_environment(),
    )


# %% argument parsing


def test_missing_required_argument_fails_with_usage(scratch_project_root: Path):
    (scratch_project_root / PlanDocument.ROADMAP).write_text("")
    script_path = _refresh_dashboard_script_path(scratch_project_root)
    result = subprocess.run(
        ["bash", str(script_path), "--plan-id", "test-plan"],
        cwd=scratch_project_root,
        capture_output=True,
        text=True,
        env=scrubbed_environment(),
    )
    assert result.returncode == 1
    assert result.stderr.startswith(f"Usage: {script_path} ")


def test_unrecognized_argument_fails(scratch_project_root: Path):
    script_path = _refresh_dashboard_script_path(scratch_project_root)
    result = subprocess.run(
        ["bash", str(script_path), "--not-a-real-flag", "value"],
        cwd=scratch_project_root,
        capture_output=True,
        text=True,
        env=scrubbed_environment(),
    )
    assert result.returncode == 1
    assert result.stderr == "Unrecognized argument: --not-a-real-flag\n"


# %% correction-triggers-a-push gate


def test_zero_corrections_does_not_push_to_personal_notes(scratch_project_root: Path):
    (scratch_project_root / PlanDocument.ROADMAP).write_text("")
    result = run_refresh_dashboard(
        scratch_project_root, corrected_ids=[], extra_arguments=[]
    )
    assert result.returncode == 0, result.stderr
    assert not (
        scratch_project_root / "write_personal_notes_file_invocation.txt"
    ).exists()


def test_a_correction_pushes_to_personal_notes(scratch_project_root: Path):
    (scratch_project_root / PlanDocument.ROADMAP).write_text("")
    result = run_refresh_dashboard(
        scratch_project_root, corrected_ids=["a"], extra_arguments=[]
    )
    assert result.returncode == 0, result.stderr
    invocation = (
        scratch_project_root / "write_personal_notes_file_invocation.txt"
    ).read_text()
    plan_path = scratch_project_root / "plan.yaml"
    assert f"--source\n{plan_path}\n" in invocation
    manifest_destination = ProjectLocation.PLANS / "test-plan" / "plan.yaml"
    assert f"--destination\n{manifest_destination}\n" in invocation
    assert "1 item(s) to done" in invocation


# %% --tracking-url passthrough


def test_tracking_url_is_not_forwarded_when_omitted(scratch_project_root: Path):
    (scratch_project_root / PlanDocument.ROADMAP).write_text("")
    run_refresh_dashboard(scratch_project_root, corrected_ids=[], extra_arguments=[])
    build_invocation = json.loads(
        (
            scratch_project_root / StubbedScript.BUILD_DASHBOARD.invocation_record
        ).read_text()
    )
    assert "--tracking-url" not in build_invocation


def test_tracking_url_is_forwarded_when_given(scratch_project_root: Path):
    (scratch_project_root / PlanDocument.ROADMAP).write_text("")
    run_refresh_dashboard(
        scratch_project_root,
        corrected_ids=[],
        extra_arguments=["--tracking-url", "https://github.com/owner/repo/issues/1"],
    )
    build_invocation = json.loads(
        (
            scratch_project_root / StubbedScript.BUILD_DASHBOARD.invocation_record
        ).read_text()
    )
    tracking_url_index = build_invocation.index("--tracking-url")
    assert build_invocation[tracking_url_index + 1] == (
        "https://github.com/owner/repo/issues/1"
    )


# %% final merged summary


def test_prints_the_merged_sync_and_build_summary(scratch_project_root: Path):
    (scratch_project_root / PlanDocument.ROADMAP).write_text("")
    result = run_refresh_dashboard(
        scratch_project_root, corrected_ids=["a"], extra_arguments=[]
    )
    assert result.returncode == 0, result.stderr
    merged = json.loads(result.stdout)
    assert merged == {
        "corrected": [{"id": "a"}],
        "drift_count": 0,
        PublicationField.CONTENT_DIGEST: merged[PublicationField.CONTENT_DIGEST],
        **PublicationCheck(url=None, unchanged=False).to_json(),
    }


# %% the publication check


def test_the_artifact_directory_is_created(scratch_project_root: Path):
    (scratch_project_root / PlanDocument.ROADMAP).write_text("")
    run_refresh_dashboard(scratch_project_root, corrected_ids=[], extra_arguments=[])
    assert (scratch_project_root / "dashboard").is_dir()


def test_a_render_already_published_is_reported_unchanged(scratch_project_root: Path):
    (scratch_project_root / PlanDocument.ROADMAP).write_text("")
    first = json.loads(
        run_refresh_dashboard(
            scratch_project_root, corrected_ids=[], extra_arguments=[]
        ).stdout
    )
    url = "https://claude.ai/code/artifact/11111111-1111-4111-8111-111111111111"
    cache_path = scratch_project_root / "dashboard-urls.yaml"
    cache_path.write_text(
        f"test-plan: {url}\n_content_digests:\n"
        f"  test-plan: {first[PublicationField.CONTENT_DIGEST]}\n"
    )
    second = run_refresh_dashboard(
        scratch_project_root,
        corrected_ids=[],
        extra_arguments=[RefreshDashboardOption.URL_CACHE, str(cache_path)],
    )
    assert second.returncode == 0, second.stderr
    merged = json.loads(second.stdout)
    assert {
        PublicationField.URL: merged[PublicationField.URL],
        PublicationField.UNCHANGED: merged[PublicationField.UNCHANGED],
    } == PublicationCheck(url=url, unchanged=True).to_json()


# %% --plans-dir passthrough


def _invocation_of(scratch_project_root: Path, script: StubbedScript) -> list[str]:
    """
    The arguments one stub recorded when refresh_dashboard.sh called it.

    :param scratch_project_root: A fixture-built scratch project root.
    :param script: The stubbed script whose invocation to read back.
    :return: The recorded arguments.
    """
    return json.loads((scratch_project_root / script.invocation_record).read_text())


@pytest.mark.parametrize("script", list(StubbedScript))
def test_plans_directory_is_not_forwarded_when_omitted(
    scratch_project_root: Path, script: StubbedScript
):
    (scratch_project_root / PlanDocument.ROADMAP).write_text("")
    run_refresh_dashboard(scratch_project_root, corrected_ids=[], extra_arguments=[])
    assert RefreshDashboardOption.PLANS_DIRECTORY not in _invocation_of(
        scratch_project_root, script
    )


@pytest.mark.parametrize("script", list(StubbedScript))
def test_plans_directory_is_forwarded_to_both_scripts_when_given(
    scratch_project_root: Path, script: StubbedScript
):
    # Both validate the manifest, so a plan whose depends_on names another
    # plan needs the directory in both places or the refresh stops at the
    # first one.
    (scratch_project_root / PlanDocument.ROADMAP).write_text("")
    plans_directory = scratch_project_root / "plans"
    run_refresh_dashboard(
        scratch_project_root,
        corrected_ids=[],
        extra_arguments=[RefreshDashboardOption.PLANS_DIRECTORY, str(plans_directory)],
    )
    invocation = _invocation_of(scratch_project_root, script)
    assert invocation[
        invocation.index(RefreshDashboardOption.PLANS_DIRECTORY) + 1
    ] == str(plans_directory)
