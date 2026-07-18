"""Unit tests for the v2 opaque identifier helpers."""

import pytest
from assertpy import assert_that

from podex.api.v2.identifiers import IdentifierKind, decode, encode


@pytest.mark.parametrize("value", [0, 1, 2, 31, 32, 100, 1000, 123456789, 2**63])
def test_encode_decode_round_trip(value: int) -> None:
    """``decode(kind, encode(kind, n))`` returns the original integer."""
    token = encode(IdentifierKind.PODCAST, value)

    assert_that(token).starts_with(f"{IdentifierKind.PODCAST}_")
    assert_that(decode(IdentifierKind.PODCAST, token)).is_equal_to(value)


def test_encode_uses_short_lowercase_body() -> None:
    """Encoded tokens use lowercase base32 without padding."""
    token = encode(IdentifierKind.PODCAST, 42)

    assert_that(token).matches(r"^pod_[a-z2-7]+$")


def test_encode_rejects_negative() -> None:
    """Negative integers cannot be encoded."""
    assert_that(encode).raises(ValueError).when_called_with(
        IdentifierKind.PODCAST,
        -1,
    )


def test_every_kind_uses_a_valid_prefix() -> None:
    """Each enum member's prefix is a short lowercase alphanumeric token."""
    for kind in IdentifierKind:
        assert_that(str(kind)).matches(r"^[a-z][a-z0-9]{1,7}$")


def test_decode_rejects_wrong_kind() -> None:
    """A token encoded for one kind cannot be decoded under another."""
    token = encode(IdentifierKind.PODCAST, 7)

    assert_that(decode).raises(ValueError).when_called_with(
        IdentifierKind.EPISODE,
        token,
    )


def test_decode_rejects_missing_body() -> None:
    """A token with an empty body is not accepted."""
    assert_that(decode).raises(ValueError).when_called_with(
        IdentifierKind.PODCAST,
        "pod_",
    )


def test_decode_rejects_non_base32_characters() -> None:
    """Uppercase or otherwise-invalid characters are rejected."""
    assert_that(decode).raises(ValueError).when_called_with(
        IdentifierKind.PODCAST,
        "pod_AE",
    )
    assert_that(decode).raises(ValueError).when_called_with(
        IdentifierKind.PODCAST,
        "pod_9!",
    )


def test_kinds_cover_expected_prefixes() -> None:
    """The closed vocabulary exposes exactly the four public resource kinds."""
    assert_that({str(kind) for kind in IdentifierKind}).is_equal_to(
        {"pod", "epi", "med", "men"},
    )
