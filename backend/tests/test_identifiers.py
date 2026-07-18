"""Unit tests for the v2 opaque identifier helpers."""

import pytest
from assertpy import assert_that

from podex.api.v2.identifiers import (
    EPISODE_PREFIX,
    MEDIA_PREFIX,
    MENTION_PREFIX,
    PODCAST_PREFIX,
    decode,
    encode,
)


@pytest.mark.parametrize("value", [0, 1, 2, 31, 32, 100, 1000, 123456789, 2**63])
def test_encode_decode_round_trip(value: int) -> None:
    """``decode(prefix, encode(prefix, n))`` returns the original integer."""
    token = encode(PODCAST_PREFIX, value)

    assert_that(token).starts_with(f"{PODCAST_PREFIX}_")
    assert_that(decode(PODCAST_PREFIX, token)).is_equal_to(value)


def test_encode_uses_short_lowercase_body() -> None:
    """Encoded tokens use lowercase base32 without padding."""
    token = encode(PODCAST_PREFIX, 42)

    assert_that(token).matches(r"^pod_[a-z2-7]+$")


def test_encode_rejects_negative() -> None:
    """Negative integers cannot be encoded."""
    assert_that(encode).raises(ValueError).when_called_with(PODCAST_PREFIX, -1)


def test_encode_rejects_invalid_prefix() -> None:
    """Prefixes must be short lowercase alphanumeric tokens."""
    for bad in ("", "A", "12", "way_too_long_prefix", "PO"):
        assert_that(encode).raises(ValueError).when_called_with(bad, 1)


def test_decode_rejects_wrong_prefix() -> None:
    """A token encoded for one prefix cannot be decoded under another."""
    token = encode(PODCAST_PREFIX, 7)

    assert_that(decode).raises(ValueError).when_called_with(EPISODE_PREFIX, token)


def test_decode_rejects_missing_body() -> None:
    """A token with an empty body is not accepted."""
    assert_that(decode).raises(ValueError).when_called_with(
        PODCAST_PREFIX,
        "pod_",
    )


def test_decode_rejects_non_base32_characters() -> None:
    """Uppercase or otherwise-invalid characters are rejected."""
    assert_that(decode).raises(ValueError).when_called_with(
        PODCAST_PREFIX,
        "pod_AE",
    )
    assert_that(decode).raises(ValueError).when_called_with(
        PODCAST_PREFIX,
        "pod_9!",
    )


def test_prefixes_are_distinct() -> None:
    """Every well-known prefix is unique so tokens never collide by mistake."""
    prefixes = {PODCAST_PREFIX, EPISODE_PREFIX, MEDIA_PREFIX, MENTION_PREFIX}

    assert_that(prefixes).is_length(4)
