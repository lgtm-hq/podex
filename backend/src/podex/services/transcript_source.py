"""Transcript acquisition from multiple sources.

This module provides a unified interface for acquiring transcripts from
different sources (podscripts.co, Whisper, etc.) with fallback logic.
"""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup

from podex.services.whisper_transcriber import (
    TranscriptResult,
    WhisperConfig,
    WhisperTranscriber,
)

if TYPE_CHECKING:
    from podex.models import Episode

logger = logging.getLogger(__name__)


BASE_URL = "https://podscripts.co"


@dataclass
class TranscriptAcquisitionResult:
    """Result from attempting to acquire a transcript."""

    success: bool
    result: TranscriptResult | None
    source: str
    error: str | None = None


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


class PodscriptsSource(TranscriptSource):
    """Fetch transcripts from podscripts.co.

    This is the preferred source as it provides pre-existing transcripts
    without needing to run Whisper.
    """

    def __init__(self, delay: float = 1.0) -> None:
        self.delay = delay
        self.client = httpx.Client(
            timeout=30.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        )

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

        if not transcript_data["transcript"]:
            return TranscriptAcquisitionResult(
                success=False,
                result=None,
                source=self.name,
                error="No transcript text found on page",
            )

        # Build TranscriptResult
        result = TranscriptResult(
            provider=self.name,
            raw_text=transcript_data["transcript"],
            segments=transcript_data["segments"] or [],
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
        1. If episode has episode_number, construct URL directly
        2. Search by title
        """
        # Strategy 1: Direct URL by episode number
        if episode.episode_number:
            # Try to find a matching episode page
            url = self._search_by_episode_number(podcast_slug, episode.episode_number)
            if url:
                return url

        # Strategy 2: Search episode list
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
                response = self.client.get(f"{url}?page={page}")
                response.raise_for_status()
            except httpx.HTTPError as e:
                logger.warning(f"Error fetching page {page}: {e}")
                break

            soup = BeautifulSoup(response.text, "html.parser")
            episodes = []

            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
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
                response = self.client.get(f"{url}?page={page}")
                response.raise_for_status()
            except httpx.HTTPError:
                break

            soup = BeautifulSoup(response.text, "html.parser")
            found_any = False

            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
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

    def _scrape_transcript(self, url: str) -> dict:
        """Scrape transcript from an episode page."""
        logger.info(f"Fetching transcript: {url}")

        response = self.client.get(url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Extract transcript text
        transcript_text = ""
        segments: list[dict] = []

        # Look for transcript content - common patterns
        content_area = (
            soup.find("article")
            or soup.find(class_=re.compile(r"transcript|content|episode"))
            or soup.find("main")
        )

        if content_area:
            text = content_area.get_text(separator="\n")

            # Try to parse timestamps
            timestamp_pattern = r"(?:\[|\()?(\d{1,2}:\d{2}(?::\d{2})?)[\]\)]?"
            lines = text.split("\n")

            current_time = 0.0
            current_text: list[str] = []

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Check for timestamp
                ts_match = re.search(timestamp_pattern, line)
                if ts_match:
                    # Save previous segment
                    if current_text:
                        segments.append(
                            {
                                "start": current_time,
                                "text": " ".join(current_text),
                            }
                        )
                        current_text = []

                    # Parse timestamp
                    ts_str = ts_match.group(1)
                    parts = ts_str.split(":")
                    if len(parts) == 2:
                        current_time = int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        current_time = (
                            int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                        )

                    # Remove timestamp from line
                    line = re.sub(timestamp_pattern, "", line).strip()

                if line:
                    current_text.append(line)

            # Save last segment
            if current_text:
                segments.append(
                    {
                        "start": current_time,
                        "text": " ".join(current_text),
                    }
                )

            # Build full transcript
            transcript_text = " ".join(seg["text"] for seg in segments)

            # If no segments found, just get all text
            if not segments:
                transcript_text = text

        time.sleep(self.delay)

        return {
            "transcript": transcript_text,
            "segments": segments if segments else None,
        }

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
    ) -> None:
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
            if not source.supports(episode):
                logger.debug(
                    f"Source {source.name} does not support episode {episode.id}"
                )
                continue

            logger.info(f"Trying {source.name} for episode {episode.id}...")
            result = source.get_transcript(episode)

            if result.success:
                logger.info(f"Got transcript from {source.name}")
                return result

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
