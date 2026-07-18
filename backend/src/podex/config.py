"""Application configuration loaded from the environment."""

from functools import lru_cache

from pydantic import Field
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
    # The limiter is process-local for now; follow-up #107 moves it to a
    # shared store so limits hold across workers.
    rate_limit_enabled: bool = True
    rate_limit_max_requests: int = 120
    rate_limit_window_seconds: float = 60.0
    rate_limit_exempt_paths: list[str] = ["/health"]

    # Aggregate/stats caching. Short TTL because we currently have no
    # write-time invalidation hooks (see ``services/stats_queries.py``);
    # setting the TTL to ``0`` disables caching entirely. ``allow_inf_nan``
    # rejects ``NaN`` (which would silently bypass caching after mypy-safe
    # comparisons) and ``inf`` (which would keep entries alive forever); the
    # ``ge=0`` bound catches negative overrides at startup instead of allowing
    # a misconfigured value to disable caching in a surprising way.
    stats_cache_ttl_seconds: float = Field(default=30.0, ge=0, allow_inf_nan=False)


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
