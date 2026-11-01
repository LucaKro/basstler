"""
The scratch fork, its upstream, and the operations that move their copies of the default
branch apart.

Testing a step that catches a fork's default branch up needs three copies of that branch
that can genuinely disagree: this clone's, the fork's and the upstream's. All three are
built locally here, laid out under ``<owner>/<name>.git`` so their URLs name a repository
the way GitHub does - which is what lets the stacked-PR configuration resolve which remote
is which, with no network access and no test-only seam in the code under test.

Kept beside :mod:`scratch_repository` rather than in it: every other hook test builds on
that plain clone, and none of them has a fork.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from basstler.locations import ProjectLocation
from basstler_domain_model.repository import (
    Repository,
)

from ..constants.notes import ScratchBranch
from ..constants.paths import PersonalNotesPath
from ..scratch_repository import (
    SCRATCH_IDENTITY,
    ScratchRepository,
    initialize_bare_repository,
)

FORK_REPOSITORY = Repository("a-fork-owner", "a-project")
"""
The repository the scratch clone's own remote points at.
"""

UPSTREAM_REPOSITORY = Repository("an-upstream-owner", "a-project")
"""
The repository the fork is forked from.
"""

DEFAULT_BRANCH = "main"
"""
The branch all three copies carry, and the one the code under test moves.
"""

FORK_REMOTE = "origin"
"""
What the scratch clone calls its fork.
"""

UPSTREAM_REMOTE = "an-upstream-remote"
"""
What it would call the upstream, whether or not it has that remote.
"""

STACK_CONFIGURATION = f"""\
fork_remote = "{FORK_REMOTE}"
upstream_repository = "{UPSTREAM_REPOSITORY.full_name}"
upstream_remote = "{UPSTREAM_REMOTE}"
upstream_base = "{DEFAULT_BRANCH}"
"""
"""
The project's committed settings naming the upstream.
"""

SESSION_BRANCH = "a-session-branch"
"""
A branch cut from the default branch with nothing committed on it yet.
"""

SHARED_FILE = "a-source-file.txt"
"""
The one file every copy of the default branch carries, rewritten by each commit the
upstream gains - so a working tree with uncommitted changes to it is one git will refuse to
fast-forward over.
"""


@dataclass(frozen=True)
class GitHubRepositoryStandIn:
    """
    A bare repository standing in for a GitHub repository, named the way GitHub names it.
    """

    repository: Repository
    """
    The repository this stand-in answers for.
    """

    path: Path
    """
    The bare repository itself.
    """

    @classmethod
    def created_under(
        cls, root: Path, repository: Repository
    ) -> GitHubRepositoryStandIn:
        """
        :param root: The directory standing in for GitHub.
        :param repository: The repository to stand in for.
        :return: The stand-in, at ``<root>/<owner>/<name>.git``.
        """
        path = Path(repository.clone_url_under(str(root)))
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(repository, initialize_bare_repository(path))

    @property
    def url(self) -> str:
        """
        :return: The URL a remote in the scratch clone points at.
        """
        return f"file://{self.path}"

    @property
    def url_rewriting_key(self) -> str:
        """
        :return: The git setting that makes the stand-in's GitHub URL resolve to it.
        """
        return f"url.{self.url}.insteadOf"


@dataclass(frozen=True)
class ForkedScratchRepository:
    """
    A scratch clone of a fork whose default branch, fork and upstream all sit at one commit,
    checked out on an ordinary work branch.
    """

    repository: ScratchRepository
    """
    The clone the code under test runs in.
    """

    fork: GitHubRepositoryStandIn
    """
    The repository its ``origin`` points at - the copy a later session is cloned from.
    """

    upstream: GitHubRepositoryStandIn
    """
    The repository the fork is forked from, reachable by its GitHub URL.
    """

    @classmethod
    def laid_out_in(cls, repository: ScratchRepository) -> ForkedScratchRepository:
        """
        Commit the settings naming the upstream onto the default branch, publish it to the
        fork and the upstream, then publish the notes branch and check a work branch out.

        :param repository: The scratch repository, committed to or not.
        :return: The laid-out fork.
        """
        repository.run_git("checkout", "--quiet", "-B", DEFAULT_BRANCH)
        repository.write(SHARED_FILE, "the shared base\n")
        repository.write(
            ProjectLocation.PROJECT_STACK_CONFIGURATION, STACK_CONFIGURATION
        )
        repository.commit_everything("the shared base")
        github = repository.project_root.parent / "github"
        forked = cls(
            repository,
            GitHubRepositoryStandIn.created_under(github, FORK_REPOSITORY),
            GitHubRepositoryStandIn.created_under(github, UPSTREAM_REPOSITORY),
        )
        repository.add_remote(FORK_REMOTE, forked.fork.url)
        forked.reach_by_github_url()
        forked.publish_default_branch_to(FORK_REMOTE)
        forked.publish_default_branch_to(forked.upstream.url)
        repository.run_git("fetch", "--quiet", FORK_REMOTE)
        repository.publish_notes_branch(
            {
                ProjectLocation.PERSONAL_NOTES_DOCUMENT: "personal notes\n",
                PersonalNotesPath.GIT_IDENTITY: SCRATCH_IDENTITY.as_git_config_file(),
            }
        )
        repository.resolve_notes_remote_to()
        return forked

    # %% moving the three copies apart

    def advance_upstream(self, commit_count: int) -> str:
        """
        Add commits to the upstream's default branch that neither this clone nor the fork
        has, leaving both where they were and the work branch checked out.

        :param commit_count: How many commits to add.
        :return: The upstream's new tip.
        """
        return self.advance_default_branch_of(self.upstream.url, commit_count)

    def advance_fork(self, commit_count: int) -> str:
        """
        Add commits to the fork's default branch that this clone does not have, as another
        clone pushing to it does.

        :param commit_count: How many commits to add.
        :return: The fork's new tip.
        """
        return self.advance_default_branch_of(FORK_REMOTE, commit_count)

    def advance_default_branch_of(self, destination: str, commit_count: int) -> str:
        """
        Add commits to a remote's default branch that this clone does not have, leaving
        this clone's own copy where it was and the work branch checked out.

        :param destination: The remote name or URL to publish the commits to.
        :param commit_count: How many commits to add.
        :return: The remote's new tip.
        """
        starting_point = self.local_default_branch_tip()
        self.check_out_default_branch()
        for index in range(commit_count):
            self.repository.write(SHARED_FILE, f"a revision from elsewhere ({index})\n")
            self.repository.commit_everything(f"a commit from elsewhere ({index})")
        tip = self.local_default_branch_tip()
        self.publish_default_branch_to(destination)
        self.repository.run_git("checkout", "--quiet", ScratchBranch.WORK)
        self.set_local_default_branch_to(starting_point)
        return tip

    def advance_local_default_branch(self) -> str:
        """
        Commit onto this clone's default branch alone, the one state a fast-forward cannot
        reconcile.

        :return: The commit the default branch now points at.
        """
        self.check_out_default_branch()
        self.repository.write("a-local-only-change.txt", "only here\n")
        self.repository.commit_everything("a commit the upstream does not have")
        self.repository.run_git("checkout", "--quiet", ScratchBranch.WORK)
        return self.local_default_branch_tip()

    def set_local_default_branch_to(self, commit: str) -> None:
        """
        Move this clone's default branch without touching the fork's.

        :param commit: The commit to move it to.
        """
        self.repository.run_git("update-ref", f"refs/heads/{DEFAULT_BRANCH}", commit)

    def delete_local_default_branch(self) -> None:
        """
        Leave this clone with no copy of the default branch, as a clone checked out
        straight onto a work branch has.
        """
        self.repository.run_git("branch", "--quiet", "-D", DEFAULT_BRANCH)

    def check_out_default_branch(self) -> None:
        """
        Put the working tree on the default branch, as a session doing trunk work is.
        """
        self.repository.run_git("checkout", "--quiet", DEFAULT_BRANCH)

    def start_a_session_branch(self) -> None:
        """
        Cut a branch from this clone's default branch and check it out, as a cloud session
        is started on one with no commits of its own.
        """
        self.repository.run_git(
            "checkout", "--quiet", "-B", SESSION_BRANCH, DEFAULT_BRANCH
        )

    def publish_default_branch_to(self, destination: str) -> None:
        """
        Push this clone's default branch to a remote or a URL.

        :param destination: The remote name or URL to publish to.
        """
        self.repository.run_git(
            "push", "--quiet", destination, f"{DEFAULT_BRANCH}:{DEFAULT_BRANCH}"
        )

    # %% which routes are open

    def reach_by_github_url(self) -> None:
        """
        Make this clone resolve the upstream's GitHub URL to its stand-in, through git's own
        URL rewriting - the case of a clone that never added the upstream remote.
        """
        self.repository.run_git(
            "config",
            self.upstream.url_rewriting_key,
            self.upstream.repository.clone_url,
        )

    def make_the_upstream_unreachable(self) -> None:
        """
        Cut off the upstream's GitHub URL, leaving a named upstream that cannot be fetched.
        """
        self.repository.run_git("config", "--unset", self.upstream.url_rewriting_key)

    def reach_the_upstream_only_through_its_remote(self) -> None:
        """
        Register the upstream as a remote and cut off its GitHub URL, so the only route left
        to it is the remote - the clone of a contributor who added it.
        """
        self.repository.add_remote(UPSTREAM_REMOTE, self.upstream.url)
        self.make_the_upstream_unreachable()

    def refuse_pushes_to_the_fork(self) -> None:
        """
        Point the fork remote's pushes somewhere that does not exist, as a git proxy that
        refuses a push to the default branch leaves it.
        """
        self.repository.run_git(
            "config",
            f"remote.{FORK_REMOTE}.pushurl",
            str(self.repository.project_root.parent / "nowhere.git"),
        )

    def forget_the_stack_configuration(self) -> None:
        """
        Delete the settings naming the upstream, leaving a clone that names none.
        """
        (
            self.repository.project_root / ProjectLocation.PROJECT_STACK_CONFIGURATION
        ).unlink()

    def forget_the_fork_remote(self) -> None:
        """
        Remove the fork remote, leaving a clone with no remote to catch up with.
        """
        self.repository.run_git("remote", "remove", FORK_REMOTE)

    # %% reading the copies back

    def commit_at(self, reference: str) -> str:
        """
        :param reference: Any reference the clone can resolve.
        :return: The commit it names.
        """
        return self.repository.run_git("rev-parse", reference).stdout.strip()

    def local_default_branch_tip(self) -> str:
        """
        :return: The commit this clone's default branch points at.
        """
        return self.commit_at(f"refs/heads/{DEFAULT_BRANCH}")

    def fork_default_branch_tip(self) -> str:
        """
        :return: The commit the fork's own default branch points at, read from the fork
            rather than from what this clone last saw of it.
        """
        return self.repository.run_git(
            "rev-parse", DEFAULT_BRANCH, cwd=self.fork.path
        ).stdout.strip()
