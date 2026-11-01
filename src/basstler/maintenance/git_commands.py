"""
Asking git what contains what among the fork's branches.
"""

from __future__ import annotations

from dataclasses import dataclass

from basstler_domain_model.git.commands import GitCommandRunner

from basstler.stack.settings import Configuration

# %% asking git what contains what


@dataclass(frozen=True)
class BranchAncestry:
    """
    Answers containment questions about the fork's branches.

    :class:`CommitMoveChecks` asks its false-merge question through this, so the
    question is asked of git rather than of anything this module remembers.
    """

    configuration: Configuration
    """
    The resolved configuration naming the fork remote.
    """

    git: GitCommandRunner
    """
    The runner to ask git through.
    """

    def is_ancestor(self, candidate: str, descendant: str) -> bool:
        """:param candidate: A fork branch that may be contained.
        :param descendant: A local branch that may contain it.
        :return: Whether the fork's copy of *candidate* is contained in *descendant*."""
        return self.git.contains(
            self.configuration.reference_on_fork(candidate), descendant
        )
