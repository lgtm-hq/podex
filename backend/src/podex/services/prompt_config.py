"""JSON-backed prompt configuration for transcription accuracy."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class GuestContext(BaseModel):
    """Context about a guest for improved transcription."""

    name: str
    background: str | None = None
    profession: str | None = None
    terminology: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)


class EpisodeConfig(BaseModel):
    """Episode-specific configuration."""

    guest: str | None = None
    guests: list[str] | None = None
    guest_context: GuestContext | None = None
    custom_prompt: str | None = None
    terminology: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    notes: str | None = None


class PodcastConfig(BaseModel):
    """Podcast-level configuration."""

    name: str
    slug: str
    host: str
    co_hosts: list[str] = Field(default_factory=list)
    common_terminology: list[str] = Field(default_factory=list)
    common_topics: list[str] = Field(default_factory=list)
    default_prompt: str | None = None


class PromptConfig(BaseModel):
    """Full prompt configuration for a podcast."""

    podcast: PodcastConfig
    episodes: dict[str, EpisodeConfig] = Field(default_factory=dict)
    initial_prompts: dict[str, str] = Field(default_factory=dict)

    def get_episode_config(self, episode_number: int | None) -> EpisodeConfig | None:
        """Get configuration for a specific episode number."""
        if episode_number is None:
            return None
        return self.episodes.get(str(episode_number))

    def get_prompt_for_episode(self, episode_number: int | None) -> str:
        """Build the initial prompt for a specific episode."""
        parts = []

        # Base podcast info
        parts.append(f"Podcast: {self.podcast.name}")
        parts.append(f"Host: {self.podcast.host}")
        if self.podcast.co_hosts:
            parts.append(f"Co-hosts: {', '.join(self.podcast.co_hosts)}")

        # Episode-specific info
        ep_config = self.get_episode_config(episode_number)
        if ep_config:
            # Custom prompt overrides everything
            if ep_config.custom_prompt:
                return ep_config.custom_prompt

            # Add guest info
            if ep_config.guest:
                parts.append(f"Guest: {ep_config.guest}")
            elif ep_config.guests:
                parts.append(f"Guests: {', '.join(ep_config.guests)}")

            # Add guest context
            if ep_config.guest_context:
                gc = ep_config.guest_context
                if gc.profession:
                    parts.append(f"Guest profession: {gc.profession}")
                if gc.background:
                    parts.append(f"Guest background: {gc.background}")
                if gc.terminology:
                    parts.append(f"Key terms: {', '.join(gc.terminology[:15])}")
                if gc.topics:
                    parts.append(f"Topics: {', '.join(gc.topics[:10])}")

            # Add episode-specific terminology
            if ep_config.terminology:
                parts.append(f"Episode terms: {', '.join(ep_config.terminology[:10])}")

        # Common terminology (always include some)
        if self.podcast.common_terminology:
            terms = self.podcast.common_terminology[:20]
            parts.append(f"Common terms: {', '.join(terms)}")

        return ". ".join(parts) + "."

    @classmethod
    def load_from_file(cls, path: Path) -> PromptConfig:
        """Load configuration from a JSON file."""
        with open(path) as f:
            data = json.load(f)
        return cls.model_validate(data)

    @classmethod
    def load_for_podcast(
        cls,
        slug: str,
        config_dir: Path | None = None,
    ) -> PromptConfig | None:
        """Load configuration for a podcast by slug."""
        if config_dir is None:
            config_dir = Path(__file__).parent.parent.parent.parent / "data" / "prompts"

        config_path = config_dir / f"{slug}.json"
        if not config_path.exists():
            logger.debug(f"No prompt config found for {slug} at {config_path}")
            return None

        try:
            return cls.load_from_file(config_path)
        except Exception as e:
            logger.warning(f"Failed to load prompt config for {slug}: {e}")
            return None


class PromptConfigManager:
    """Manages prompt configurations for multiple podcasts."""

    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            config_dir = Path(__file__).parent.parent.parent.parent / "data" / "prompts"
        self.config_dir = config_dir
        self._cache: dict[str, PromptConfig | None] = {}

    def get_config(self, podcast_slug: str) -> PromptConfig | None:
        """Get configuration for a podcast (cached)."""
        if podcast_slug not in self._cache:
            self._cache[podcast_slug] = PromptConfig.load_for_podcast(
                podcast_slug,
                self.config_dir,
            )
        return self._cache[podcast_slug]

    def get_prompt(
        self,
        podcast_slug: str,
        episode_number: int | None = None,
    ) -> str | None:
        """Get the initial prompt for a specific episode."""
        config = self.get_config(podcast_slug)
        if config is None:
            return None
        return config.get_prompt_for_episode(episode_number)

    def get_episode_config(
        self,
        podcast_slug: str,
        episode_number: int,
    ) -> EpisodeConfig | None:
        """Get episode configuration for context."""
        config = self.get_config(podcast_slug)
        if config is None:
            return None
        return config.get_episode_config(episode_number)

    def clear_cache(self) -> None:
        """Clear the configuration cache."""
        self._cache.clear()

    def list_configured_podcasts(self) -> list[str]:
        """List all podcasts that have configuration files."""
        if not self.config_dir.exists():
            return []
        return [p.stem for p in self.config_dir.glob("*.json") if p.is_file()]
