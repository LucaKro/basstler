"""
The throwaway git repository the hook integration tests run against.

Every hook reads git config, fetches a personal-notes branch, or pushes to one, so
testing any of them needs a real repository with a real remote. Both are built locally
here - a project root, and a bare repository standing in for the notes remote - so no
test needs network access or a real personal-notes branch. Beside them sits a scratch
basstler checkout holding the plugin under test, since the plugin lives outside the
project it serves.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .script_runner import BashScriptRunner
from basstler.locations import PackageLocation, PluginLocation
from basstler_domain_model.git.commands import (
    GitCommandRunner,
)
from basstler.shell_configuration import ShellConfigurationFunction

from .constants.environment import (
    ConfigurationVariable,
    FileMode,
    ScrubbedEnvironmentPrefix,
)
from .constants.notes import ScratchBranch
from .constants.paths import DatasetLocation, RepositoryLocation, SkillDirectory


class PackageSourceFile(StrEnum):
    """
    The parts of a basstler checkout that make it one, relative to its root.
    """

    METADATA = "pyproject.toml"
    """
    The package's metadata, declaring its dependencies.
    """

    README = "README.md"
    """
    The readme the metadata names.
    """

    SOURCE = "src"
    """
    The directory holding the package itself.
    """


def install_hook_scripts_into(plugin_root: Path, *script_names: str) -> None:
    """
    Copy the real hook scripts under test into a plugin.

    :param plugin_root: The plugin to copy them into.
    :param script_names: File names within the hooks directory.
    """
    hooks_directory = plugin_root / PluginLocation.HOOKS.value.name
    hooks_directory.mkdir(parents=True, exist_ok=True)
    for script_name in script_names:
        shutil.copy(
            RepositoryLocation.HOOKS / script_name, hooks_directory / script_name
        )


def install_package_into(checkout_root: Path) -> None:
    """
    Copy the real :mod:`basstler` package and its metadata into a checkout, so a plugin in
    that checkout runs ``python3 -m basstler.<module>`` from the checkout's own source,
    the way a plugin run from a clone of its repository does.

    Build output and the board snapshot are left behind, as a fresh clone has neither.

    :param checkout_root: The checkout to copy it into.
    """
    checkout_root.mkdir(parents=True, exist_ok=True)
    for file in (PackageSourceFile.METADATA, PackageSourceFile.README):
        shutil.copy(RepositoryLocation.ROOT / file, checkout_root / file)
    shutil.copytree(
        RepositoryLocation.ROOT / PackageSourceFile.SOURCE,
        checkout_root / PackageSourceFile.SOURCE,
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
        dirs_exist_ok=True,
    )


def write_interpreter_wrapper(destination: Path) -> Path:
    """
    Write an executable at *destination* that runs the interpreter running this suite.

    :param destination: Where to write it, its directories created as needed.
    :return: *destination*.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    destination.chmod(FileMode.EXECUTABLE)
    return destination


class SetupPrerequisiteFile(StrEnum):
    """
    The files check-setup.sh's ``tooling_files`` check requires, relative to the plugin
    root.

    Stated here as well as in the fixture tree deliberately. A rename that breaks the
    check then has to be made in both places, rather than the fixture and the tests
    following each other silently and asserting nothing.
    """

    PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
    """
    The plugin's manifest, naming its version.
    """

    REFRESH_DASHBOARD = str(SkillDirectory.DASHBOARD_REFRESH_SCRIPT)
    """
    The refresh entry point the plan-dashboard skill runs.
    """

    PLAN_SCHEMA = str(SkillDirectory.PLAN_DASHBOARD / "plan-schema.md")
    """
    The manifest field reference.
    """


@dataclass(frozen=True)
class GitIdentity:
    """
    The name and email a commit is authored with.
    """

    name: str
    """
    The value of ``user.name``.
    """

    email: str
    """
    The value of ``user.email``.
    """

    def as_git_config_file(self) -> str:
        """
        Render this identity in the git-config format the hooks read it back from.

        :return: The file's contents.
        """
        return f"[user]\n\tname = {self.name}\n\temail = {self.email}\n"

    @classmethod
    def from_git_config_file(cls, path: Path) -> GitIdentity:
        """
        Read an identity back through git itself, rather than by parsing the file, so a
        test asserts what the hooks will actually resolve from it.

        :param path: The git-config-format file to read.
        :return: The identity it records.
        """
        return cls(
            read_git_config_value(path, "user.name"),
            read_git_config_value(path, "user.email"),
        )


def read_git_config_value(path: Path, key: str) -> str:
    """
    Read one key out of a git-config-format file.

    :param path: The file to read.
    :param key: The dotted config key, such as ``user.name``.
    :return: The value, stripped of its trailing newline.
    """
    result = subprocess.run(
        ["git", "config", "--file", str(path), "--get", key],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


SCRATCH_IDENTITY = GitIdentity("Scratch Repo", "scratch-repo@example.com")
"""
The repository-local identity every scratch repository is created with.

A CI runner has no ambient git identity configured, so committing in the scratch layout
has to depend on this rather than on the environment already having one.
"""


def initialize_bare_repository(path: Path) -> Path:
    """
    Create an empty bare repository, usable as a git remote.

    :param path: Where to create it.
    :return: The same path, for passing to git as a remote.
    """
    subprocess.run(
        ["git", "init", "--quiet", "--bare", str(path)],
        check=True,
        capture_output=True,
    )
    return path


@dataclass
class ScratchRepository:
    """
    A scratch project root, a bare repository standing in for its notes remote, and the
    git operations the hook tests perform on the pair.
    """

    project_root: Path
    """
    The working clone the hook scripts under test are run against.
    """

    notes_remote_path: Path
    """
    The bare repository the notes branch is pushed to and fetched from.
    """

    work_remote_path: Path | None = None
    """
    The bare repository standing in for the project's own remote, created only by
    :meth:`add_work_remote` so a test that never publishes a work branch has no
    ``origin`` it did not ask for.
    """

    @property
    def checkout_root(self) -> Path:
        """
        The scratch basstler checkout the plugin under test sits in, beside the project.
        """
        return self.project_root.parent / "basstler-checkout"

    @property
    def plugin_root(self) -> Path:
        """
        The plugin under test, whose hooks are run against the project.
        """
        return self.checkout_root / RepositoryLocation.PLUGIN.value.name

    @property
    def basstler_home(self) -> Path:
        """
        The scratch stand-in for the per-user directory the package's environment lives in.
        """
        return self.project_root.parent / "basstler-home"

    @classmethod
    def create(cls, parent_directory: Path) -> ScratchRepository:
        """
        Build a scratch repository with git initialized and its notes remote created,
        but nothing committed yet.

        :param parent_directory: Where to put the project root, the plugin and the notes
            remote, typically pytest's per-test temporary directory.
        :return: The new scratch repository.
        """
        project_root = parent_directory / "project"
        project_root.mkdir(parents=True)
        repository = cls(
            project_root,
            initialize_bare_repository(parent_directory / "personal-notes.git"),
        )
        repository.run_git("init", "--quiet")
        repository.run_git("config", "user.name", SCRATCH_IDENTITY.name)
        repository.run_git("config", "user.email", SCRATCH_IDENTITY.email)
        # A throwaway repository has no reason to sign, and an environment that signs
        # by default makes every commit here depend on a reachable signing service -
        # observed failing mid-suite with "signing server returned status 520", on a
        # different test each run.
        repository.run_git("config", "commit.gpgsign", "false")
        return repository

    def clear_local_git_identity(self) -> None:
        """
        Remove the repository-local identity :meth:`create` sets, leaving the clone in
        the state a fresh one is really in.
        """
        self.run_git("config", "--unset", "user.name")
        self.run_git("config", "--unset", "user.email")

    def local_git_identity(self) -> GitIdentity | None:
        """
        Read the identity configured in this repository's own config.

        :return: The identity, or ``None`` if either half is unset.
        """
        values = []
        for key in ("user.name", "user.email"):
            result = self.run_git_allowing_failure("config", "--local", "--get", key)
            if result.returncode != 0:
                return None
            values.append(result.stdout.strip())
        return GitIdentity(*values)

    @property
    def git(self) -> GitCommandRunner:
        """:return: A runner for git in the project root."""
        return GitCommandRunner(working_directory=self.project_root)

    def run_git(
        self, *arguments: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        """
        Run git in the project root, failing the test if it reports an error.

        :param arguments: The arguments to pass to git.
        :param cwd: Where to run it, defaulting to the project root.
        :return: The finished subprocess.
        """
        result = self.run_git_allowing_failure(*arguments, cwd=cwd)
        assert result.returncode == 0, result.stderr
        return result

    def add_remote(self, name: str, url: str) -> None:
        """
        Add a git remote to the project.

        :param name: What to call the remote.
        :param url: Where it points.
        """
        self.run_git("remote", "add", name, url)

    def run_git_allowing_failure(
        self, *arguments: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        """
        Run git in the project root, for the queries whose failure is a valid answer
        rather than a broken test.

        :param arguments: The arguments to pass to git.
        :param cwd: Where to run it, defaulting to the project root.
        :return: The finished subprocess.
        """
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd or self.project_root,
            capture_output=True,
            text=True,
        )

    def install_hook_scripts(self, *script_names: str) -> None:
        """
        Copy the real hook scripts under test into the scratch plugin.

        :param script_names: File names within the hooks directory.
        """
        install_hook_scripts_into(self.plugin_root, *script_names)

    def install_stack_configuration(self, content: str) -> Path:
        """
        Write the package's shipped ``stack.toml`` defaults into the scratch checkout,
        beside the shell file the personal-notes override is fetched through.

        :param content: The configuration to write.
        :return: The path :meth:`basstler.stack.settings.Configuration.load` should be pointed at.
        """
        self.install_hook_scripts(
            PluginLocation.PERSONAL_NOTES_CONFIGURATION_SCRIPT.value.name
        )
        written = (
            self.checkout_root
            / PackageSourceFile.SOURCE
            / PackageLocation.DIRECTORY.value.name
            / PackageLocation.STACK_CONFIGURATION.value.name
        )
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_text(content)
        return written

    def install_package(self) -> None:
        """
        Copy the real :mod:`basstler` package into the scratch checkout.
        """
        install_package_into(self.checkout_root)

    def configuration_value(self, variable_name: str) -> str:
        """
        :param variable_name: A variable the scratch plugin's shell configuration assigns.
        :return: Its value once that configuration is sourced in the project.
        """
        return self.configuration_output(f'printf "%s" "${{{variable_name}}}"')

    def configuration_output(self, command: str, **overrides: str) -> str:
        """
        :param command: A shell command run once the scratch plugin's configuration is
            sourced in the project, such as a call to one of its functions.
        :param overrides: Variables the run sets.
        :return: What it printed.
        """
        self.install_hook_scripts(
            PluginLocation.PERSONAL_NOTES_CONFIGURATION_SCRIPT.value.name
        )
        script = (
            self.plugin_root
            / PluginLocation.HOOKS.value.name
            / PluginLocation.PERSONAL_NOTES_CONFIGURATION_SCRIPT.value.name
        )
        return subprocess.run(
            ["bash", "-c", f'source "{script}" && {command}'],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=True,
            env=self.runner_environment(**overrides),
        ).stdout

    def install_package_environment(self) -> Path:
        """
        Stand in for the package's own environment, as a set up machine has one: its
        interpreter runs the one running this suite, which has every requirement, and it
        records the package as installed from what the plugin names now.

        A script rather than a link, because an interpreter reached through a link outside
        its own environment no longer finds that environment's installed packages.

        :return: The interpreter written.
        """
        interpreter = write_interpreter_wrapper(
            Path(
                self.configuration_value(ConfigurationVariable.ENVIRONMENT_INTERPRETER)
            )
        )
        Path(
            self.configuration_value(ConfigurationVariable.INSTALLED_SOURCE_FILE)
        ).write_text(
            self.configuration_output(
                ShellConfigurationFunction.BASSTLER_PACKAGE_SOURCE
            )
        )
        return interpreter

    def write_setup_prerequisites(self) -> None:
        """
        Write everything check-setup.sh requires of a set up plugin and project, apart
        from the personal-notes branch and CLAUDE.local.md.

        Leaves CLAUDE.local.md out deliberately: session-start.sh writes it, so a test
        of that script must not find it already there - which is why this is a named
        step rather than part of building the repository.
        """
        shutil.copytree(
            DatasetLocation.SET_UP_CHECKOUT, self.checkout_root, dirs_exist_ok=True
        )
        shutil.copytree(
            DatasetLocation.SET_UP_PROJECT, self.project_root, dirs_exist_ok=True
        )

    def runner_environment(self, **overrides: str) -> dict[str, str]:
        """
        :param overrides: Variables a run sets.
        :return: The environment a hook runs with here: this process's own, scrubbed of
            everything that could change what a test asserts (see
            :class:`ScrubbedEnvironmentPrefix`), with the package's home in the scratch
            layout.
        """
        return self.hook_runner(Path()).environment(
            {ConfigurationVariable.HOME: str(self.basstler_home), **overrides}
        )

    def hook_runner(self, script_path: Path) -> BashScriptRunner:
        """
        :param script_path: The script to run.
        :return: A runner for it from the project root, scrubbing what
            :class:`ScrubbedEnvironmentPrefix` names.
        """
        return BashScriptRunner(
            project_root=self.project_root,
            removed_variable_prefixes=tuple(ScrubbedEnvironmentPrefix),
            script_path=script_path,
        )

    def run_hook_script(
        self,
        script_name: str,
        *arguments: str,
        **environment_overrides: str,
    ) -> subprocess.CompletedProcess[str]:
        """
        Run one of the installed hook scripts from the project root, against an
        environment scrubbed of everything that could change what a test asserts (see
        :class:`ScrubbedEnvironmentPrefix`).

        Returns the finished process rather than asserting on it, since a hook's exit
        code and stderr are often what a test is about.

        :param script_name: File name within the scratch layout's hooks directory.
        :param arguments: The arguments to pass to the script.
        :param environment_overrides: Variables to set for this run, for the tests that
            exercise resolution from the environment.
        :return: The finished subprocess.
        """
        return self.hook_runner(
            self.plugin_root / PluginLocation.HOOKS.value.name / script_name
        ).run(
            *arguments,
            **{
                ConfigurationVariable.HOME: str(self.basstler_home),
                **environment_overrides,
            },
        )

    def write(self, relative_path: str | os.PathLike[str], content: str) -> Path:
        """
        Write a file in the project root, creating any missing parent directories.

        :param relative_path: Path relative to the project root.
        :param content: The file's contents.
        :return: The path written to.
        """
        destination = self.project_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
        return destination

    def commit_everything(self, message: str) -> None:
        """
        Stage every change in the project root and commit it.

        :param message: The commit message.
        """
        self.run_git("add", "--all")
        self.run_git("commit", "--quiet", "-m", message)

    def publish_notes_branch(self, files: Mapping[str | os.PathLike[str], str]) -> None:
        """
        Push *files* to the notes branch on the notes remote, then leave the repository
        on a work branch that does not carry them.

        Keeping them off the work branch matches how the hooks are really used: notes
        exist only on the notes branch, and are fetched rather than checked out.

        :param files: File contents, keyed by path relative to the project root.
        """
        self.run_git("checkout", "--quiet", "-b", ScratchBranch.PERSONAL_NOTES)
        for relative_path, content in files.items():
            self.write(relative_path, content)
        self.commit_everything("bootstrap personal-notes")
        self.run_git(
            "push", "--quiet", str(self.notes_remote_path), ScratchBranch.PERSONAL_NOTES
        )

        self.run_git("checkout", "--quiet", "-b", ScratchBranch.WORK)
        for relative_path in files:
            (self.project_root / relative_path).unlink()
        self.commit_everything("drop the notes from the work branch")

    def remove_from_notes_branch(self, relative_path: str | os.PathLike[str]) -> None:
        """
        Delete a file from the notes branch and push the deletion, for the tests whose
        subject is a notes branch that carries everything except one thing.

        :param relative_path: Path relative to the project root.
        """
        self.run_git("checkout", "--quiet", ScratchBranch.PERSONAL_NOTES)
        (self.project_root / relative_path).unlink()
        self.commit_everything(f"drop {relative_path}")
        self.run_git(
            "push", "--quiet", str(self.notes_remote_path), ScratchBranch.PERSONAL_NOTES
        )
        self.run_git("checkout", "--quiet", ScratchBranch.WORK)

    def clone_notes_branch(self, destination: Path) -> Path:
        """
        Check the notes branch out of the notes remote, for asserting against what a
        hook actually pushed rather than what it reported.

        :param destination: Where to put the checkout.
        :return: The checkout's path.
        """
        self.run_git(
            "clone",
            "--quiet",
            "--branch",
            ScratchBranch.PERSONAL_NOTES,
            str(self.notes_remote_path),
            str(destination),
        )
        return destination

    def update_notes_branch_file(
        self, relative_path: str | os.PathLike[str], content: str
    ) -> None:
        """
        Change one file on the already-published notes branch, the way an edit made from
        another clone would reach it.

        :param relative_path: Path relative to the notes branch's root.
        :param content: The content to commit there.
        """
        checkout = self.project_root.parent / "notes-update-checkout"
        shutil.rmtree(checkout, ignore_errors=True)
        self.clone_notes_branch(checkout)
        self.run_git("config", "user.name", SCRATCH_IDENTITY.name, cwd=checkout)
        self.run_git("config", "user.email", SCRATCH_IDENTITY.email, cwd=checkout)
        self.run_git("config", "commit.gpgsign", "false", cwd=checkout)

        destination = checkout / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
        self.run_git("add", relative_path, cwd=checkout)
        self.run_git("commit", "--quiet", "-m", f"Set {relative_path}", cwd=checkout)
        self.run_git(
            "push", "--quiet", "origin", ScratchBranch.PERSONAL_NOTES, cwd=checkout
        )
        shutil.rmtree(checkout)

    def add_work_remote(self) -> Path:
        """
        Create a bare repository standing in for the project's own remote and register
        it as ``origin``, for a hook that publishes a work branch rather than notes.

        :return: The work remote's path.
        """
        self.work_remote_path = initialize_bare_repository(
            self.project_root.parent / "work-remote.git"
        )
        self.run_git("remote", "add", "origin", str(self.work_remote_path))
        return self.work_remote_path

    def resolve_notes_remote_to(self, remote: Path | None = None) -> None:
        """
        Point the personal-notes remote at *remote* through local git config.

        :param remote: The remote the hooks should resolve to, defaulting to this
            repository's own notes remote.
        """
        self.run_git(
            "config",
            "claude.personalNotesRemote",
            str(self.notes_remote_path if remote is None else remote),
        )
