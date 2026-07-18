"""Opaque public identifier helpers for the v2 API.

Integer primary keys stay internal to the database and ORM layer. The public
API exposes prefixed, opaque identifiers (e.g. ``pod_ci``) alongside the
numeric ids so that clients that treat identifiers as free-form tokens keep
working when the internal identifier strategy changes.

The encoding is intentionally simple and dependency-free: the integer is
serialised as big-endian bytes and base32-encoded without padding, then
concatenated with a short kind-specific prefix. It is *not* a cryptographic
scheme — callers that need unpredictability should compose an HMAC on top.

Example:

    >>> encode(IdentifierKind.PODCAST, 1)
    'pod_ae'
    >>> decode(IdentifierKind.PODCAST, "pod_ae")
    1
"""

from __future__ import annotations

import base64
import re
from enum import StrEnum, unique


@unique
class IdentifierKind(StrEnum):
    """Public identifier prefix for each exposed resource kind.

    The enum is the single closed vocabulary of identifier kinds: adding a
    new public resource means adding a member here, and ``@unique`` rejects
    duplicate prefixes at import time.
    """

    PODCAST = "pod"
    EPISODE = "epi"
    MEDIA = "med"
    MENTION = "men"


_ALLOWED_PREFIX = re.compile(r"^[a-z][a-z0-9]{1,7}$")
_ALLOWED_BODY = re.compile(r"^[a-z2-7]+$")


def _validate_prefix(prefix: str) -> None:
    """Raise ``ValueError`` when ``prefix`` is not a short lowercase token."""
    if not _ALLOWED_PREFIX.match(prefix):
        raise ValueError(
            f"prefix must be 2-8 lowercase alphanumerics starting with a letter: "
            f"{prefix!r}",
        )


def encode(kind: IdentifierKind, value: int) -> str:
    """Encode a non-negative integer id into a prefixed opaque token.

    Args:
        kind: The identifier kind whose prefix the token carries.
        value: The non-negative integer identifier to encode.

    Returns:
        The token ``"{prefix}_{body}"`` where ``body`` is the lowercase
        unpadded base32 representation of ``value``'s big-endian bytes.

    Raises:
        ValueError: If ``kind``'s prefix is not a short lowercase token or
            ``value`` is negative.
    """
    _validate_prefix(kind)
    if value < 0:
        raise ValueError(f"value must be non-negative: {value!r}")
    length = max(1, (value.bit_length() + 7) // 8)
    body = base64.b32encode(value.to_bytes(length, "big")).decode("ascii")
    return f"{kind}_{body.rstrip('=').lower()}"


def decode(kind: IdentifierKind, token: str) -> int:
    """Decode a prefixed opaque token back into its integer id.

    Args:
        kind: The expected identifier kind; the token must start with
            ``f"{kind}_"``.
        token: The opaque token to decode.

    Returns:
        The non-negative integer identifier originally passed to :func:`encode`.

    Raises:
        ValueError: If ``token`` is malformed, uses a different kind's prefix,
            or contains characters outside the base32 alphabet.
    """
    _validate_prefix(kind)
    expected_start = f"{kind}_"
    if not token.startswith(expected_start):
        raise ValueError(
            f"identifier does not start with {expected_start!r}: {token!r}",
        )
    body = token[len(expected_start) :]
    if not body or not _ALLOWED_BODY.match(body):
        raise ValueError(f"identifier body is not valid base32: {token!r}")
    padding = "=" * ((-len(body)) % 8)
    try:
        raw = base64.b32decode(body.upper() + padding)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise ValueError(f"identifier body is not valid base32: {token!r}") from exc
    return int.from_bytes(raw, "big")
