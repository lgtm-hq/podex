"""Pure helpers for matching extracted media titles to canonical aliases."""

from __future__ import annotations

import string
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum, auto


class MediaAliasSource(StrEnum):
    """Source of a candidate alias value."""

    TITLE = auto()
    ALIAS = auto()


class MediaAliasMatchReason(StrEnum):
    """Reason explaining the outcome of canonical media alias matching."""

    EXACT_TITLE = auto()
    EXACT_ALIAS = auto()
    EMPTY_TITLE = auto()
    TYPE_MISMATCH = auto()
    NO_MATCH = auto()


@dataclass(frozen=True, slots=True)
class CandidateAlias:
    """Normalized alias value attached to a canonical media candidate.

    Attributes:
        value: Original title or alias value.
        normalized_value: Normalized value used for exact comparisons.
        source: Whether the value came from the canonical title or alias list.
    """

    value: str
    normalized_value: str
    source: MediaAliasSource


@dataclass(frozen=True, slots=True)
class CanonicalMediaCandidate:
    """Canonical media item available for alias matching.

    Attributes:
        media_id: Canonical media identifier.
        media_type: Canonical media type value.
        title: Canonical media title.
        aliases: Known alternate titles or aliases for the media item.
    """

    media_id: int
    media_type: str
    title: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CanonicalMediaMatch:
    """Result of matching an extracted title to canonical media.

    Attributes:
        candidate: Matched canonical candidate, if any.
        matched_alias: Title or alias value that matched, if any.
        confidence: Match confidence from 0.0 to 1.0.
        reason: Machine-readable reason for the match result.
    """

    candidate: CanonicalMediaCandidate | None
    matched_alias: CandidateAlias | None
    confidence: float
    reason: MediaAliasMatchReason

    @property
    def media_id(self) -> int | None:
        """Return the matched media identifier when present.

        Returns:
            Canonical media identifier, or ``None`` when no candidate matched.
        """
        if self.candidate is None:
            return None
        return self.candidate.media_id


def normalize_media_alias(value: str | None) -> str | None:
    """Normalize a title or alias for replay-safe exact matching.

    Args:
        value: Raw title or alias text.

    Returns:
        Normalized text, or ``None`` when the input is empty.
    """
    if value is None:
        return None

    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    normalized = normalized.casefold()
    punctuation_as_spaces = str.maketrans(
        string.punctuation,
        " " * len(string.punctuation),
    )
    normalized = normalized.translate(punctuation_as_spaces)
    normalized = " ".join(normalized.split()).strip()
    return normalized or None


def normalize_media_title(value: str | None) -> str | None:
    """Normalize a canonical or extracted media title.

    Args:
        value: Raw title text.

    Returns:
        Normalized title, or ``None`` when the input is empty.
    """
    return normalize_media_alias(value)


def normalize_media_type(value: str | None) -> str | None:
    """Normalize a media type value for compatibility checks.

    Args:
        value: Raw media type value.

    Returns:
        Normalized media type, or ``None`` when the input is empty.
    """
    if value is None:
        return None

    normalized = "_".join(value.casefold().replace("-", "_").split()).strip()
    return normalized or None


def media_types_are_compatible(
    *,
    requested_type: str | None,
    candidate_type: str,
) -> bool:
    """Check whether an extracted type can match a canonical type.

    Args:
        requested_type: Media type from the extracted mention.
        candidate_type: Canonical media type for the candidate.

    Returns:
        ``True`` when the media types are an exact normalized match.
    """
    normalized_requested = normalize_media_type(requested_type)
    normalized_candidate = normalize_media_type(candidate_type)
    return (
        normalized_requested is not None
        and normalized_candidate is not None
        and normalized_requested == normalized_candidate
    )


def candidate_aliases(
    *,
    candidate: CanonicalMediaCandidate,
) -> tuple[CandidateAlias, ...]:
    """Build normalized alias values for a canonical media candidate.

    Args:
        candidate: Canonical media candidate.

    Returns:
        Tuple of normalized title and alias values in matching priority order.
    """
    aliases: list[CandidateAlias] = []

    normalized_title = normalize_media_title(candidate.title)
    if normalized_title is not None:
        aliases.append(
            CandidateAlias(
                value=candidate.title,
                normalized_value=normalized_title,
                source=MediaAliasSource.TITLE,
            )
        )

    seen = {alias.normalized_value for alias in aliases}
    for value in candidate.aliases:
        normalized_alias = normalize_media_alias(value)
        if normalized_alias is None or normalized_alias in seen:
            continue

        aliases.append(
            CandidateAlias(
                value=value,
                normalized_value=normalized_alias,
                source=MediaAliasSource.ALIAS,
            )
        )
        seen.add(normalized_alias)

    return tuple(aliases)


def match_canonical_media(
    *,
    title: str | None,
    media_type: str | None,
    candidates: Sequence[CanonicalMediaCandidate],
) -> CanonicalMediaMatch:
    """Choose the best canonical media candidate for an extracted title.

    Args:
        title: Extracted title or alias text.
        media_type: Extracted media type.
        candidates: Canonical candidates to search.

    Returns:
        Match result with confidence and reason.
    """
    normalized_title = normalize_media_title(title)
    if normalized_title is None:
        return CanonicalMediaMatch(
            candidate=None,
            matched_alias=None,
            confidence=0.0,
            reason=MediaAliasMatchReason.EMPTY_TITLE,
        )

    best_match: CanonicalMediaMatch | None = None
    has_incompatible_exact_match = False

    for candidate in candidates:
        for alias in candidate_aliases(candidate=candidate):
            if alias.normalized_value != normalized_title:
                continue

            if not media_types_are_compatible(
                requested_type=media_type,
                candidate_type=candidate.media_type,
            ):
                has_incompatible_exact_match = True
                continue

            match = _exact_match(candidate=candidate, alias=alias)
            if best_match is None or match.confidence > best_match.confidence:
                best_match = match

    if best_match is not None:
        return best_match

    return CanonicalMediaMatch(
        candidate=None,
        matched_alias=None,
        confidence=0.0,
        reason=(
            MediaAliasMatchReason.TYPE_MISMATCH
            if has_incompatible_exact_match
            else MediaAliasMatchReason.NO_MATCH
        ),
    )


def _exact_match(
    *,
    candidate: CanonicalMediaCandidate,
    alias: CandidateAlias,
) -> CanonicalMediaMatch:
    """Build a match result for a compatible exact alias value.

    Args:
        candidate: Canonical media candidate.
        alias: Matched alias value.

    Returns:
        Exact match result.
    """
    if alias.source == MediaAliasSource.TITLE:
        return CanonicalMediaMatch(
            candidate=candidate,
            matched_alias=alias,
            confidence=1.0,
            reason=MediaAliasMatchReason.EXACT_TITLE,
        )

    return CanonicalMediaMatch(
        candidate=candidate,
        matched_alias=alias,
        confidence=0.95,
        reason=MediaAliasMatchReason.EXACT_ALIAS,
    )
