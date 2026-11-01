"""
The run on the fork's Action branch that answers the request a session pushed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from basstler_domain_model.github.repository_client import (
    GitHubRepository,
)
from basstler.maintenance.upstream_action import (
    OpenPullRequestsAnswer,
    OpenPullRequestsRequest,
)
from basstler.upstream_reviews.action.exchange import (
    ActionAnswer,
    ActionRequest,
    ActionRequestKind,
)
from basstler.upstream_reviews.action.recording import ActionResult
from basstler_domain_model.github.clients import (
    GitHubCommandLineClient,
)
from basstler.upstream_reviews.request import UpstreamReadRequest

# %% the run


@dataclass(frozen=True)
class ActionRun:
    """
    One run answering the request pushed to the Action branch, with the runner's
    credential.
    """

    request_file: Path
    """
    The pushed request.
    """

    result_file: Path
    """
    Where the answer is written for the run to commit.
    """

    def answer(self) -> ActionAnswer:
        """
        :return: The upstream read as the pushed request asks.
        """
        data = json.loads(self.request_file.read_text())
        if ActionRequest.kind_of(data) is ActionRequestKind.OPEN_PULL_REQUESTS:
            request = OpenPullRequestsRequest.from_json(data)
            return OpenPullRequestsAnswer.answering(
                request, GitHubRepository.from_environment(request.upstream)
            )
        return ActionResult.answering(
            UpstreamReadRequest.from_json(data), GitHubCommandLineClient()
        )

    def write_answer(self) -> None:
        """
        Answer the pushed request and write the answer to :attr:`result_file`.
        """
        self.result_file.write_text(json.dumps(self.answer().to_json()) + "\n")
