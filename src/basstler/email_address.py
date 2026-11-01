"""
An email address, read out of the text a user types.
"""

from __future__ import annotations

from dataclasses import dataclass

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.exceptions import MalformedEmailAddressError

# %% the address


@dataclass(frozen=True)
class EmailAddress:
    """
    An email address, written as its text wherever a string is expected.
    """

    local_part: str
    """
    The part before the last ``@``: the mailbox.
    """

    domain: str
    """
    The part after the last ``@``: the host that receives the mail.
    """

    @classproperty
    def separator(cls) -> str:
        """
        What separates the local part from the domain.
        """
        return "@"

    @classmethod
    def parse(cls, text: str) -> EmailAddress:
        """
        Parse a ``local-part@domain`` address.

        :param text: The address to parse.
        :return: The parsed address.
        :raises MalformedEmailAddressError: If *text* lacks a local part or a domain.
        """
        if not cls.is_well_formed(text):
            raise MalformedEmailAddressError(text)
        local_part, _, domain = text.rpartition(cls.separator)
        return cls(local_part, domain)

    @classmethod
    def is_well_formed(cls, text: str) -> bool:
        """
        :param text: The text to test.
        :return: Whether *text* has a local part and a domain, so :meth:`parse` accepts
            it.
        """
        local_part, separator, domain = text.rpartition(cls.separator)
        return bool(local_part and separator and domain)

    def __str__(self) -> str:
        """:return: The address as it is written."""
        return f"{self.local_part}{self.separator}{self.domain}"
