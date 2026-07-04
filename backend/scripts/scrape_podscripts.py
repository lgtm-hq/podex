#!/usr/bin/env python3
"""Scrape episode metadata and transcripts from podscripts.co.

This script fetches:
- Episode numbers and guest names
- Full transcripts with timestamps
- Episode metadata (date, topics)

The scraped transcripts can be compared against MLX-Whisper output
to evaluate transcript quality.

Usage:
    # Scrape from a specific podcast (default: JRE)
    python scrape_podscripts.py --podcast-slug the-joe-rogan-experience

    # Scrape with episode range
    python scrape_podscripts.py --podcast-slug huberman-lab --episode-min 100

    # Store to database
    python scrape_podscripts.py --podcast-slug jre --store-db
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from podex.config import get_settings
from podex.database import SessionLocal
from podex.models import Episode, IngestionRun, Podcast, Transcript
from podex.services.podscripts_client import (
    BASE_URL,
    build_podscripts_client,
    fetch_podscripts_html,
)
from podex.services.transcript_artifacts import (
    build_transcript_artifact_store,
    persist_transcript_acquisition,
)
from podex.services.transcript_source import (
    TranscriptAcquisitionResult,
    parse_podscripts_transcript_html,
)
from podex.services.whisper_transcriber import TranscriptResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def html_attr_to_str(value: object) -> str:
    """Return a BeautifulSoup attribute value when it is a single string."""
    return value if isinstance(value, str) else ""


def episode_link_title(*, href: str, link_text: str) -> tuple[int | None, str]:
    """Extract a stable title and optional number from an episode link."""
    slug = href.rstrip("/").split("/")[-1]
    comment_label = re.fullmatch(r"\d+\s*comments?", link_text, flags=re.IGNORECASE)
    title = link_text if link_text and comment_label is None else ""
    match = re.match(r"(\d+)-(.+)", slug)
    if match:
        return (
            int(match.group(1)),
            title or match.group(2).replace("-", " ").title(),
        )
    return None, title or slug.replace("-", " ").title()


# Common podcast slug mappings (podscripts slug -> our slug)
PODCAST_SLUG_MAP = {
    "the-joe-rogan-experience": "jre",
    "crime-junkie": "crime-junkie",
    "the-rest-is-history": "the-rest-is-history",
    "pbd-podcast": "pbd-podcast",
    "huberman-lab": "huberman-lab",
    "lex-fridman-podcast": "lex-fridman",
    "the-tim-ferriss-show": "tim-ferriss",
    "all-in-with-chamath-jason-sacks-friedberg": "all-in",
}


@dataclass
class ScrapedEpisode:
    """Scraped episode data from podscripts.co."""

    episode_number: int | None
    title: str
    url: str
    date: str | None = None
    transcript: str | None = None
    segments: list[dict[str, str | float]] | None = None


class PodscriptsScraper:
    """Scraper for podscripts.co podcasts.

    Args:
        podcast_slug: The podscripts.co slug for the podcast
                     (e.g., "the-joe-rogan-experience", "huberman-lab")
        delay: Delay between requests in seconds
    """

    def __init__(self, podcast_slug: str, delay: float = 1.0) -> None:
        self.podcast_slug = podcast_slug
        self.podcast_url = f"{BASE_URL}/podcasts/{podcast_slug}"
        self.delay = delay
        self.client = build_podscripts_client()

    def get_episode_list(self, page: int = 1) -> list[dict[str, str | int | None]]:
        """Get list of episodes from index page."""
        url = f"{self.podcast_url}?page={page}"
        logger.info(f"Fetching episode list page {page}...")

        html = fetch_podscripts_html(
            client=self.client,
            url=url,
            retry_delay_seconds=self.delay,
        )
        soup = BeautifulSoup(html, "html.parser")
        episodes = []

        # Find episode links - they're typically in a list format
        for link in soup.find_all("a", href=True):
            href = html_attr_to_str(link.get("href", ""))
            expected_prefix = f"/podcasts/{self.podcast_slug}/"
            if (
                href.startswith(expected_prefix)
                and href.rstrip("/") != f"/podcasts/{self.podcast_slug}"
            ):
                # Extract episode info from URL and link text
                slug = href.rstrip("/").split("/")[-1]
                link_text = link.get_text(strip=True)
                episode_num, title = episode_link_title(
                    href=href,
                    link_text=link_text,
                )

                episodes.append(
                    {
                        "episode_number": episode_num,
                        "title": title,
                        "url": f"{BASE_URL}{href}" if href.startswith("/") else href,
                        "slug": slug,
                    }
                )

        # Deduplicate by URL
        seen = set()
        unique = []
        for ep in episodes:
            if ep["url"] not in seen:
                seen.add(ep["url"])
                unique.append(ep)

        time.sleep(self.delay)
        return unique

    def get_episode_transcript(
        self, url: str
    ) -> dict[str, str | list[dict[str, float | str]] | None]:
        """Get transcript and metadata for a single episode."""
        logger.info(f"Fetching transcript: {url}")

        html = fetch_podscripts_html(
            client=self.client,
            url=url,
            retry_delay_seconds=self.delay,
        )
        parsed = parse_podscripts_transcript_html(html)
        time.sleep(self.delay)
        if parsed is None:
            raise ValueError("No valid timestamped transcript content found on page")
        result: dict[str, str | list[dict[str, float | str]] | None] = {
            "date": parsed.published_date,
            "transcript": parsed.raw_text,
            "segments": parsed.segments,
        }
        return result

    def scrape_episodes(
        self,
        episode_min: int | None = None,
        episode_max: int | None = None,
        limit: int | None = None,
        exclude_urls: set[str] | None = None,
    ) -> list[ScrapedEpisode]:
        """Scrape episodes within a range."""
        all_episodes: list[dict[str, str | int | None]] = []
        excluded = exclude_urls or set()

        # Fetch pages until we have what we need
        page = 1
        max_pages = 150  # Safety limit

        while page <= max_pages:
            episodes = self.get_episode_list(page)
            if not episodes:
                break

            for ep in episodes:
                num = ep.get("episode_number")
                if ep.get("url") in excluded:
                    continue

                # Filter by range (only if episode has a number)
                if num is not None and isinstance(num, int):
                    if episode_min and num < episode_min:
                        continue
                    if episode_max and num > episode_max:
                        continue

                all_episodes.append(ep)

                if limit and len(all_episodes) >= limit:
                    break

            # Check if we should continue
            if limit and len(all_episodes) >= limit:
                break

            # Check if we've passed our range (only for numbered episodes)
            numbered_episodes = [
                e
                for e in episodes
                if e.get("episode_number") is not None
                and isinstance(e.get("episode_number"), int)
            ]
            if numbered_episodes and episode_min:
                min_on_page = min(
                    int(e["episode_number"])
                    for e in numbered_episodes
                    if e["episode_number"] is not None
                )
                if min_on_page < episode_min:
                    break

            page += 1

        # Sort by episode number (unnumbered episodes go last)
        def sort_key(x: dict[str, str | int | None]) -> int:
            num = x.get("episode_number")
            return num if isinstance(num, int) else 999999

        all_episodes.sort(key=sort_key)

        # Fetch transcripts
        results: list[ScrapedEpisode] = []
        for i, ep in enumerate(all_episodes, 1):
            ep_num = ep.get("episode_number")
            ep_title_raw = ep.get("title", "Unknown")
            ep_title = str(ep_title_raw) if ep_title_raw is not None else "Unknown"
            logger.info(
                f"[{i}/{len(all_episodes)}] "
                + (f"Episode {ep_num}: " if ep_num else "")
                + f"{ep_title}"
            )

            try:
                ep_url = ep.get("url")
                if ep_url is None:
                    logger.error("  No URL found for episode")
                    continue
                transcript_data = self.get_episode_transcript(str(ep_url))

                # Extract values with proper typing
                date_val = transcript_data.get("date")
                date_str = str(date_val) if isinstance(date_val, str) else None

                transcript_val = transcript_data.get("transcript")
                transcript_str = (
                    str(transcript_val) if isinstance(transcript_val, str) else None
                )

                segments_val = transcript_data.get("segments")
                segments_list = segments_val if isinstance(segments_val, list) else None

                episode_number = int(ep_num) if isinstance(ep_num, int) else None

                results.append(
                    ScrapedEpisode(
                        episode_number=episode_number,
                        title=ep_title,
                        url=str(ep_url),
                        date=date_str,
                        transcript=transcript_str,
                        segments=segments_list,
                    )
                )
            except Exception as e:
                logger.error(f"  Failed: {e}")

        return results

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> PodscriptsScraper:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def main() -> None:
    """Scrape and store transcripts from podscripts.co."""
    parser = argparse.ArgumentParser(
        description="Scrape transcripts from podscripts.co",
    )
    parser.add_argument(
        "--podcast-slug",
        default=os.getenv("SCRAPE_PODCAST_SLUG", "the-joe-rogan-experience"),
        help="Podscripts.co podcast slug (default: the-joe-rogan-experience)",
    )
    parser.add_argument(
        "--episode-min",
        type=int,
        default=None,
        help="Minimum episode number to scrape",
    )
    parser.add_argument(
        "--episode-max",
        type=int,
        default=None,
        help="Maximum episode number to scrape",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of episodes to scrape",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="Delay between provider requests in seconds (default: 1.5)",
    )
    parser.add_argument(
        "--store-db",
        action="store_true",
        default=False,
        help="Store transcripts to database",
    )
    parser.add_argument(
        "--skip-stored",
        action="store_true",
        default=False,
        help="Skip provider URLs that already have stored Podscripts transcripts",
    )
    parser.add_argument(
        "--export-json",
        action="store_true",
        default=False,
        help="Write plaintext scraped transcripts to backend/data/scraped for inspection",
    )
    parser.add_argument(
        "--our-slug",
        default=None,
        help="Our podcast slug (for database lookup). Auto-mapped if not provided.",
    )

    args = parser.parse_args()

    # Override with environment variables if set
    episode_min_env = os.getenv("SCRAPE_EPISODE_MIN", "").strip()
    episode_min = int(episode_min_env) if episode_min_env else args.episode_min

    episode_max_env = os.getenv("SCRAPE_EPISODE_MAX", "").strip()
    episode_max = int(episode_max_env) if episode_max_env else args.episode_max

    limit_env = os.getenv("SCRAPE_LIMIT", "").strip()
    limit = int(limit_env) if limit_env else args.limit

    store_db = (
        os.getenv("SCRAPE_STORE_DB", "").lower() in ("true", "1", "yes")
        or args.store_db
    )

    podcast_slug = args.podcast_slug
    our_slug = args.our_slug or PODCAST_SLUG_MAP.get(podcast_slug, podcast_slug)

    artifact_store = None
    excluded_urls: set[str] = set()
    if store_db:
        artifact_store = build_transcript_artifact_store(settings=get_settings())
        if artifact_store is None:
            parser.error(
                "--store-db requires TRANSCRIPT_ARTIFACT_ENCRYPTION_KEY before fetching"
            )
        with SessionLocal() as session:
            podcast_exists = (
                session.query(Podcast).filter(Podcast.slug == our_slug).first()
                is not None
            )
        if not podcast_exists:
            parser.error(
                f"--store-db requires podcast '{our_slug}' in the database before fetching"
            )
        if args.skip_stored:
            with SessionLocal() as session:
                excluded_urls = {
                    episode_url
                    for (episode_url,) in (
                        session.query(Episode.episode_url)
                        .join(Transcript, Transcript.episode_id == Episode.id)
                        .join(Podcast, Podcast.id == Episode.podcast_id)
                        .filter(Podcast.slug == our_slug)
                        .filter(Transcript.provider == "podscripts.co")
                        .filter(Episode.episode_url.isnot(None))
                        .all()
                    )
                    if episode_url is not None
                }

    logger.info(f"Scraping podcast: {podcast_slug}")
    logger.info(f"Episode range: min={episode_min}, max={episode_max}, limit={limit}")
    logger.info(f"Store to database: {store_db} (as '{our_slug}')")

    with PodscriptsScraper(podcast_slug=podcast_slug, delay=args.delay) as scraper:
        episodes = scraper.scrape_episodes(
            episode_min=episode_min,
            episode_max=episode_max,
            limit=limit,
            exclude_urls=excluded_urls,
        )

    logger.info(f"Scraped {len(episodes)} episodes")

    output_file: Path | None = None
    if args.export_json:
        output_dir = Path(__file__).parent.parent / "data" / "scraped"
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_slug = podcast_slug.replace("-", "_")
        output_file = output_dir / f"podscripts_{safe_slug}.json"
        data = [
            {
                "episode_number": ep.episode_number,
                "title": ep.title,
                "url": ep.url,
                "date": ep.date,
                "transcript_length": len(ep.transcript) if ep.transcript else 0,
                "segment_count": len(ep.segments) if ep.segments else 0,
                "transcript": ep.transcript,
                "segments": ep.segments,
            }
            for ep in episodes
        ]

        with open(output_file, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved plaintext inspection export to {output_file}")

    # Optionally store in database
    if store_db:
        with SessionLocal() as session:
            run = IngestionRun(status="running")
            session.add(run)
            session.commit()

            try:
                # Get the podcast by our slug
                podcast = (
                    session.query(Podcast).filter(Podcast.slug == our_slug).first()
                )
                if not podcast:
                    logger.error(f"Podcast '{our_slug}' not found in database")
                    run.status = "failed"
                    run.error_summary = "Podcast not found"
                    run.completed_at = datetime.now(UTC)
                    session.commit()
                    return

                stored = 0
                skipped = 0

                for ep in episodes:
                    # Prefer the provider URL so unnumbered episodes remain idempotent.
                    episode = (
                        session.query(Episode)
                        .filter(Episode.podcast_id == podcast.id)
                        .filter(Episode.episode_url == ep.url)
                        .first()
                    )
                    if not episode and ep.episode_number:
                        episode = (
                            session.query(Episode)
                            .filter(Episode.podcast_id == podcast.id)
                            .filter(Episode.episode_number == ep.episode_number)
                            .first()
                        )

                    if not episode:
                        episode = Episode(
                            podcast_id=podcast.id,
                            title=ep.title,
                            episode_number=ep.episode_number,
                            episode_url=ep.url,
                            discovery_source="podscripts",
                        )
                        session.add(episode)
                        session.flush()
                    elif episode.title != ep.title:
                        episode.title = ep.title

                    # Check if we already have a podscripts transcript
                    existing = (
                        session.query(Transcript)
                        .filter(Transcript.episode_id == episode.id)
                        .filter(Transcript.provider == "podscripts.co")
                        .first()
                    )

                    if existing:
                        skipped += 1
                        continue

                    persist_transcript_acquisition(
                        db=session,
                        episode=episode,
                        acquisition=TranscriptAcquisitionResult(
                            success=True,
                            result=TranscriptResult(
                                provider="podscripts.co",
                                raw_text=ep.transcript or "",
                                segments=ep.segments or [],
                                fetched_at=datetime.now(UTC),
                            ),
                            source="podscripts.co",
                        ),
                        artifact_store=artifact_store,
                    )
                    episode.transcript_status = "completed"
                    session.commit()
                    stored += 1
                    logger.info(f"  Stored episode {ep.episode_number or ep.title}")

                run.status = "completed"
                run.completed_at = datetime.now(UTC)
                session.commit()

                logger.info(f"Stored {stored} transcripts, skipped {skipped}")

            except Exception as e:
                logger.exception("Error storing transcripts")
                session.rollback()
                run.status = "failed"
                run.error_summary = str(e)
                run.completed_at = datetime.now(UTC)
                session.commit()

    # Print summary
    print("\n" + "=" * 50)
    print("SCRAPE COMPLETE")
    print("=" * 50)
    print(f"  Podcast:          {podcast_slug}")
    print(f"  Episodes scraped: {len(episodes)}")
    if output_file is not None:
        print(f"  Output file:      {output_file}")
    if episodes:
        avg_len = sum(len(ep.transcript or "") for ep in episodes) / len(episodes)
        print(f"  Avg transcript:   {avg_len:.0f} chars")
    print("=" * 50)


if __name__ == "__main__":
    main()
