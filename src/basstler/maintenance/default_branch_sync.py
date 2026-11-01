"""
Fast-forward the base branch sessions start from, and the fork's copy of it, to the
upstream this fork tracks.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from basstler.exit_codes import CommandFailure, DomainModelFailure
from basstler.maintenance.fast_forward import (
    FastForwardOutcome,
    ForkBaseFastForward,
    RefusedPushHandling,
)
from basstler_domain_model.git.commands import (
    GitCommandRunner,
)
from basstler_domain_model.exceptions import (
    MalformedRepositoryError,
)
from basstler_domain_model.repository import (
    Repository,
)
from basstler.stack.settings import Configuration
from basstler.stack.configuration import (
    CommittedStackConfiguration,
    MissingUpstreamRepositoryError,
    StackSetting,
)

FOLLOW_UP_ROW_INDENT = "    "
"""
What each row under the outcome is indented by, matching the setup line's rows.
"""


class BaseOutcome(StrEnum):
    """
    What became of this clone's copy of the upstream's base branch.
    """

    NOT_SYNCED = "not-synced"
    """
    No upstream could be resolved, so there was nothing to catch up with.
    """

    UPSTREAM_UNREACHABLE = "upstream-unreachable"
    """
    The upstream is named but could not be fetched, so how stale the clone is stays
    unknown.
    """

    DIVERGED = "diverged"
    """
    The base carries commits the upstream does not, which a fast-forward cannot
    reconcile and a force push must never resolve.
    """

    LOCAL_UPDATE_REFUSED = "local-update-refused"
    """
    The base is checked out with changes git will not overwrite.
    """

    CURRENT = "current"
    """
    The base already carried the upstream's tip.
    """

    FAST_FORWARDED = "fast-forwarded"
    """
    The base was behind and has been brought up to the upstream's tip.
    """

    @property
    def carries_upstream_tip(self) -> bool:
        """
        :return: Whether the base ends the pass at the upstream's tip.
        """
        return self in {BaseOutcome.CURRENT, BaseOutcome.FAST_FORWARDED}


@dataclass(frozen=True)
class DefaultBranchReport:
    """
    What one catching-up pass did, and what it leaves for the session to do.
    """

    outcome: BaseOutcome
    """
    What became of this clone's copy of the base.
    """

    base_branch: str = ""
    """
    The branch caught up, as the upstream names it.
    """

    upstream: str = ""
    """
    The upstream, as ``owner/name``.
    """

    reason: str = ""
    """
    Why nothing was synced, in the words the resolution refused with.
    """

    commits_behind: int = 0
    """
    How many commits this clone's base was behind the upstream.
    """

    fork_remote: str = ""
    """
    The remote the fork's copy was published to.
    """

    fork_fast_forward: FastForwardOutcome | None = None
    """
    What became of the fork's copy, absent when it was not attempted.
    """

    checked_out_branch: str = ""
    """
    The branch the session is on, empty when it is the base or no branch at all.
    """

    checked_out_branch_fast_forwarded: int = 0
    """
    How many commits the checked-out branch was fast-forwarded onto the base.
    """

    checked_out_branch_behind: int = 0
    """
    How many commits the checked-out branch is still behind the base.
    """

    @property
    def outcome_line(self) -> str:
        """
        :return: The first line of the report, saying what became of the base.
        """
        match self.outcome:
            case BaseOutcome.NOT_SYNCED:
                return f"not synced - {self.reason}"
            case BaseOutcome.UPSTREAM_UNREACHABLE:
                return f"'{self.base_branch}' left as it is - {self.upstream} is unreachable"
            case BaseOutcome.DIVERGED:
                return (
                    f"'{self.base_branch}' has commits {self.upstream} does not - "
                    f"left as it is, nothing force-pushed"
                )
            case BaseOutcome.LOCAL_UPDATE_REFUSED:
                return (
                    f"'{self.base_branch}' is behind {self.upstream} but is checked out "
                    f"with changes git will not overwrite - left as it is"
                )
            case BaseOutcome.CURRENT:
                return f"'{self.base_branch}' already matches {self.upstream}"
            case BaseOutcome.FAST_FORWARDED:
                return (
                    f"'{self.base_branch}' fast-forwarded {self.commits_behind} "
                    f"commit(s) to {self.upstream}"
                )

    @property
    def follow_up_rows(self) -> tuple[str, ...]:
        """
        :return: What is left for the session to do, one row each.
        """
        rows = []
        match self.fork_fast_forward:
            case FastForwardOutcome.PUSHED:
                rows.append(f"pushed it to '{self.fork_remote}', which was behind")
            case FastForwardOutcome.PUSH_REFUSED:
                rows.append(
                    f"pushing it to '{self.fork_remote}' was refused - the fork is "
                    f"still behind, so the next clone starts stale"
                )
            case FastForwardOutcome.REFUSED_NOT_FAST_FORWARD:
                rows.append(
                    f"'{self.fork_remote}' has commits {self.upstream} does not - left "
                    f"as it is, nothing force-pushed, so the next clone starts stale"
                )
            case None if self.outcome.carries_upstream_tip:
                rows.append(
                    f"no upstream is named, so this follows '{self.fork_remote}' itself"
                )
        if self.checked_out_branch_fast_forwarded:
            rows.append(
                f"fast-forwarded '{self.checked_out_branch}' "
                f"{self.checked_out_branch_fast_forwarded} commit(s) onto "
                f"'{self.base_branch}' - it had no commits of its own"
            )
        if self.checked_out_branch_behind:
            rows.append(
                f"this branch is {self.checked_out_branch_behind} commit(s) behind "
                f"'{self.base_branch}' - merge or rebase it before planning on a stale base"
            )
        return tuple(rows)

    def render(self) -> str:
        """
        :return: The outcome, then each follow-up row indented beneath it.
        """
        return "\n".join(
            [
                self.outcome_line,
                *(f"{FOLLOW_UP_ROW_INDENT}{row}" for row in self.follow_up_rows),
            ]
        )


@dataclass(frozen=True)
class BaseSource:
    """
    Where the base is caught up from, and where the result is published.
    """

    base_branch: str
    """
    The branch every session starts from.
    """

    location: str
    """
    The remote name or URL the base is fetched from.
    """

    remote_name: str
    """
    The remote whose tracking refs the fetched base is kept under.
    """

    name: str
    """
    The repository caught up with, as ``owner/name`` where it can be read.
    """

    fork_remote: str
    """
    The fork's remote, or the clone's own remote when no upstream is named.
    """

    publishes_to_fork: bool
    """
    Whether the fork's copy is a separate copy that needs bringing level too.
    """

    @classmethod
    def upstream_of(cls, configuration: Configuration) -> BaseSource:
        """
        :param configuration: The resolved stacked-PR configuration.
        :return: The upstream it names, published to the fork after.
        """
        return cls(
            base_branch=configuration.upstream_base,
            location=configuration.upstream_location,
            remote_name=configuration.upstream_remote,
            name=configuration.upstream_repository.full_name,
            fork_remote=configuration.fork_remote,
            publishes_to_fork=True,
        )

    @classmethod
    def own_remote_of(cls, project_root: Path, git: GitCommandRunner) -> BaseSource:
        """
        :param project_root: The clone, whose committed settings name its remote and base.
        :param git: The runner to read the remote's URL through.
        :return: The clone's own remote, for a project that names no upstream.
        :raises GitCommandFailed: If the clone has no such remote.
        """
        values = CommittedStackConfiguration(project_root=project_root).values()
        remote = values[StackSetting.FORK_REMOTE]
        url = git.run("remote", "get-url", remote)
        try:
            name = Repository.from_remote_url(url).full_name
        except MalformedRepositoryError:
            name = remote
        return cls(
            base_branch=values[StackSetting.UPSTREAM_BASE],
            location=remote,
            remote_name=remote,
            name=name,
            fork_remote=remote,
            publishes_to_fork=False,
        )


@dataclass(frozen=True)
class DefaultBranchSync:
    """
    Catches one clone's copy of the base branch up, and the fork's after it.
    """

    source: BaseSource
    """
    Where the base is caught up from, and where it is published.
    """

    git: GitCommandRunner
    """
    The runner every git command against the clone goes through.
    """

    @classmethod
    def catch_up_checkout(cls, project_root: Path) -> DefaultBranchReport:
        """
        Resolve this checkout's upstream - or, when it names none, its own remote - and
        catch its base up, reporting rather than raising whatever stops it.

        :param project_root: The clone to catch up, which must be the current directory,
            since the configuration resolves the project from it.
        :return: What was done.
        """
        git = GitCommandRunner(project_root)
        try:
            try:
                source = BaseSource.upstream_of(Configuration.load())
            except MissingUpstreamRepositoryError:
                source = BaseSource.own_remote_of(project_root, git)
            return cls(source, git).run()
        except (
            CommandFailure,
            *DomainModelFailure.errors,
            MalformedRepositoryError,
        ) as refusal:
            return DefaultBranchReport(BaseOutcome.NOT_SYNCED, reason=str(refusal))

    @property
    def base_branch(self) -> str:
        """
        :return: The branch every session starts from.
        """
        return self.source.base_branch

    @property
    def upstream_tracking_reference(self) -> str:
        """
        The ref the upstream's base is fetched into - the one the stacked-PR tooling reads
        it from, whether or not this clone has added the upstream as a remote.
        """
        return f"refs/remotes/{self.source.remote_name}/{self.base_branch}"

    @property
    def local_reference(self) -> str:
        """
        :return: This clone's own copy of the base.
        """
        return f"refs/heads/{self.base_branch}"

    def report(self, outcome: BaseOutcome, **details: object) -> DefaultBranchReport:
        """
        :param outcome: What became of the base.
        :param details: The rest of the report's fields.
        :return: The report, naming this pass's base and upstream.
        """
        return DefaultBranchReport(
            outcome=outcome,
            base_branch=self.base_branch,
            upstream=self.source.name,
            fork_remote=self.source.fork_remote,
            **details,
        )

    def run(self) -> DefaultBranchReport:
        """
        Catch the base up, then the fork's copy of it.

        :return: What was done, and what is left for the session to do.
        """
        if not self.git.fetch_branch(
            self.source.location, self.base_branch, self.source.remote_name
        ).succeeded:
            return self.report(BaseOutcome.UPSTREAM_UNREACHABLE)
        upstream_tip = self.git.commit_at(self.upstream_tracking_reference)
        local_tip = self.git.commit_or_none(self.local_reference)

        commits_behind = 0
        if local_tip is not None and local_tip != upstream_tip:
            if not self.git.contains(local_tip, upstream_tip):
                return self.report(BaseOutcome.DIVERGED)
            commits_behind = int(
                self.git.run("rev-list", "--count", f"{local_tip}..{upstream_tip}")
            )
        if local_tip != upstream_tip and not self.move_local_base(
            local_tip, upstream_tip
        ):
            return self.report(BaseOutcome.LOCAL_UPDATE_REFUSED)

        return self.report(
            BaseOutcome.FAST_FORWARDED if commits_behind else BaseOutcome.CURRENT,
            commits_behind=commits_behind,
            fork_fast_forward=self.publish_to_fork(),
            **self.catch_up_checked_out_branch(upstream_tip),
        )

    def catch_up_checked_out_branch(self, base_tip: str) -> dict[str, object]:
        """
        Fast-forward the checked-out branch onto the base when it has no commits of its
        own, and otherwise measure how far behind the base it is.

        A branch with nothing of its own is one a session was cut onto from a stale
        base, before this pass ran; moving it changes no work. A dirty tree that the
        fast-forward would overwrite stops it, in which case the branch is reported
        behind like one with work on it.

        :param base_tip: The commit the base now points at.
        :return: The report's checked-out-branch fields.
        """
        branch = self.git.attempt("branch", "--show-current").output
        behind = int(self.git.run("rev-list", "--count", f"HEAD..{base_tip}"))
        if not branch or branch == self.base_branch or not behind:
            return {"checked_out_branch_behind": behind}
        head = self.git.commit_at("HEAD")
        if self.git.contains(head, base_tip) and (
            self.git.attempt("merge", "--ff-only", "--quiet", base_tip).succeeded
        ):
            return {
                "checked_out_branch": branch,
                "checked_out_branch_fast_forwarded": behind,
            }
        return {"checked_out_branch": branch, "checked_out_branch_behind": behind}

    def move_local_base(self, local_tip: str | None, upstream_tip: str) -> bool:
        """
        Fast-forward this clone's base onto the upstream's tip.

        Two ways to move the same ref, because git refuses to fetch into the branch that is
        checked out: a fast-forward merge when the session is sitting on the base, and a
        ref update guarded by the old tip otherwise, which needs no working-tree change.

        :param local_tip: Where the base points now, ``None`` if this clone has none.
        :param upstream_tip: Where it is to point.
        :return: Whether it moved.
        """
        if self.git.attempt("branch", "--show-current").output == self.base_branch:
            return self.git.attempt(
                "merge", "--ff-only", "--quiet", upstream_tip
            ).succeeded
        return self.git.attempt(
            "update-ref", self.local_reference, upstream_tip, local_tip or ""
        ).succeeded

    def publish_to_fork(self) -> FastForwardOutcome | None:
        """
        Bring the fork's copy of the base level with the upstream's tip too.

        Decided on the fork's own evidence rather than on the local base having moved: a
        clone whose base was already current still leaves every later clone stale if an
        earlier session's push was refused and nobody noticed.

        :return: What became of the fork's copy, ``None`` when there is no separate
            fork.
        """
        if not self.source.publishes_to_fork:
            return None
        return (
            ForkBaseFastForward(
                self.git,
                self.source.fork_remote,
                self.base_branch,
                RefusedPushHandling.REPORTED,
            )
            .onto(self.upstream_tracking_reference)
            .outcome
        )


def main() -> int:
    """
    Catch the current checkout's base up and print what was done.

    :return: The process exit code, 0 whatever the outcome.
    """
    print(DefaultBranchSync.catch_up_checkout(Path.cwd()).render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
