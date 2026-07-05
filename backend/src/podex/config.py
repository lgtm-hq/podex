"""Application configuration loaded from the environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, overridable via ``PODEX_`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="PODEX_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "podex"
    environment: str = "development"
    debug: bool = False
    api_v2_prefix: str = "/api/v2"
    cors_origins: list[str] = ["http://localhost:4321"]


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
