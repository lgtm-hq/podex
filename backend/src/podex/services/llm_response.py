"""Response decoding shared by Anthropic-backed ingestion tasks."""

from anthropic.types import Message


def first_response_text(response: Message) -> str:
    """Return the first text block, or an empty string for non-text output."""
    return next(
        (block.text for block in response.content if block.type == "text"),
        "",
    ).strip()
