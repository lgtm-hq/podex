"""Discovery orchestrator - coordinates discovery across multiple sources.

This module provides the DiscoveryOrchestrator class which:
1. Discovers episodes from multiple sources (podscripts.co, RSS, Spotify)
2. Deduplicates episodes across sources
3. Creates/updates episode records in the database
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from podex.config import get_settings
from podex.models import Episode
from podex.services.discovery import (
    DiscoveredEpisode,
    DiscoveryProvider,
    DiscoveryResult,
    EpisodeListing,
    ListingCursor,
    ListingCursorProvider,
    as_utc,
    published_in_window,
)
from podex.services.discovery_podscripts import PodscriptsDiscovery
from podex.services.discovery_rss import RSSDiscovery
from podex.services.discovery_spotify import SpotifyDiscovery
from podex.services.episode_matching import (
    STABLE_ID_FIELDS,
    group_same_episodes,
    known_number,
)
from podex.services.failure_descriptors import describe_failure, log_failure

if TYPE_CHECKING:
    from podex.models import Podcast

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


class DiscoveryOrchestrator:
    """Coordinate discovery across multiple sources.

    Args:
        session: SQLAlchemy session for database operations
        full_walk_interval: Longest gap between complete walks of a paged
            listing; defaults to the discovery setting.
        clock: Source of the current time.
    """

    def __init__(
        self,
        session: Session,
        *,
        full_walk_interval: timedelta | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.session = session
        self._full_walk_interval = (
            full_walk_interval
            if full_walk_interval is not None
            else timedelta(
                hours=get_settings().discovery.podscripts_full_walk_interval_hours,
            )
        )
        self._clock = clock
        self._sources: dict[str, DiscoveryProvider] = {
            "podscripts": PodscriptsDiscovery(),
            "rss": RSSDiscovery(),
            "spotify": SpotifyDiscovery(),
        }

    def discover_for_podcast(
        self,
        podcast: Podcast,
        since: datetime | None = None,
    ) -> DiscoveryResult:
        """Discover new episodes for a podcast from all configured sources.

        Episodes dated before ``since`` are neither created nor returned once
        their date is known, from any source or from the matching catalog
        episode. Matching catalog rows are still enriched with missing fields.

        Args:
            podcast: The podcast to discover episodes for
            since: Only discover episodes published at or after this date

        Returns:
            DiscoveryResult with counts and any errors
        """
        discovered: list[DiscoveredEpisode] = []
        errors: list[str] = []
        started_at = self._clock()
        listing = EpisodeListing()

        # Try each source that has configuration for this podcast
        if podcast.podscripts_slug:
            try:
                listing = self._read_podscripts(podcast, since=since, now=started_at)
                logger.info(
                    "Discovered %s episodes from podscripts.co",
                    len(listing.episodes),
                )
                discovered.extend(listing.episodes)
                errors.extend(listing.errors)
                if listing.errors:
                    # A failed page may have left a gap below known pages; the
                    # next run must read the whole listing to fill it.
                    podcast.podscripts_listing_walked_at = None
            # Keep other providers and tasks running after this failure.
            # pylint: disable-next=broad-exception-caught
            except Exception as e:
                error = describe_failure("podscripts", e)
                log_failure(logger, error, e)
                errors.append(error)

        if podcast.rss_url:
            try:
                source = self._sources["rss"]
                eps = source.discover_episodes(podcast, since=since)
                logger.info("Discovered %s episodes from RSS", len(eps))
                discovered.extend(eps)
            # Keep other providers and tasks running after this failure.
            # pylint: disable-next=broad-exception-caught
            except Exception as e:
                error = describe_failure("rss", e)
                log_failure(logger, error, e)
                errors.append(error)

        if podcast.spotify_id and self._sources["spotify"].is_configured:
            try:
                source = self._sources["spotify"]
                eps = source.discover_episodes(podcast, since=since)
                logger.info("Discovered %s episodes from Spotify", len(eps))
                discovered.extend(eps)
            # Keep other providers and tasks running after this failure.
            # pylint: disable-next=broad-exception-caught
            except Exception as e:
                error = describe_failure("spotify", e)
                log_failure(logger, error, e)
                errors.append(error)

        # Deduplicate and merge
        merged = self._deduplicate_episodes(discovered)
        logger.info("After deduplication: %s unique episodes", len(merged))

        # Create/update episode records
        in_window, new_count, updated_count = self._store_episodes(
            podcast,
            merged,
            since=since,
            listing_walked_at=started_at if listing.complete else None,
        )

        return DiscoveryResult(
            new_episodes=new_count,
            updated_episodes=updated_count,
            total_discovered=len(discovered),
            errors=errors,
            discovered_episodes=in_window,
        )

    def _read_podscripts(
        self,
        podcast: Podcast,
        *,
        since: datetime | None,
        now: datetime,
    ) -> EpisodeListing:
        """Read the podscripts source, through its listing capability if offered."""
        source = self._sources["podscripts"]
        if isinstance(source, ListingCursorProvider):
            listing: EpisodeListing = source.discover_listing(
                podcast,
                cursor=self._listing_cursor(podcast, now=now),
            )
            if listing.complete and not self._walk_covers_catalog(
                podcast,
                listing=listing,
                prefix=source.listing_url_prefix(podcast),
            ):
                listing.complete = False
            return listing
        return EpisodeListing(episodes=source.discover_episodes(podcast, since=since))

    def _walk_covers_catalog(
        self,
        podcast: Podcast,
        *,
        listing: EpisodeListing,
        prefix: str | None,
    ) -> bool:
        """Return whether a complete walk saw at least what the catalog holds.

        A page that loads without episode links ends a walk like the real end
        of the listing does. A walk is not recorded when it saw no episode
        while the catalog has some, or saw fewer distinct listing URLs than
        the catalog already stores under this podcast's listing.
        """
        stored = self.session.scalar(
            select(func.count())
            .select_from(Episode)
            .where(Episode.podcast_id == podcast.id),
        )
        if not listing.episodes and stored:
            logger.warning(
                "Not recording a complete podscripts.co walk for %s: "
                "the listing read as empty but the catalog has %s episodes",
                podcast.slug,
                stored,
            )
            return False
        if prefix is None:
            return True
        seen = {episode.episode_url for episode in listing.episodes}
        listed = self.session.scalar(
            select(func.count())
            .select_from(Episode)
            .where(
                Episode.podcast_id == podcast.id,
                Episode.episode_url.startswith(prefix, autoescape=True),
            ),
        )
        if listed is not None and len(seen) < listed:
            logger.warning(
                "Not recording a complete podscripts.co walk for %s: "
                "it saw %s listing URLs but the catalog holds %s",
                podcast.slug,
                len(seen),
                listed,
            )
            return False
        return True

    def _listing_cursor(
        self,
        podcast: Podcast,
        *,
        now: datetime,
    ) -> ListingCursor | None:
        """Return the cursor for a paged listing, or None to read it all.

        The catalog serves as the cursor only after a complete walk has been
        recorded, and only until that walk is older than the full-walk
        interval. The cursor holds this podcast's stored episode URLs.
        """
        walked_at = podcast.podscripts_listing_walked_at
        if walked_at is None:
            logger.info(
                "Reading the whole podscripts.co listing for %s: "
                "no complete walk is recorded",
                podcast.slug,
            )
            return None
        if as_utc(walked_at) > now:
            logger.info(
                "Reading the whole podscripts.co listing for %s: "
                "the recorded complete walk is dated in the future",
                podcast.slug,
            )
            return None
        if as_utc(walked_at) + self._full_walk_interval <= now:
            logger.info(
                "Reading the whole podscripts.co listing for %s: "
                "the last complete walk is older than %s",
                podcast.slug,
                self._full_walk_interval,
            )
            return None
        urls = self.session.scalars(
            select(Episode.episode_url).where(
                Episode.podcast_id == podcast.id,
                Episode.episode_url.is_not(None),
            ),
        ).all()
        return ListingCursor(known_episode_urls=frozenset(url for url in urls if url))

    def _deduplicate_episodes(
        self,
        episodes: list[DiscoveredEpisode],
    ) -> list[DiscoveredEpisode]:
        """Deduplicate and merge episodes from multiple sources.

        Records are grouped by the rule in ``podex.services.episode_matching``
        (ADR 0018): a shared stable identifier joins them; then an equal
        episode number joins them and a differing one from the same source
        keeps them apart; then a shared episode URL joins them; otherwise
        similar titles whose numbers and dates do not conflict join them.
        """
        return [self._merge_episodes(group) for group in group_same_episodes(episodes)]

    def _merge_episodes(
        self,
        episodes: list[DiscoveredEpisode],
    ) -> DiscoveredEpisode:
        """Merge multiple matched episodes into one.

        Combines information from all sources, preferring non-null values.
        """
        if len(episodes) == 1:
            return episodes[0]

        # Start with the first episode
        merged = replace(episodes[0])

        # Merge in values from other episodes
        for ep in episodes[1:]:
            # Prefer values from podscripts (has transcripts)
            if ep.has_transcript and not merged.has_transcript:
                merged.has_transcript = True
                # Also prefer podscripts title as it's often cleaner
                merged.title = ep.title

            # Identity precedence is handled above; fill the remaining data fields.
            for record_field in fields(DiscoveredEpisode):
                name = record_field.name
                if name in {"title", "has_transcript", "discovery_source"}:
                    continue
                value = getattr(ep, name)
                if value and not getattr(merged, name):
                    setattr(merged, name, value)

        return merged

    def _upsert_episodes(
        self,
        podcast: Podcast,
        episodes: list[DiscoveredEpisode],
    ) -> tuple[int, int]:
        """Create or update episodes in the database.

        Returns:
            Tuple of (new_count, updated_count)
        """
        _, new_count, updated_count = self._store_episodes(podcast, episodes)
        return new_count, updated_count

    def _store_episodes(
        self,
        podcast: Podcast,
        episodes: list[DiscoveredEpisode],
        *,
        since: datetime | None = None,
        listing_walked_at: datetime | None = None,
    ) -> tuple[list[DiscoveredEpisode], int, int]:
        """Create or update episodes in the database, inside the ``since`` window.

        Each episode is matched against the catalog once. An undated episode
        takes the date of the catalog row it matches for the ``since`` window.
        Episodes outside the window are not created or returned, but a
        matching catalog row is still enriched with fields it lacks.

        Returns:
            Tuple of (episodes inside the window, new_count, updated_count)
        """
        in_window: list[DiscoveredEpisode] = []
        new_count = 0
        updated_count = 0

        stored_all = False
        try:
            for discovered in episodes:
                existing = self._find_existing_episode(podcast, discovered)
                published_at = discovered.published_at
                if published_at is None and existing is not None:
                    published_at = existing.published_at
                inside = published_in_window(published_at, since)

                if existing is not None:
                    if self._update_episode(existing, discovered):
                        updated_count += 1
                elif inside:
                    self._create_episode(podcast, discovered)
                    new_count += 1

                if inside:
                    in_window.append(discovered)
            stored_all = True
        finally:
            if not stored_all:
                # The scheduled runner records the failure and commits what was
                # stored so far. Clearing the record in that same commit makes
                # the next run read the whole listing and fill any gap.
                podcast.podscripts_listing_walked_at = None

        if listing_walked_at is not None:
            # Recorded only once every episode of the complete walk is stored,
            # and committed with them.
            podcast.podscripts_listing_walked_at = listing_walked_at
        self.session.commit()
        return in_window, new_count, updated_count

    def _find_existing_episode(
        self,
        podcast: Podcast,
        discovered: DiscoveredEpisode,
    ) -> Episode | None:
        """Find the catalog episode that matches the discovered one.

        Applies the catalog part of ADR 0018, in order: a shared feed GUID,
        Spotify URI or YouTube id finds the row whatever its number; then an
        equal episode number; then the episode URL, but only on a row whose
        number does not differ from the discovered one. Episode number 0
        means no number. A stored number's source is not recorded, so a URL
        never overrides a differing number here. Titles and dates are not
        compared against the catalog.
        """
        query = self.session.query(Episode).filter(Episode.podcast_id == podcast.id)

        for name in STABLE_ID_FIELDS:
            value = getattr(discovered, name)
            if value:
                ep = query.filter(getattr(Episode, name) == value).first()
                if ep:
                    return ep

        number = known_number(discovered.episode_number)
        if number is not None:
            ep = query.filter(Episode.episode_number == number).first()
            if ep:
                return ep

        if discovered.episode_url:
            by_url = query.filter(Episode.episode_url == discovered.episode_url)
            if number is not None:
                by_url = by_url.filter(
                    or_(
                        Episode.episode_number.is_(None),
                        Episode.episode_number == 0,
                        Episode.episode_number == number,
                    ),
                )
            ep = by_url.first()
            if ep:
                return ep

        return None

    def _create_episode(
        self,
        podcast: Podcast,
        discovered: DiscoveredEpisode,
    ) -> Episode:
        """Create a new episode from discovered data."""
        episode = Episode(
            podcast_id=podcast.id,
            title=discovered.title,
            episode_number=discovered.episode_number,
            youtube_id=discovered.youtube_id,
            published_at=discovered.published_at,
            duration_seconds=discovered.duration_seconds,
            thumbnail_url=discovered.thumbnail_url,
            spotify_uri=discovered.spotify_uri,
            rss_guid=discovered.rss_guid,
            apple_id=discovered.apple_id,
            episode_url=discovered.episode_url,
            discovery_source=discovered.discovery_source,
        )
        self.session.add(episode)
        logger.debug("Created episode: %s", discovered.title)
        return episode

    def _update_episode(
        self,
        episode: Episode,
        discovered: DiscoveredEpisode,
    ) -> bool:
        """Update an existing episode with new data.

        Returns True if any changes were made.
        """
        changed = False

        # Fill in missing fields
        if discovered.spotify_uri and not episode.spotify_uri:
            episode.spotify_uri = discovered.spotify_uri
            changed = True
        if discovered.rss_guid and not episode.rss_guid:
            episode.rss_guid = discovered.rss_guid
            changed = True
        if discovered.apple_id and not episode.apple_id:
            episode.apple_id = discovered.apple_id
            changed = True
        if discovered.episode_url and not episode.episode_url:
            episode.episode_url = discovered.episode_url
            changed = True
        if discovered.youtube_id and not episode.youtube_id:
            episode.youtube_id = discovered.youtube_id
            changed = True
        if discovered.duration_seconds and not episode.duration_seconds:
            episode.duration_seconds = discovered.duration_seconds
            changed = True
        if discovered.thumbnail_url and not episode.thumbnail_url:
            episode.thumbnail_url = discovered.thumbnail_url
            changed = True
        if discovered.published_at and not episode.published_at:
            episode.published_at = discovered.published_at
            changed = True

        if changed:
            logger.debug("Updated episode: %s", episode.title)

        return changed

    def close(self) -> None:
        """Close any resources held by discovery sources."""
        for source in self._sources.values():
            if hasattr(source, "close"):
                source.close()

    def __enter__(self) -> DiscoveryOrchestrator:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
