"""Application configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Pydantic plugin not available in linter Docker environment
class Settings(BaseSettings):  # type: ignore[misc]
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Podex"
    debug: bool = False

    database_url: str = "sqlite:///./podex.db"

    api_v1_prefix: str = "/api/v1"
    api_v2_prefix: str = "/api/v2"

    cors_origins: list[str] = ["http://localhost:4321", "http://localhost:3000"]

    youtube_api_key: str = ""
    youtube_channel_id: str = ""

    # API Key authentication (empty string = disabled)
    api_key: str = ""

    # Rate limiting (requests per minute per IP)
    rate_limit_per_minute: int = 100

    # Media enrichment API keys
    tmdb_api_key: str = ""
    omdb_api_key: str = ""
    google_books_api_key: str = ""

    # Meilisearch configuration
    meilisearch_url: str = "http://localhost:7700"
    meilisearch_master_key: str = ""
    meilisearch_enabled: bool = True

    @property
    def project_root(self) -> Path:
        """Get the project root directory."""
        return Path(__file__).parent.parent.parent.parent

    @property
    def api_key_enabled(self) -> bool:
        """Check if API key authentication is enabled."""
        return bool(self.api_key)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
