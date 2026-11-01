"""
One read through the fork's Action, from the session's side: request it, wait for its
result, replay it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Generic

from basstler_domain_model.repository import (
    Repository,
)
from basstler.upstream_reviews.action.branch import (
    RemoteBranch,
)
from basstler.upstream_reviews.vocabulary import (
    ActionBranch,
    ActionFile,
)
from basstler.upstream_reviews.action.exchange import (
    ActionRequest,
    AnswerType,
)
from basstler.upstream_reviews.action.recording import (
    ActionJSONKey,
    ActionResult,
)
from basstler_domain_model.github.clients import (
    RestClient,
)
from basstler.upstream_reviews.exceptions import (
    ActionNotInstalled,
    ActionResultMissing,
    ActionRunFailed,
    ActionRunNotStarted,
)
from basstler_domain_model.github.exceptions import (
    GitHubCommandFailed,
)
from basstler_domain_model.github.vocabulary import (
    CheckOutcome,
    JSONModel,
)

# %% the session's side


class WorkflowRunJSONKey(StrEnum):
    """
    Every field read out of the fork's workflow-run listing.
    """

    WORKFLOW_RUNS = "workflow_runs"
    IDENTIFIER = "id"
    HEAD_SHA = "head_sha"
    CONCLUSION = "conclusion"


@dataclass(frozen=True)
class WorkflowRun(JSONModel):
    """
    One run of the fork's workflow.
    """

    identifier: int
    """
    The run's own number, which its page is addressed by.
    """

    commit: str
    """
    The commit it ran on - the request's, for the run answering it.
    """

    outcome: CheckOutcome
    """
    How it ended, :attr:`CheckOutcome.PENDING` while it runs.
    """

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> WorkflowRun:
        """
        :param data: One listed run.
        :return: The parsed run.
        """
        conclusion = data[WorkflowRunJSONKey.CONCLUSION]
        return cls(
            data[WorkflowRunJSONKey.IDENTIFIER],
            data[WorkflowRunJSONKey.HEAD_SHA],
            CheckOutcome[conclusion.upper()] if conclusion else CheckOutcome.PENDING,
        )

    @property
    def failed(self) -> bool:
        """:return: Whether it ended without succeeding."""
        return self.outcome not in {CheckOutcome.SUCCESS, CheckOutcome.PENDING}


@dataclass(frozen=True)
class WorkflowRunPage(JSONModel):
    """
    The fork's latest runs on the Action branch.
    """

    runs: list[WorkflowRun]
    """
    The runs, newest first.
    """

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> WorkflowRunPage:
        """
        :param data: The listing.
        :return: The parsed page.
        """
        return cls(
            [
                WorkflowRun.from_json(run)
                for run in data[WorkflowRunJSONKey.WORKFLOW_RUNS]
            ]
        )

    @staticmethod
    def path(fork: Repository) -> str:
        """
        :param fork: The fork.
        :return: The REST path listing its latest runs on the Action branch.
        """
        return f"{fork.api_path}/actions/runs?branch={ActionBranch.NAME}&per_page=10"

    def run_for(self, commit: str) -> WorkflowRun | None:
        """
        :param commit: A pushed request.
        :return: The run answering it, if GitHub started one.
        """
        return next((run for run in self.runs if run.commit == commit), None)


@dataclass(frozen=True)
class ActionRead(Generic[AnswerType]):
    """
    One read through the fork's Action: request it, then wait for its answer.
    """

    branch: RemoteBranch
    """
    The fork's Action branch.
    """

    request: ActionRequest
    """
    The read.
    """

    runs: RestClient | None = None
    """
    What lists the fork's runs, so a run that never started or already failed is told
    apart from one still going; ``None`` waits for the result alone.
    """

    poll_interval: float = 5.0
    """
    How many seconds to wait between fetches.
    """

    start_allowance: float = 60.0
    """
    How many seconds GitHub gets to start the run before it is looked for.
    """

    answer_type: type[AnswerType] = ActionResult
    """
    What the run answers the read with: a pull request's report unless named.
    """

    def send(self) -> str:
        """
        Push the request, which starts the run.

        :return: The pushed commit.
        :raises ActionNotInstalled: If the fork has no Action branch.
        """
        if self.branch.fetch() is None:
            raise ActionNotInstalled(self.branch.remote)
        return self.branch.write(
            {ActionFile.REQUEST: json.dumps(self.request.as_sent(), indent=2)},
            f"Request {self.request.kind} {self.request.identifier}",
        )

    def wait(self, timeout: float, commit: str | None = None) -> AnswerType:
        """
        :param timeout: How many seconds to wait at most.
        :param commit: The pushed request, whose run is looked for once
            :attr:`start_allowance` has passed.
        :return: The result answering this request.
        :raises ActionRunNotStarted: If GitHub started no run for it.
        :raises ActionRunFailed: If its run failed before committing a result.
        :raises ActionResultMissing: If no result arrives in time.
        """
        started = time.monotonic()
        while True:
            result = self.collect()
            if result is not None:
                return result
            elapsed = time.monotonic() - started
            if commit is not None and elapsed >= self.start_allowance:
                self.check_run(commit, int(elapsed))
            if elapsed >= timeout:
                raise ActionResultMissing(
                    self.request.identifier, self.branch.fork, int(timeout)
                )
            time.sleep(self.poll_interval)

    def collect(self) -> AnswerType | None:
        """
        :return: The result on the branch, when it answers this request; ``None`` for a
            result answering another request, including one from a release whose results
            named no request at all.
        """
        tip = self.branch.fetch()
        text = self.branch.read(tip, ActionFile.RESULT) if tip else None
        if text is None:
            return None
        data = json.loads(text)
        if data.get(ActionJSONKey.REQUEST_IDENTIFIER) != self.request.identifier:
            return None
        return self.answer_type.from_json(data)

    def check_run(self, commit: str, elapsed: int) -> None:
        """
        Stop waiting early when there is nothing to wait for.

        :param commit: The pushed request.
        :param elapsed: How many seconds have passed since it was pushed.
        :raises ActionRunNotStarted: If GitHub started no run for it.
        :raises ActionRunFailed: If its run failed - but only once a last look finds
            no result, since a run may fail after committing one.
        """
        fork = self.branch.fork
        if self.runs is None or fork is None:
            return
        try:
            page = WorkflowRunPage.from_json(self.runs.get(WorkflowRunPage.path(fork)))
        except GitHubCommandFailed:
            return
        run = page.run_for(commit)
        if run is None:
            raise ActionRunNotStarted(fork, elapsed)
        if run.failed and self.collect() is None:
            raise ActionRunFailed(
                f"its run ended {run.outcome}: {fork.workflow_run_url(run.identifier)}"
            )
