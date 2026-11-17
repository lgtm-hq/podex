"""Podscripts.co discovery service.

Discovers podcasts and episodes from podscripts.co, which provides
pre-existing transcripts for popular podcasts.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup, Tag

from podex.services.discovery import (
    DiscoveredEpisode,
    DiscoveredPodcast,
    KnownEpisodes,
)
from podex.services.failure_descriptors import describe_failure, log_failure
from podex.services.podscripts_client import (
    BASE_URL,
    build_podscripts_client,
    fetch_podscripts_html,
)

if TYPE_CHECKING:
    from podex.models import Podcast


logger = logging.getLogger(__name__)

MAX_LISTING_PAGES = 200
"""Most listing pages one discovery run requests for one podcast."""


def _html_attr_to_str(value: object) -> str:
    """Return a BeautifulSoup attribute value when it is a single string."""
    return value if isinstance(value, str) else ""


def _episode_link_title(*, href: str, link_text: str) -> tuple[int | None, str]:
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


@dataclass(slots=True)
class _ListingOrder:
    """Track whether the episode numbers read so far run newest first.

    Listing pages carry no publication dates, so the episode numbers in the
    links are the only evidence of order. The order counts as newest first
    only once numbers have been seen falling and never rising.
    """

    previous: int | None = None
    falling: bool = False
    rising: bool = False

    def observe(self, episodes: Iterable[DiscoveredEpisode]) -> None:
        """Fold the numbers of ``episodes``, in listing order, into the evidence."""
        for episode in episodes:
            number = episode.episode_number
            if number is None:
                continue
            if self.previous is not None and number < self.previous:
                self.falling = True
            elif self.previous is not None and number > self.previous:
                self.rising = True
            self.previous = number

    @property
    def newest_first(self) -> bool:
        """Return whether the listing is shown to be newest first."""
        return self.falling and not self.rising


class PodscriptsDiscovery:
    """Discover podcasts and episodes from podscripts.co.

    Args:
        delay: Delay between requests in seconds (be nice to the server)
    """

    def __init__(self, delay: float = 1.0) -> None:
        self.delay = delay
        self.client = build_podscripts_client()

    @property
    def name(self) -> str:
        """Return the discovery provider identifier."""
        return "podscripts"

    @property
    def is_configured(self) -> bool:
        """Podscripts discovery is always configured (no API keys needed)."""
        return True

    def discover_podcast(self, slug: str) -> DiscoveredPodcast | None:
        """Discover podcast metadata from podscripts.co.

        Args:
            slug: The podscripts.co slug (e.g., "example-show")

        Returns:
            DiscoveredPodcast with metadata, or None if not found
        """
        url = f"{BASE_URL}/podcasts/{slug}"

        try:
            html = fetch_podscripts_html(
                client=self.client,
                url=url,
                retry_delay_seconds=self.delay,
            )
        except httpx.HTTPError as e:
            log_failure(
                logger,
                describe_failure("podscripts", e),
                e,
                context=("podcast_fetch", slug),
            )
            return None

        soup = BeautifulSoup(html, "html.parser")

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
            description = _html_attr_to_str(desc_meta.get("content"))

        # Extract cover image
        cover_url = None
        og_image = soup.find("meta", property="og:image")
        if og_image:
            cover_url = _html_attr_to_str(og_image.get("content"))

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
        *,
        known: KnownEpisodes | None = None,
    ) -> list[DiscoveredEpisode]:
        """Discover episodes for a podcast from podscripts.co.

        All discovered episodes have has_transcript=True since
        podscripts.co only lists episodes with transcripts.

        Listing pages carry no publication dates, so this provider cannot
        apply ``since`` itself; the caller applies it once a date is known.
        Paging instead stops at the first page whose episodes are all
        ``known``, provided the listing is shown to be newest first. When the
        order cannot be shown, every page is read, up to
        ``MAX_LISTING_PAGES``.

        Args:
            podcast: The podcast to discover episodes for
            since: Only discover episodes published after this date. Unused
                here: listing pages carry no dates.
            known: Episodes the catalog already holds for this podcast.

        Returns:
            List of discovered episodes
        """
        del since  # Listing pages carry no dates; the caller filters by date.
        if not podcast.podscripts_slug:
            logger.warning("Podcast %s has no podscripts_slug", podcast.slug)
            return []

        if known is None:
            known = KnownEpisodes()
        order = _ListingOrder()
        episodes: list[DiscoveredEpisode] = []

        for page in range(1, MAX_LISTING_PAGES + 1):
            page_episodes = self._fetch_episode_page(podcast.podscripts_slug, page)

            if not page_episodes:
                break

            episodes.extend(page_episodes)
            order.observe(page_episodes)

            if known.is_empty or not all(map(known.includes, page_episodes)):
                continue
            if order.newest_first:
                logger.info(
                    "Stopped podscripts.co listing for %s at page %s: "
                    "every episode on it is already known",
                    podcast.podscripts_slug,
                    page,
                )
                break
        else:
            logger.warning(
                "Stopped podscripts.co listing for %s at the %s-page limit "
                "before the listing ended",
                podcast.podscripts_slug,
                MAX_LISTING_PAGES,
            )

        if not known.is_empty and not order.newest_first:
            logger.info(
                "Read podscripts.co listing for %s without stopping early: "
                "its order is not shown to be newest first",
                podcast.podscripts_slug,
            )

        logger.info("Discovered %s episodes from podscripts.co", len(episodes))
        return episodes

    def _fetch_episode_page(
        self,
        slug: str,
        page: int,
    ) -> list[DiscoveredEpisode]:
        """Fetch a single page of episodes."""
        url = f"{BASE_URL}/podcasts/{slug}?page={page}"
        logger.debug("Fetching page %s: %s", page, url)

        try:
            html = fetch_podscripts_html(
                client=self.client,
                url=url,
                retry_delay_seconds=self.delay,
            )
        except httpx.HTTPError as e:
            log_failure(
                logger,
                describe_failure("podscripts", e),
                e,
                context=("episode_page", slug, page),
            )
            return []

        soup = BeautifulSoup(html, "html.parser")
        episodes = []

        for link in soup.find_all("a", href=True):
            href = _html_attr_to_str(link.get("href", ""))
            expected_prefix = f"/podcasts/{slug}/"

            if (
                href.startswith(expected_prefix)
                and href.rstrip("/") != f"/podcasts/{slug}"
            ):
                episode = self._parse_episode_link(link, href)
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
        link: Tag,
        href: str,
    ) -> DiscoveredEpisode | None:
        """Parse an episode from a link element."""
        link_text = link.get_text(strip=True)
        episode_number, title = _episode_link_title(
            href=href,
            link_text=link_text,
        )

        episode_url = f"{BASE_URL}{href}" if href.startswith("/") else href

        return DiscoveredEpisode(
            title=title,
            episode_number=episode_number,
            episode_url=episode_url,
            has_transcript=True,  # podscripts.co always has transcripts
            discovery_source=self.name,
        )

    def list_available_podcasts(self) -> list[dict[str, str]]:
        """List all podcasts available on podscripts.co.

        Returns:
            List of dicts with podcast info (slug, name, episode_count)
        """
        podcasts = []
        url = f"{BASE_URL}/podcasts"

        try:
            html = fetch_podscripts_html(
                client=self.client,
                url=url,
                retry_delay_seconds=self.delay,
            )
        except httpx.HTTPError as e:
            log_failure(
                logger,
                describe_failure("podscripts", e),
                e,
                level=logging.ERROR,
                context=("catalog_fetch",),
            )
            return []

        soup = BeautifulSoup(html, "html.parser")

        for link in soup.find_all("a", href=True):
            href = _html_attr_to_str(link.get("href", ""))

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

        logger.info("Found %s podcasts on podscripts.co", len(unique))
        return unique

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> PodscriptsDiscovery:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
