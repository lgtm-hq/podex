"""TMDB (The Movie Database) enrichment provider."""

from __future__ import annotations

import contextlib
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
from podex.services.enrichment.search_utils import (
    calculate_similarity,
    clean_for_api_search,
    generate_search_variations,
)

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)

# When a detail fetch fails, emit a search-document result at this fraction of
# the match confidence so the fallback is clearly below the detail-path score.
_DETAIL_FALLBACK_CONFIDENCE_FACTOR = 0.75


class TMDBProvider(EnrichmentProvider):  # type: ignore[misc, unused-ignore]
    """Enrich movies and TV shows from The Movie Database.

    When a detail fetch fails after a confident search hit, a reduced-confidence
    result is built from the search document
    (``match_confidence * 0.75``) instead of returning ``None``.

    Args:
        api_key: TMDB API key.
        base_url: Override for the TMDB API root.
        requests_per_second: Rate limit for API calls.
    """

    BASE_URL = "https://api.themoviedb.org/3"
    IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

    source = EnrichmentSource.TMDB

    SUPPORTED_TYPES = {
        MediaType.MOVIE,
        MediaType.TV_SHOW,
        MediaType.DOCUMENTARY,
        "standup_special",
    }

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        requests_per_second: float = 3.0,
    ) -> None:
        super().__init__(requests_per_second)
        self.api_key = api_key
        self.client = httpx.Client(
            base_url=base_url or self.BASE_URL,
            params={"api_key": api_key},
            timeout=30.0,
        )

    def supports_media_type(self, media_type: str | MediaType) -> bool:
        """Check if TMDB supports this media type."""
        return media_type in self.SUPPORTED_TYPES

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Search TMDB and enrich media item.

        Uses flexible search strategies to improve match rates.

        Args:
            media: The media item to enrich.

        Returns:
            EnrichmentResult if found, None otherwise.
        """
        if not self.supports_media_type(media.type):
            return None

        # Determine primary search type (TV shows try TV first, others try movie first)
        search_types = (
            ["tv", "movie"] if media.type == MediaType.TV_SHOW else ["movie", "tv"]
        )

        # Direct lookup by stored TMDB ID before any title search
        if media.tmdb_id:
            for search_type in search_types:
                details = self._get_details(media.tmdb_id, search_type)
                if details:
                    return self._build_result(details, search_type, confidence=1.0)

        # Generate search variations
        variations = generate_search_variations(media.title, media.author)

        # Try each search type
        for search_type in search_types:
            # Try each title variation
            for variation in variations:
                # Clean the query for API
                query = clean_for_api_search(variation)
                if not query:
                    continue

                # First try with year, then without
                year_options = [media.year, None] if media.year else [None]

                for year in year_options:
                    search_results = self._search(query, search_type, year)
                    if not search_results:
                        continue

                    # Find best match
                    best_match = self._find_best_match(
                        search_results,
                        media,
                        original_query=variation,
                    )
                    if best_match:
                        tmdb_id, confidence = best_match

                        # Fetch full details
                        details = self._get_details(tmdb_id, search_type)
                        if details:
                            logger.debug(
                                f"TMDB match for '{media.title}' using "
                                f"query='{query}' type={search_type}"
                            )
                            return self._build_result(
                                details,
                                search_type,
                                confidence,
                            )

                        search_doc = next(
                            (
                                result
                                for result in search_results
                                if result.get("id") == tmdb_id
                            ),
                            None,
                        )
                        if search_doc is not None:
                            fallback_confidence = (
                                confidence * _DETAIL_FALLBACK_CONFIDENCE_FACTOR
                            )
                            logger.debug(
                                "TMDB detail fetch failed for '%s' (id=%s); "
                                "falling back to search document "
                                "(confidence=%.2f)",
                                media.title,
                                tmdb_id,
                                fallback_confidence,
                            )
                            return self._build_result(
                                search_doc,
                                search_type,
                                fallback_confidence,
                            )

        logger.debug(f"No TMDB results for: {media.title}")
        return None

    def _search(
        self,
        title: str,
        search_type: str,
        year: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search TMDB for a title.

        Args:
            title: Title to search for.
            search_type: 'movie' or 'tv'.
            year: Optional release year.

        Returns:
            List of search results.
        """
        self.rate_limiter.wait_sync()
        try:
            params: dict[str, str | int] = {"query": title}
            if year:
                if search_type == "movie":
                    params["year"] = year
                else:
                    params["first_air_date_year"] = year

            response = self.client.get(f"/search/{search_type}", params=params)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            result: list[dict[str, Any]] = data.get("results", [])
            return result
        except httpx.HTTPError as e:
            logger.warning(
                f"TMDB search error for '{title}': {describe_http_error(e)}",
            )
            return []

    def _find_best_match(
        self,
        results: list[dict[str, Any]],
        media: Media,
        original_query: str | None = None,
    ) -> tuple[int, float] | None:
        """Find best matching result.

        Args:
            results: Search results from TMDB.
            media: Original media item.
            original_query: The search query used (for matching).

        Returns:
            Tuple of (tmdb_id, confidence) or None.
        """
        if not results:
            return None

        best_id = None
        best_score = 0.0

        # Use original query if provided, otherwise use media title
        search_title = original_query or media.title

        for result in results[:7]:  # Check top 7 results
            result_title = result.get("title") or result.get("name", "")

            # Calculate similarity against both the search query and original title
            query_score = calculate_similarity(search_title, result_title)
            title_score = calculate_similarity(media.title, result_title)

            # Use the better score
            base_score = max(query_score, title_score)

            # Also check against original_name (for international titles)
            original_name = result.get("original_title") or result.get("original_name")
            if original_name and original_name != result_title:
                original_score = calculate_similarity(media.title, original_name)
                base_score = max(base_score, original_score)

            # Boost score if year matches
            result_year = None
            release_date = result.get("release_date") or result.get("first_air_date")
            if release_date:
                with contextlib.suppress(ValueError, IndexError):
                    result_year = int(release_date[:4])

            year_boost = 0.0
            if media.year and result_year:
                if media.year == result_year:
                    year_boost = 0.15
                elif abs(media.year - result_year) <= 1:
                    year_boost = 0.10
                elif abs(media.year - result_year) <= 3:
                    year_boost = 0.05

            # Boost for popularity (helps with disambiguation)
            popularity_boost = 0.0
            popularity = result.get("popularity", 0)
            if popularity > 50:
                popularity_boost = 0.05
            elif popularity > 20:
                popularity_boost = 0.02

            total_score = base_score + year_boost + popularity_boost

            if total_score > best_score:
                best_score = total_score
                best_id = result.get("id")

        # Lower threshold since we're using flexible search
        if best_id and best_score >= 0.55:
            return (best_id, min(best_score, 1.0))

        return None

    def _get_details(self, tmdb_id: int, media_type: str) -> dict[str, Any] | None:
        """Fetch full details for a TMDB item.

        Args:
            tmdb_id: TMDB ID.
            media_type: 'movie' or 'tv'.

        Returns:
            Details dict or None.
        """
        self.rate_limiter.wait_sync()
        try:
            params = {"append_to_response": "credits,external_ids"}
            response = self.client.get(f"/{media_type}/{tmdb_id}", params=params)
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result
        except httpx.HTTPError as e:
            logger.warning(
                f"TMDB details error for ID {tmdb_id}: {describe_http_error(e)}",
            )
            return None

    def _build_result(
        self,
        details: dict[str, Any],
        media_type: str,
        confidence: float,
    ) -> EnrichmentResult:
        """Build enrichment result from TMDB details.

        Args:
            details: TMDB details response.
            media_type: 'movie' or 'tv'.
            confidence: Match confidence score.

        Returns:
            EnrichmentResult with all available data.
        """
        # Build cover URL
        cover_url = None
        poster_path = details.get("poster_path")
        if poster_path:
            cover_url = f"{self.IMAGE_BASE}{poster_path}"

        # Get description
        description = details.get("overview")

        # External IDs
        tmdb_id = details.get("id")
        external_ids: dict[str, str | int] = {}
        if tmdb_id is not None:
            external_ids["tmdb_id"] = tmdb_id
        ext_ids = details.get("external_ids", {})
        if ext_ids.get("imdb_id"):
            external_ids["imdb_id"] = ext_ids["imdb_id"]

        # Build metadata
        metadata: dict[str, Any] = {}

        # Ratings
        vote_average = details.get("vote_average")
        if vote_average:
            metadata["tmdb_rating"] = round(vote_average, 1)
            metadata["tmdb_vote_count"] = details.get("vote_count", 0)

        # Runtime
        if media_type == "movie":
            runtime = details.get("runtime")
            if runtime:
                metadata["runtime_minutes"] = runtime
        else:
            # TV show episode runtime
            episode_runtime = details.get("episode_run_time", [])
            if episode_runtime:
                metadata["episode_runtime_minutes"] = episode_runtime[0]
            # Seasons info
            metadata["seasons"] = details.get("number_of_seasons")
            metadata["episodes"] = details.get("number_of_episodes")

        # Genres
        genres = details.get("genres", [])
        if genres:
            metadata["genres"] = [g["name"] for g in genres]

        # Cast (top 5)
        credits = details.get("credits", {})
        cast = credits.get("cast", [])
        if cast:
            metadata["cast"] = [
                {"name": c["name"], "character": c.get("character")} for c in cast[:5]
            ]

        # Director(s) for movies
        if media_type == "movie":
            crew = credits.get("crew", [])
            directors = [c["name"] for c in crew if c.get("job") == "Director"]
            if directors:
                metadata["directors"] = directors

        # Release/air date
        if media_type == "movie":
            release_date = details.get("release_date")
            if release_date:
                metadata["release_date"] = release_date
        else:
            first_air_date = details.get("first_air_date")
            if first_air_date:
                metadata["first_air_date"] = first_air_date
            last_air_date = details.get("last_air_date")
            if last_air_date:
                metadata["last_air_date"] = last_air_date
            metadata["status"] = details.get("status")

        # Production companies
        companies = details.get("production_companies", [])
        if companies:
            metadata["production_companies"] = [c["name"] for c in companies[:3]]

        # Tagline (movies mostly)
        tagline = details.get("tagline")
        if tagline:
            metadata["tagline"] = tagline

        # Budget and revenue (movies)
        if media_type == "movie":
            budget = details.get("budget")
            if budget and budget > 0:
                metadata["budget"] = budget
            revenue = details.get("revenue")
            if revenue and revenue > 0:
                metadata["revenue"] = revenue

        # Production countries
        countries = details.get("production_countries", [])
        if countries:
            metadata["production_countries"] = [
                c.get("name", c.get("iso_3166_1", "")) for c in countries
            ]

        # Spoken languages
        languages = details.get("spoken_languages", [])
        if languages:
            metadata["spoken_languages"] = [
                lang.get("english_name", lang.get("name", "")) for lang in languages
            ]

        # Networks (TV shows)
        if media_type == "tv":
            networks = details.get("networks", [])
            if networks:
                metadata["networks"] = [n.get("name", "") for n in networks]

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

    def __enter__(self) -> TMDBProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
