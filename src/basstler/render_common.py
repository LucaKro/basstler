"""
Shared rendering helpers for the plan-dashboard scripts.

No plan-specific content belongs here - this module only knows how to (a)
build the Jinja2 environment every page template renders through and (b)
turn generic markdown into HTML. dashboard/plan_page.py and dashboard/index_page.py both
import it rather than duplicating the logic.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from enum import IntEnum, StrEnum
from functools import cache
from typing import Any
from urllib.parse import urlsplit

import jinja2
import markdown as markdown_library
import nh3

from basstler_domain_model.class_property import (
    classproperty,
)
from basstler.locations import PackageLocation


class AllowedUrlScheme(StrEnum):
    """
    The URL schemes a manifest- or index-authored link may use.
    """

    HTTP = "http"
    """
    Plain web links.
    """

    HTTPS = "https"
    """
    Secure web links.
    """


class HeadingShift(IntEnum):
    """
    How a roadmap's own headings are pushed down to nest inside a dashboard page.

    A roadmap's ``<h1>Title</h1>`` becomes ``<h4>Title</h4>`` once embedded, and its
    ``<h5>Deep</h5>`` becomes ``<h6>Deep</h6>`` - shifted but capped, rather than
    overflowing to a nonexistent ``<h8>``.
    """

    LEVELS = 3
    """
    How many levels to push a roadmap's own headings down by, so they nest below the
    embedding dashboard page's own h1-h3.
    """

    DEEPEST_LEVEL = 6
    """
    The deepest valid HTML heading level - shifting never produces anything past this,
    even for an already-deep roadmap heading.
    """

    @staticmethod
    @cache
    def tag_pattern() -> re.Pattern[str]:
        """
        :return: The pattern matching one opening or closing HTML heading tag, ``<h1>``
            through ``</h6>``; group 1 is the closing slash, group 2 the level.
        """
        return re.compile(r"<(/?)h([1-6])>")

    @classmethod
    def shifted(cls, heading_tag_match: re.Match[str]) -> str:
        """
        Shift one heading tag match down by :attr:`LEVELS`, capped at
        :attr:`DEEPEST_LEVEL`.

        Used as the substitution callback for :meth:`tag_pattern`.

        :param heading_tag_match: A match of :meth:`tag_pattern`.
        :return: The tag with its level shifted.
        """
        closing_slash, original_level = heading_tag_match.group(1), int(
            heading_tag_match.group(2)
        )
        shifted_level = min(original_level + cls.LEVELS, cls.DEEPEST_LEVEL)
        return f"<{closing_slash}h{shifted_level}>"


def create_template_environment() -> jinja2.Environment:
    """
    Build the Jinja2 environment every template renders through.

    Autoescaping is on for every value substituted into an HTML template - the
    one deliberately unescaped value either script ever passes in
    (already-rendered roadmap markdown HTML) is marked with Jinja2's
    ``| safe`` filter at its point of use in the template itself, rather
    than disabling escaping globally and trusting every call site to
    remember to escape by hand. A template of any other format is rendered as
    written.

    :return: A configured, ready-to-use Jinja2 environment.
    """
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(PackageLocation.TEMPLATES.value),
        autoescape=jinja2.select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment


class TemplatePage(ABC):
    """
    A page rendered from one of the package's Jinja2 templates.

    Every page builder loads its template through the same environment, so autoescaping
    and whitespace handling cannot differ between pages.
    """

    @classproperty
    @abstractmethod
    def template(cls) -> str:
        """
        The file name of the template this page is rendered from, in
        :attr:`PackageLocation.TEMPLATES`.
        """

    def render_template(self, **context: Any) -> str:
        """
        :param context: The values the template substitutes, by name.
        :return: The page's rendered HTML.
        """
        return self.render_named_template(self.template, **context)

    @staticmethod
    def render_named_template(template_name: str, **context: Any) -> str:
        """
        :param template_name: The file name of a template in
            :attr:`PackageLocation.TEMPLATES`.
        :param context: The values the template substitutes, by name.
        :return: The rendered template.
        """
        environment = create_template_environment()
        return environment.get_template(template_name).render(**context)


def render_markdown_to_html(markdown_text: str) -> str:
    """
    Render GitHub-flavored markdown (headings, lists, code, tables) to HTML.

    Delegates to the ``markdown`` library (with its ``tables`` and
    ``fenced_code`` extensions) rather than a hand-rolled parser, then
    sanitizes the result with ``nh3`` - the ``markdown`` library passes raw
    HTML embedded in its source through unchanged, and this function's
    output is later marked ``| safe`` and embedded directly into a published
    page, so a ``<script>`` tag or an event-handler attribute in a
    contributor-authored ``roadmap.md`` must not survive to here.

    :param markdown_text: The raw markdown source (typically a plan's
        ``roadmap.md``).
    :return: The rendered, sanitized HTML. Callers embedding this into a
        Jinja2 template must mark it ``| safe`` - it is HTML, not text to
        escape.
    """
    html_text = markdown_library.markdown(
        markdown_text, extensions=["tables", "fenced_code"]
    )
    sanitized_html_text = nh3.clean(html_text, link_rel=None)
    return HeadingShift.tag_pattern().sub(HeadingShift.shifted, sanitized_html_text)


def sanitize_http_url(url: str | None) -> str | None:
    """
    Reject a manifest- or index-authored URL that isn't ``http``/``https``
    before it reaches an ``<a href>`` - a ``javascript:`` or ``data:`` value
    would otherwise render as a clickable, script-executing link.

    :param url: A raw URL from plan.yaml (``session``) or plans.json
        (``dashboard_url``), or ``None`` if unset.
    :return: *url* unchanged if its scheme is ``http`` or ``https``;
        ``None`` otherwise.
    """
    if url is None or urlsplit(url).scheme.lower() not in set(AllowedUrlScheme):
        return None
    return url
