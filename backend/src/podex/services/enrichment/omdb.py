"""OMDB (Open Movie Database) enrichment provider.

OMDB provides IMDB data including ratings, awards, and Rotten Tomatoes scores.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import TYPE_CHECKING, Any

import httpx

from podex.services.enrichment.base import (
    EnrichmentProvider,
    EnrichmentResult,
    EnrichmentSource,
    describe_http_error,
)

if TYPE_CHECKING:
    from podex.models.media import Media

logger = logging.getLogger(__name__)


class OMDBProvider(EnrichmentProvider):  # type: ignore[misc, unused-ignore]
    """Enrich movies and TV shows from OMDB (IMDB data wrapper).

    Args:
        api_key: OMDB API key.
        requests_per_second: Rate limit for API calls.
    """

    BASE_URL = "https://www.omdbapi.com"

    source = EnrichmentSource.OMDB

    SUPPORTED_TYPES = {"movie", "tv_show", "documentary", "standup_special"}

    def __init__(self, api_key: str, requests_per_second: float = 2.0) -> None:
        super().__init__(requests_per_second)
        self.api_key = api_key
        self.client = httpx.Client(
            base_url=self.BASE_URL,
            timeout=30.0,
        )

    def supports_media_type(self, media_type: str) -> bool:
        """Check if OMDB supports this media type."""
        return media_type in self.SUPPORTED_TYPES

    def search_and_enrich(self, media: Media) -> EnrichmentResult | None:
        """Search OMDB and enrich media item.

        If media already has an IMDB ID, use it directly.
        Otherwise, search by title.

        Args:
            media: The media item to enrich.

        Returns:
            EnrichmentResult if found, None otherwise.
        """
        if not self.supports_media_type(media.type):
            return None

        # If we have an IMDB ID, use it directly
        if media.imdb_id:
            data = self._get_by_imdb_id(media.imdb_id)
            if data and data.get("Response") == "True":
                return self._build_result(data, confidence=1.0)

        # Otherwise search by title
        omdb_type = self._get_omdb_type(media.type)
        data = self._search_by_title(media.title, media.year, omdb_type)

        if not data or data.get("Response") != "True":
            logger.debug(f"No OMDB results for: {media.title}")
            return None

        # Calculate confidence based on title match
        result_title = data.get("Title", "")
        confidence = self._calculate_title_similarity(media.title, result_title)

        # Boost confidence if year matches
        result_year = data.get("Year", "")
        if media.year and result_year:
            try:
                # OMDB year can be a range like "2011-2019"
                year_str = result_year.split("-")[0]
                if int(year_str) == media.year:
                    confidence = min(confidence + 0.15, 1.0)
            except ValueError:
                pass

        if confidence < 0.7:
            logger.debug(
                f"OMDB match too weak for '{media.title}': "
                f"got '{result_title}' with confidence {confidence:.2f}"
            )
            return None

        return self._build_result(data, confidence)

    def _get_omdb_type(self, media_type: str) -> str:
        """Convert media type to OMDB type parameter."""
        if media_type == "tv_show":
            return "series"
        return "movie"

    def _get_by_imdb_id(self, imdb_id: str) -> dict[str, Any] | None:
        """Fetch OMDB data by IMDB ID.

        Args:
            imdb_id: IMDB ID (e.g., 'tt1234567').

        Returns:
            OMDB response dict or None.
        """
        self.rate_limiter.wait_sync()
        try:
            response = self.client.get(
                "/",
                params={
                    "apikey": self.api_key,
                    "i": imdb_id,
                    "plot": "full",
                },
            )
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result
        except httpx.HTTPError as e:
            logger.warning(
                f"OMDB error for IMDB ID {imdb_id}: {describe_http_error(e)}",
            )
            return None

    def _search_by_title(
        self,
        title: str,
        year: int | None,
        omdb_type: str,
    ) -> dict[str, Any] | None:
        """Search OMDB by title.

        Args:
            title: Title to search for.
            year: Optional release year.
            omdb_type: 'movie' or 'series'.

        Returns:
            OMDB response dict or None.
        """
        self.rate_limiter.wait_sync()
        try:
            params: dict[str, str | int] = {
                "apikey": self.api_key,
                "t": title,
                "type": omdb_type,
                "plot": "full",
            }
            if year:
                params["y"] = year

            response = self.client.get("/", params=params)
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result
        except httpx.HTTPError as e:
            logger.warning(
                f"OMDB search error for '{title}': {describe_http_error(e)}",
            )
            return None

    def _build_result(
        self, data: dict[str, Any], confidence: float
    ) -> EnrichmentResult:
        """Build enrichment result from OMDB data.

        Args:
            data: OMDB response data.
            confidence: Match confidence score.

        Returns:
            EnrichmentResult with all available data.
        """
        # Cover URL (poster)
        cover_url = data.get("Poster")
        if cover_url == "N/A":
            cover_url = None

        # Description
        description = data.get("Plot")
        if description == "N/A":
            description = None

        # External IDs
        external_ids: dict[str, str | int] = {}
        imdb_id = data.get("imdbID")
        if imdb_id:
            external_ids["imdb_id"] = imdb_id

        # Build metadata
        metadata: dict[str, Any] = {}

        # IMDB rating
        imdb_rating = data.get("imdbRating")
        if imdb_rating and imdb_rating != "N/A":
            with contextlib.suppress(ValueError):
                metadata["imdb_rating"] = float(imdb_rating)

        imdb_votes = data.get("imdbVotes")
        if imdb_votes and imdb_votes != "N/A":
            with contextlib.suppress(ValueError):
                metadata["imdb_votes"] = int(imdb_votes.replace(",", ""))

        # Other ratings (Rotten Tomatoes, Metacritic)
        ratings = data.get("Ratings", [])
        for rating in ratings:
            source = rating.get("Source", "")
            value = rating.get("Value", "")

            if source == "Rotten Tomatoes":
                # Value is like "91%"
                with contextlib.suppress(ValueError):
                    metadata["rotten_tomatoes"] = int(value.rstrip("%"))
            elif source == "Metacritic":
                # Value is like "79/100"
                with contextlib.suppress(ValueError):
                    metadata["metacritic"] = int(value.split("/")[0])

        # Awards
        awards = data.get("Awards")
        if awards and awards != "N/A":
            metadata["awards"] = awards
            # Parse Oscar wins/nominations
            oscar_wins, oscar_noms = self._parse_oscar_awards(awards)
            if oscar_wins:
                metadata["oscar_wins"] = oscar_wins
            if oscar_noms:
                metadata["oscar_nominations"] = oscar_noms

        # Runtime
        runtime = data.get("Runtime")
        if runtime and runtime != "N/A":
            # Value is like "136 min"
            with contextlib.suppress(ValueError, IndexError):
                metadata["runtime_minutes"] = int(runtime.split()[0])

        # Genres
        genres = data.get("Genre")
        if genres and genres != "N/A":
            metadata["genres"] = [g.strip() for g in genres.split(",")]

        # Director
        director = data.get("Director")
        if director and director != "N/A":
            metadata["directors"] = [d.strip() for d in director.split(",")]

        # Cast (actors)
        actors = data.get("Actors")
        if actors and actors != "N/A":
            metadata["cast"] = [{"name": a.strip()} for a in actors.split(",")]

        # Box office
        box_office = data.get("BoxOffice")
        if box_office and box_office != "N/A":
            metadata["box_office"] = box_office

        # Rated (e.g., PG-13, R)
        rated = data.get("Rated")
        if rated and rated != "N/A":
            metadata["rated"] = rated

        # Release date
        released = data.get("Released")
        if released and released != "N/A":
            metadata["released"] = released

        # TV-specific
        total_seasons = data.get("totalSeasons")
        if total_seasons and total_seasons != "N/A":
            with contextlib.suppress(ValueError):
                metadata["seasons"] = int(total_seasons)

        return EnrichmentResult(
            source=self.source,
            cover_url=cover_url,
            description=description,
            external_ids=external_ids,
            metadata=metadata,
            confidence=confidence,
        )

    def _parse_oscar_awards(self, awards_text: str) -> tuple[int, int]:
        """Parse Oscar wins and nominations from awards text.

        Args:
            awards_text: OMDB awards string.

        Returns:
            Tuple of (wins, nominations).
        """
        wins = 0
        nominations = 0

        # Look for "Won X Oscars" or "Won X Oscar"
        won_match = re.search(r"Won (\d+) Oscar", awards_text)
        if won_match:
            wins = int(won_match.group(1))

        # Look for "Nominated for X Oscars"
        nom_match = re.search(r"Nominated for (\d+) Oscar", awards_text)
        if nom_match:
            nominations = int(nom_match.group(1))

        return wins, nominations

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> OMDBProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
