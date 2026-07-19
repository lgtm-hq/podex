"""Application configuration loaded from the environment."""

from functools import lru_cache
from pathlib import Path

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
    public_web_url: str = "http://localhost:4321"
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

    # Aggregate/stats caching. Short TTL because we currently have no
    # write-time invalidation hooks (see ``services/stats_queries.py``);
    # setting the TTL to ``0`` disables caching entirely. ``allow_inf_nan``
    # rejects ``NaN`` (which would silently bypass caching after mypy-safe
    # comparisons) and ``inf`` (which would keep entries alive forever); the
    # ``ge=0`` bound catches negative overrides at startup instead of allowing
    # a misconfigured value to disable caching in a surprising way.
    stats_cache_ttl_seconds: float = Field(default=30.0, ge=0, allow_inf_nan=False)

    # Encrypted raw transcript artifact storage. Raw transcripts are stored
    # only as encrypted objects; without an encryption key no artifact store
    # is built and raw storage is unavailable. Placeholder-free by default —
    # keys and buckets come from the deployment environment.
    transcript_artifact_storage_backend: str = "encrypted_filesystem"
    transcript_artifact_storage_path: Path = Path("./data/transcript-artifacts")
    transcript_artifact_encryption_key: str = ""
    transcript_artifact_s3_bucket: str = ""
    transcript_artifact_s3_endpoint_url: str = ""
    transcript_artifact_s3_region_name: str = ""
    transcript_artifact_s3_access_key_id: str = ""
    transcript_artifact_s3_secret_access_key: str = ""

    # Passwordless account authentication. Magic-link email delivery stays
    # disabled until SMTP settings are provided by the deployment
    # environment; defaults are placeholder-free.
    auth_magic_link_ttl_minutes: int = 15
    auth_session_ttl_days: int = 30
    auth_session_cookie_name: str = "podex_session"
    auth_session_cookie_secure: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_starttls: bool = True

    # Paid tier and billing. Both gates default off: ``paid_tier_enabled``
    # controls whether checkout can begin, ``paid_tier_enforced`` controls
    # whether personalization writes require entitlement. The checkout URL
    # and provider name come from the deployment environment.
    paid_tier_enabled: bool = False
    paid_tier_enforced: bool = False
    paid_api_requests_per_month: int = 500
    paid_llm_requests_per_month: int = 25
    billing_provider_name: str = ""
    billing_checkout_url: str = ""

    # Ops console API. The ops surface stays disabled until a key is
    # configured by the deployment environment; requests must present it in
    # the X-Ops-Key header.
    ops_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
