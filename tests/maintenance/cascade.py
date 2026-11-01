"""
A cascading rebase made of plain git rebases, standing in for ``gh stack`` so a restack
runs against real branches without the extension installed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from basstler.maintenance.git_commands import GitCommandRunner
from basstler.stack.cascading_rebase import (
    CascadeStep,
    CascadeStepOutcome,
    CascadingRebase,
)

from ..constants.stack import RemoteName

# %% the stand-in


@dataclass(frozen=True)
class CascadeOfPlainRebases(CascadingRebase):
    """
    Rebases each layer onto the one below it with ``git rebase``, the bottom one onto the
    fork's copy of the trunk, and pushes every layer with a lease - the cascade
    ``gh stack rebase --downstack`` and ``gh stack push`` run.
    """

    working_directory: Path
    """
    The checkout the branches are rebased in.
    """

    lines: dict[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    """
    Each adopted branch's line: its trunk and every layer, bottom first.
    """

    adopted: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    """
    Every line adopted, in order, for a test to read back.
    """

    @property
    def git(self) -> GitCommandRunner:
        """
        :return: The runner every rebase goes through.
        """
        return GitCommandRunner(working_directory=self.working_directory)

    def adopt(self, trunk: str, branches: Sequence[str]) -> CascadeStep:
        line = (trunk, tuple(branches))
        self.adopted.append(line)
        for branch in branches:
            self.lines[branch] = line
        return CascadeStep(CascadeStepOutcome.COMPLETED)

    def rebase_up_to(self, branch: str) -> CascadeStep:
        trunk, layers = self.lines[branch]
        self.git.fetch(RemoteName.ORIGIN)
        for layer in layers:
            published = f"{RemoteName.ORIGIN}/{layer}"
            if self.git.contains(layer, published):
                self.git.run("branch", "--force", layer, published)
        onto = f"{RemoteName.ORIGIN}/{trunk}"
        for layer in layers[: layers.index(branch) + 1]:
            rebased = self.git.attempt("rebase", "--quiet", onto, layer)
            if not rebased.succeeded:
                return CascadeStep(CascadeStepOutcome.CONFLICTED, rebased.error_output)
            onto = layer
        self.git.run("checkout", "--quiet", "--detach")
        return CascadeStep(CascadeStepOutcome.COMPLETED)

    def abort_rebase(self) -> CascadeStep:
        self.git.run("rebase", "--abort")
        self.git.run("checkout", "--quiet", "--detach")
        return CascadeStep(CascadeStepOutcome.COMPLETED)

    def publish(self) -> CascadeStep:
        _, layers = self.lines[self.git.checked_out_branch()]
        pushed = self.git.attempt(
            "push",
            "--quiet",
            "--atomic",
            *(
                f"--force-with-lease={layer}:{RemoteName.ORIGIN}/{layer}"
                for layer in layers
            ),
            RemoteName.ORIGIN,
            *(f"{layer}:{layer}" for layer in layers),
        )
        if not pushed.succeeded:
            return CascadeStep(CascadeStepOutcome.FAILED, pushed.error_output)
        return CascadeStep(CascadeStepOutcome.COMPLETED)
