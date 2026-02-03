#!/usr/bin/env python3
"""Batch enrich media items with external data.

This script enriches media items that haven't been enriched yet,
adding cover art, descriptions, external IDs, and metadata.

Usage:
    # Enrich all unenriched media (uses multi-pass by default)
    uv run python scripts/enrich_media.py

    # Enrich with limit
    uv run python scripts/enrich_media.py --limit 100

    # Enrich only books
    uv run python scripts/enrich_media.py --type book

    # Re-enrich items (even if already enriched)
    uv run python scripts/enrich_media.py --force

    # Dry run (don't save changes)
    uv run python scripts/enrich_media.py --dry-run

    # Single pass with specific confidence (disable multi-pass)
    uv run python scripts/enrich_media.py --single-pass --min-confidence 0.5
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add backend/src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.config import get_settings
from podex.database import engine
from podex.models.media import Media, MediaType
from podex.services.media_enrichment import MediaEnricher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Enrich media items with external data",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum number of items to enrich (0 = unlimited)",
    )
    parser.add_argument(
        "--type",
        type=str,
        choices=[t.value for t in MediaType],
        help="Only enrich media of this type",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-enrich items even if already enriched",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't save changes to database",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.5,
        help="Minimum confidence threshold for final pass (0.0-1.0)",
    )
    parser.add_argument(
        "--single-pass",
        action="store_true",
        help="Disable multi-pass enrichment (use single pass with --min-confidence)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


# Default confidence thresholds for multi-pass enrichment
CONFIDENCE_PASSES = [0.85, 0.7, 0.55]


def get_unenriched_media(
    db: Session,
    media_type: str | None = None,
    force: bool = False,
    limit: int = 0,
) -> list[Media]:
    """Get media items that need enrichment.

    Args:
        db: Database session.
        media_type: Optional filter by media type.
        force: If True, include already-enriched items.
        limit: Maximum items to return (0 = unlimited).

    Returns:
        List of Media items to enrich.
    """
    query = select(Media)

    # Filter by enrichment status
    if not force:
        query = query.where(Media.enriched_at.is_(None))

    # Filter by type
    if media_type:
        query = query.where(Media.type == media_type)

    # Order by ID for consistent processing
    query = query.order_by(Media.id)

    # Apply limit
    if limit > 0:
        query = query.limit(limit)

    return list(db.scalars(query))


def enrich_single_pass(
    db: Session,
    enricher: MediaEnricher,
    media_items: list[Media],
    min_confidence: float,
    dry_run: bool,
) -> dict[str, int | set[int]]:
    """Run a single enrichment pass on media items.

    Args:
        db: Database session.
        enricher: MediaEnricher instance.
        media_items: List of media items to enrich.
        min_confidence: Minimum confidence threshold.
        dry_run: If True, don't save changes.

    Returns:
        Dict with enriched/skipped/failed counts and list of enriched IDs.
    """
    stats: dict[str, int | set[int]] = {
        "enriched": 0,
        "skipped": 0,
        "failed": 0,
        "enriched_ids": set(),
    }

    for i, media in enumerate(media_items, 1):
        logger.info(f"  [{i}/{len(media_items)}] {media.title} ({media.type})")

        try:
            success = enricher.enrich_and_apply(
                media,
                min_confidence=min_confidence,
            )

            if success:
                enriched_count = stats["enriched"]
                assert isinstance(enriched_count, int)
                stats["enriched"] = enriched_count + 1
                enriched_ids = stats["enriched_ids"]
                assert isinstance(enriched_ids, set)
                enriched_ids.add(media.id)
                logger.info(
                    f"    -> Enriched from {media.enrichment_source} "
                    f"(confidence: {media.enrichment_confidence:.2f})"
                )

                # Save to database (unless dry run)
                if not dry_run:
                    db.add(media)
                    db.commit()
            else:
                skipped_count = stats["skipped"]
                assert isinstance(skipped_count, int)
                stats["skipped"] = skipped_count + 1

        except Exception:
            failed_count = stats["failed"]
            assert isinstance(failed_count, int)
            stats["failed"] = failed_count + 1
            logger.exception(f"    Error enriching {media.title}")
            if not dry_run:
                db.rollback()

    return stats


def main() -> int:
    """Main entry point."""
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    settings = get_settings()

    # Check for API keys
    if not settings.tmdb_api_key and not settings.omdb_api_key:
        logger.warning(
            "No TMDB or OMDB API keys configured. Movies and TV shows will not be enriched."
        )

    # Initialize enricher
    enricher = MediaEnricher(
        tmdb_api_key=settings.tmdb_api_key or None,
        omdb_api_key=settings.omdb_api_key or None,
        google_books_api_key=settings.google_books_api_key or None,
    )

    logger.info(f"Available providers: {enricher.get_available_providers()}")

    # Determine confidence thresholds
    if args.single_pass:
        passes = [args.min_confidence]
        logger.info(f"Single-pass mode with confidence >= {args.min_confidence}")
    else:
        # Filter passes to only include those >= min_confidence
        passes = [c for c in CONFIDENCE_PASSES if c >= args.min_confidence]
        if not passes:
            passes = [args.min_confidence]
        logger.info(f"Multi-pass mode with thresholds: {passes}")

    # Get media to enrich
    with Session(engine) as db:
        media_items = get_unenriched_media(
            db,
            media_type=args.type,
            force=args.force,
            limit=args.limit,
        )

        if not media_items:
            logger.info("No media items to enrich")
            return 0

        logger.info(f"Found {len(media_items)} media items to enrich")

        # Track overall statistics
        total_stats = {
            "total": len(media_items),
            "enriched": 0,
            "skipped": 0,
            "failed": 0,
        }
        enriched_ids: set[int] = set()

        # Run multiple passes with decreasing confidence
        for pass_num, confidence in enumerate(passes, 1):
            # Filter out already-enriched items
            remaining = [m for m in media_items if m.id not in enriched_ids]

            if not remaining:
                logger.info(f"Pass {pass_num}: No remaining items to process")
                break

            logger.info(
                f"\n{'=' * 50}\n"
                f"Pass {pass_num}/{len(passes)}: "
                f"confidence >= {confidence:.2f} ({len(remaining)} items)\n"
                f"{'=' * 50}"
            )

            pass_stats = enrich_single_pass(
                db=db,
                enricher=enricher,
                media_items=remaining,
                min_confidence=confidence,
                dry_run=args.dry_run,
            )

            # Update totals
            pass_enriched = pass_stats["enriched"]
            pass_failed = pass_stats["failed"]
            pass_enriched_ids = pass_stats["enriched_ids"]
            assert isinstance(pass_enriched, int)
            assert isinstance(pass_failed, int)
            assert isinstance(pass_enriched_ids, set)
            total_stats["enriched"] += pass_enriched
            total_stats["failed"] += pass_failed
            enriched_ids.update(pass_enriched_ids)

            logger.info(
                f"Pass {pass_num} results: "
                f"{pass_stats['enriched']} enriched, "
                f"{pass_stats['skipped']} skipped"
            )

        # Calculate final skipped count
        total_stats["skipped"] = (
            total_stats["total"] - total_stats["enriched"] - total_stats["failed"]
        )

    # Print summary
    logger.info("\n" + "=" * 50)
    logger.info("Enrichment complete!")
    logger.info(f"  Total processed: {total_stats['total']}")
    logger.info(f"  Enriched: {total_stats['enriched']}")
    logger.info(f"  Skipped (no match): {total_stats['skipped']}")
    logger.info(f"  Failed: {total_stats['failed']}")

    if args.dry_run:
        logger.info("  (Dry run - no changes saved)")

    enricher.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
