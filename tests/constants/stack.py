"""
The branches, labels and remotes the stacked-pull-request suites share.
"""

from __future__ import annotations

from enum import StrEnum


class StackBranch(StrEnum):
    """
    The branches a stack under test is built from, named once for every suite that
    builds one.

    A suite names several of them per test, in a board entry, a git command and an
    assertion at once, so a spelling that differs anywhere is a test quietly about a
    branch its stack does not contain.
    """

    PARENT = "a-parent"
    """
    The bottom of the chain, cut from the base.
    """

    CHILD = "a-child"
    """
    Stacked directly on the parent.
    """

    BASE = "main"
    """
    The upstream base every stack in the suites ultimately targets.
    """

    ENGINE = "engine"
    """
    The bottom of a three-deep stack, cut from the base.
    """

    ENGINE_UI = "engine-ui"
    """
    Stacked on :attr:`ENGINE`.
    """

    ENGINE_DOCS = "engine-docs"
    """
    Stacked on :attr:`ENGINE_UI`, the top of the three-deep stack.
    """

    PARSER = "parser"
    """
    A single branch cut from the base, beside the three-deep stack.
    """

    FEATURE = "feature"
    """
    The one branch of a stack that needs no other.
    """

    FIRST_ROOT = "first-root"
    """
    One of two branches cut from the base.
    """

    SECOND_ROOT = "second-root"
    """
    The other of two branches cut from the base.
    """

    CHILD_OF_FIRST_ROOT = "child-of-first-root"
    """
    Stacked on :attr:`FIRST_ROOT`.
    """

    LANDED = "landed"
    """
    A branch already in the upstream base.
    """

    LANDED_ELSEWHERE = "landed-elsewhere"
    """
    A parent in the upstream base that no open pull request describes.
    """

    UNLANDED_ELSEWHERE = "unlanded-elsewhere"
    """
    A parent no open pull request describes and that is not in the upstream base.
    """

    OFF_BOARD_PARENT = "off-board-parent"
    """
    A parent other than the base that the board carries no pull request for.
    """

    UNDER_REVIEW = "under-review"
    """
    A branch in the upstream review queue.
    """

    WORK_IN_PROGRESS = "work-in-progress"
    """
    A drafted branch, still being worked on.
    """

    STUCK = "stuck"
    """
    A branch withheld for conflict resolution.
    """

    FINE = "fine"
    """
    A branch beside :attr:`STUCK` that nothing holds back.
    """

    GRANDCHILD = "a-grandchild"
    """
    Stacked on the child, so it reaches the parent only through it.
    """

    SIBLING = "a-sibling"
    """
    A second branch cut from the parent beside the child, so the parent carries two.
    """

    OFF_THE_CHAIN = "off-the-chain"
    """
    Cut from the base beside the chain, so nothing stacks it on the parent.
    """

    ON_ANOTHER_FORK = "on-another-fork"
    """
    The head of a pull request open on the fork whose branch lives on another
    contributor's fork, so the fork remote has no copy of it.
    """

    LONG_LIVED_BASE = "a-long-lived-base"
    """
    A fork branch other than the upstream base that a pull request is based on, while no
    open pull request has it as its head.
    """

    NAMESPACED = "a-namespace/a-branch"
    """
    A branch whose name has a slash in it, as session branches do.
    """


class StackLabel(StrEnum):
    """
    The labels the workflow under test reads and writes, named once for every suite that
    puts one on a pull request.

    A label is written into a board entry, handed to a command and read back in an
    assertion, so a suite that spells it is holding the code to a name nothing else in
    the suite has to agree with.
    """

    IN_REVIEW = "in-review"
    """
    Carried by a branch that has reached the upstream review queue.
    """

    NEEDS_RESOLUTION = "needs-resolution"
    """
    Put on a branch whose owner has been asked to resolve a conflict.
    """

    BUG = "bug"
    """
    Carried by a fix, and never acted on by this tooling - a label it reads past.
    """

    SOMEBODY_ELSES = "a-label-somebody-else-put-here"
    """
    Stands for whatever else a pull request happens to carry - the labels a write must
    preserve precisely because this tool knows nothing about them.
    """


class RemoteName(StrEnum):
    """
    The git remote names the scratch checkouts are built with: the fork and upstream
    names the shipped ``stack.toml`` defaults to.
    """

    ORIGIN = "origin"
    """
    The name git gives the remote a checkout was cloned from.
    """

    UPSTREAM = "upstream"
    """
    The conventional name of the remote a fork was forked from.
    """

    MINE = "mine"
    """
    A remote pointing at the user's own fork, under a name no default matches.
    """

    THEIRS = "theirs"
    """
    A remote pointing at somebody else's fork, beside :attr:`MINE`.
    """

    BACKUP = "backup"
    """
    A remote at a local path, which names no repository.
    """

    UNEXPECTED = "a-name-nobody-expects"
    """
    A fork remote under a name no default or setting mentions.
    """

    PERSONAL_UPSTREAM = "my-own-upstream-name"
    """
    The upstream remote name a personal-notes override picks.
    """

    PREFERRED_UPSTREAM = "an-upstream-remote"
    """
    The name a missing upstream remote is to be added under.
    """
