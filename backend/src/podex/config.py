"""Application configuration loaded from the environment."""

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    """Database URL and connection-pool tuning.

    Nested under ``settings.database``. Environment grammar:
    ``PODEX_DATABASE__URL``, ``PODEX_DATABASE__POOL_SIZE``, etc.
    Flat names such as ``PODEX_DATABASE_URL`` are not recognized.
    """

    url: str = "sqlite:///./podex.db"

    # Connection pooling for server-backed databases (Railway + Neon).
    # Applied only when ``url`` is not SQLite; the local SQLite default
    # keeps SQLAlchemy's stock pooling untouched. Defaults are sized for
    # a small Railway service talking to Neon's pooled endpoint: a modest
    # steady pool with equal burst headroom, recycled well inside typical
    # idle-connection cutoffs, and pre-ping so suspended/rotated backends
    # are detected instead of surfacing stale-connection errors.
    pool_size: int = Field(default=5, ge=1)
    max_overflow: int = Field(default=5, ge=0)
    pool_recycle_seconds: int = Field(default=300, ge=1)


class RateLimitSettings(BaseModel):
    """HTTP rate limiting and Redis store selection.

    Nested under ``settings.rate_limit``. Environment grammar:
    ``PODEX_RATE_LIMIT__ENABLED``, ``PODEX_RATE_LIMIT__REDIS_URL``, etc.
    """

    # Defaults are deliberately generous so ordinary traffic (and the test
    # suite) never trips the limiter; tests inject tight values. When
    # ``redis_url`` is unset the limiter runs process-locally; setting it
    # (e.g. ``redis://redis:6379/0``) switches to a shared Redis store so
    # limits hold across workers/instances.
    enabled: bool = True
    max_requests: int = 120
    window_seconds: float = 60.0
    exempt_paths: list[str] = ["/health"]
    redis_url: str = ""
    redis_prefix: str = "podex:ratelimit"


class StatsCacheSettings(BaseModel):
    """Aggregate/stats response caching.

    Nested under ``settings.stats_cache``. Environment grammar:
    ``PODEX_STATS_CACHE__TTL_SECONDS``.
    """

    # Short TTL because we currently have no write-time invalidation hooks
    # (see ``services/stats_queries.py``); setting the TTL to ``0`` disables
    # caching entirely. ``allow_inf_nan`` rejects ``NaN`` (which would
    # silently bypass caching after mypy-safe comparisons) and ``inf``
    # (which would keep entries alive forever); the ``ge=0`` bound catches
    # negative overrides at startup instead of allowing a misconfigured
    # value to disable caching in a surprising way.
    ttl_seconds: float = Field(default=30.0, ge=0, allow_inf_nan=False)


class TranscriptStorageSettings(BaseModel):
    """Encrypted raw transcript artifact storage (filesystem or S3/R2).

    Nested under ``settings.transcripts``. Environment grammar:
    ``PODEX_TRANSCRIPTS__STORAGE_BACKEND``,
    ``PODEX_TRANSCRIPTS__ENCRYPTION_KEY``, etc.
    """

    # Raw transcripts are stored only as encrypted objects; without an
    # encryption key no artifact store is built and raw storage is
    # unavailable. Placeholder-free by default — keys and buckets come
    # from the deployment environment.
    storage_backend: str = "encrypted_filesystem"
    storage_path: Path = Path("./data/transcript-artifacts")
    encryption_key: str = ""
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region_name: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""


class AuthSettings(BaseModel):
    """Passwordless auth (magic link + hosted WorkOS AuthKit).

    Nested under ``settings.auth``. Environment grammar:
    ``PODEX_AUTH__MAGIC_LINK_TTL_MINUTES``, ``PODEX_AUTH__WORKOS_CLIENT_ID``,
    ``PODEX_AUTH__SMTP_HOST``, etc.
    """

    # Magic-link email delivery stays disabled until SMTP settings are
    # provided by the deployment environment; defaults are placeholder-free.
    magic_link_ttl_minutes: int = 15
    session_ttl_days: int = 30
    session_cookie_name: str = "podex_session"
    session_cookie_secure: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_starttls: bool = True

    # Hosted sign-in via WorkOS AuthKit. All three values come from the
    # deployment environment; with any of them missing the feature stays
    # off and the SMTP magic-link flow remains the only sign-in path.
    workos_client_id: str = ""
    workos_api_key: str = ""
    workos_redirect_uri: str = ""

    @property
    def workos_enabled(self) -> bool:
        """Whether hosted WorkOS AuthKit sign-in is fully configured."""
        return bool(
            self.workos_client_id and self.workos_api_key and self.workos_redirect_uri,
        )


class BillingSettings(BaseModel):
    """Paid-tier gates and Paddle Merchant of Record checkout/webhooks.

    Nested under ``settings.billing``. Environment grammar:
    ``PODEX_BILLING__PAID_TIER_ENABLED``, ``PODEX_BILLING__PADDLE_PRICE_ID``,
    etc.
    """

    # Both gates default off: ``paid_tier_enabled`` controls whether
    # checkout can begin, ``paid_tier_enforced`` controls whether
    # personalization writes require entitlement. The checkout URL and
    # provider name come from the deployment environment.
    paid_tier_enabled: bool = False
    paid_tier_enforced: bool = False
    paid_api_requests_per_month: int = 500
    paid_llm_requests_per_month: int = 25
    provider_name: str = ""
    checkout_url: str = ""

    # Paddle Merchant of Record. Every identifier defaults to an empty
    # string so the Paddle provider stays disabled until the deployment
    # environment configures it; the webhook route rejects requests until
    # the signing secret is set.
    paddle_api_key: str = ""
    paddle_webhook_secret: str = ""
    paddle_price_id: str = ""
    paddle_checkout_url: str = ""

    @property
    def paddle_checkout_enabled(self) -> bool:
        """Whether Paddle-hosted checkout is fully configured."""
        return bool(self.paddle_checkout_url and self.paddle_price_id)


class ObservabilitySettings(BaseModel):
    """Error tracking (Sentry).

    Nested under ``settings.observability``. Environment grammar:
    ``PODEX_OBSERVABILITY__SENTRY_DSN``,
    ``PODEX_OBSERVABILITY__SENTRY_ENVIRONMENT``.

    Prometheus ``/metrics`` gating remains on ``settings.ops_api_key``
    (ops console domain); it is not part of this sub-model.
    """

    # Disabled until a DSN is provided by the deployment environment;
    # defaults are placeholder-free. Error tracking only — no
    # tracing/profiling sample rates are configured.
    sentry_dsn: str = ""
    sentry_environment: str = "production"


class Settings(BaseSettings):
    """Runtime settings, overridable via ``PODEX_`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="PODEX_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "podex"
    environment: str = "development"
    debug: bool = False
    api_v2_prefix: str = "/api/v2"
    cors_origins: list[str] = ["http://localhost:4321"]
    public_web_url: str = "http://localhost:4321"
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    stats_cache: StatsCacheSettings = Field(default_factory=StatsCacheSettings)
    transcripts: TranscriptStorageSettings = Field(
        default_factory=TranscriptStorageSettings,
    )
    auth: AuthSettings = Field(default_factory=AuthSettings)
    billing: BillingSettings = Field(default_factory=BillingSettings)
    observability: ObservabilitySettings = Field(
        default_factory=ObservabilitySettings,
    )

    # Ops console API. The ops surface stays disabled until a key is
    # configured by the deployment environment; requests must present it in
    # the X-Ops-Key header.
    ops_api_key: str = ""
    ops_review_pending_alert_threshold: int = 50
    ops_alert_delivery_pending_threshold: int = 25

    # Scheduler runner deployable. The loop plans due interval work and
    # executes recurring discovery, digest delivery, and retention sweeps.
    scheduler_tick_seconds: int = 60
    scheduler_digest_interval_minutes: int = 1440
    scheduler_retention_interval_minutes: int = 1440


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
