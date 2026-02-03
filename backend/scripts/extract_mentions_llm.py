#!/usr/bin/env python3
"""Extract media mentions from transcripts using LLM.

This script processes each transcript with a SINGLE API call to Claude,
making it efficient and cost-effective.

Uses EpisodeIterator to only process episodes with extraction_status="pending".
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
from podex.models import IngestionRun, JobType, Transcript, TranscriptionJob
from podex.services.episode_iterator import (
    EpisodeIterator,
    ProcessingStage,
)
from podex.services.extraction_review import persist_extracted_candidates
from podex.services.llm_extraction import LLMExtractor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Run LLM extraction on pending episodes."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set in environment")
        return

    # Configuration from environment
    min_confidence = float(os.getenv("EXTRACTION_MIN_CONFIDENCE", "0.5"))

    limit_env = os.getenv("EXTRACTION_LIMIT", "").strip()
    limit = int(limit_env) if limit_env else None

    podcast_slug = os.getenv("EXTRACTION_PODCAST_SLUG", "").strip() or None

    episode_min_env = os.getenv("EXTRACTION_EPISODE_MIN", "").strip()
    episode_min = int(episode_min_env) if episode_min_env else None

    episode_max_env = os.getenv("EXTRACTION_EPISODE_MAX", "").strip()
    episode_max = int(episode_max_env) if episode_max_env else None

    logger.info(f"Min confidence: {min_confidence}")
    if podcast_slug:
        logger.info(f"Filtering to podcast: {podcast_slug}")
    if episode_min or episode_max:
        logger.info(
            f"Episode range: {episode_min or 'start'} to {episode_max or 'end'}"
        )

    with LLMExtractor(api_key=api_key) as extractor, SessionLocal() as session:
        # Create ingestion run
        run = IngestionRun(status="running")
        session.add(run)
        session.commit()

        try:
            # Use EpisodeIterator to get only pending episodes
            iterator = EpisodeIterator(session)
            episodes = iterator.get_pending(
                ProcessingStage.EXTRACTION,
                podcast_slug=podcast_slug,
                episode_min=episode_min,
                episode_max=episode_max,
                limit=limit,
            )
            total = len(episodes)
            logger.info(f"Found {total} episodes pending extraction")

            if total == 0:
                # Show stats for context
                all_stats = iterator.get_all_stats(
                    podcast_slug, episode_min, episode_max
                )
                extract_stats = all_stats.get("extraction")
                logger.info(f"Extraction status: {extract_stats}")
                run.status = "completed"
                run.completed_at = datetime.now(UTC)
                session.commit()
                return

            # Track stats
            stats = {
                "episodes_processed": 0,
                "candidates_created": 0,
                "candidates_updated": 0,
                "review_items_created": 0,
                "errors": 0,
                "skipped": 0,
                "skipped_existing_mentions": 0,
            }

            for i, episode in enumerate(episodes, 1):
                logger.info(f"\n[{i}/{total}] {episode.title}")

                job = TranscriptionJob(
                    episode_id=episode.id,
                    job_type=JobType.EXTRACT.value,
                    backend="anthropic",
                    model=extractor.model,
                )
                session.add(job)
                session.flush()
                job.start()

                # Get transcript for this episode
                transcript = (
                    session.query(Transcript)
                    .filter(Transcript.episode_id == episode.id)
                    .filter(Transcript.segments_json.isnot(None))
                    .first()
                )

                if not transcript:
                    logger.warning("  No transcript with segments found")
                    episode.extraction_status = "skipped"
                    job.skip(reason="No transcript with segments found")
                    session.commit()
                    stats["skipped"] += 1
                    continue

                # Mark as in progress
                episode.extraction_status = "in_progress"
                session.commit()

                try:
                    segments = transcript.segments_json or []
                    if not segments:
                        logger.warning("  No segments in transcript")
                        episode.extraction_status = "skipped"
                        job.skip(reason="Transcript had no segments")
                        session.commit()
                        stats["skipped"] += 1
                        continue

                    # Extract media - ONE API call per transcript
                    result = extractor.extract_from_transcript(segments)

                    if not result.items:
                        logger.info("  No media found")
                        episode.extraction_status = "completed"
                        job.complete(metadata={"items_extracted": 0})
                        session.commit()
                        stats["episodes_processed"] += 1
                        continue

                    summary = persist_extracted_candidates(
                        db=session,
                        episode=episode,
                        items=result.items,
                        segments=segments,
                        min_confidence=min_confidence,
                        extraction_source="llm_extract",
                        source_job_id=job.id,
                    )
                    stats["candidates_created"] += summary.candidates_created
                    stats["candidates_updated"] += summary.candidates_updated
                    stats["review_items_created"] += summary.review_items_created
                    stats[
                        "skipped_existing_mentions"
                    ] += summary.skipped_existing_mentions

                    # Mark as completed
                    episode.extraction_status = "completed"
                    job.complete(
                        metadata={
                            "items_extracted": len(result.items),
                            "candidates_created": summary.candidates_created,
                            "candidates_updated": summary.candidates_updated,
                            "review_items_created": summary.review_items_created,
                        }
                    )
                    session.commit()
                    stats["episodes_processed"] += 1
                    logger.info(
                        (
                            "  Queued %s review candidates "
                            "(%s updated, %s existing mentions skipped)"
                        ),
                        summary.candidates_created,
                        summary.candidates_updated,
                        summary.skipped_existing_mentions,
                    )

                except Exception as e:
                    stats["errors"] += 1
                    logger.error(f"  Error: {e}")
                    episode.extraction_status = "failed"
                    job.fail(error=str(e))
                    session.commit()

            # Update run status
            run.status = (
                "completed" if stats["errors"] == 0 else "completed_with_errors"
            )
            run.completed_at = datetime.now(UTC)
            session.commit()

        except Exception as exc:
            logger.exception("Fatal error during extraction")
            run.status = "failed"
            run.error_summary = str(exc)
            run.completed_at = datetime.now(UTC)
            session.commit()
            raise

    # Print summary
    print("\n" + "=" * 60)
    print("LLM EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"  Episodes processed: {stats['episodes_processed']}")
    print(f"  Candidates created: {stats['candidates_created']}")
    print(f"  Candidates updated: {stats['candidates_updated']}")
    print(f"  Review items made:  {stats['review_items_created']}")
    print(f"  Existing skipped:   {stats['skipped_existing_mentions']}")
    print(f"  Skipped:            {stats['skipped']}")
    print(f"  Errors:             {stats['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
