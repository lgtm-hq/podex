"""Podcast configuration loader.

Loads podcast configuration from a YAML file for curated podcast management.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PodcastSourceConfig:
    """Configuration for a podcast's discovery sources."""

    podscripts: str | None = None
    youtube_channel: str | None = None
    rss: str | None = None
    spotify: str | None = None
    apple: str | None = None


@dataclass
class PodcastConfig:
    """Configuration for a single podcast."""

    slug: str
    name: str
    status: str = "watchlist"  # watchlist, active, paused
    sources: PodcastSourceConfig = field(default_factory=PodcastSourceConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PodcastConfig:
        """Create from a dictionary."""
        sources_data = data.get("sources", {})
        sources = PodcastSourceConfig(
            podscripts=sources_data.get("podscripts"),
            youtube_channel=sources_data.get("youtube_channel"),
            rss=sources_data.get("rss"),
            spotify=sources_data.get("spotify"),
            apple=sources_data.get("apple"),
        )

        return cls(
            slug=data["slug"],
            name=data["name"],
            status=data.get("status", "watchlist"),
            sources=sources,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dictionary for YAML export."""
        sources_dict = {}
        if self.sources.podscripts:
            sources_dict["podscripts"] = self.sources.podscripts
        if self.sources.youtube_channel:
            sources_dict["youtube_channel"] = self.sources.youtube_channel
        if self.sources.rss:
            sources_dict["rss"] = self.sources.rss
        if self.sources.spotify:
            sources_dict["spotify"] = self.sources.spotify
        if self.sources.apple:
            sources_dict["apple"] = self.sources.apple

        result: dict[str, Any] = {
            "slug": self.slug,
            "name": self.name,
            "status": self.status,
        }
        if sources_dict:
            result["sources"] = sources_dict

        return result


@dataclass
class PodcastsConfig:
    """Configuration for all tracked podcasts."""

    podcasts: list[PodcastConfig] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> PodcastsConfig:
        """Load configuration from a YAML file."""
        if not path.exists():
            logger.warning(f"Config file not found: {path}")
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        podcasts = [PodcastConfig.from_dict(p) for p in data.get("podcasts", [])]

        return cls(podcasts=podcasts)

    def to_yaml(self, path: Path) -> None:
        """Save configuration to a YAML file."""
        data = {
            "podcasts": [p.to_dict() for p in self.podcasts],
        }

        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def get_by_slug(self, slug: str) -> PodcastConfig | None:
        """Get a podcast by slug."""
        for podcast in self.podcasts:
            if podcast.slug == slug:
                return podcast
        return None

    def get_active(self) -> list[PodcastConfig]:
        """Get all active podcasts."""
        return [p for p in self.podcasts if p.status == "active"]

    def get_watchlist(self) -> list[PodcastConfig]:
        """Get all podcasts on the watchlist."""
        return [p for p in self.podcasts if p.status == "watchlist"]

    def get_paused(self) -> list[PodcastConfig]:
        """Get all paused podcasts."""
        return [p for p in self.podcasts if p.status == "paused"]


class PodcastConfigManager:
    """Manages podcast configuration.

    Args:
        config_path: Path to the podcasts.yaml file.
                    Defaults to {project_root}/podcasts.yaml
    """

    DEFAULT_CONFIG_PATH = (
        Path(__file__).parent.parent.parent.parent.parent / "podcasts.yaml"
    )

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._config: PodcastsConfig | None = None

    @property
    def config(self) -> PodcastsConfig:
        """Get the current configuration, loading if necessary."""
        if self._config is None:
            self._config = PodcastsConfig.from_yaml(self.config_path)
        return self._config

    def reload(self) -> PodcastsConfig:
        """Reload configuration from disk."""
        self._config = PodcastsConfig.from_yaml(self.config_path)
        return self._config

    def save(self) -> None:
        """Save current configuration to disk."""
        if self._config:
            self._config.to_yaml(self.config_path)

    def add_podcast(
        self,
        slug: str,
        name: str,
        status: str = "watchlist",
        **sources: str | None,
    ) -> PodcastConfig:
        """Add a new podcast to the configuration.

        Args:
            slug: Unique identifier for the podcast
            name: Display name
            status: Initial status (watchlist, active, paused)
            **sources: Source identifiers (podscripts, rss, spotify, etc.)

        Returns:
            The created PodcastConfig
        """
        source_config = PodcastSourceConfig(
            podscripts=sources.get("podscripts"),
            youtube_channel=sources.get("youtube_channel"),
            rss=sources.get("rss"),
            spotify=sources.get("spotify"),
            apple=sources.get("apple"),
        )

        podcast = PodcastConfig(
            slug=slug,
            name=name,
            status=status,
            sources=source_config,
        )

        self.config.podcasts.append(podcast)
        return podcast

    def set_status(self, slug: str, status: str) -> bool:
        """Set the status of a podcast.

        Args:
            slug: Podcast slug
            status: New status (watchlist, active, paused)

        Returns:
            True if the podcast was found and updated
        """
        podcast = self.config.get_by_slug(slug)
        if podcast:
            podcast.status = status
            return True
        return False

    def activate(self, slug: str) -> bool:
        """Activate a podcast for processing."""
        return self.set_status(slug, "active")

    def pause(self, slug: str) -> bool:
        """Pause a podcast."""
        return self.set_status(slug, "paused")

    def watchlist(self, slug: str) -> bool:
        """Move a podcast to the watchlist."""
        return self.set_status(slug, "watchlist")
