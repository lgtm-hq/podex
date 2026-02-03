"""Podscripts.co discovery service.

Discovers podcasts and episodes from podscripts.co, which provides
pre-existing transcripts for popular podcasts.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup

from podex.services.discovery import (
    DiscoveredEpisode,
    DiscoveredPodcast,
)

if TYPE_CHECKING:
    from podex.models import Podcast

logger = logging.getLogger(__name__)

BASE_URL = "https://podscripts.co"


class PodscriptsDiscovery:
    """Discover podcasts and episodes from podscripts.co.

    Args:
        delay: Delay between requests in seconds (be nice to the server)
    """

    def __init__(self, delay: float = 1.0) -> None:
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

    @property
    def name(self) -> str:
        return "podscripts"

    @property
    def is_configured(self) -> bool:
        """Podscripts discovery is always configured (no API keys needed)."""
        return True

    def discover_podcast(self, slug: str) -> DiscoveredPodcast | None:
        """Discover podcast metadata from podscripts.co.

        Args:
            slug: The podscripts.co slug (e.g., "the-joe-rogan-experience")

        Returns:
            DiscoveredPodcast with metadata, or None if not found
        """
        url = f"{BASE_URL}/podcasts/{slug}"

        try:
            response = self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch podcast {slug}: {e}")
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # Extract podcast name from title or h1
        name = None
        title = soup.find("title")
        if title:
            name = title.get_text(strip=True).split(" - ")[0].split(" | ")[0]

        h1 = soup.find("h1")
        if h1:
            name = h1.get_text(strip=True)

        if not name:
            name = slug.replace("-", " ").title()

        # Extract description
        description = None
        desc_meta = soup.find("meta", attrs={"name": "description"})
        if desc_meta:
            description = desc_meta.get("content")

        # Extract cover image
        cover_url = None
        og_image = soup.find("meta", property="og:image")
        if og_image:
            cover_url = og_image.get("content")

        time.sleep(self.delay)

        return DiscoveredPodcast(
            name=name,
            slug=slug,
            description=description,
            cover_url=cover_url,
            podscripts_slug=slug,
            discovery_source=self.name,
        )

    def discover_episodes(
        self,
        podcast: Podcast,
        since: datetime | None = None,
    ) -> list[DiscoveredEpisode]:
        """Discover episodes for a podcast from podscripts.co.

        All discovered episodes have has_transcript=True since
        podscripts.co only lists episodes with transcripts.

        Args:
            podcast: The podcast to discover episodes for
            since: Only discover episodes published after this date

        Returns:
            List of discovered episodes
        """
        if not podcast.podscripts_slug:
            logger.warning(f"Podcast {podcast.slug} has no podscripts_slug")
            return []

        episodes = []
        page = 1
        max_pages = 200  # Safety limit

        while page <= max_pages:
            page_episodes = self._fetch_episode_page(podcast.podscripts_slug, page)

            if not page_episodes:
                break

            # Filter by date if specified
            if since:
                # Since we don't have exact dates from the list page,
                # we'll include all episodes and let the caller filter
                pass

            episodes.extend(page_episodes)
            page += 1

        logger.info(f"Discovered {len(episodes)} episodes from podscripts.co")
        return episodes

    def _fetch_episode_page(
        self,
        slug: str,
        page: int,
    ) -> list[DiscoveredEpisode]:
        """Fetch a single page of episodes."""
        url = f"{BASE_URL}/podcasts/{slug}?page={page}"
        logger.debug(f"Fetching page {page}: {url}")

        try:
            response = self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch page {page}: {e}")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        episodes = []

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            expected_prefix = f"/podcasts/{slug}/"

            if expected_prefix in href and href != f"/podcasts/{slug}":
                episode = self._parse_episode_link(link, href, slug)
                if episode:
                    episodes.append(episode)

        # Deduplicate by URL
        seen = set()
        unique = []
        for ep in episodes:
            if ep.episode_url not in seen:
                seen.add(ep.episode_url)
                unique.append(ep)

        time.sleep(self.delay)
        return unique

    def _parse_episode_link(
        self,
        link,
        href: str,
        podcast_slug: str,
    ) -> DiscoveredEpisode | None:
        """Parse an episode from a link element."""
        slug = href.split("/")[-1]
        link_text = link.get_text(strip=True)

        # Try to extract episode number from slug
        episode_number = None
        title = link_text or slug.replace("-", " ").title()

        match = re.match(r"(\d+)-(.+)", slug)
        if match:
            episode_number = int(match.group(1))
            if not link_text:
                title = match.group(2).replace("-", " ").title()

        episode_url = f"{BASE_URL}{href}" if href.startswith("/") else href

        return DiscoveredEpisode(
            title=title,
            episode_number=episode_number,
            episode_url=episode_url,
            has_transcript=True,  # podscripts.co always has transcripts
            discovery_source=self.name,
        )

    def list_available_podcasts(self) -> list[dict]:
        """List all podcasts available on podscripts.co.

        Returns:
            List of dicts with podcast info (slug, name, episode_count)
        """
        podcasts = []
        url = f"{BASE_URL}/podcasts"

        try:
            response = self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch podcast catalog: {e}")
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")

            if href.startswith("/podcasts/") and href != "/podcasts":
                slug = href.replace("/podcasts/", "").strip("/")

                if slug and "/" not in slug:
                    name = link.get_text(strip=True)
                    if not name:
                        name = slug.replace("-", " ").title()

                    podcasts.append(
                        {
                            "slug": slug,
                            "name": name,
                            "url": f"{BASE_URL}{href}",
                        }
                    )

        # Deduplicate
        seen = set()
        unique = []
        for p in podcasts:
            if p["slug"] not in seen:
                seen.add(p["slug"])
                unique.append(p)

        logger.info(f"Found {len(unique)} podcasts on podscripts.co")
        return unique

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> PodscriptsDiscovery:
        return self

    def __exit__(self, *args) -> None:
        self.close()
