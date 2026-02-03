#!/usr/bin/env python3
"""Discover new episodes for configured podcasts.

This script runs discovery across all active podcasts, finding new episodes
from configured sources (podscripts.co, RSS, Spotify).

Usage:
    python discover_episodes.py                    # All active podcasts
    python discover_episodes.py --podcast jre     # Specific podcast
    python discover_episodes.py --all             # All podcasts (including watchlist)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from podex.database import SessionLocal
from podex.models import Podcast, PodcastStatus
from podex.services.discovery_orchestrator import DiscoveryOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover new episodes for configured podcasts",
    )
    parser.add_argument(
        "--podcast",
        "-p",
        default=None,
        help="Discover for a specific podcast (by slug)",
    )
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Discover for all podcasts (including watchlist)",
    )

    args = parser.parse_args()

    with SessionLocal() as session:
        # Get podcasts to process
        query = session.query(Podcast)

        if args.podcast:
            query = query.filter(Podcast.slug == args.podcast)
        elif not args.all:
            # Only active podcasts by default
            query = query.filter(Podcast.status == PodcastStatus.ACTIVE)

        podcasts = query.all()

        if not podcasts:
            if args.podcast:
                logger.error(f"Podcast '{args.podcast}' not found")
            else:
                logger.warning(
                    "No active podcasts found. Use --all to include watchlist."
                )
            return

        logger.info(f"Discovering episodes for {len(podcasts)} podcast(s)")

        with DiscoveryOrchestrator(session) as orchestrator:
            total_new = 0
            total_updated = 0

            for podcast in podcasts:
                logger.info(f"\n{'=' * 50}")
                logger.info(f"Processing: {podcast.name} ({podcast.slug})")
                logger.info(f"{'=' * 50}")

                result = orchestrator.discover_for_podcast(podcast)

                total_new += result.new_episodes
                total_updated += result.updated_episodes

                logger.info(
                    f"  New episodes: {result.new_episodes}, Updated: {result.updated_episodes}"
                )

                if result.errors:
                    for error in result.errors:
                        logger.warning(f"  Error: {error}")

        # Summary
        print("\n" + "=" * 50)
        print("DISCOVERY COMPLETE")
        print("=" * 50)
        print(f"  Podcasts processed: {len(podcasts)}")
        print(f"  New episodes:       {total_new}")
        print(f"  Updated episodes:   {total_updated}")
        print("=" * 50)


if __name__ == "__main__":
    main()
