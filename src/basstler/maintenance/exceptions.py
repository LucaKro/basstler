"""
The failures the maintenance executor refuses its own output with.
"""

from __future__ import annotations

from dataclasses import dataclass

from basstler_domain_model.exceptions import (
    DataclassException,
)


@dataclass
class MarkdownLinkTargetError(DataclassException, ValueError):
    """
    Raised when a markdown link would point at something that is neither a web URL nor
    an anchor on the same page.
    """

    label: str
    """
    The text the link was to show.
    """

    target: str
    """
    What it was to point at.
    """

    def error_message(self) -> str:
        return (
            f"the link '{self.label}' would point at {self.target!r}, which is neither "
            f"a web URL nor an anchor"
        )

    def suggest_correction(self) -> str:
        return "Build the target from structured data rather than from free text."
