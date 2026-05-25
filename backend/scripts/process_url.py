#!/usr/bin/env python3
"""Process a YouTube URL through the full Podex pipeline.

This script provides a single-command entry point for processing podcast episodes:
1. Parse YouTube URL to extract video ID
2. Fetch video metadata (yt-dlp or YouTube API)
3. Create/retrieve podcast and episode records
4. Run transcription if needed
5. Run cleanup if needed
6. Run extraction if needed

Usage:
    python process_url.py "https://www.youtube.com/watch?v=VIDEO_ID"
    python process_url.py "https://youtu.be/VIDEO_ID"
    python process_url.py "VIDEO_ID"
    python process_url.py "VIDEO_ID" --podcast jre
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

load_dotenv(Path(__file__).parent.parent / ".env")

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import contextlib

from podex.config import get_settings
from podex.database import SessionLocal
from podex.models import (
    Episode,
    JobType,
    Mention,
    Podcast,
    Transcript,
    TranscriptionJob,
)
from podex.services.extraction_review import persist_extracted_candidates
from podex.services.llm_extraction import LLMExtractor
from podex.services.llm_transcript_cleanup import TranscriptCleaner
from podex.services.prompt_config import PromptConfigManager
from podex.services.transcript_artifacts import (
    build_transcript_artifact_store,
    load_transcript_processing_payload,
    persist_transcript_acquisition,
)
from podex.services.transcript_source import TranscriptAcquirer
from podex.services.whisper_transcriber import WhisperConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_PODCAST_SLUG = "misc"
DEFAULT_PODCAST_NAME = "Miscellaneous"


@dataclass
class VideoMetadata:
    """Video metadata from YouTube."""

    video_id: str
    title: str
    duration_seconds: int | None
    published_at: datetime | None
    thumbnail_url: str | None


def extract_video_id(url_or_id: str) -> str:
    """Extract video ID from a YouTube URL or return as-is if already an ID.

    Supported formats:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://www.youtube.com/embed/VIDEO_ID
    - VIDEO_ID (raw 11-character ID)
    """
    url_or_id = url_or_id.strip()

    # Check if it's already a video ID (11 alphanumeric + _ - chars)
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url_or_id):
        return url_or_id

    # Parse as URL
    parsed = urlparse(url_or_id)

    # Handle youtu.be short URLs
    if parsed.netloc in ("youtu.be", "www.youtu.be"):
        video_id = parsed.path.lstrip("/").split("/")[0]
        if video_id:
            return video_id

    # Handle youtube.com URLs
    if "youtube.com" in parsed.netloc:
        # /watch?v=VIDEO_ID
        if parsed.path == "/watch":
            query = parse_qs(parsed.query)
            if "v" in query:
                return query["v"][0]

        # /embed/VIDEO_ID or /v/VIDEO_ID
        if parsed.path.startswith(("/embed/", "/v/")):
            video_id = parsed.path.split("/")[2]
            if video_id:
                return video_id

    raise ValueError(f"Could not extract video ID from: {url_or_id}")


def fetch_metadata_ytdlp(video_id: str) -> VideoMetadata:
    """Fetch video metadata using yt-dlp.

    This works without a YouTube API key.
    """
    logger.info(f"Fetching metadata for {video_id} using yt-dlp...")

    url = f"https://www.youtube.com/watch?v={video_id}"
    result = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-download", url],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr}")

    data = json.loads(result.stdout)

    # Parse upload date
    published_at = None
    upload_date = data.get("upload_date")
    if upload_date:
        with contextlib.suppress(ValueError):
            published_at = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC)

    # Get best thumbnail
    thumbnail_url = None
    thumbnails = data.get("thumbnails", [])
    if thumbnails:
        # Sort by preference (higher resolution first)
        sorted_thumbs = sorted(
            thumbnails,
            key=lambda t: t.get("height", 0) or 0,
            reverse=True,
        )
        thumbnail_url = sorted_thumbs[0].get("url")

    return VideoMetadata(
        video_id=video_id,
        title=data.get("title", "Unknown"),
        duration_seconds=data.get("duration"),
        published_at=published_at,
        thumbnail_url=thumbnail_url,
    )


def ensure_podcast(session: Session, slug: str, name: str) -> Podcast:
    """Get or create a podcast by slug."""
    podcast = session.query(Podcast).filter(Podcast.slug == slug).first()
    if podcast:
        return cast(Podcast, podcast)

    new_podcast = Podcast(name=name, slug=slug)
    session.add(new_podcast)
    session.commit()
    logger.info(f"Created podcast: {name} ({slug})")
    return new_podcast


def get_or_create_episode(
    session: Session, podcast_id: int, metadata: VideoMetadata
) -> Episode:
    """Get existing episode or create new one."""
    episode = (
        session.query(Episode).filter(Episode.youtube_id == metadata.video_id).first()
    )
    if episode:
        logger.info(f"Found existing episode: {episode.title}")
        return cast(Episode, episode)

    new_episode = Episode(
        podcast_id=podcast_id,
        title=metadata.title,
        youtube_id=metadata.video_id,
        published_at=metadata.published_at,
        duration_seconds=metadata.duration_seconds,
        thumbnail_url=metadata.thumbnail_url,
    )
    session.add(new_episode)
    session.commit()
    logger.info(f"Created episode: {metadata.title}")
    return new_episode


def run_transcription(session: Session, episode: Episode) -> bool:
    """Acquire transcript (try podscripts.co first, then Whisper)."""
    if episode.transcript_status == "completed":
        logger.info("Transcription already completed, skipping")
        return True

    if episode.transcript_status == "failed":
        logger.info("Previous transcription failed, retrying...")

    logger.info("Starting transcript acquisition...")
    episode.transcript_status = "in_progress"
    session.commit()

    try:
        # Get initial prompt if available (for Whisper fallback)
        prompt_manager = PromptConfigManager()
        initial_prompt = prompt_manager.get_prompt(
            episode.podcast.slug,
            episode.episode_number,
        )

        # Use TranscriptAcquirer which tries podscripts.co first, then Whisper
        config = WhisperConfig.from_env()
        with TranscriptAcquirer(
            whisper_config=config,
            initial_prompt=initial_prompt,
        ) as acquirer:
            acquisition = acquirer.acquire(episode)

        if not acquisition.success:
            logger.error(f"Transcript acquisition failed: {acquisition.error}")
            episode.transcript_status = "failed"
            session.commit()
            return False

        result = acquisition.result
        assert result is not None  # Guaranteed by acquisition.success check above

        persist_transcript_acquisition(
            db=session,
            episode=episode,
            acquisition=acquisition,
            artifact_store=build_transcript_artifact_store(settings=get_settings()),
        )
        episode.transcript_status = "completed"
        session.commit()

        segment_count = len(result.segments) if result.segments else 0
        duration_min = (
            result.audio_duration_seconds / 60 if result.audio_duration_seconds else 0
        )

        logger.info(
            f"Transcript acquired from {acquisition.source}: "
            f"{segment_count} segments"
            + (f", {duration_min:.1f} min" if duration_min > 0 else "")
        )
        return True

    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        episode.transcript_status = "failed"
        session.commit()
        return False


def run_cleanup(session: Session, episode: Episode) -> bool:
    """Run LLM cleanup if needed."""
    if episode.cleanup_status == "completed":
        logger.info("Cleanup already completed, skipping")
        return True

    if episode.cleanup_status == "failed":
        logger.info("Previous cleanup failed, retrying...")

    # Get transcript
    transcript = (
        session.query(Transcript)
        .filter(Transcript.episode_id == episode.id)
        .order_by(Transcript.id.desc())
        .first()
    )

    if not transcript:
        logger.warning("No transcript found for cleanup")
        episode.cleanup_status = "skipped"
        session.commit()
        return False

    if transcript.cleaned_text:
        logger.info("Transcript already cleaned, skipping")
        episode.cleanup_status = "completed"
        session.commit()
        return True

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, skipping cleanup")
        episode.cleanup_status = "skipped"
        session.commit()
        return False

    logger.info("Starting cleanup...")
    episode.cleanup_status = "in_progress"
    session.commit()

    try:
        prompt_manager = PromptConfigManager()
        podcast = episode.podcast

        # Get context
        prompt_config = prompt_manager.get_config(podcast.slug)
        host_name = None
        guest_name = None
        terminology: list[str] = []

        if prompt_config:
            host_name = prompt_config.podcast.host
            terminology.extend(prompt_config.podcast.common_terminology)

            ep_config = prompt_config.get_episode_config(episode.episode_number)
            if ep_config:
                guest_name = ep_config.guest
                if ep_config.guest_context:
                    terminology.extend(ep_config.guest_context.terminology)
                terminology.extend(ep_config.terminology)

        payload = load_transcript_processing_payload(
            db=session,
            transcript=transcript,
            artifact_store=build_transcript_artifact_store(settings=get_settings()),
        )
        if payload is None or not payload.raw_text:
            logger.warning("Transcript has no retained raw text, skipping cleanup")
            episode.cleanup_status = "skipped"
            session.commit()
            return False

        with TranscriptCleaner(api_key=api_key) as cleaner:
            result = cleaner.cleanup_transcript(
                transcript_text=payload.raw_text,
                podcast_name=podcast.name,
                host_name=host_name,
                guest_name=guest_name,
                terminology=terminology,
            )

        if result.success:
            transcript.cleaned_text = result.cleaned_text
            transcript.cleaned_at = datetime.now(UTC)
            episode.cleanup_status = "completed"
            session.commit()
            logger.info(f"Cleanup complete: {result.correction_count} corrections")
            return True
        else:
            logger.error(f"Cleanup failed: {result.errors}")
            episode.cleanup_status = "failed"
            session.commit()
            return False

    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        episode.cleanup_status = "failed"
        session.commit()
        return False


def run_extraction(session: Session, episode: Episode) -> bool:
    """Run LLM extraction if needed."""
    if episode.extraction_status == "completed":
        logger.info("Extraction already completed, skipping")
        return True

    if episode.extraction_status == "failed":
        logger.info("Previous extraction failed, retrying...")

    # Get transcript
    transcript = (
        session.query(Transcript)
        .filter(Transcript.episode_id == episode.id)
        .order_by(Transcript.id.desc())
        .first()
    )

    if not transcript:
        logger.warning("No transcript with segments found for extraction")
        episode.extraction_status = "skipped"
        session.commit()
        return False

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, skipping extraction")
        episode.extraction_status = "skipped"
        session.commit()
        return False

    logger.info("Starting extraction...")
    extractor_job = TranscriptionJob(
        episode_id=episode.id,
        job_type=JobType.EXTRACT.value,
        backend="anthropic",
    )
    session.add(extractor_job)
    session.flush()
    extractor_job.start()
    episode.extraction_status = "in_progress"
    session.commit()

    try:
        min_confidence = float(os.getenv("EXTRACTION_MIN_CONFIDENCE", "0.5"))

        payload = load_transcript_processing_payload(
            db=session,
            transcript=transcript,
            artifact_store=build_transcript_artifact_store(settings=get_settings()),
        )
        if payload is None or not payload.segments:
            logger.warning("Transcript has no retained segments, skipping extraction")
            episode.extraction_status = "skipped"
            session.commit()
            return False

        with LLMExtractor(api_key=api_key) as extractor:
            extractor_job.model = extractor.model
            result = extractor.extract_from_transcript(payload.segments)

        if not result.items:
            logger.info("No media found in transcript")
            episode.extraction_status = "completed"
            extractor_job.complete(metadata={"items_extracted": 0})
            session.commit()
            return True

        summary = persist_extracted_candidates(
            db=session,
            episode=episode,
            items=result.items,
            segments=payload.segments,
            min_confidence=min_confidence,
            extraction_source="llm_extract",
            source_job_id=extractor_job.id,
        )

        episode.extraction_status = "completed"
        extractor_job.complete(
            metadata={
                "items_extracted": len(result.items),
                "candidates_created": summary.candidates_created,
                "candidates_updated": summary.candidates_updated,
                "review_items_created": summary.review_items_created,
            }
        )
        session.commit()
        logger.info(
            (
                "Extraction complete: %s candidates queued, %s updated, "
                "%s existing mentions skipped"
            ),
            summary.candidates_created,
            summary.candidates_updated,
            summary.skipped_existing_mentions,
        )
        return True

    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        episode.extraction_status = "failed"
        extractor_job.fail(error=str(e))
        session.commit()
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process a YouTube URL through the full Podex pipeline"
    )
    parser.add_argument(
        "url",
        help="YouTube URL or video ID",
    )
    parser.add_argument(
        "--podcast",
        "-p",
        default=DEFAULT_PODCAST_SLUG,
        help=f"Podcast slug (default: {DEFAULT_PODCAST_SLUG})",
    )
    parser.add_argument(
        "--podcast-name",
        default=None,
        help="Podcast name (only used when creating new podcast)",
    )
    parser.add_argument(
        "--skip-transcription",
        action="store_true",
        help="Skip transcription step",
    )
    parser.add_argument(
        "--skip-cleanup",
        action="store_true",
        help="Skip cleanup step",
    )
    parser.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Skip extraction step",
    )

    args = parser.parse_args()

    # Extract video ID
    try:
        video_id = extract_video_id(args.url)
        logger.info(f"Video ID: {video_id}")
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    # Fetch metadata
    try:
        metadata = fetch_metadata_ytdlp(video_id)
        logger.info(f"Title: {metadata.title}")
        if metadata.duration_seconds:
            logger.info(f"Duration: {metadata.duration_seconds / 60:.1f} minutes")
    except Exception as e:
        logger.error(f"Failed to fetch metadata: {e}")
        sys.exit(1)

    # Process in database
    with SessionLocal() as session:
        # Get or create podcast
        podcast_name = args.podcast_name or (
            DEFAULT_PODCAST_NAME
            if args.podcast == DEFAULT_PODCAST_SLUG
            else args.podcast.title()
        )
        podcast = ensure_podcast(session, args.podcast, podcast_name)

        # Get or create episode
        episode = get_or_create_episode(session, podcast.id, metadata)

        # Run pipeline steps
        print("\n" + "=" * 60)
        print("PIPELINE STATUS")
        print("=" * 60)

        # Step 1: Transcription
        if args.skip_transcription:
            logger.info("Skipping transcription (--skip-transcription)")
        else:
            run_transcription(session, episode)

        # Step 2: Cleanup
        if args.skip_cleanup:
            logger.info("Skipping cleanup (--skip-cleanup)")
        else:
            run_cleanup(session, episode)

        # Step 3: Extraction
        if args.skip_extraction:
            logger.info("Skipping extraction (--skip-extraction)")
        else:
            run_extraction(session, episode)

        # Final status
        session.refresh(episode)
        print("\n" + "=" * 60)
        print("FINAL STATUS")
        print("=" * 60)
        print(f"  Episode:     {episode.title}")
        print(f"  Podcast:     {podcast.name}")
        print(f"  Transcription: {episode.transcript_status}")
        print(f"  Cleanup:       {episode.cleanup_status}")
        print(f"  Extraction:    {episode.extraction_status}")

        # Count mentions
        mention_count = (
            session.query(Mention).filter(Mention.episode_id == episode.id).count()
        )
        print(f"  Mentions:      {mention_count}")
        print("=" * 60)


if __name__ == "__main__":
    main()
