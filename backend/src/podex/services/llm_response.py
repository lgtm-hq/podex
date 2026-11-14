"""Response decoding shared by Anthropic-backed ingestion tasks."""

import json

import anthropic


class MalformedResponseError(ValueError):
    """A successful provider response did not have the Messages API shape."""


# The SDK does not validate success bodies by default: a non-JSON body arrives
# as text, an incomplete object is built without its missing fields, and an
# invalid JSON body raises the decoder error from ``messages.create``.
MALFORMED_RESPONSE_ERRORS = (
    anthropic.APIResponseValidationError,
    json.JSONDecodeError,
    MalformedResponseError,
)


def first_response_text(response: object) -> str:
    """Return the first text block, or an empty string for non-text output.

    Args:
        response: Value returned by ``messages.create``.

    Returns:
        The stripped text of the first text block, or ``""`` when the
        response contains no text block.

    Raises:
        MalformedResponseError: When the response has no content block list
            or its first text block carries no text.
    """
    content = getattr(response, "content", None)
    if not isinstance(content, list):
        raise MalformedResponseError("response has no content block list")
    for block in content:
        if getattr(block, "type", None) != "text":
            continue
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            raise MalformedResponseError("text block has no text")
        return text.strip()
    return ""
