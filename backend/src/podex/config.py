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
    database_url: str = "sqlite:///./podex.db"

    # Rate limiting. Defaults are deliberately generous so ordinary traffic
    # (and the test suite) never trips the limiter; tests inject tight values.
    # When ``rate_limit_redis_url`` is unset the limiter runs process-locally;
    # setting it (e.g. ``redis://redis:6379/0``) switches to a shared Redis
    # store so limits hold across workers/instances.
    rate_limit_enabled: bool = True
    rate_limit_max_requests: int = 120
    rate_limit_window_seconds: float = 60.0
    rate_limit_exempt_paths: list[str] = ["/health"]
    rate_limit_redis_url: str = ""
    rate_limit_redis_prefix: str = "podex:ratelimit"


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
