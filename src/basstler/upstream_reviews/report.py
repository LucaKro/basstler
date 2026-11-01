"""
The markdown report of an upstream pull request's checks, reviews and threads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from basstler.upstream_reviews.failure_logs import (
    FailureLog,
)
from basstler_domain_model.github.pull_request_models import (
    UpstreamPullRequestSnapshot,
)
from basstler_domain_model.github.review_models import (
    ReviewThread,
)

# %% report


class ReportText(StrEnum):
    """
    The fixed lines the report states rather than computes.
    """

    CHECKS_HEADING = "## Checks"
    NO_CHECKS = "No checks have reported."
    FAILURE_LOGS_HEADING = "## The log behind each failed check"
    REVIEWS_HEADING = "## Reviews"
    NO_REVIEWS = "No reviews submitted."
    NO_UNRESOLVED_HEADING = "## No unresolved review threads"
    NOTHING_TO_ACT_ON = "Nothing to act on."


@dataclass
class UpstreamPullRequestReport:
    """
    Renders a pull request's current upstream state as the markdown a session or a phone
    reads.
    """

    snapshot: UpstreamPullRequestSnapshot
    """
    The upstream state to describe.
    """

    include_resolved: bool = False
    """
    Whether threads already marked resolved are shown too.
    """

    failure_logs: list[FailureLog] = field(default_factory=list)
    """
    The excerpts to quote under the checks, empty where none were read.
    """

    def render(self) -> str:
        """:return: The report as markdown."""
        lines = [
            f"# Upstream pull request: #{self.snapshot.number} {self.snapshot.title}",
            "",
            self.snapshot.url,
            "",
        ]
        lines.extend(self._render_checks())
        lines.extend(self._render_failure_logs())
        lines.extend(self._render_reviews())
        lines.extend(self._render_threads())
        return "\n".join(lines)

    def heading(self, shown_count: int) -> str:
        """
        Describe the set actually being listed, not just the unresolved one.

        :param shown_count: How many threads the section goes on to list.
        :return: The section heading.
        """
        unresolved_count = len(self.snapshot.unresolved_threads)
        if not unresolved_count:
            return ReportText.NO_UNRESOLVED_HEADING
        if self.include_resolved:
            return f"## {shown_count} review threads, {unresolved_count} unresolved"
        return f"## {unresolved_count} unresolved review threads"

    @property
    def shown_threads(self) -> list[ReviewThread]:
        """:return: The threads this report lists."""
        if self.include_resolved:
            return self.snapshot.threads
        return self.snapshot.unresolved_threads

    def _render_checks(self) -> list[str]:
        """:return: The checks section."""
        checks = self.snapshot.checks
        if checks is None:
            return [ReportText.CHECKS_HEADING, "", ReportText.NO_CHECKS, ""]
        unsuccessful = checks.unsuccessful
        succeeded = len(checks.results) - len(unsuccessful)
        lines = [
            f"{ReportText.CHECKS_HEADING}: {checks.state} "
            f"({succeeded}/{len(checks.results)} passed)",
            "",
        ]
        for result in unsuccessful:
            location = f" <{result.url}>" if result.url else ""
            lines.append(f"- **{result.name}** — {result.outcome}{location}")
        lines.append("")
        return lines

    def _render_failure_logs(self) -> list[str]:
        """:return: The quoted-log section, empty where nothing was read."""
        if not self.failure_logs:
            return []
        lines = [ReportText.FAILURE_LOGS_HEADING, ""]
        for failure in self.failure_logs:
            lines.extend([f"### {failure.check_name}", "", "```"])
            lines.extend(failure.lines)
            lines.extend(["```", ""])
        return lines

    def _render_reviews(self) -> list[str]:
        """:return: The submitted-reviews section."""
        if not self.snapshot.reviews:
            return [ReportText.REVIEWS_HEADING, "", ReportText.NO_REVIEWS, ""]
        lines = [ReportText.REVIEWS_HEADING, ""]
        for review in self.snapshot.reviews:
            lines.append(
                f"- **{review.author.login}** — {review.state} "
                f"({review.submitted_at})"
            )
            if review.body.strip():
                lines.append(f"  > {review.body.strip()}")
        lines.append("")
        return lines

    def _render_threads(self) -> list[str]:
        """:return: The review-threads section."""
        shown = self.shown_threads
        lines = [self.heading(len(shown)), ""]
        if not shown:
            lines.extend([ReportText.NOTHING_TO_ACT_ON, ""])
            return lines
        for thread in shown:
            lines.extend(self._render_thread(thread))
        return lines

    def _render_thread(self, thread: ReviewThread) -> list[str]:
        """
        Render one thread with every comment in it.

        :param thread: The thread to render.
        :return: The thread's markdown lines.
        """
        markers = thread.markers
        suffix = f" _({', '.join(markers)})_" if markers else ""
        lines = [f"### `{thread.location}`{suffix}", ""]
        for comment in thread.comments:
            lines.append(f"- **{comment.author.login}**: {comment.body.strip()}")
        if thread.comments:
            lines.extend(["", f"<{thread.comments[0].url}>", ""])
        return lines
