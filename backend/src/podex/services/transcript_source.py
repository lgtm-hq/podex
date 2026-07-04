"""Transcript acquisition from multiple sources.

This module provides a unified interface for acquiring transcripts from
different sources (podscripts.co, Whisper, etc.) with fallback logic.
"""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup

from podex.services.podscripts_client import (
    BASE_URL,
    build_podscripts_client,
    fetch_podscripts_html,
)
from podex.services.whisper_transcriber import (
    TranscriptResult,
    WhisperConfig,
    WhisperTranscriber,
)

if TYPE_CHECKING:
    from podex.models import Episode

logger = logging.getLogger(__name__)


MIN_PODSCRIPTS_TRANSCRIPT_CHARACTERS = 100


def _html_attr_to_str(value: object) -> str:
    """Return a BeautifulSoup attribute value when it is a single string."""
    return value if isinstance(value, str) else ""


@dataclass
class TranscriptAcquisitionResult:
    """Result from attempting to acquire a transcript."""

    success: bool
    result: TranscriptResult | None
    source: str
    error: str | None = None
    should_store_raw: bool = True
    source_retention_opt_out: bool = False


@dataclass(frozen=True, slots=True)
class ParsedPodscriptsTranscript:
    """Transcript fields parsed from a Podscripts episode page."""

    raw_text: str
    segments: list[dict[str, str | float]]
    published_date: str | None = None


def parse_podscripts_transcript_html(html: str) -> ParsedPodscriptsTranscript | None:
    """Extract validated timestamped transcript content from Podscripts HTML.

    Podscripts episode pages contain substantial page chrome and episode metadata.
    Only transcript-specific nodes are accepted so a layout change cannot silently
    turn a page title or publication date into a stored transcript.

    Args:
        html: Podscripts episode HTML document.

    Returns:
        Parsed transcript content, or ``None`` when valid transcript nodes are absent.
    """
    soup = BeautifulSoup(html, "html.parser")
    transcript_container = soup.select_one(".podcast-transcript")
    if transcript_container is None:
        return None

    segments: list[dict[str, str | float]] = []
    for group in transcript_container.select(".single-sentence"):
        timestamp = group.select_one(".pod_timestamp_indicator")
        if timestamp is None:
            continue
        start_seconds = _parse_timestamp_seconds(timestamp.get_text(strip=True))
        if start_seconds is None:
            continue

        segment_text = " ".join(
            value
            for node in group.select(".transcript-text")
            if (value := node.get_text(" ", strip=True))
        )
        if segment_text:
            segments.append({"start": start_seconds, "text": segment_text})

    raw_text = " ".join(str(segment["text"]) for segment in segments)
    if len(raw_text) < MIN_PODSCRIPTS_TRANSCRIPT_CHARACTERS:
        return None

    date_element = soup.find("time") or soup.find(
        class_=re.compile(r"date|published"),
    )
    published_date = date_element.get_text(strip=True) if date_element else None
    return ParsedPodscriptsTranscript(
        raw_text=raw_text,
        segments=segments,
        published_date=published_date,
    )


def _parse_timestamp_seconds(timestamp: str) -> float | None:
    """Convert a Podscripts timestamp label into seconds."""
    match = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)", timestamp)
    if match is None:
        return None
    values = [int(part) for part in match.group(1).split(":")]
    if len(values) == 2:
        return float(values[0] * 60 + values[1])
    return float(values[0] * 3600 + values[1] * 60 + values[2])


@dataclass(frozen=True, slots=True)
class TranscriptAcquisitionPolicy:
    """Retention-aware source policy for transcript acquisition.

    Args:
        source_retention_opt_out: Whether the podcast/source suppresses raw storage.
        allow_generated_after_opt_out: Whether generated transcripts may be created
            after a source opted out of raw transcript retention.
    """

    source_retention_opt_out: bool = False
    allow_generated_after_opt_out: bool = False

    def allows_source(self, source: TranscriptSource) -> bool:
        """Return whether an acquisition source may be attempted.

        Args:
            source: Candidate transcript source.

        Returns:
            ``True`` when the source may run under this policy.
        """
        return bool(
            not self.source_retention_opt_out
            or not source.produces_new_raw_transcript
            or self.allow_generated_after_opt_out,
        )

    def apply(self, result: TranscriptAcquisitionResult) -> TranscriptAcquisitionResult:
        """Attach retention storage flags to an acquisition result.

        Args:
            result: Source acquisition result.

        Returns:
            Result with retention-aware storage metadata.
        """
        return replace(
            result,
            should_store_raw=not self.source_retention_opt_out,
            source_retention_opt_out=self.source_retention_opt_out,
        )


class TranscriptSource(ABC):
    """Interface for transcript sources."""

    @abstractmethod
    def supports(self, episode: Episode) -> bool:
        """Check if this source can provide a transcript for the episode."""
        ...

    @abstractmethod
    def get_transcript(self, episode: Episode) -> TranscriptAcquisitionResult:
        """Attempt to get transcript from this source."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of this transcript source."""
        ...

    @property
    def produces_new_raw_transcript(self) -> bool:
        """Whether this source generates new raw transcript text from media."""
        return False


class PodscriptsSource(TranscriptSource):
    """Fetch transcripts from podscripts.co.

    This is the preferred source as it provides pre-existing transcripts
    without needing to run Whisper.
    """

    def __init__(self, delay: float = 1.0) -> None:
        self.delay = delay
        self.client = build_podscripts_client()

    @property
    def name(self) -> str:
        return "podscripts.co"

    def supports(self, episode: Episode) -> bool:
        """Check if the podcast has a podscripts.co mapping."""
        return bool(episode.podcast.podscripts_slug)

    def get_transcript(self, episode: Episode) -> TranscriptAcquisitionResult:
        """Fetch transcript from podscripts.co."""
        if not self.supports(episode):
            return TranscriptAcquisitionResult(
                success=False,
                result=None,
                source=self.name,
                error="Podcast has no podscripts_slug configured",
            )

        podcast_slug = episode.podcast.podscripts_slug
        if podcast_slug is None:
            return TranscriptAcquisitionResult(
                success=False,
                result=None,
                source=self.name,
                error="Podcast has no podscripts_slug configured",
            )

        # Try to find the episode URL
        episode_url = self._find_episode_url(podcast_slug, episode)
        if not episode_url:
            return TranscriptAcquisitionResult(
                success=False,
                result=None,
                source=self.name,
                error=f"Episode not found on podscripts.co for {podcast_slug}",
            )

        # Fetch the transcript
        try:
            transcript_data = self._scrape_transcript(episode_url)
        except Exception as e:
            logger.exception(f"Error fetching transcript from {episode_url}")
            return TranscriptAcquisitionResult(
                success=False,
                result=None,
                source=self.name,
                error=str(e),
            )

        if transcript_data is None:
            return TranscriptAcquisitionResult(
                success=False,
                result=None,
                source=self.name,
                error="No valid timestamped transcript content found on page",
            )

        # Build TranscriptResult
        result = TranscriptResult(
            provider=self.name,
            raw_text=transcript_data.raw_text,
            segments=transcript_data.segments,
            fetched_at=datetime.now(UTC),
            audio_duration_seconds=0.0,
        )

        return TranscriptAcquisitionResult(
            success=True,
            result=result,
            source=self.name,
        )

    def _find_episode_url(self, podcast_slug: str, episode: Episode) -> str | None:
        """Find the episode URL on podscripts.co.

        Try multiple strategies:
        1. Use a previously discovered Podscripts URL
        2. If episode has episode_number, find it in the episode list
        3. Search by title
        """
        expected_prefix = f"{BASE_URL}/podcasts/{podcast_slug}/"
        if episode.episode_url and episode.episode_url.startswith(expected_prefix):
            return episode.episode_url

        # Strategy 2: Direct URL by episode number
        if episode.episode_number:
            # Try to find a matching episode page
            url = self._search_by_episode_number(podcast_slug, episode.episode_number)
            if url:
                return url

        # Strategy 3: Search episode list
        return self._search_episode_list(podcast_slug, episode)

    def _search_by_episode_number(
        self,
        podcast_slug: str,
        episode_number: int,
    ) -> str | None:
        """Search for an episode by number in the episode list."""
        url = f"{BASE_URL}/podcasts/{podcast_slug}"
        page = 1
        max_pages = 50  # Safety limit

        while page <= max_pages:
            try:
                html = fetch_podscripts_html(
                    client=self.client,
                    url=f"{url}?page={page}",
                    retry_delay_seconds=self.delay,
                )
            except httpx.HTTPError as e:
                logger.warning(f"Error fetching page {page}: {e}")
                break

            soup = BeautifulSoup(html, "html.parser")
            episodes = []

            for link in soup.find_all("a", href=True):
                href = _html_attr_to_str(link.get("href", ""))
                if (
                    f"/podcasts/{podcast_slug}/" in href
                    and href != f"/podcasts/{podcast_slug}"
                ):
                    # Extract episode number from URL
                    slug = href.split("/")[-1]
                    match = re.match(r"(\d+)-(.+)", slug)
                    if match:
                        ep_num = int(match.group(1))
                        if ep_num == episode_number:
                            return f"{BASE_URL}{href}" if href.startswith("/") else href
                        episodes.append(ep_num)

            # Check if we've passed the target episode
            if episodes and min(episodes) > episode_number:
                page += 1
                time.sleep(self.delay)
                continue

            if not episodes:
                break

            page += 1
            time.sleep(self.delay)

        return None

    def _search_episode_list(
        self,
        podcast_slug: str,
        episode: Episode,
    ) -> str | None:
        """Search the episode list for a matching title."""
        url = f"{BASE_URL}/podcasts/{podcast_slug}"
        page = 1
        max_pages = 10  # Limit title search to first 10 pages

        # Normalize the target title for comparison
        target_title = self._normalize_title(episode.title)

        while page <= max_pages:
            try:
                html = fetch_podscripts_html(
                    client=self.client,
                    url=f"{url}?page={page}",
                    retry_delay_seconds=self.delay,
                )
            except httpx.HTTPError:
                break

            soup = BeautifulSoup(html, "html.parser")
            found_any = False

            for link in soup.find_all("a", href=True):
                href = _html_attr_to_str(link.get("href", ""))
                if (
                    f"/podcasts/{podcast_slug}/" in href
                    and href != f"/podcasts/{podcast_slug}"
                ):
                    found_any = True
                    # Check if the link text matches the episode title
                    link_text = link.get_text(strip=True)
                    if self._titles_match(
                        target_title, self._normalize_title(link_text)
                    ):
                        return f"{BASE_URL}{href}" if href.startswith("/") else href

            if not found_any:
                break

            page += 1
            time.sleep(self.delay)

        return None

    def _normalize_title(self, title: str) -> str:
        """Normalize a title for comparison."""
        import string

        table = str.maketrans("", "", string.punctuation)
        return title.translate(table).lower().strip()

    def _titles_match(self, title1: str, title2: str) -> bool:
        """Check if two normalized titles match closely enough."""
        # Exact match
        if title1 == title2:
            return True

        # Check if one contains the other (for partial matches)
        return bool(
            len(title1) > 20
            and len(title2) > 20
            and (title1 in title2 or title2 in title1)
        )

    def _scrape_transcript(self, url: str) -> ParsedPodscriptsTranscript | None:
        """Scrape transcript from an episode page."""
        logger.info(f"Fetching transcript: {url}")

        html = fetch_podscripts_html(
            client=self.client,
            url=url,
            retry_delay_seconds=self.delay,
        )

        time.sleep(self.delay)
        return parse_podscripts_transcript_html(html)

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> PodscriptsSource:
        return self

    def __exit__(self, *args) -> None:
        self.close()


class WhisperSource(TranscriptSource):
    """Transcribe using Whisper (local or API).

    This is the fallback source when podscripts.co doesn't have the transcript.
    """

    def __init__(
        self,
        config: WhisperConfig | None = None,
        initial_prompt: str | None = None,
    ) -> None:
        self.config = config or WhisperConfig.from_env()
        self.initial_prompt = initial_prompt

    @property
    def name(self) -> str:
        return f"whisper:{self.config.backend}"

    @property
    def produces_new_raw_transcript(self) -> bool:
        """Whether Whisper creates new raw transcript text from source audio."""
        return True

    def supports(self, episode: Episode) -> bool:
        """Check if we can transcribe this episode (needs YouTube ID)."""
        return bool(episode.youtube_id)

    def get_transcript(self, episode: Episode) -> TranscriptAcquisitionResult:
        """Transcribe the episode using Whisper."""
        if not self.supports(episode):
            return TranscriptAcquisitionResult(
                success=False,
                result=None,
                source=self.name,
                error="Episode has no youtube_id",
            )

        try:
            if episode.youtube_id is None:
                return TranscriptAcquisitionResult(
                    success=False,
                    result=None,
                    source=self.name,
                    error="Episode has no youtube_id",
                )
            transcriber = WhisperTranscriber(self.config)
            result = transcriber.transcribe(
                episode.youtube_id,
                initial_prompt=self.initial_prompt,
            )

            return TranscriptAcquisitionResult(
                success=True,
                result=result,
                source=self.name,
            )

        except Exception as e:
            logger.exception(f"Whisper transcription failed for {episode.youtube_id}")
            return TranscriptAcquisitionResult(
                success=False,
                result=None,
                source=self.name,
                error=str(e),
            )


class TranscriptAcquirer:
    """Acquire transcripts by trying sources in priority order.

    The default priority is:
    1. podscripts.co (free, already transcribed)
    2. Whisper (requires compute/API calls)

    Args:
        sources: Custom list of sources (in priority order).
                 If None, uses default [PodscriptsSource, WhisperSource].
        whisper_config: Configuration for Whisper (if using default sources).
        initial_prompt: Initial prompt for Whisper transcription.
    """

    def __init__(
        self,
        sources: list[TranscriptSource] | None = None,
        whisper_config: WhisperConfig | None = None,
        initial_prompt: str | None = None,
        acquisition_policy: TranscriptAcquisitionPolicy | None = None,
    ) -> None:
        self.acquisition_policy = acquisition_policy or TranscriptAcquisitionPolicy()
        if sources is not None:
            self.sources = sources
        else:
            self.sources = [
                PodscriptsSource(),
                WhisperSource(config=whisper_config, initial_prompt=initial_prompt),
            ]

    def acquire(self, episode: Episode) -> TranscriptAcquisitionResult:
        """Try to acquire a transcript from any available source.

        Tries sources in order until one succeeds.

        Args:
            episode: The episode to get a transcript for.

        Returns:
            TranscriptAcquisitionResult with success status and transcript (if found).
        """
        errors: list[str] = []

        for source in self.sources:
            if not self.acquisition_policy.allows_source(source):
                errors.append(
                    f"{source.name}: skipped by retention acquisition policy",
                )
                logger.info(
                    "Skipping %s for episode %s because raw retention is suppressed",
                    source.name,
                    episode.id,
                )
                continue

            if not source.supports(episode):
                logger.debug(
                    f"Source {source.name} does not support episode {episode.id}"
                )
                continue

            logger.info(f"Trying {source.name} for episode {episode.id}...")
            result = source.get_transcript(episode)

            if result.success:
                logger.info(f"Got transcript from {source.name}")
                return self.acquisition_policy.apply(result)

            error_msg = f"{source.name}: {result.error}"
            errors.append(error_msg)
            logger.warning(
                f"Failed to get transcript from {source.name}: {result.error}"
            )

        # All sources failed
        return TranscriptAcquisitionResult(
            success=False,
            result=None,
            source="none",
            error=f"All sources failed: {'; '.join(errors)}",
        )

    def close(self) -> None:
        """Close any resources held by sources."""
        for source in self.sources:
            if hasattr(source, "close"):
                source.close()

    def __enter__(self) -> TranscriptAcquirer:
        return self

    def __exit__(self, *args) -> None:
        self.close()
