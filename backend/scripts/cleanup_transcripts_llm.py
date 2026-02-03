#!/usr/bin/env python3
"""Clean up transcripts using LLM post-processing.

This script processes transcripts with Claude to fix:
- Proper nouns (names, places, products)
- Technical terminology
- Obvious transcription errors
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from podex.database import SessionLocal
from podex.models import IngestionRun, Transcript
from podex.services.episode_iterator import (
    EpisodeIterator,
    ProcessingStage,
)
from podex.services.llm_transcript_cleanup import TranscriptCleaner
from podex.services.prompt_config import PromptConfigManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Run LLM cleanup on transcripts."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set in environment")
        return

    # Configuration from environment
    limit_env = os.getenv("CLEANUP_LIMIT")
    limit = int(limit_env) if limit_env else None

    podcast_slug = os.getenv("CLEANUP_PODCAST_SLUG")

    episode_min_env = os.getenv("CLEANUP_EPISODE_MIN")
    episode_min = int(episode_min_env) if episode_min_env else None

    episode_max_env = os.getenv("CLEANUP_EPISODE_MAX")
    episode_max = int(episode_max_env) if episode_max_env else None

    # Initialize services
    prompt_manager = PromptConfigManager()

    with TranscriptCleaner(api_key=api_key) as cleaner, SessionLocal() as session:
        # Create ingestion run
        run = IngestionRun(status="running")
        session.add(run)
        session.commit()

        try:
            iterator = EpisodeIterator(session)

            # Get episodes pending cleanup
            episodes = iterator.get_pending(
                ProcessingStage.CLEANUP,
                podcast_slug=podcast_slug,
                episode_min=episode_min,
                episode_max=episode_max,
                limit=limit,
            )
            total = len(episodes)
            logger.info(f"Found {total} episodes pending cleanup")

            if total == 0:
                logger.info("No episodes to process")
                run.status = "completed"
                run.completed_at = datetime.now(UTC)
                session.commit()
                return

            # Track stats
            stats = {
                "processed": 0,
                "corrections": 0,
                "skipped": 0,
                "errors": 0,
            }
            error_samples: list[str] = []

            for i, episode in enumerate(episodes, 1):
                podcast = episode.podcast
                logger.info(
                    f"\n[{i}/{total}] Episode {episode.episode_number}: {episode.title[:50]}..."
                )

                # Mark as in progress
                episode.cleanup_status = "in_progress"
                session.commit()

                try:
                    # Get the transcript
                    transcript = (
                        session.query(Transcript)
                        .filter(Transcript.episode_id == episode.id)
                        .filter(Transcript.raw_text.isnot(None))
                        .first()
                    )

                    if not transcript:
                        logger.warning("  No transcript found")
                        episode.cleanup_status = "skipped"
                        session.commit()
                        stats["skipped"] += 1
                        continue

                    if transcript.cleaned_text:
                        logger.info("  Already cleaned, skipping")
                        episode.cleanup_status = "completed"
                        session.commit()
                        stats["skipped"] += 1
                        continue

                    # Get context from prompt config
                    prompt_config = prompt_manager.get_config(podcast.slug)

                    host_name = None
                    guest_name = None
                    terminology: list[str] = []

                    if prompt_config:
                        host_name = prompt_config.podcast.host
                        terminology.extend(prompt_config.podcast.common_terminology)

                        ep_config = prompt_config.get_episode_config(
                            episode.episode_number
                        )
                        if ep_config:
                            guest_name = ep_config.guest
                            if ep_config.guest_context:
                                terminology.extend(ep_config.guest_context.terminology)
                            terminology.extend(ep_config.terminology)

                    # Run cleanup
                    result = cleaner.cleanup_transcript(
                        transcript_text=transcript.raw_text,
                        podcast_name=podcast.name,
                        host_name=host_name,
                        guest_name=guest_name,
                        terminology=terminology,
                    )

                    if result.success:
                        # Store cleaned text
                        transcript.cleaned_text = result.cleaned_text
                        transcript.cleaned_at = datetime.now(UTC)
                        episode.cleanup_status = "completed"
                        session.commit()

                        stats["processed"] += 1
                        stats["corrections"] += result.correction_count
                        logger.info(f"  Made {result.correction_count} corrections")

                        # Log some corrections for visibility
                        for corr in result.corrections_made[:3]:
                            logger.info(
                                f"    '{corr.original}' -> '{corr.corrected}' ({corr.reason})"
                            )
                    else:
                        episode.cleanup_status = "failed"
                        session.commit()
                        stats["errors"] += 1
                        error_msg = f"Episode {episode.id}: {result.errors}"
                        logger.error(f"  Cleanup failed: {result.errors}")
                        if len(error_samples) < 5:
                            error_samples.append(error_msg)

                except Exception as e:
                    episode.cleanup_status = "failed"
                    session.commit()
                    stats["errors"] += 1
                    error_msg = f"Episode {episode.id}: {e}"
                    logger.exception(f"  Error: {e}")
                    if len(error_samples) < 5:
                        error_samples.append(error_msg)

            # Update run status
            run.status = (
                "completed" if stats["errors"] == 0 else "completed_with_errors"
            )
            if error_samples:
                run.error_summary = "\n".join(error_samples)
            run.completed_at = datetime.now(UTC)
            session.commit()

        except Exception as exc:
            logger.exception("Fatal error during cleanup")
            run.status = "failed"
            run.error_summary = str(exc)
            run.completed_at = datetime.now(UTC)
            session.commit()
            raise

    # Print summary
    print("\n" + "=" * 50)
    print("TRANSCRIPT CLEANUP COMPLETE")
    print("=" * 50)
    print(f"  Processed:   {stats['processed']}")
    print(f"  Corrections: {stats['corrections']}")
    print(f"  Skipped:     {stats['skipped']}")
    print(f"  Errors:      {stats['errors']}")
    print("=" * 50)


if __name__ == "__main__":
    main()
