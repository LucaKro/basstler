"""
Moving the fork's copy of the upstream base onto the upstream's tip.

This is the step that closes the pull requests whose work has landed, since GitHub marks
one merged the moment its head becomes an ancestor of its base.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from basstler_domain_model.git.commands import (
    GitCommandRunner,
    ProposedPush,
)
from basstler.stack.settings import Configuration


class FastForwardOutcome(StrEnum):
    """
    What became of the fork's base branch.
    """

    PUSHED = "pushed"
    """
    It was moved onto the upstream's tip.
    """

    ALREADY_CURRENT = "already-current"
    """
    It already pointed at the upstream's tip.
    """

    REFUSED_NOT_FAST_FORWARD = "refused-not-fast-forward"
    """
    It carries commits the upstream does not, so moving it would discard them.
    """

    PUSH_REFUSED = "push-refused"
    """
    The fork rejected the push, so it is still behind the upstream.
    """


class RefusedPushHandling(StrEnum):
    """
    What a fork rejecting the fast-forward's push does to its caller.
    """

    RAISED = "raised"
    """
    The rejection stops the caller with :class:`GitCommandFailed`.
    """

    REPORTED = "reported"
    """
    The rejection is reported as :attr:`FastForwardOutcome.PUSH_REFUSED`.
    """


@dataclass(frozen=True)
class FastForwardReport:
    """
    What the fast-forward did, and to what.
    """

    outcome: FastForwardOutcome
    """
    What became of the fork's base branch.
    """

    upstream_reference: str
    """
    The upstream ref the fork's base was compared against.
    """

    fork_reference: str
    """
    The fork ref that was to be moved.
    """

    commit: str | None
    """
    The commit the fork's base points at now, absent when the fork has no such branch.
    """

    explanation: str | None = None
    """
    Why the fork's base was not moved, absent when nothing was refused.
    """


@dataclass(frozen=True)
class ForkBaseFastForward:
    """
    Moves the fork's copy of a base branch onto a commit the upstream already holds.

    A move that is not a fast-forward is refused rather than forced - the fork's base is a
    mirror of the upstream trunk, and anything else on it would flow into every branch
    above. Fetching the fork's base never moves ``FETCH_HEAD``.
    """

    git: GitCommandRunner
    """
    The runner every git command goes through.
    """

    fork_remote: str
    """
    The remote holding the fork's copy of the base.
    """

    base_branch: str
    """
    The base branch, by the name the upstream and the fork share.
    """

    refused_push: RefusedPushHandling
    """
    What the fork rejecting the push does to the caller.
    """

    @property
    def fork_reference(self) -> str:
        """
        :return: The tracking ref the fork's copy of the base is fetched into.
        """
        return f"{self.fork_remote}/{self.base_branch}"

    def onto(self, upstream_reference: str) -> FastForwardReport:
        """
        Move the fork's base onto the commit an upstream ref names.

        :param upstream_reference: The already-fetched upstream ref to move it onto.
        :return: What was done.
        :raises GitCommandFailed: If the fork rejects the push and
            :attr:`refused_push` is :attr:`RefusedPushHandling.RAISED`.
        """
        upstream_commit = self.git.commit_at(upstream_reference)
        self.git.fetch_branch(self.fork_remote, self.base_branch, self.fork_remote)
        fork_commit = self.git.commit_or_none(self.fork_reference)

        if fork_commit == upstream_commit:
            return self.report(
                FastForwardOutcome.ALREADY_CURRENT, upstream_reference, fork_commit
            )
        if fork_commit is not None and not self.git.contains(
            fork_commit, upstream_commit
        ):
            return self.report(
                FastForwardOutcome.REFUSED_NOT_FAST_FORWARD,
                upstream_reference,
                fork_commit,
                explanation=(
                    f"'{self.fork_reference}' is not contained in "
                    f"'{upstream_reference}', so moving it would discard commits; "
                    f"resolve this by hand rather than forcing"
                ),
            )
        pushed = self.git.push(
            ProposedPush(
                remote=self.fork_remote,
                refspec=f"{upstream_commit}:refs/heads/{self.base_branch}",
            )
        )
        if self.refused_push is RefusedPushHandling.RAISED:
            pushed.raise_if_failed()
        if not pushed.succeeded:
            return self.report(
                FastForwardOutcome.PUSH_REFUSED,
                upstream_reference,
                fork_commit,
                explanation=pushed.error_output,
            )
        self.git.fetch_branch(self.fork_remote, self.base_branch, self.fork_remote)
        return self.report(
            FastForwardOutcome.PUSHED, upstream_reference, upstream_commit
        )

    def report(
        self,
        outcome: FastForwardOutcome,
        upstream_reference: str,
        commit: str | None,
        explanation: str | None = None,
    ) -> FastForwardReport:
        """
        :param outcome: What became of the fork's base.
        :param upstream_reference: The upstream ref it was compared against.
        :param commit: The commit the fork's base points at now.
        :param explanation: Why it was not moved, if it was not.
        :return: The report, naming this fast-forward's fork ref.
        """
        return FastForwardReport(
            outcome, upstream_reference, self.fork_reference, commit, explanation
        )


def fast_forward(
    configuration: Configuration, git: GitCommandRunner
) -> FastForwardReport:
    """
    Fetch the upstream's base, then move the fork's copy of it onto the upstream's tip.

    This is what closes the pull requests whose work has landed: GitHub marks one merged
    the moment its head becomes an ancestor of its base.

    :param configuration: The resolved configuration.
    :param git: The runner to execute through.
    :return: What was done.
    :raises GitCommandFailed: If the fork rejects the push.
    """
    git.fetch_branch(
        configuration.upstream_location,
        configuration.upstream_base,
        configuration.upstream_remote,
    ).raise_if_failed()
    return ForkBaseFastForward(
        git,
        configuration.fork_remote,
        configuration.upstream_base,
        RefusedPushHandling.RAISED,
    ).onto(f"{configuration.upstream_remote}/{configuration.upstream_base}")
