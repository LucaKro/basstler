"""
Tests for what the stack helper asks to have written: label writes and promotion links.
"""

from __future__ import annotations

import pytest

from basstler.stack.exceptions import (
    ContradictoryLabelWriteError,
    PromotionLinkTooLongError,
)
from basstler.stack.label_write import LabelWrite
from basstler.stack.promotion_link import PromotionLink, PromotionLinkParameter

from ..constants.stack import StackBranch, StackLabel

from .dataset import (
    StackRepository,
    StackSampleText,
    make_configuration,
    prefilled,
)


# %% label writes


def test_adding_a_label_keeps_every_label_the_pull_request_already_carries():
    """
    The label write replaces the whole set, so computing it from the addition alone
    silently wipes the rest - which has happened, stripping `in-review` off branches that
    were already promoted.
    """
    configuration = make_configuration()

    write = LabelWrite.replacing(
        [configuration.in_review_label, StackLabel.SOMEBODY_ELSES],
        added=[configuration.needs_resolution_label],
    )

    assert write.labels == (
        configuration.in_review_label,
        StackLabel.SOMEBODY_ELSES,
        configuration.needs_resolution_label,
    )


def test_removing_a_label_keeps_every_other_label():
    configuration = make_configuration()

    write = LabelWrite.replacing(
        [configuration.in_review_label, StackLabel.SOMEBODY_ELSES],
        removed=[StackLabel.SOMEBODY_ELSES],
    )

    assert write.labels == (configuration.in_review_label,)


def test_adding_a_label_already_carried_leaves_the_set_unchanged():
    carried = make_configuration().in_review_label

    write = LabelWrite.replacing([carried], added=[carried])

    assert write.labels == (carried,)


def test_removing_a_label_that_is_not_there_leaves_the_set_unchanged():
    configuration = make_configuration()

    write = LabelWrite.replacing(
        [configuration.in_review_label], removed=[configuration.needs_resolution_label]
    )

    assert write.labels == (configuration.in_review_label,)


def test_asking_to_add_and_remove_one_label_at_once_is_refused():
    """
    Either outcome would be a guess at what the caller meant, and the wrong guess is a
    label silently kept or silently dropped.
    """
    configuration = make_configuration()

    with pytest.raises(ContradictoryLabelWriteError):
        LabelWrite.replacing(
            [configuration.in_review_label],
            added=[configuration.needs_resolution_label],
            removed=[configuration.needs_resolution_label],
        )


# %% promotion links


def a_promotion_link(
    title: str = StackSampleText.TITLE, body: str = StackSampleText.BODY
) -> PromotionLink:
    """
    The compare-and-create link for that stack's root branch.

    :param title: The title to prefill.
    :param body: The body to prefill.
    :return: The built link.
    """
    return PromotionLink.build(make_configuration(), StackBranch.ENGINE, title, body)


def test_the_link_compares_the_upstream_base_against_the_fork_branch():
    assert a_promotion_link().url.startswith(
        f"{StackRepository.UPSTREAM.value.url}/compare/{StackBranch.BASE}..."
        f"{StackRepository.FORK.value.owner}:{StackBranch.ENGINE}"
        f"?{PromotionLinkParameter.EXPAND}=1"
    )


def test_the_title_and_body_are_encoded_rather_than_pasted_in_raw():
    """
    An unencoded ``&`` or ``#`` truncates the query string at that character, so the
    prefill silently loses everything after it.
    """
    link = a_promotion_link(
        title=StackSampleText.TITLE_WITH_SEPARATORS,
        body=StackSampleText.BODY_WITH_FRAGMENT,
    )

    assert prefilled(link.url, PromotionLinkParameter.TITLE) == (
        StackSampleText.TITLE_WITH_SEPARATORS
    )
    assert prefilled(link.url, PromotionLinkParameter.BODY) == (
        StackSampleText.BODY_WITH_FRAGMENT
    )


def test_a_body_within_the_limit_is_left_whole():
    link = a_promotion_link(body=StackSampleText.SHORT_BODY)

    assert not link.body_was_truncated
    assert (
        prefilled(link.url, PromotionLinkParameter.BODY) == StackSampleText.SHORT_BODY
    )


def test_an_oversized_body_is_truncated_rather_than_silently_dropped():
    """
    Over the limit the whole prefill is discarded by the server, so the link arrives
    with an empty description and nobody is told.
    """
    link = a_promotion_link(
        body=StackSampleText.FILLER * (PromotionLink.url_character_limit + 1)
    )

    assert link.body_was_truncated
    assert link.url.endswith(PromotionLink.truncation_marker)
    assert len(link.url) <= PromotionLink.url_character_limit


def test_a_link_that_cannot_fit_even_without_a_description_is_refused():
    """
    Returning a link known to be over the limit would be the silent failure this class
    exists to prevent, so the one input that cannot be fixed by shortening the body says
    so.
    """
    with pytest.raises(PromotionLinkTooLongError):
        a_promotion_link(
            title=StackSampleText.FILLER * (PromotionLink.url_character_limit + 1),
            body="",
        )
