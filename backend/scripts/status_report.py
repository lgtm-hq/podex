#!/usr/bin/env python3
"""Generate a status report for all podcasts.

Shows discovery completeness, transcript coverage, and extraction status
across all tracked podcasts.

Usage:
    python status_report.py               # All podcasts
    python status_report.py --podcast jre # Specific podcast
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import func

from podex.database import SessionLocal
from podex.models import Episode, Podcast, Transcript

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def print_podcast_status(session: Session, podcast: Podcast) -> None:
    """Print detailed status for a single podcast."""
    # Episode counts
    base_query = session.query(Episode).filter(Episode.podcast_id == podcast.id)
    total_episodes = base_query.count()

    # Status counts
    transcript_pending = base_query.filter(
        Episode.transcript_status == "pending"
    ).count()
    transcript_completed = base_query.filter(
        Episode.transcript_status == "completed"
    ).count()
    transcript_failed = base_query.filter(Episode.transcript_status == "failed").count()

    extraction_pending = base_query.filter(
        Episode.extraction_status == "pending"
    ).count()
    extraction_completed = base_query.filter(
        Episode.extraction_status == "completed"
    ).count()
    extraction_failed = base_query.filter(Episode.extraction_status == "failed").count()

    # Transcript providers
    providers = (
        session.query(Transcript.provider, func.count(Transcript.id))
        .join(Episode)
        .filter(Episode.podcast_id == podcast.id)
        .group_by(Transcript.provider)
        .all()
    )

    # Discovery sources configured
    sources = []
    if podcast.podscripts_slug:
        sources.append(f"podscripts ({podcast.podscripts_slug})")
    if podcast.rss_url:
        sources.append("rss")
    if podcast.spotify_id:
        sources.append(f"spotify ({podcast.spotify_id})")
    if podcast.youtube_channel_id:
        sources.append(f"youtube ({podcast.youtube_channel_id})")

    # Print status
    print(f"\n{'=' * 60}")
    print(f" {podcast.name} ({podcast.slug})")
    print(f"{'=' * 60}")
    print(f" Status:    {podcast.status}")
    print(f" Sources:   {', '.join(sources) if sources else 'none'}")
    print()

    print(f" Episodes:  {total_episodes} total")
    print()

    # Transcription status
    print(" Transcription:")
    print(f"   Completed: {transcript_completed}")
    print(f"   Pending:   {transcript_pending}")
    if transcript_failed > 0:
        print(f"   Failed:    {transcript_failed}")

    if providers:
        print("   By provider:")
        for provider, count in sorted(providers, key=lambda x: -x[1]):
            print(f"     {provider}: {count}")
    print()

    # Extraction status
    print(" Extraction:")
    print(f"   Completed: {extraction_completed}")
    print(f"   Pending:   {extraction_pending}")
    if extraction_failed > 0:
        print(f"   Failed:    {extraction_failed}")

    # Progress bars
    print()
    if total_episodes > 0:
        tx_pct = transcript_completed / total_episodes * 100
        ex_pct = extraction_completed / total_episodes * 100
        bar_width = 30

        tx_filled = int(tx_pct / 100 * bar_width)
        ex_filled = int(ex_pct / 100 * bar_width)

        print(
            f" Transcripts: [{'#' * tx_filled}{'-' * (bar_width - tx_filled)}] {tx_pct:.0f}%"
        )
        print(
            f" Extractions: [{'#' * ex_filled}{'-' * (bar_width - ex_filled)}] {ex_pct:.0f}%"
        )


def print_global_status(session: Session) -> None:
    """Print global system status."""
    # Counts
    total_podcasts = session.query(func.count(Podcast.id)).scalar() or 0
    active_podcasts = (
        session.query(func.count(Podcast.id))
        .filter(Podcast.status == "active")
        .scalar()
        or 0
    )
    watchlist_podcasts = (
        session.query(func.count(Podcast.id))
        .filter(Podcast.status == "watchlist")
        .scalar()
        or 0
    )
    paused_podcasts = (
        session.query(func.count(Podcast.id))
        .filter(Podcast.status == "paused")
        .scalar()
        or 0
    )

    total_episodes = session.query(func.count(Episode.id)).scalar() or 0
    transcribed = (
        session.query(func.count(Episode.id))
        .filter(Episode.transcript_status == "completed")
        .scalar()
        or 0
    )
    extracted = (
        session.query(func.count(Episode.id))
        .filter(Episode.extraction_status == "completed")
        .scalar()
        or 0
    )

    # Source coverage
    with_podscripts = (
        session.query(func.count(Podcast.id))
        .filter(Podcast.podscripts_slug.isnot(None))
        .scalar()
        or 0
    )
    with_rss = (
        session.query(func.count(Podcast.id))
        .filter(Podcast.rss_url.isnot(None))
        .scalar()
        or 0
    )
    with_spotify = (
        session.query(func.count(Podcast.id))
        .filter(Podcast.spotify_id.isnot(None))
        .scalar()
        or 0
    )

    print()
    print("=" * 60)
    print(" PODEX STATUS REPORT")
    print("=" * 60)
    print()
    print(" PODCASTS")
    print(f"   Total:      {total_podcasts}")
    print(f"   Active:     {active_podcasts}")
    print(f"   Watchlist:  {watchlist_podcasts}")
    print(f"   Paused:     {paused_podcasts}")
    print()
    print(" SOURCE COVERAGE")
    print(f"   Podscripts: {with_podscripts}")
    print(f"   RSS:        {with_rss}")
    print(f"   Spotify:    {with_spotify}")
    print()
    print(" EPISODES")
    print(f"   Total:       {total_episodes}")
    print(f"   Transcribed: {transcribed}")
    print(f"   Extracted:   {extracted}")

    if total_episodes > 0:
        print()
        bar_width = 30
        tx_pct = transcribed / total_episodes * 100
        ex_pct = extracted / total_episodes * 100
        tx_filled = int(tx_pct / 100 * bar_width)
        ex_filled = int(ex_pct / 100 * bar_width)
        print(
            f"   Transcripts: [{'#' * tx_filled}{'-' * (bar_width - tx_filled)}] {tx_pct:.0f}%"
        )
        print(
            f"   Extractions: [{'#' * ex_filled}{'-' * (bar_width - ex_filled)}] {ex_pct:.0f}%"
        )

    print()
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a status report",
    )
    parser.add_argument(
        "--podcast",
        "-p",
        default=None,
        help="Show status for a specific podcast",
    )

    args = parser.parse_args()

    with SessionLocal() as session:
        if args.podcast:
            podcast = (
                session.query(Podcast).filter(Podcast.slug == args.podcast).first()
            )
            if not podcast:
                print(f"Podcast '{args.podcast}' not found")
                sys.exit(1)
            print_podcast_status(session, podcast)
        else:
            print_global_status(session)

            # Show summary for each podcast
            podcasts = (
                session.query(Podcast).order_by(Podcast.status, Podcast.name).all()
            )
            for podcast in podcasts:
                print_podcast_status(session, podcast)


if __name__ == "__main__":
    main()
