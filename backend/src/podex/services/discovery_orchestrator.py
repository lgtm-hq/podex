"""Discovery orchestrator - coordinates discovery across multiple sources.

This module provides the DiscoveryOrchestrator class which:
1. Discovers episodes from multiple sources (podscripts.co, RSS, Spotify)
2. Deduplicates episodes across sources
3. Creates/updates episode records in the database
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Query, Session

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
    GUID_WHITESPACE,
    group_same_episodes,
    guid_key,
    known_number,
)
from podex.services.failure_descriptors import describe_failure, log_failure

if TYPE_CHECKING:
    from podex.models import Podcast

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _CatalogMatch:
    """A stored row found for a discovered record, and how it was found."""

    episode: Episode
    by_guid_or_url: bool


def _same_instant(stored: datetime | None, reported: datetime | None) -> bool:
    """Return whether a row's time and a record's time are the same, or both unknown.

    A stored time without an offset is read as UTC; the feed reader reports
    times in UTC, so the stored value is the reported instant on every database.
    """
    if reported is None:
        return stored is None
    return stored is not None and as_utc(stored) == as_utc(reported)


def _conflicts(row: Episode, discovered: DiscoveredEpisode) -> bool:
    """Return whether a row holds a GUID, URL or number the record contradicts.

    GUIDs compare by ``guid_key``; numbers in two known, different seasons
    differ even when they are equal. A value missing on either side
    contradicts nothing.
    """
    record_guid, row_guid = guid_key(discovered.rss_guid), guid_key(row.rss_guid)
    if record_guid is not None and row_guid is not None and record_guid != row_guid:
        return True
    if (
        discovered.episode_url
        and row.episode_url
        and discovered.episode_url != row.episode_url
    ):
        return True
    record_number = known_number(discovered.episode_number)
    row_number = known_number(row.episode_number)
    if record_number is None or row_number is None:
        return False
    record_season = known_number(discovered.season_number)
    row_season = known_number(row.season_number)
    seasons_differ = (
        record_season is not None
        and row_season is not None
        and record_season != row_season
    )
    return record_number != row_number or seasons_differ


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
        (ADR 0019). The catalog lookup in ``_find_existing_episode`` is not
        part of that rule.
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
                if name in {
                    "title",
                    "has_transcript",
                    "discovery_source",
                    "season_number",
                }:
                    continue
                value = getattr(ep, name)
                if value and not getattr(merged, name):
                    setattr(merged, name, value)

        # A season qualifies the number it came with, never another source's.
        merged.season_number = next(
            (
                ep.season_number
                for ep in episodes
                if ep.season_number and ep.episode_number == merged.episode_number
            ),
            None,
        )
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
                match = self._match_existing(podcast, discovered)
                existing = match.episode if match is not None else None
                published_at = discovered.published_at
                if published_at is None and existing is not None:
                    published_at = existing.published_at
                inside = published_in_window(published_at, since)

                if match is not None:
                    if self._update_episode(
                        match.episode,
                        discovered,
                        fill_season=match.by_guid_or_url,
                    ):
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
        """Find an existing episode that matches the discovered one (ADR 0020)."""
        match = self._match_existing(podcast, discovered)
        return match.episode if match is not None else None

    def _match_existing(
        self,
        podcast: Podcast,
        discovered: DiscoveredEpisode,
    ) -> _CatalogMatch | None:
        """Find the stored row for a discovered record, and how it was found.

        In order (ADR 0019, extended by ADR 0020): YouTube id, Spotify URI,
        feed GUID (by ``guid_key``), URL, episode number (within the record's
        season when it has one), and, for a record without a YouTube id or
        Spotify URI, title and publication time together. Each step runs only
        when the earlier ones found nothing.
        """
        query = self.session.query(Episode).filter(Episode.podcast_id == podcast.id)
        return next(
            (
                _CatalogMatch(episode=row, by_guid_or_url=by_guid_or_url)
                for by_guid_or_url, row in self._lookup_steps(query, discovered)
                if row is not None
            ),
            None,
        )

    def _lookup_steps(
        self,
        query: Query[Episode],
        discovered: DiscoveredEpisode,
    ) -> Iterator[tuple[bool, Episode | None]]:
        """Yield each step's row, or None, with whether it was a GUID or URL step."""
        # Try matching by source-specific IDs first
        if discovered.youtube_id:
            yield (
                False,
                query.filter(Episode.youtube_id == discovered.youtube_id).first(),
            )
        if discovered.spotify_uri:
            yield (
                False,
                query.filter(Episode.spotify_uri == discovered.spotify_uri).first(),
            )
        if discovered.rss_guid and guid_key(discovered.rss_guid) is not None:
            yield True, self._find_by_guid(query, discovered.rss_guid)
        if discovered.episode_url:
            yield (
                True,
                query.filter(Episode.episode_url == discovered.episode_url).first(),
            )
        # Try matching by episode number
        if discovered.episode_number:
            yield False, self._find_by_number(query, discovered)
        if not (discovered.youtube_id or discovered.spotify_uri):
            yield False, self._find_by_title_and_time(query, discovered)

    @staticmethod
    def _find_by_guid(query: Query[Episode], guid: str) -> Episode | None:
        """Return the row whose GUID equals ``guid`` by ``guid_key``.

        An exactly equal GUID is preferred, as before. Otherwise the rows whose
        trimmed, lowercased GUID equals the key's lowercased form are checked
        with ``guid_key`` (PostgreSQL also lowercases non-ASCII letters, which
        the rule compares exactly), oldest first.
        """
        ep = query.filter(Episode.rss_guid == guid).first()
        if ep:
            return ep
        key = guid_key(guid)
        candidates = query.filter(
            func.lower(func.trim(Episode.rss_guid, GUID_WHITESPACE)) == func.lower(key),
        ).order_by(Episode.id)
        return next(
            (row for row in candidates if guid_key(row.rss_guid) == key),
            None,
        )

    @staticmethod
    def _find_by_number(
        query: Query[Episode],
        discovered: DiscoveredEpisode,
    ) -> Episode | None:
        """Return the first row with the record's number, in a compatible season.

        A record with a season matches a row of the same season first, then a
        row without a season; never a row of another season. A row without a
        season is skipped when the record has a GUID and the row another one:
        the GUID step has already ruled it out, so it is another item of the
        feed. A record without a season matches any row with the number.
        """
        numbered = query.filter(Episode.episode_number == discovered.episode_number)
        season = known_number(discovered.season_number)
        if season is None:
            return numbered.first()
        same_season = numbered.filter(Episode.season_number == season).first()
        if same_season is not None:
            return same_season
        seasonless = numbered.filter(Episode.season_number.is_(None))
        if guid_key(discovered.rss_guid) is None:
            return seasonless.first()
        return next(
            (row for row in seasonless if guid_key(row.rss_guid) is None),
            None,
        )

    @staticmethod
    def _find_by_title_and_time(
        query: Query[Episode],
        discovered: DiscoveredEpisode,
    ) -> Episode | None:
        """Return the oldest row with the record's exact title and publication time.

        An undated record matches only an undated row, so for it this step is a
        match by title alone. A dated record matches only a row dated the same
        instant, never an undated row. A row that holds a GUID, URL or number
        the record contradicts is never taken.
        """
        candidates = query.filter(Episode.title == discovered.title)
        if discovered.published_at is None:
            candidates = candidates.filter(Episode.published_at.is_(None))
        return next(
            (
                row
                for row in candidates.order_by(Episode.id)
                if _same_instant(row.published_at, discovered.published_at)
                and not _conflicts(row, discovered)
            ),
            None,
        )

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
            season_number=known_number(discovered.season_number),
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
        *,
        fill_season: bool = False,
    ) -> bool:
        """Update an existing episode with new data.

        With ``fill_season`` (the row was found by GUID or URL), a row without
        a season takes the record's season when both carry the same episode
        number. A stored season is never overwritten.

        Returns True if any changes were made.
        """
        changed = False
        season = known_number(discovered.season_number)
        if (
            fill_season
            and season is not None
            and episode.season_number is None
            and known_number(episode.episode_number) is not None
            and episode.episode_number == discovered.episode_number
        ):
            episode.season_number = season
            changed = True

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
