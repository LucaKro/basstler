"""
The verdict over every open branch's checks, as a few lines whatever the size of the
stack.
"""

from __future__ import annotations

from dataclasses import dataclass

from basstler.stack.model import Branch, BranchStatus, Stack
from basstler_domain_model.github.vocabulary import (
    RollupState,
)

# %% the overview


@dataclass(frozen=True)
class CheckOverview:
    """
    How many open branches' checks pass, run or fail, naming the ones that do not pass.
    """

    stack: Stack
    """
    The stack whose branches carry the verdict over their head's checks.
    """

    entries_per_state: int = 5
    """
    How many branches a line names before counting the rest.
    """

    def open_branches(self) -> list[Branch]:
        """
        :return: Every branch the pass maintains that has not landed, parent before
            child.
        """
        return [
            branch
            for branch in self.stack.maintained_branches()
            if branch.status is not BranchStatus.MERGED
        ]

    def branches_in(self, state: RollupState) -> list[Branch]:
        """
        :param state: A verdict.
        :return: The open branches whose checks have that verdict.
        """
        return [
            branch for branch in self.open_branches() if branch.check_state == state
        ]

    def as_lines(self) -> list[str]:
        """
        :return: One line counting every verdict, then one line per verdict other than
            success naming its branches.
        """
        counted = {state: len(self.branches_in(state)) for state in RollupState}
        counts = ", ".join(
            f"{count} {state}" for state, count in counted.items() if count
        )
        lines = [f"checks on {len(self.open_branches())} open branches: {counts}"]
        for state in RollupState:
            if state is RollupState.SUCCESS or not counted[state]:
                continue
            lines.append(f"{state}: {self.named(state)}")
        return lines

    def named(self, state: RollupState) -> str:
        """
        :param state: A verdict.
        :return: The first :attr:`entries_per_state` branches with it, by pull request
            and name, and a count of the rest.
        """
        branches = self.branches_in(state)
        named = ", ".join(
            f"#{branch.pull_request_number} `{branch.name}`"
            for branch in branches[: self.entries_per_state]
        )
        hidden = len(branches) - self.entries_per_state
        return named + (f" and {hidden} more" if hidden > 0 else "")
