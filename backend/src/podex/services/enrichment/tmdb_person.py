"""TMDB Person API enrichment provider for people."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from podex.models.media import MediaType
from podex.services.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentSource,
    describe_http_error,
)

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)


class TMDBPersonProvider(EnrichmentProvider):  # type: ignore[misc, unused-ignore]
    """Enrich people from TMDB Person API.

    Also used as fallback for standup_special when the title
    appears to be a person's name rather than a show title.

    Args:
        api_key: TMDB API key.
        requests_per_second: Rate limit for API calls.
    """

    BASE_URL = "https://api.themoviedb.org/3"
    IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

    source = EnrichmentSource.TMDB_PERSON

    SUPPORTED_TYPES = {MediaType.PERSON, "standup_special"}

    def __init__(self, api_key: str, requests_per_second: float = 3.0) -> None:
        super().__init__(requests_per_second)
        self.api_key = api_key
        self.client = httpx.Client(
            base_url=self.BASE_URL,
            params={"api_key": api_key},
            timeout=30.0,
        )

    def supports_media_type(self, media_type: str | MediaType) -> bool:
        """Check if TMDB Person supports this media type."""
        return media_type in self.SUPPORTED_TYPES

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Search TMDB for a person and enrich.

        Args:
            media: The media item to enrich.

        Returns:
            EnrichmentResult if found, None otherwise.
        """
        if not self.supports_media_type(media.type):
            return None

        self.rate_limiter.wait_sync()

        # For standup_special, check if title looks like a person name
        # (no colons, parentheses, or typical show title patterns)
        if media.type == "standup_special" and not self._looks_like_person_name(
            media.title
        ):
            return None

        # Search for person
        results = self._search_person(media.title)

        if not results:
            logger.debug(f"No TMDB person results for: {media.title}")
            return None

        # Find best match
        best_match = self._find_best_match(results, media)
        if not best_match:
            return None

        person_id, confidence = best_match

        # Fetch full details
        self.rate_limiter.wait_sync()
        details = self._get_person_details(person_id)
        if not details:
            return None

        return self._build_result(details, confidence)

    def _looks_like_person_name(self, title: str) -> bool:
        """Check if a title looks like a person's name.

        Args:
            title: The title to check.

        Returns:
            True if it looks like a person name.
        """
        # Titles with these patterns are likely show names, not people
        show_indicators = [":", "-", "(", ")", "Live", "Special", "Tour", "Show"]
        title_lower = title.lower()

        for indicator in show_indicators:
            if indicator.lower() in title_lower:
                return False

        # Check if it's 2-4 words (typical name length)
        words = title.split()
        return not (len(words) < 1 or len(words) > 4)

    def _search_person(self, query: str) -> list[dict[str, Any]]:
        """Search TMDB for a person.

        Args:
            query: Search query (person name).

        Returns:
            List of person results.
        """
        try:
            response = self.client.get(
                "/search/person",
                params={"query": query},
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            result: list[dict[str, Any]] = data.get("results", [])
            return result
        except httpx.HTTPError as e:
            logger.warning(
                f"TMDB person search error for '{query}': {describe_http_error(e)}",
            )
            return []

    def _find_best_match(
        self,
        results: list[dict[str, Any]],
        media: Media,
    ) -> tuple[int, float] | None:
        """Find best matching person result.

        Args:
            results: Search results from TMDB.
            media: Original media item.

        Returns:
            Tuple of (person_id, confidence) or None.
        """
        if not results:
            return None

        best_id = None
        best_score = 0.0

        for person in results[:5]:  # Check top 5 results
            person_name = person.get("name", "")

            # Calculate name similarity
            name_score = self._calculate_title_similarity(
                media.title,
                person_name,
            )

            # Boost if they're known for relevant work
            known_for = person.get("known_for", [])
            relevance_boost = 0.0

            # For standup specials, boost if known for comedy
            if media.type == "standup_special":
                for work in known_for:
                    genres = work.get("genre_ids", [])
                    # 35 is Comedy genre in TMDB
                    if 35 in genres:
                        relevance_boost = 0.1
                        break

            # Boost popular people slightly
            popularity = person.get("popularity", 0)
            if popularity > 10:
                relevance_boost += 0.05

            total_score = name_score + relevance_boost

            if total_score > best_score:
                best_score = total_score
                best_id = person.get("id")

        if best_id and best_score >= 0.7:
            return (best_id, min(best_score, 1.0))

        return None

    def _get_person_details(self, person_id: int) -> dict[str, Any] | None:
        """Fetch full person details from TMDB.

        Args:
            person_id: TMDB person ID.

        Returns:
            Person details dict or None.
        """
        try:
            params = {"append_to_response": "combined_credits,external_ids"}
            response = self.client.get(f"/person/{person_id}", params=params)
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result
        except httpx.HTTPError as e:
            logger.warning(
                f"TMDB person details error for ID {person_id}: "
                f"{describe_http_error(e)}",
            )
            return None

    def _build_result(
        self,
        details: dict[str, Any],
        confidence: float,
    ) -> EnrichmentResult:
        """Build enrichment result from TMDB person details.

        Args:
            details: TMDB person details.
            confidence: Match confidence score.

        Returns:
            EnrichmentResult with all available data.
        """
        # Profile image
        cover_url = None
        profile_path = details.get("profile_path")
        if profile_path:
            cover_url = f"{self.IMAGE_BASE}{profile_path}"

        # Biography
        description = details.get("biography")
        if description and len(description) > 500:
            # Truncate long bios
            description = description[:500] + "..."

        # External IDs
        tmdb_id = details.get("id")
        external_ids: dict[str, str | int] = {}
        if tmdb_id is not None:
            external_ids["tmdb_person_id"] = tmdb_id
        ext_ids = details.get("external_ids", {})
        if ext_ids.get("imdb_id"):
            external_ids["imdb_id"] = ext_ids["imdb_id"]
        if ext_ids.get("twitter_id"):
            external_ids["twitter_id"] = ext_ids["twitter_id"]
        if ext_ids.get("instagram_id"):
            external_ids["instagram_id"] = ext_ids["instagram_id"]

        # Build metadata
        metadata: dict[str, Any] = {}

        # Basic info
        if details.get("birthday"):
            metadata["birthday"] = details["birthday"]
        if details.get("deathday"):
            metadata["deathday"] = details["deathday"]
        if details.get("place_of_birth"):
            metadata["place_of_birth"] = details["place_of_birth"]

        # Known for department
        known_for = details.get("known_for_department")
        if known_for:
            metadata["known_for"] = known_for

        # Popularity
        popularity = details.get("popularity")
        if popularity:
            metadata["tmdb_popularity"] = round(popularity, 1)

        # Notable works (top 5 by popularity)
        credits = details.get("combined_credits", {})
        cast_credits = credits.get("cast", [])
        if cast_credits:
            # Sort by popularity and take top 5
            sorted_credits = sorted(
                cast_credits,
                key=lambda x: x.get("popularity", 0),
                reverse=True,
            )[:5]
            metadata["notable_works"] = [
                {
                    "title": c.get("title") or c.get("name"),
                    "year": (c.get("release_date") or c.get("first_air_date") or "")[
                        :4
                    ],
                    "type": "movie" if c.get("title") else "tv",
                }
                for c in sorted_credits
                if c.get("title") or c.get("name")
            ]

        # Also known as (aliases)
        also_known_as = details.get("also_known_as", [])
        if also_known_as:
            metadata["also_known_as"] = also_known_as[:5]

        return EnrichmentResult(
            source=self.source,
            cover_url=cover_url,
            description=description,
            external_ids=external_ids,
            metadata=metadata,
            confidence=confidence,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> TMDBPersonProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
