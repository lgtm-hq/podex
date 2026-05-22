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

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from podex.database import SessionLocal
from podex.models import Episode, IngestionRun, Podcast, Transcript

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://podscripts.co"


def html_attr_to_str(value: object) -> str:
    """Return a BeautifulSoup attribute value when it is a single string."""
    return value if isinstance(value, str) else ""


# Common podcast slug mappings (podscripts slug -> our slug)
PODCAST_SLUG_MAP = {
    "the-joe-rogan-experience": "jre",
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
    segments: list[dict[str, float | str]] | None = None


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

    def get_episode_list(self, page: int = 1) -> list[dict[str, str | int | None]]:
        """Get list of episodes from index page."""
        url = f"{self.podcast_url}?page={page}"
        logger.info(f"Fetching episode list page {page}...")

        response = self.client.get(url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        episodes = []

        # Find episode links - they're typically in a list format
        for link in soup.find_all("a", href=True):
            href = html_attr_to_str(link.get("href", ""))
            expected_prefix = f"/podcasts/{self.podcast_slug}/"
            if expected_prefix in href and href != f"/podcasts/{self.podcast_slug}":
                # Extract episode info from URL and link text
                slug = href.split("/")[-1]
                link_text = link.get_text(strip=True)

                # Try to extract episode number from slug
                episode_num = None
                title = link_text or slug.replace("-", " ").title()

                match = re.match(r"(\d+)-(.+)", slug)
                if match:
                    episode_num = int(match.group(1))
                    if not link_text:
                        title = match.group(2).replace("-", " ").title()

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

        response = self.client.get(url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Extract date if available
        date = None
        date_elem = soup.find("time") or soup.find(class_=re.compile(r"date|published"))
        if date_elem:
            date = date_elem.get_text(strip=True)

        # Extract transcript text
        # The transcript is usually in the main content area
        transcript_text = ""
        segments: list[dict[str, float | str]] = []

        # Look for transcript content - common patterns
        content_area = (
            soup.find("article")
            or soup.find(class_=re.compile(r"transcript|content|episode"))
            or soup.find("main")
        )

        if content_area:
            # Try to find timestamped segments
            # Pattern: [00:00:00] or (00:00:00) or just plain text
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
            transcript_text = " ".join(str(seg["text"]) for seg in segments)

            # If no segments found, just get all text
            if not segments:
                transcript_text = text

        time.sleep(self.delay)

        segments_result: list[dict[str, float | str]] | None = (
            segments if segments else None
        )
        result: dict[str, str | list[dict[str, float | str]] | None] = {
            "date": date,
            "transcript": transcript_text,
            "segments": segments_result,
        }
        return result

    def scrape_episodes(
        self,
        episode_min: int | None = None,
        episode_max: int | None = None,
        limit: int | None = None,
    ) -> list[ScrapedEpisode]:
        """Scrape episodes within a range."""
        all_episodes: list[dict[str, str | int | None]] = []

        # Fetch pages until we have what we need
        page = 1
        max_pages = 150  # Safety limit

        while page <= max_pages:
            episodes = self.get_episode_list(page)
            if not episodes:
                break

            for ep in episodes:
                num = ep.get("episode_number")

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
        "--store-db",
        action="store_true",
        default=False,
        help="Store transcripts to database",
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

    logger.info(f"Scraping podcast: {podcast_slug}")
    logger.info(f"Episode range: min={episode_min}, max={episode_max}, limit={limit}")
    logger.info(f"Store to database: {store_db} (as '{our_slug}')")

    with PodscriptsScraper(podcast_slug=podcast_slug, delay=1.5) as scraper:
        episodes = scraper.scrape_episodes(
            episode_min=episode_min,
            episode_max=episode_max,
            limit=limit,
        )

    logger.info(f"Scraped {len(episodes)} episodes")

    # Save to JSON for inspection
    output_dir = Path(__file__).parent.parent / "data" / "scraped"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Use podcast slug in filename
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

    logger.info(f"Saved to {output_file}")

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
                    # Find matching episode by number or title
                    episode = None
                    if ep.episode_number:
                        episode = (
                            session.query(Episode)
                            .filter(Episode.podcast_id == podcast.id)
                            .filter(Episode.episode_number == ep.episode_number)
                            .first()
                        )

                    if not episode:
                        logger.warning(
                            f"  Episode {ep.episode_number or ep.title} not in database, skipping"
                        )
                        skipped += 1
                        continue

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

                    # Store transcript
                    transcript = Transcript(
                        episode_id=episode.id,
                        provider="podscripts.co",
                        raw_text=ep.transcript,
                        segments_json=ep.segments,
                        fetched_at=datetime.now(UTC),
                    )
                    session.add(transcript)
                    session.commit()
                    stored += 1
                    logger.info(f"  Stored episode {ep.episode_number or ep.title}")

                run.status = "completed"
                run.completed_at = datetime.now(UTC)
                session.commit()

                logger.info(f"Stored {stored} transcripts, skipped {skipped}")

            except Exception as e:
                logger.exception("Error storing transcripts")
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
    print(f"  Output file:      {output_file}")
    if episodes:
        avg_len = sum(len(ep.transcript or "") for ep in episodes) / len(episodes)
        print(f"  Avg transcript:   {avg_len:.0f} chars")
    print("=" * 50)


if __name__ == "__main__":
    main()
