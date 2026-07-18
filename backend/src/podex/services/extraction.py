"""Mention extraction service."""

from __future__ import annotations

import re
import string
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from podex.models import Media, Mention

TITLE_PATTERN = re.compile(r"\b([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+){1,5})\b")
STOPWORDS = {
    "The",
    "A",
    "An",
    "And",
    "Or",
    "But",
    "If",
    "Then",
    "This",
    "That",
    "These",
    "Those",
    "You",
    "Your",
    "We",
    "Our",
    "I",
    "He",
    "She",
    "They",
    "It",
    "His",
    "Her",
    "Their",
    "My",
    "Mine",
    "On",
    "In",
    "At",
    "Of",
    "To",
    "For",
}


@dataclass
class ExtractedMention:
    """Mention extracted from transcript."""

    media_id: int
    timestamp_seconds: int | None
    context: str
    confidence: float


def normalize_text(value: str) -> str:
    table = str.maketrans("", "", string.punctuation)
    return value.translate(table).lower().strip()


def extract_mentions_from_segments(
    segments: list[dict[str, Any]],
    media_items: list[Media],
    enable_heuristic: bool = False,
) -> list[ExtractedMention]:
    """Extract mentions using known media items.

    Args:
        segments: Transcript segments with text and timestamps
        media_items: Known media items to match against
        enable_heuristic: If True, also extract title-cased sequences as potential
            media references. Disabled by default as it creates many false positives
            (place names, person names, etc.). For production use, consider using
            an LLM to identify actual media references.
    """
    normalized_media = {
        normalize_text(media.title): media.id for media in media_items if media.title
    }

    results: list[ExtractedMention] = []
    seen: set[tuple[int, int | None]] = set()

    for segment in segments:
        text = segment.get("text", "") or ""
        if not text:
            continue
        segment_lower = text.lower()
        timestamp = (
            int(segment.get("start", 0)) if segment.get("start") is not None else None
        )

        for title_norm, media_id in normalized_media.items():
            if title_norm and title_norm in normalize_text(segment_lower):
                key = (media_id, timestamp)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    ExtractedMention(
                        media_id=media_id,
                        timestamp_seconds=timestamp,
                        context=text.strip(),
                        confidence=0.9,
                    )
                )

        # Heuristic extraction: title-cased sequences
        # Disabled by default - creates too many false positives (places, names, etc.)
        if enable_heuristic:
            matches = TITLE_PATTERN.findall(text)
            for match in matches[:5]:
                words = match.split()
                if all(word in STOPWORDS for word in words):
                    continue
                title_norm = normalize_text(match)
                if title_norm in normalized_media:
                    continue
                # Create placeholder media later; mark with id -1 for now
                results.append(
                    ExtractedMention(
                        media_id=-1,
                        timestamp_seconds=timestamp,
                        context=text.strip(),
                        confidence=0.4,
                    )
                )

    return results


def materialize_mentions(
    episode_id: int,
    segments: list[dict[str, Any]],
    media_items: list[Media],
    create_media_fn: Callable[[str], Media],
) -> list[Mention]:
    """Convert extracted mentions into Mention models, creating media when needed."""
    extracted = extract_mentions_from_segments(segments, media_items)
    mentions: list[Mention] = []

    for item in extracted:
        media_id = item.media_id
        if media_id == -1:
            # Create a placeholder media item from context substring
            title = _best_guess_title(item.context)
            media = create_media_fn(title)
            media_id = media.id

        mentions.append(
            Mention(
                episode_id=episode_id,
                media_id=media_id,
                timestamp_seconds=item.timestamp_seconds,
                context=item.context,
                confidence=item.confidence,
            )
        )

    return mentions


def _best_guess_title(context: str) -> str:
    matches: list[str] = TITLE_PATTERN.findall(context)
    if matches:
        return matches[0]
    return context[:80].strip() or "Unknown Title"
