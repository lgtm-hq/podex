#!/usr/bin/env python3
"""Transcribe YouTube videos using Whisper AI.

Supports multiple backends (faster-whisper, OpenAI, Groq, MLX-Whisper)
and uses prompt configuration for improved accuracy.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Load .env file
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from podex.config import get_settings
from podex.database import SessionLocal
from podex.models import IngestionRun, Transcript
from podex.services.episode_iterator import (
    EpisodeIterator,
    ProcessingStage,
)
from podex.services.prompt_config import PromptConfigManager
from podex.services.transcript_artifacts import (
    build_transcript_artifact_store,
    persist_transcript_acquisition,
)
from podex.services.transcript_source import TranscriptAcquisitionResult
from podex.services.whisper_transcriber import (
    WhisperBackend,
    WhisperConfig,
    WhisperModel,
    WhisperTranscriber,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    # Configuration from environment (treat empty strings as None)
    limit_env = os.getenv("TRANSCRIPT_LIMIT", "").strip()
    limit = int(limit_env) if limit_env else None

    podcast_slug = os.getenv("TRANSCRIPT_PODCAST_SLUG", "").strip() or None

    episode_min_env = os.getenv("TRANSCRIPT_EPISODE_MIN", "").strip()
    episode_min = int(episode_min_env) if episode_min_env else None

    episode_max_env = os.getenv("TRANSCRIPT_EPISODE_MAX", "").strip()
    episode_max = int(episode_max_env) if episode_max_env else None

    # Title pattern filter (for podcasts with multiple shows using same numbers)
    title_pattern = os.getenv("TRANSCRIPT_TITLE_PATTERN", "").strip() or None
    if not title_pattern and podcast_slug == "jre":
        # Auto-detect for JRE: filter to main episodes only (not MMA Show, etc.)
        title_pattern = "Joe Rogan Experience #%"

    # Whisper settings
    backend_env = os.getenv("WHISPER_BACKEND", "faster_whisper")
    model_env = os.getenv("WHISPER_MODEL", "base")

    try:
        backend = WhisperBackend(backend_env.lower())
    except ValueError:
        logger.error(f"Invalid backend: {backend_env}")
        logger.info(f"Valid backends: {[b.value for b in WhisperBackend]}")
        return

    try:
        model = WhisperModel(model_env.lower())
    except ValueError:
        logger.error(f"Invalid model: {model_env}")
        logger.info(f"Valid models: {[m.value for m in WhisperModel]}")
        return

    # Initialize transcriber
    config = WhisperConfig.from_env()
    config.backend = backend
    config.model = model

    # Validate API keys for API backends
    if backend == WhisperBackend.OPENAI and not config.openai_api_key:
        logger.error("OPENAI_API_KEY required for OpenAI backend")
        return
    if backend == WhisperBackend.GROQ and not config.groq_api_key:
        logger.error("GROQ_API_KEY required for Groq backend")
        return

    transcriber = WhisperTranscriber(config)
    prompt_manager = PromptConfigManager()

    logger.info(f"Whisper config: backend={backend.value}, model={model.value}")
    logger.info(f"Audio quality: {config.audio_quality}, beam size: {config.beam_size}")
    if podcast_slug:
        logger.info(f"Filtering to podcast: {podcast_slug}")
    if episode_min or episode_max:
        logger.info(
            f"Episode range: {episode_min or 'start'} to {episode_max or 'end'}"
        )
    if title_pattern:
        logger.info(f"Title pattern: {title_pattern}")

    # Stats tracking
    stats = {
        "total_audio_seconds": 0.0,
        "total_segments": 0,
        "videos_processed": 0,
    }

    with SessionLocal() as session:
        run = IngestionRun(status="running")
        session.add(run)
        session.commit()

        try:
            # Use episode iterator
            iterator = EpisodeIterator(session)

            # Get episodes pending transcription
            episodes = iterator.get_pending(
                ProcessingStage.TRANSCRIPTION,
                podcast_slug=podcast_slug,
                episode_min=episode_min,
                episode_max=episode_max,
                title_pattern=title_pattern,
                limit=limit,
            )
            total = len(episodes)
            logger.info(f"Found {total} episodes pending transcription")

            if total == 0:
                # Show stats for context
                all_stats = iterator.get_all_stats(
                    podcast_slug, episode_min, episode_max, title_pattern
                )
                trans_stats = all_stats.get("transcription")
                logger.info(f"Transcription status: {trans_stats}")
                run.status = "completed"
                run.completed_at = datetime.now(UTC)
                session.commit()
                return

            # Counters
            new_count = 0
            skipped_count = 0
            error_count = 0
            error_samples: list[str] = []

            for i, episode in enumerate(episodes, 1):
                video_id = episode.youtube_id
                if video_id is None:
                    skipped_count += 1
                    continue
                podcast = episode.podcast

                # Check if transcript already exists (extra safety)
                existing = (
                    session.query(Transcript)
                    .filter(Transcript.episode_id == episode.id)
                    .first()
                )
                if existing:
                    episode.transcript_status = "completed"
                    session.commit()
                    skipped_count += 1
                    continue

                # Mark as in progress
                episode.transcript_status = "in_progress"
                session.commit()

                logger.info(
                    f"[{i}/{total}] Episode {episode.episode_number}: {episode.title[:50]}..."
                )

                try:
                    # Get initial prompt for this episode
                    initial_prompt = prompt_manager.get_prompt(
                        podcast.slug,
                        episode.episode_number,
                    )
                    if initial_prompt:
                        logger.info(f"  Using prompt: {initial_prompt[:80]}...")

                    # Transcribe
                    result = transcriber.transcribe(
                        video_id,
                        initial_prompt=initial_prompt,
                    )

                    persist_transcript_acquisition(
                        db=session,
                        episode=episode,
                        acquisition=TranscriptAcquisitionResult(
                            success=True,
                            result=result,
                            source=result.provider,
                        ),
                        artifact_store=build_transcript_artifact_store(
                            settings=get_settings()
                        ),
                    )

                    # Update episode status
                    episode.transcript_status = "completed"
                    session.commit()

                    new_count += 1
                    stats["total_audio_seconds"] += result.audio_duration_seconds
                    stats["total_segments"] += len(result.segments)
                    stats["videos_processed"] += 1

                    logger.info(
                        f"  Success: {len(result.segments)} segments, "
                        f"{result.audio_duration_seconds / 60:.1f} min"
                    )

                except Exception as e:
                    episode.transcript_status = "failed"
                    session.commit()

                    error_count += 1
                    if len(error_samples) < 10:
                        error_samples.append(f"{video_id}: {type(e).__name__}: {e}")
                    logger.exception(f"  Failed: {e}")

            # Update run status
            if error_count > 0:
                run.status = "completed_with_errors"
                samples = "; ".join(error_samples[:5])
                run.error_summary = (
                    f"errors={error_count}, skipped={skipped_count}. samples={samples}"
                )
            else:
                run.status = "completed"

            run.completed_at = datetime.now(UTC)
            session.commit()

        except Exception as exc:
            logger.exception("Fatal error during ingestion")
            run.status = "failed"
            run.error_summary = str(exc)
            run.completed_at = datetime.now(UTC)
            session.commit()
            raise

    # Print summary
    print("\n" + "=" * 60)
    print("WHISPER TRANSCRIPTION SUMMARY")
    print("=" * 60)
    print(f"  Backend:    {backend.value}")
    print(f"  Model:      {model.value}")
    print()
    print(f"  Created:    {new_count}")
    print(f"  Skipped:    {skipped_count}")
    print(f"  Errors:     {error_count}")
    print()
    print(f"  Total audio:    {stats['total_audio_seconds'] / 60:.1f} minutes")
    print(f"  Total segments: {stats['total_segments']}")

    if backend == WhisperBackend.OPENAI:
        # OpenAI costs ~$0.006/minute
        cost = (stats["total_audio_seconds"] / 60) * 0.006
        print(f"  Est. cost:      ${cost:.2f}")
    elif backend == WhisperBackend.GROQ:
        # Groq is much cheaper
        cost = (stats["total_audio_seconds"] / 60) * 0.001
        print(f"  Est. cost:      ${cost:.2f}")
    elif backend == WhisperBackend.MLX_WHISPER:
        print("  Est. cost:      $0.00 (local)")

    print("=" * 60)


if __name__ == "__main__":
    main()
