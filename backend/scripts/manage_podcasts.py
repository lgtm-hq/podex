#!/usr/bin/env python3
"""Manage podcasts in the Podex system.

This script provides commands to add, activate, pause, and list podcasts.

Usage:
    python manage_podcasts.py list                           # List all podcasts
    python manage_podcasts.py add --podscripts huberman-lab  # Add from podscripts
    python manage_podcasts.py add --rss "https://..."        # Add from RSS
    python manage_podcasts.py activate jre                   # Activate a podcast
    python manage_podcasts.py pause jre                      # Pause a podcast
    python manage_podcasts.py sync                           # Sync from config file
    python manage_podcasts.py export                         # Export to YAML
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from dotenv import load_dotenv

if TYPE_CHECKING:
    from argparse import Namespace

load_dotenv(Path(__file__).parent.parent / ".env")

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from podex.database import SessionLocal
from podex.models import Episode, Podcast, PodcastStatus
from podex.services.discovery_podscripts import PodscriptsDiscovery
from podex.services.discovery_rss import RSSDiscovery
from podex.services.discovery_spotify import SpotifyDiscovery
from podex.services.podcast_config import PodcastConfigManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def cmd_list(args: Namespace) -> None:
    """List all podcasts with their status."""
    with SessionLocal() as session:
        podcasts = session.query(Podcast).order_by(Podcast.status, Podcast.name).all()

        if not podcasts:
            print("No podcasts found.")
            return

        # Group by status
        by_status: dict[str, list[Podcast]] = {}
        for podcast in podcasts:
            status = podcast.status or "unknown"
            if status not in by_status:
                by_status[status] = []
            by_status[status].append(podcast)

        # Print each group
        for status in ["active", "watchlist", "paused", "unknown"]:
            if status not in by_status:
                continue

            print(f"\n{status.upper()} ({len(by_status[status])}):")
            print("-" * 60)

            for podcast in by_status[status]:
                # Count episodes
                total_episodes = (
                    session.query(Episode)
                    .filter(Episode.podcast_id == podcast.id)
                    .count()
                )
                transcribed = (
                    session.query(Episode)
                    .filter(Episode.podcast_id == podcast.id)
                    .filter(Episode.transcript_status == "completed")
                    .count()
                )

                sources = []
                if podcast.podscripts_slug:
                    sources.append("podscripts")
                if podcast.rss_url:
                    sources.append("rss")
                if podcast.spotify_id:
                    sources.append("spotify")
                if podcast.youtube_channel_id:
                    sources.append("youtube")

                source_str = ", ".join(sources) if sources else "none"

                print(
                    f"  {podcast.slug:<20} {podcast.name:<35} "
                    f"{transcribed:>4}/{total_episodes:<4} episodes  [{source_str}]"
                )


def cmd_add(args: Namespace) -> None:
    """Add a new podcast."""
    with SessionLocal() as session:
        # Determine the source and discover podcast metadata
        discovered = None
        slug = None
        name = None

        if args.podscripts:
            with PodscriptsDiscovery() as podscripts_discovery:
                discovered = podscripts_discovery.discover_podcast(args.podscripts)
                if discovered:
                    slug = args.slug or args.podscripts.replace("-", "")[:20]
                    name = args.name or discovered.name

        elif args.rss:
            rss_discovery = RSSDiscovery()
            discovered = rss_discovery.discover_podcast(args.rss)
            if discovered:
                slug = args.slug or discovered.slug
                name = args.name or discovered.name

        elif args.spotify:
            spotify_discovery = SpotifyDiscovery()
            if not spotify_discovery.is_configured:
                logger.error("Spotify credentials not configured")
                return
            discovered = spotify_discovery.discover_podcast(args.spotify)
            if discovered:
                slug = args.slug or discovered.slug
                name = args.name or discovered.name

        if not discovered:
            logger.error("Could not discover podcast from the provided source")
            return

        if not slug or not name:
            logger.error("Could not determine slug or name for podcast")
            return

        # Check if podcast already exists
        existing = session.query(Podcast).filter(Podcast.slug == slug).first()
        if existing:
            logger.error(f"Podcast with slug '{slug}' already exists")
            return

        # Create the podcast
        podcast = Podcast(
            name=name,
            slug=slug,
            description=discovered.description,
            cover_url=discovered.cover_url,
            status=args.status,
            rss_url=discovered.rss_url,
            spotify_id=discovered.spotify_id,
            podscripts_slug=discovered.podscripts_slug,
            youtube_channel_id=discovered.youtube_channel_id,
            discovery_source=discovered.discovery_source,
        )
        session.add(podcast)
        session.commit()

        print(f"Added podcast: {name} ({slug})")
        print(f"  Status: {args.status}")
        print("  Sources: ", end="")
        sources = []
        if podcast.podscripts_slug:
            sources.append(f"podscripts ({podcast.podscripts_slug})")
        if podcast.rss_url:
            sources.append("rss")
        if podcast.spotify_id:
            sources.append(f"spotify ({podcast.spotify_id})")
        print(", ".join(sources) if sources else "none")


def cmd_activate(args: Namespace) -> None:
    """Activate a podcast for processing."""
    with SessionLocal() as session:
        podcast = session.query(Podcast).filter(Podcast.slug == args.slug).first()
        if not podcast:
            logger.error(f"Podcast '{args.slug}' not found")
            return

        podcast.status = PodcastStatus.ACTIVE
        session.commit()
        print(f"Activated: {podcast.name} ({podcast.slug})")


def cmd_pause(args: Namespace) -> None:
    """Pause a podcast."""
    with SessionLocal() as session:
        podcast = session.query(Podcast).filter(Podcast.slug == args.slug).first()
        if not podcast:
            logger.error(f"Podcast '{args.slug}' not found")
            return

        podcast.status = PodcastStatus.PAUSED
        session.commit()
        print(f"Paused: {podcast.name} ({podcast.slug})")


def cmd_sync(args: Namespace) -> None:
    """Sync podcasts from the config file."""
    config_path = Path(args.config) if args.config else None
    manager = PodcastConfigManager(config_path)

    with SessionLocal() as session:
        added = 0
        updated = 0

        for config in manager.config.podcasts:
            existing = (
                session.query(Podcast).filter(Podcast.slug == config.slug).first()
            )

            if existing:
                # Update existing podcast
                existing.status = config.status
                if config.sources.podscripts:
                    existing.podscripts_slug = config.sources.podscripts
                if config.sources.rss:
                    existing.rss_url = config.sources.rss
                if config.sources.spotify:
                    existing.spotify_id = config.sources.spotify
                if config.sources.youtube_channel:
                    existing.youtube_channel_id = config.sources.youtube_channel
                updated += 1
            else:
                # Create new podcast
                podcast = Podcast(
                    name=config.name,
                    slug=config.slug,
                    status=config.status,
                    podscripts_slug=config.sources.podscripts,
                    rss_url=config.sources.rss,
                    spotify_id=config.sources.spotify,
                    youtube_channel_id=config.sources.youtube_channel,
                )
                session.add(podcast)
                added += 1

        session.commit()

        print(f"Sync complete: {added} added, {updated} updated")


def cmd_export(args: Namespace) -> None:
    """Export podcasts to YAML format."""
    with SessionLocal() as session:
        podcasts = session.query(Podcast).order_by(Podcast.status, Podcast.name).all()

        data = {
            "podcasts": [
                {
                    "slug": p.slug,
                    "name": p.name,
                    "status": p.status,
                    "sources": {
                        k: v
                        for k, v in {
                            "podscripts": p.podscripts_slug,
                            "youtube_channel": p.youtube_channel_id,
                            "rss": p.rss_url,
                            "spotify": p.spotify_id,
                        }.items()
                        if v
                    },
                }
                for p in podcasts
            ]
        }

        print(yaml.dump(data, default_flow_style=False, sort_keys=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage podcasts in the Podex system",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list command
    list_parser = subparsers.add_parser("list", help="List all podcasts")
    list_parser.set_defaults(func=cmd_list)

    # add command
    add_parser = subparsers.add_parser("add", help="Add a new podcast")
    add_source = add_parser.add_mutually_exclusive_group(required=True)
    add_source.add_argument("--podscripts", help="Podscripts.co slug")
    add_source.add_argument("--rss", help="RSS feed URL")
    add_source.add_argument("--spotify", help="Spotify show ID")
    add_parser.add_argument(
        "--slug", help="Custom slug (auto-generated if not provided)"
    )
    add_parser.add_argument(
        "--name", help="Custom name (auto-discovered if not provided)"
    )
    add_parser.add_argument(
        "--status",
        default="watchlist",
        choices=["watchlist", "active", "paused"],
        help="Initial status (default: watchlist)",
    )
    add_parser.set_defaults(func=cmd_add)

    # activate command
    activate_parser = subparsers.add_parser("activate", help="Activate a podcast")
    activate_parser.add_argument("slug", help="Podcast slug")
    activate_parser.set_defaults(func=cmd_activate)

    # pause command
    pause_parser = subparsers.add_parser("pause", help="Pause a podcast")
    pause_parser.add_argument("slug", help="Podcast slug")
    pause_parser.set_defaults(func=cmd_pause)

    # sync command
    sync_parser = subparsers.add_parser("sync", help="Sync from config file")
    sync_parser.add_argument("--config", help="Path to podcasts.yaml")
    sync_parser.set_defaults(func=cmd_sync)

    # export command
    export_parser = subparsers.add_parser("export", help="Export to YAML")
    export_parser.set_defaults(func=cmd_export)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
