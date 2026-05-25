"""Application configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Podex"
    debug: bool = False

    database_url: str = "sqlite:///./podex.db"
    transcript_artifact_storage_backend: str = "encrypted_filesystem"
    transcript_artifact_storage_path: Path = Path("./data/transcript-artifacts")
    transcript_artifact_encryption_key: str = ""
    transcript_artifact_s3_bucket: str = ""
    transcript_artifact_s3_endpoint_url: str = ""
    transcript_artifact_s3_region_name: str = ""
    transcript_artifact_s3_access_key_id: str = ""
    transcript_artifact_s3_secret_access_key: str = ""

    api_v2_prefix: str = "/api/v2"

    cors_origins: list[str] = ["http://localhost:4321", "http://localhost:3000"]

    youtube_api_key: str = ""
    youtube_channel_id: str = ""

    # API Key authentication (empty string = disabled)
    api_key: str = ""

    # Rate limiting (requests per minute per IP)
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 100
    public_search_rate_limit_per_minute: int = 30
    auth_rate_limit_per_minute: int = 10
    ops_rate_limit_per_minute: int = 60
    admin_rate_limit_per_minute: int = 30

    public_web_url: str = "http://localhost:4321"
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
    paid_tier_enabled: bool = False
    paid_tier_enforced: bool = False
    paid_api_requests_per_month: int = 500
    paid_llm_requests_per_month: int = 25
    billing_provider_name: str = ""
    billing_checkout_url: str = ""
    ops_review_pending_alert_threshold: int = 50
    ops_projection_pending_alert_threshold: int = 25
    ops_projection_oldest_pending_minutes: int = 60
    ops_alert_delivery_pending_threshold: int = 25

    # Public discovery read-model caching
    stats_cache_ttl_seconds: int = 300

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
