"""
Tests for the stack configuration a checkout resolves to: the personal-notes overrides,
the fork remote, and what the shell tooling reads.

Configuration layering genuinely needs a git remote, so these run against a
:class:`ScratchRepository`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basstler.repository import Repository
from basstler.stack.configuration import StackSetting
from basstler.stack.exceptions import (
    AmbiguousForkRemoteError,
    ForkRemoteNotFoundError,
)
from basstler.stack.settings import (
    Configuration,
    PrintedConfiguration,
    Remote,
    RemoteResolution,
)

from basstler.locations import PackageLocation, ProjectLocation

from ..constants.paths import DatasetLocation
from ..constants.stack import RemoteName, StackBranch, StackLabel
from ..scratch_repository import ScratchRepository

from .dataset import (
    StackRepository,
    StackSampleText,
    make_configuration,
)


# %% configuration layering (personal-notes overrides)


def a_setting_line(setting: StackSetting, value: object) -> str:
    """
    :param setting: The setting to write.
    :param value: Its value.
    :return: The ``stack.toml`` line giving *setting* that value.
    """
    return f'{setting} = "{value}"\n'


def _committed_configuration_path(scratch_repository: ScratchRepository) -> Path:
    """
    Write and commit the repo-default ``stack.toml`` into a scratch repository, beside
    the shell file the personal-notes override is fetched through.

    :param scratch_repository: The scratch repository to write into.
    :return: The path :meth:`Configuration.load` should be pointed at.
    """
    path = scratch_repository.install_stack_configuration(
        DatasetLocation.COMMITTED_DEFAULT_STACK_CONFIGURATION.read_text()
    )
    scratch_repository.add_remote(
        RemoteName.UNEXPECTED, StackRepository.FORK.value.clone_url
    )
    return path


def test_load_configuration_uses_committed_defaults_when_no_personal_notes_branch(
    scratch_repository: ScratchRepository, monkeypatch
):
    configuration_path = _committed_configuration_path(scratch_repository)
    scratch_repository.resolve_notes_remote_to()
    monkeypatch.chdir(scratch_repository.project_root)

    configuration = Configuration.load(configuration_path)

    assert configuration.upstream_remote == RemoteName.UPSTREAM


def test_load_configuration_layers_personal_notes_override_on_top_of_defaults(
    scratch_repository: ScratchRepository, monkeypatch
):
    configuration_path = _committed_configuration_path(scratch_repository)
    scratch_repository.publish_notes_branch(
        {
            ProjectLocation.PERSONAL_STACK_CONFIGURATION: a_setting_line(
                StackSetting.UPSTREAM_REMOTE, RemoteName.PERSONAL_UPSTREAM
            )
        }
    )
    scratch_repository.resolve_notes_remote_to()
    monkeypatch.chdir(scratch_repository.project_root)

    configuration = Configuration.load(configuration_path)

    assert configuration.upstream_remote == RemoteName.PERSONAL_UPSTREAM
    assert configuration.upstream_base == StackBranch.BASE  # untouched default


def test_load_configuration_ignores_personal_notes_branch_without_a_stack_file(
    scratch_repository: ScratchRepository, monkeypatch
):
    configuration_path = _committed_configuration_path(scratch_repository)
    scratch_repository.publish_notes_branch(
        {StackSampleText.UNRELATED_FILE: StackSampleText.UNRELATED_CONTENT}
    )
    scratch_repository.resolve_notes_remote_to()
    monkeypatch.chdir(scratch_repository.project_root)

    configuration = Configuration.load(configuration_path)

    assert configuration.upstream_remote == RemoteName.UPSTREAM


def test_load_configuration_resolves_the_fork_whatever_the_remote_is_called(
    scratch_repository: ScratchRepository, monkeypatch
):
    """
    Nothing has to be configured: a checkout already knows its own fork.
    """
    configuration_path = _committed_configuration_path(scratch_repository)
    scratch_repository.resolve_notes_remote_to()
    monkeypatch.chdir(scratch_repository.project_root)

    configuration = Configuration.load(configuration_path)

    assert configuration.fork_repository == StackRepository.FORK.value
    assert configuration.fork_remote == RemoteName.UNEXPECTED


def test_load_configuration_takes_the_fork_from_a_personal_notes_override(
    scratch_repository: ScratchRepository, monkeypatch
):
    """
    An override picks between real remotes; it cannot name a fork the checkout cannot
    push to.
    """
    configuration_path = _committed_configuration_path(scratch_repository)
    scratch_repository.add_remote(
        RemoteName.THEIRS, StackRepository.SOMEBODY_ELSES_FORK.value.clone_url
    )
    scratch_repository.publish_notes_branch(
        {
            ProjectLocation.PERSONAL_STACK_CONFIGURATION: a_setting_line(
                StackSetting.FORK_REPOSITORY,
                StackRepository.SOMEBODY_ELSES_FORK.value,
            )
        }
    )
    scratch_repository.resolve_notes_remote_to()
    monkeypatch.chdir(scratch_repository.project_root)

    configuration = Configuration.load(configuration_path)

    assert configuration.fork_repository == StackRepository.SOMEBODY_ELSES_FORK.value
    assert configuration.fork_remote == RemoteName.THEIRS


# %% resolving which remote is the fork


def resolve(remote_urls: dict[str, str], fork_repository: Repository | None = None):
    return RemoteResolution.resolve(
        remote_urls,
        StackRepository.UPSTREAM.value,
        RemoteName.PREFERRED_UPSTREAM,
        fork_repository,
    )


def test_the_fork_is_the_remote_that_is_not_the_upstream():
    """
    Names carry no meaning: the fork is identified by the repository its URL points at.
    """
    resolution = resolve(
        {
            RemoteName.ORIGIN: StackRepository.UPSTREAM.value.clone_url,
            RemoteName.MINE: StackRepository.SAME_NAMED_FORK.value.clone_url,
        }
    )

    assert resolution.fork == Remote(
        RemoteName.MINE, StackRepository.SAME_NAMED_FORK.value
    )
    assert resolution.upstream == Remote(
        RemoteName.ORIGIN, StackRepository.UPSTREAM.value
    )


def test_the_fork_resolves_the_same_when_origin_happens_to_be_the_fork():
    resolution = resolve(
        {
            RemoteName.ORIGIN: StackRepository.SAME_NAMED_FORK.value.clone_url,
            RemoteName.UPSTREAM: StackRepository.UPSTREAM.value.clone_url,
        }
    )

    assert resolution.fork == Remote(
        RemoteName.ORIGIN, StackRepository.SAME_NAMED_FORK.value
    )


def test_the_fork_resolves_from_a_checkout_that_has_only_the_fork():
    """
    A fresh cloud clone has one remote and no upstream at all.
    """
    resolution = resolve(
        {RemoteName.ORIGIN: StackRepository.SAME_NAMED_FORK.value.clone_url}
    )

    assert resolution.fork == Remote(
        RemoteName.ORIGIN, StackRepository.SAME_NAMED_FORK.value
    )
    assert resolution.upstream is None


def test_a_checkout_without_an_upstream_remote_fetches_the_upstream_by_url():
    resolution = resolve(
        {RemoteName.ORIGIN: StackRepository.SAME_NAMED_FORK.value.clone_url}
    )

    assert resolution.upstream_location == StackRepository.UPSTREAM.value.clone_url
    assert resolution.upstream_name == RemoteName.PREFERRED_UPSTREAM


def test_a_checkout_with_an_upstream_remote_fetches_the_upstream_through_it():
    resolution = resolve(
        {
            RemoteName.ORIGIN: StackRepository.SAME_NAMED_FORK.value.clone_url,
            RemoteName.UPSTREAM: StackRepository.UPSTREAM.value.clone_url,
        }
    )

    assert resolution.upstream_location == RemoteName.UPSTREAM
    assert resolution.upstream_name == RemoteName.UPSTREAM


def test_a_checkout_with_only_the_upstream_is_rejected_rather_than_guessed():
    """
    Treating the upstream as the fork would target every push at the review repository.
    """
    with pytest.raises(ForkRemoteNotFoundError):
        resolve({RemoteName.ORIGIN: StackRepository.UPSTREAM.value.clone_url})


def test_two_possible_forks_are_rejected_rather_than_guessed():
    with pytest.raises(AmbiguousForkRemoteError):
        resolve(
            {
                RemoteName.MINE: StackRepository.SAME_NAMED_FORK.value.clone_url,
                RemoteName.THEIRS: StackRepository.ANOTHER_OWNERS_FORK.value.clone_url,
            }
        )


def test_configuration_disambiguates_two_possible_forks():
    resolution = resolve(
        {
            RemoteName.MINE: StackRepository.SAME_NAMED_FORK.value.clone_url,
            RemoteName.THEIRS: StackRepository.ANOTHER_OWNERS_FORK.value.clone_url,
        },
        fork_repository=StackRepository.SAME_NAMED_FORK.value,
    )

    assert resolution.fork == Remote(
        RemoteName.MINE, StackRepository.SAME_NAMED_FORK.value
    )


def test_a_remote_that_names_no_repository_is_ignored():
    """
    A local-path remote is not a candidate, and must not make the fork ambiguous.
    """
    resolution = resolve(
        {
            RemoteName.ORIGIN: StackRepository.SAME_NAMED_FORK.value.clone_url,
            RemoteName.BACKUP: StackSampleText.LOCAL_PATH_REMOTE,
        }
    )

    assert resolution.fork == Remote(
        RemoteName.ORIGIN, StackRepository.SAME_NAMED_FORK.value
    )


# %% the configuration the shell tooling reads


def test_every_setting_is_printed_under_its_own_field_name(capsys):
    """
    Callers read one setting by name out of this output, so a key that is not a field name
    is a key nobody can look up - and a field that never prints is a setting nobody can read.
    """
    make_configuration().print_settings()

    printed = PrintedConfiguration.read(capsys.readouterr().out)

    assert printed == {
        StackSetting.IN_REVIEW_LABEL: StackLabel.IN_REVIEW,
        StackSetting.NEEDS_RESOLUTION_LABEL: StackLabel.NEEDS_RESOLUTION,
        StackSetting.FORK_REPOSITORY: StackRepository.FORK.value.full_name,
        StackSetting.FORK_REMOTE: RemoteName.ORIGIN,
        StackSetting.UPSTREAM_REPOSITORY: StackRepository.UPSTREAM.value.full_name,
        StackSetting.UPSTREAM_REMOTE: RemoteName.UPSTREAM,
        StackSetting.UPSTREAM_BASE: StackBranch.BASE,
        StackSetting.UPSTREAM_LOCATION: RemoteName.UPSTREAM,
    }


# %% configuration named rather than inferred


def test_a_named_fork_is_used_where_inference_would_have_refused_to_guess(
    scratch_repository: ScratchRepository, monkeypatch
):
    """
    Two candidate remotes are ambiguous to inference; naming the fork settles it without
    anything being written to the checkout.
    """
    configuration_path = _committed_configuration_path(scratch_repository)
    scratch_repository.add_remote(
        RemoteName.THEIRS, StackRepository.SOMEBODY_ELSES_FORK.value.clone_url
    )
    scratch_repository.resolve_notes_remote_to()
    monkeypatch.chdir(scratch_repository.project_root)

    with pytest.raises(AmbiguousForkRemoteError):
        Configuration.load(configuration_path)

    configuration = Configuration.load(
        configuration_path, fork_repository=StackRepository.SOMEBODY_ELSES_FORK.value
    )

    assert configuration.fork_repository == StackRepository.SOMEBODY_ELSES_FORK.value
    assert configuration.fork_remote == RemoteName.THEIRS


def test_a_project_committing_no_stack_file_takes_its_upstream_from_personal_notes(
    scratch_repository: ScratchRepository, monkeypatch
):
    """
    A project that commits nothing of basstler's still has an upstream: the user's own
    personal-notes layer names it.
    """
    configuration_path = scratch_repository.install_stack_configuration(
        PackageLocation.STACK_CONFIGURATION.value.read_text()
    )
    scratch_repository.add_remote(
        RemoteName.UNEXPECTED, StackRepository.FORK.value.clone_url
    )
    scratch_repository.publish_notes_branch(
        {
            ProjectLocation.PERSONAL_STACK_CONFIGURATION: a_setting_line(
                StackSetting.UPSTREAM_REPOSITORY,
                StackRepository.DIFFERENT_UPSTREAM.value.full_name,
            )
        }
    )
    scratch_repository.resolve_notes_remote_to()
    monkeypatch.chdir(scratch_repository.project_root)

    configuration = Configuration.load(configuration_path)

    assert configuration.upstream_repository == StackRepository.DIFFERENT_UPSTREAM.value


def test_a_named_upstream_replaces_the_committed_default(
    scratch_repository: ScratchRepository, monkeypatch
):
    configuration_path = _committed_configuration_path(scratch_repository)
    scratch_repository.resolve_notes_remote_to()
    monkeypatch.chdir(scratch_repository.project_root)

    configuration = Configuration.load(
        configuration_path,
        upstream_repository=StackRepository.DIFFERENT_UPSTREAM.value,
    )

    assert configuration.upstream_repository == StackRepository.DIFFERENT_UPSTREAM.value
    assert (
        configuration.upstream_location
        == StackRepository.DIFFERENT_UPSTREAM.value.clone_url
    )
