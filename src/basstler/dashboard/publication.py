"""
What a plan's dashboard Artifact is published as, and whether a fresh render needs
publishing at all.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.locations import DashboardArtifactFile

# %% the published files


class PublicationField(StrEnum):
    """
    The keys a publication is reported and recorded under.
    """

    URL = "url"
    """
    The Artifact URL a dashboard is published at.
    """

    CONTENT_DIGEST = "content_digest"
    """
    The digest of every file a dashboard was published as.
    """

    UNCHANGED = "unchanged"
    """
    Whether a fresh render is exactly what was last published.
    """


@dataclass(frozen=True)
class DashboardArtifact:
    """
    A plan's dashboard as the files its Artifact is published as.
    """

    files: dict[DashboardArtifactFile, str]
    """
    Each published file's content.
    """

    @property
    def content_digest(self) -> str:
        """
        The SHA-256 hex digest of every file's path and content, equal for two artifacts
        exactly when they would publish the same files.
        """
        digest = hashlib.sha256()
        for file in DashboardArtifactFile:
            content = self.files[file].encode()
            digest.update(f"{file}\0{len(content)}\0".encode())
            digest.update(content)
        return digest.hexdigest()

    def write_to(self, directory: Path) -> None:
        """
        Write every file at its published path under ``directory``.

        :param directory: An existing directory.
        """
        for file, content in self.files.items():
            (directory / file).write_text(content)


@dataclass(frozen=True)
class DashboardBuildSummary:
    """
    The part of a dashboard build's printed summary that decides its publication.
    """

    content_digest: str
    """
    The :attr:`DashboardArtifact.content_digest` of the files the build wrote.
    """

    @classmethod
    def from_json(cls, text: str) -> DashboardBuildSummary:
        """
        :param text: The build's printed JSON summary.
        :return: The summary it holds.
        """
        return cls(content_digest=json.loads(text)[PublicationField.CONTENT_DIGEST])


# %% the url cache


@dataclass(frozen=True)
class PublicationCheck:
    """
    Where a dashboard is published, and whether a fresh render needs publishing.
    """

    url: str | None
    """
    The Artifact URL to update, or ``None`` when the dashboard was never published.
    """

    unchanged: bool
    """
    Whether the fresh render is exactly what was last published there, so publishing
    it again would change nothing.
    """

    def to_json(self) -> dict[str, Any]:
        """
        :return: The check as a JSON-serializable mapping.
        """
        return {
            PublicationField.URL: self.url,
            PublicationField.UNCHANGED: self.unchanged,
        }


@dataclass
class DashboardUrlCache:
    """
    The dashboard-URL cache: each dashboard's Artifact URL by key, and the digest of
    what was last published there.
    """

    urls: dict[str, str] = field(default_factory=dict)
    """
    Each dashboard's Artifact URL, by plan id or ``_index``.
    """

    content_digests: dict[str, str] = field(default_factory=dict)
    """
    The :attr:`DashboardArtifact.content_digest` last published at each key's URL.
    """

    @classproperty
    def content_digest_section(cls) -> str:
        """
        The top-level key the digests are nested under, beside the URLs.
        """
        return "_content_digests"

    @classmethod
    def from_yaml(cls, text: str) -> DashboardUrlCache:
        """
        :param text: The cache file's text, empty when it does not exist yet.
        :return: The cache it holds.
        """
        entries = yaml.safe_load(text) or {}
        content_digests = entries.pop(cls.content_digest_section, None) or {}
        return cls(urls=entries, content_digests=content_digests)

    def check(self, key: str, content_digest: str) -> PublicationCheck:
        """
        :param key: The dashboard's cache key.
        :param content_digest: The :attr:`DashboardArtifact.content_digest` of a fresh
            render.
        :return: Where that render goes, and whether it is already published there.
        """
        url = self.urls.get(key)
        return PublicationCheck(
            url=url,
            unchanged=url is not None
            and self.content_digests.get(key) == content_digest,
        )
