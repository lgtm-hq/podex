"""Wikipedia API enrichment provider.

Universal fallback provider that works for any media type.
Especially useful for studies, historical events, places, and people.

IMPORTANT: This provider validates that results match the expected media type
to avoid incorrect matches (e.g., matching an album when searching for a study).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import httpx

from podex.models.media import MediaType
from podex.services.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentSource,
)
from podex.services.enrichment.data import (
    ALBUM_CATEGORY_PATTERNS,
    CATEGORY_TYPE_PATTERNS,
    TYPE_NEGATIVE_SIGNALS,
    TYPE_POSITIVE_SIGNALS,
)
from podex.services.enrichment.search_utils import (
    calculate_similarity,
    generate_search_variations,
)

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)


class WikipediaProvider(EnrichmentProvider):  # type: ignore[misc, unused-ignore]
    """Enrich media from Wikipedia with strict type validation.

    Wikipedia API is free with no authentication required.
    Works as a universal fallback for any media type.

    This provider validates that search results match the expected media type
    to prevent incorrect matches (e.g., albums matching study searches).

    Args:
        base_url: Override for the MediaWiki API endpoint (``API_URL``).
        requests_per_second: Rate limit for API calls.
    """

    API_URL = "https://en.wikipedia.org/w/api.php"

    source = EnrichmentSource.WIKIPEDIA

    SUPPORTED_TYPES = {
        MediaType.BOOK,
        MediaType.MOVIE,
        MediaType.DOCUMENTARY,
        MediaType.TV_SHOW,
        MediaType.STUDY,
        MediaType.PODCAST,
        MediaType.ARTICLE,
        MediaType.PERSON,
        MediaType.PLACE,
    }

    def __init__(
        self,
        *,
        base_url: str | None = None,
        requests_per_second: float = 5.0,
    ) -> None:
        super().__init__(requests_per_second)
        if base_url is not None:
            self.API_URL = base_url
        self.client = httpx.Client(
            timeout=30.0,
            headers={
                "User-Agent": "Podex/1.0 (Podcast Media Index; educational project)",
            },
        )

    def supports_media_type(self, media_type: str | MediaType) -> bool:
        """Wikipedia supports all media types."""
        try:
            return MediaType(media_type) in self.SUPPORTED_TYPES
        except ValueError:
            return False

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Search Wikipedia and enrich media item with type validation.

        Args:
            media: The media item to enrich.

        Returns:
            EnrichmentResult if found and validated, None otherwise.
        """
        if not self.supports_media_type(media.type):
            return None

        # For studies, try specific search queries first
        if media.type in (MediaType.STUDY, MediaType.ARTICLE):
            result = self._search_for_study(media)
            if result:
                return result

        # Generate search variations
        variations = generate_search_variations(media.title, media.author)

        # Add type-specific variations
        type_variations = self._get_type_specific_variations(media)

        # Prioritize type-specific variations for better accuracy
        all_variations = type_variations + variations

        # Remove duplicates while preserving order
        seen = set()
        unique_variations = []
        for v in all_variations:
            v_lower = v.lower()
            if v_lower not in seen:
                seen.add(v_lower)
                unique_variations.append(v)

        # Try each variation
        for query in unique_variations:
            self.rate_limiter.wait_sync()
            results = self._search(query)

            if not results:
                continue

            # Find best match with type validation
            best_match = self._find_best_match_with_validation(results, media)
            if best_match:
                page_id, title, confidence = best_match
                self.rate_limiter.wait_sync()
                page_data = self._get_page_details(page_id)
                if page_data:
                    # Final validation using categories
                    if self._validate_page_type(page_data, media.type):
                        return self._build_result(page_data, confidence)
                    else:
                        logger.debug(
                            f"Wikipedia page '{title}' failed type "
                            f"validation for {media.type}"
                        )

        logger.debug(f"No valid Wikipedia results for: {media.title}")
        return None

    def _search_for_study(self, media: Media) -> EnrichmentResult | None:
        """Specialized search for studies/experiments.

        Uses more specific queries to find the actual study, not albums/songs.

        Args:
            media: The media item to enrich.

        Returns:
            EnrichmentResult if found, None otherwise.
        """
        title = media.title

        # Specific search queries for studies
        study_queries = [
            f"{title} syphilis study",  # For Tuskegee specifically
            f"{title} medical experiment",
            f"{title} clinical trial",
            f"{title} research study",
            f"{title} scientific study",
            f"{title} experiment ethics",
            f"{title} study scandal",
        ]

        for query in study_queries:
            self.rate_limiter.wait_sync()
            results = self._search(query)

            if not results:
                continue

            for result in results[:3]:
                page_id = result.get("pageid")
                if page_id is None or not isinstance(page_id, int):
                    continue
                result_title: str = result.get("title", "")
                snippet: str = (result.get("snippet") or "").lower()

                # Check for strong study signals in snippet
                study_signals = [
                    "study",
                    "experiment",
                    "conducted",
                    "participants",
                    "subjects",
                    "medical",
                    "clinical",
                    "research",
                    "ethical",
                    "syphilis",
                    "trial",
                ]
                signal_count = sum(1 for s in study_signals if s in snippet)

                # Check for negative signals (albums, songs, etc.)
                negative_signals = [
                    "album",
                    "song",
                    "discography",
                    "musician",
                    "band",
                    "singer",
                    "studio album",
                ]
                has_negative = any(s in snippet for s in negative_signals)

                if signal_count >= 2 and not has_negative:
                    # Good candidate - fetch and validate
                    self.rate_limiter.wait_sync()
                    page_data = self._get_page_details(page_id)
                    if page_data and self._validate_page_type(
                        page_data,
                        MediaType.STUDY,
                    ):
                        confidence = min(0.85 + (signal_count * 0.02), 0.95)
                        logger.info(
                            f"Found study match for '{media.title}': {result_title}"
                        )
                        return self._build_result(page_data, confidence)

        return None

    def _get_type_specific_variations(self, media: Media) -> list[str]:
        """Generate type-specific search variations."""
        variations = []
        title = media.title

        if media.type == MediaType.STUDY:
            variations.extend(
                [
                    f"{title} study",
                    f"{title} experiment",
                    f"{title} medical study",
                    f"{title} research",
                ]
            )
        elif media.type == MediaType.MOVIE:
            variations.append(f"{title} film")
            if media.year:
                variations.append(f"{title} {media.year} film")
        elif media.type == MediaType.TV_SHOW:
            variations.extend(
                [
                    f"{title} TV series",
                    f"{title} television series",
                ]
            )
        elif media.type == MediaType.BOOK:
            variations.append(f"{title} novel")
            if media.author:
                variations.append(f"{title} {media.author} book")
        elif media.type == MediaType.DOCUMENTARY:
            variations.extend(
                [
                    f"{title} documentary",
                    f"{title} documentary film",
                ]
            )

        return variations

    def _search(self, query: str) -> list[dict[str, Any]]:
        """Search Wikipedia for pages."""
        params: dict[str, str | int] = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": 10,
            "format": "json",
            "srprop": "snippet|titlesnippet|sectionsnippet",
        }

        try:
            response = self.client.get(self.API_URL, params=params)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            result: list[dict[str, Any]] = data.get("query", {}).get("search", [])
            return result
        except httpx.HTTPError as e:
            logger.warning(f"Wikipedia search error: {e}")
            return []

    def _find_best_match_with_validation(
        self,
        results: list[dict[str, Any]],
        media: Media,
    ) -> tuple[int, str, float] | None:
        """Find best matching result with type validation.

        Args:
            results: Search results from Wikipedia.
            media: Original media item.

        Returns:
            Tuple of (page_id, title, confidence) or None.
        """
        if not results:
            return None

        candidates: list[tuple[int, str, float]] = []

        for result in results[:7]:
            page_id = result.get("pageid")
            if page_id is None or not isinstance(page_id, int):
                continue
            result_title: str = result.get("title", "")
            snippet = (result.get("snippet", "") or "").lower()

            # Calculate base title similarity
            title_score = calculate_similarity(media.title, result_title)

            # Check for positive type signals
            positive_signals = TYPE_POSITIVE_SIGNALS.get(media.type, [])
            positive_count = sum(1 for s in positive_signals if s in snippet)
            positive_boost = min(positive_count * 0.03, 0.15)

            # Check for negative type signals - these are PENALTIES
            negative_signals = TYPE_NEGATIVE_SIGNALS.get(media.type, [])
            has_negative = any(s in snippet for s in negative_signals)
            negative_penalty = 0.4 if has_negative else 0.0

            # Special case: check result title for type indicators
            result_title_lower = result_title.lower()
            if media.type == MediaType.STUDY and any(
                x in result_title_lower
                for x in ["(album)", "(song)", "(single)", "(ep)"]
            ):
                # Penalize if result title contains album/song indicators
                negative_penalty = 0.5

            # Calculate final score
            total_score = title_score + positive_boost - negative_penalty

            # Only consider if score is reasonable and no strong negative signals
            if total_score >= 0.4 and not (has_negative and positive_count < 2):
                candidates.append((page_id, result_title, total_score))

        if not candidates:
            return None

        # Sort by score and return best
        candidates.sort(key=lambda x: x[2], reverse=True)
        best = candidates[0]

        if best[2] >= 0.5:
            return (best[0], best[1], min(best[2], 0.9))

        return None

    def _validate_page_type(
        self,
        page: dict[str, Any],
        expected_type: MediaType,
    ) -> bool:
        """Validate that a Wikipedia page matches the expected media type.

        Args:
            page: Wikipedia page data with categories.
            expected_type: Expected media type.

        Returns:
            True if the page appears to match the expected type.
        """
        categories = page.get("categories", [])
        if not categories:
            # No categories to validate - allow with lower confidence
            return True

        cat_titles = [c.get("title", "").lower() for c in categories]
        cat_text = " ".join(cat_titles)

        # Check for negative category patterns (wrong type)
        if expected_type == MediaType.STUDY:
            # Reject if it's clearly an album, song, or film
            for pattern in ALBUM_CATEGORY_PATTERNS:
                if re.search(pattern, cat_text):
                    logger.debug(f"Rejecting: matched album pattern '{pattern}'")
                    return False

            # Also check for explicit album/music categories
            music_keywords = [
                "albums",
                "songs",
                "singles",
                "discograph",
                "musician",
                "jazz album",
                "rock album",
            ]
            if any(kw in cat_text for kw in music_keywords):
                logger.debug("Rejecting: has music-related categories")
                return False

        # Check for positive category patterns (right type)
        positive_patterns = CATEGORY_TYPE_PATTERNS.get(expected_type, [])
        for pattern in positive_patterns:
            if re.search(pattern, cat_text):
                return True

        # If no strong positive match but also no negative, allow it
        # (Wikipedia might not have perfect categories)
        return True

    def _get_page_details(self, page_id: int) -> dict[str, Any] | None:
        """Get detailed page information including categories for validation."""
        params: dict[str, str | int | bool] = {
            "action": "query",
            "pageids": page_id,
            "prop": "extracts|pageimages|info|categories",
            "exintro": True,
            "explaintext": True,
            "exsentences": 10,
            "piprop": "thumbnail|original",
            "pithumbsize": 500,
            "inprop": "url",
            "cllimit": 50,  # Get more categories for better validation
            "format": "json",
        }

        try:
            response = self.client.get(self.API_URL, params=params)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            pages: dict[str, Any] = data.get("query", {}).get("pages", {})
            if str(page_id) in pages:
                result: dict[str, Any] = pages[str(page_id)]
                return result
            return None
        except httpx.HTTPError as e:
            logger.warning(f"Wikipedia page fetch error: {e}")
            return None

    def _build_result(
        self, page: dict[str, Any], confidence: float
    ) -> EnrichmentResult:
        """Build enrichment result from Wikipedia page."""
        page_id = page.get("pageid")
        title = page.get("title", "")

        # Get image URL
        cover_url = None
        if "thumbnail" in page:
            cover_url = page["thumbnail"].get("source")
        elif "original" in page:
            cover_url = page["original"].get("source")

        # Get extract as description
        description = page.get("extract", "")
        if description:
            description = description.strip()
            if len(description) > 1000:
                sentences = description[:1000].split(". ")
                description = ". ".join(sentences[:-1]) + "."

        # External IDs
        external_ids: dict[str, str | int] = {}
        if page_id:
            external_ids["wikipedia_id"] = title.replace(" ", "_")

        # Build metadata
        metadata: dict[str, Any] = {
            "wikipedia_title": title,
        }

        # Get categories
        categories = page.get("categories", [])
        if categories:
            cat_titles = [
                c.get("title", "").replace("Category:", "") for c in categories[:10]
            ]
            metadata["wikipedia_categories"] = cat_titles

        # Add Wikipedia URL
        url = page.get("fullurl")
        if url:
            metadata["wikipedia_url"] = url

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

    def __enter__(self) -> WikipediaProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
