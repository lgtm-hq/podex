"""Bounded feed bytes and the response metadata needed by the parser."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FeedResponse:
    """A fetched feed body with its final base URL and HTTP content type."""

    body: bytes
    final_url: str
    content_type: str
