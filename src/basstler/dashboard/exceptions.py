"""
The errors the dashboard commands raise.
"""

from __future__ import annotations

from dataclasses import dataclass

from basstler_domain_model.exceptions import (
    DataclassException,
)

# %% merging a refresh's summaries


@dataclass
class SummaryKeyCollisionError(DataclassException):
    """
    Raised when two of a refresh's step summaries share a key, so merging them would
    silently drop one value.
    """

    shared_keys: list[str]
    """
    The keys more than one summary carries.
    """

    def error_message(self) -> str:
        return f"summaries share key(s): {', '.join(self.shared_keys)}"

    def suggest_correction(self) -> str:
        return "Rename the key in one of the steps' summaries."
