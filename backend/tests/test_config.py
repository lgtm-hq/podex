"""Tests for application settings."""

import math
from pathlib import Path

import pytest
from assertpy import assert_that
from pydantic import ValidationError
from pytest import MonkeyPatch

from podex.config import (
    AuthSettings,
    BillingSettings,
    DatabaseSettings,
    ObservabilitySettings,
    RateLimitSettings,
    Settings,
    StatsCacheSettings,
    TranscriptStorageSettings,
    get_settings,
)


def test_default_settings() -> None:
    """Defaults are sensible for local development."""
    settings = Settings()

    assert_that(settings.app_name).is_equal_to("podex")
    assert_that(settings.api_v2_prefix).is_equal_to("/api/v2")
    assert_that(settings.debug).is_false()
    assert_that(settings.database.url).is_equal_to("sqlite:///./podex.db")


def test_database_settings_load_from_nested_env(monkeypatch: MonkeyPatch) -> None:
    """Nested ``PODEX_DATABASE__*`` env vars populate ``settings.database``."""
    monkeypatch.setenv(
        "PODEX_DATABASE__URL",
        "postgresql+psycopg://podex:podex@db.example.internal/podex",
    )
    monkeypatch.setenv("PODEX_DATABASE__POOL_SIZE", "11")
    monkeypatch.setenv("PODEX_DATABASE__MAX_OVERFLOW", "4")
    monkeypatch.setenv("PODEX_DATABASE__POOL_RECYCLE_SECONDS", "180")

    settings = Settings()

    assert_that(settings.database.url).is_equal_to(
        "postgresql+psycopg://podex:podex@db.example.internal/podex",
    )
    assert_that(settings.database.pool_size).is_equal_to(11)
    assert_that(settings.database.max_overflow).is_equal_to(4)
    assert_that(settings.database.pool_recycle_seconds).is_equal_to(180)


def test_flat_database_env_name_is_ignored(monkeypatch: MonkeyPatch) -> None:
    """Old flat ``PODEX_DATABASE_URL`` is ignored after the nested cutover."""
    monkeypatch.setenv("PODEX_DATABASE_URL", "sqlite:///./flat-ignored.db")
    monkeypatch.delenv("PODEX_DATABASE__URL", raising=False)

    settings = Settings()

    assert_that(settings.database.url).is_equal_to("sqlite:///./podex.db")
    assert_that(settings.database).is_instance_of(DatabaseSettings)


def test_rate_limit_defaults_are_generous() -> None:
    """Rate-limit defaults are enabled but roomy so normal traffic is fine."""
    settings = Settings()

    assert_that(settings.rate_limit.enabled).is_true()
    assert_that(settings.rate_limit.max_requests).is_greater_than(0)
    assert_that(settings.rate_limit.window_seconds).is_greater_than(0)
    assert_that(settings.rate_limit.exempt_paths).contains("/health")


def test_rate_limit_settings_load_from_nested_env(
    monkeypatch: MonkeyPatch,
) -> None:
    """Nested ``PODEX_RATE_LIMIT__*`` env vars populate rate limiting."""
    monkeypatch.setenv("PODEX_RATE_LIMIT__ENABLED", "false")
    monkeypatch.setenv("PODEX_RATE_LIMIT__MAX_REQUESTS", "7")
    monkeypatch.setenv("PODEX_RATE_LIMIT__WINDOW_SECONDS", "2.5")
    monkeypatch.setenv("PODEX_RATE_LIMIT__EXEMPT_PATHS", '["/health","/ready"]')
    monkeypatch.setenv("PODEX_RATE_LIMIT__REDIS_URL", "redis://redis:6379/1")
    monkeypatch.setenv("PODEX_RATE_LIMIT__REDIS_PREFIX", "podex-test")

    settings = Settings()

    assert_that(settings.rate_limit.enabled).is_false()
    assert_that(settings.rate_limit.max_requests).is_equal_to(7)
    assert_that(settings.rate_limit.window_seconds).is_equal_to(2.5)
    assert_that(settings.rate_limit.exempt_paths).is_equal_to(["/health", "/ready"])
    assert_that(settings.rate_limit.redis_url).is_equal_to("redis://redis:6379/1")
    assert_that(settings.rate_limit.redis_prefix).is_equal_to("podex-test")


def test_flat_rate_limit_env_name_is_ignored(monkeypatch: MonkeyPatch) -> None:
    """Old flat ``PODEX_RATE_LIMIT_*`` names are ignored."""
    monkeypatch.setenv("PODEX_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("PODEX_RATE_LIMIT_REDIS_URL", "redis://flat-ignored")
    monkeypatch.delenv("PODEX_RATE_LIMIT__ENABLED", raising=False)
    monkeypatch.delenv("PODEX_RATE_LIMIT__REDIS_URL", raising=False)

    settings = Settings()

    assert_that(settings.rate_limit.enabled).is_true()
    assert_that(settings.rate_limit.redis_url).is_equal_to("")
    assert_that(settings.rate_limit).is_instance_of(RateLimitSettings)


def test_get_settings_is_cached() -> None:
    """``get_settings`` returns a cached singleton."""
    assert_that(get_settings()).is_same_as(get_settings())


def test_stats_cache_ttl_rejects_non_finite_values() -> None:
    """``NaN`` and ``inf`` are refused so misconfig fails at startup."""
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError):
            Settings(stats_cache=StatsCacheSettings(ttl_seconds=bad))


def test_stats_cache_ttl_rejects_negative_values() -> None:
    """A negative TTL is refused (``ge=0`` guard)."""
    with pytest.raises(ValidationError):
        Settings(stats_cache=StatsCacheSettings(ttl_seconds=-1.0))


def test_stats_cache_ttl_accepts_zero_and_positive() -> None:
    """``0`` (disabled) and positive floats are accepted."""
    zero = Settings(stats_cache=StatsCacheSettings(ttl_seconds=0))
    positive = Settings(stats_cache=StatsCacheSettings(ttl_seconds=45.5))

    assert_that(zero.stats_cache.ttl_seconds).is_equal_to(0)
    assert_that(positive.stats_cache.ttl_seconds).is_equal_to(45.5)


def test_stats_cache_settings_load_from_nested_env(monkeypatch: MonkeyPatch) -> None:
    """Nested ``PODEX_STATS_CACHE__*`` env vars populate stats caching."""
    monkeypatch.setenv("PODEX_STATS_CACHE__TTL_SECONDS", "12.5")

    settings = Settings()

    assert_that(settings.stats_cache.ttl_seconds).is_equal_to(12.5)
    assert_that(settings.stats_cache).is_instance_of(StatsCacheSettings)


def test_flat_stats_cache_env_name_is_ignored(monkeypatch: MonkeyPatch) -> None:
    """Old flat ``PODEX_STATS_CACHE_TTL_SECONDS`` is ignored."""
    monkeypatch.setenv("PODEX_STATS_CACHE_TTL_SECONDS", "0")
    monkeypatch.delenv("PODEX_STATS_CACHE__TTL_SECONDS", raising=False)

    settings = Settings()

    assert_that(settings.stats_cache.ttl_seconds).is_equal_to(30.0)


def test_rate_limit_redis_url_defaults_to_disabled() -> None:
    """Default deployment stays on the in-memory backend (empty Redis URL)."""
    settings = Settings()

    assert_that(settings.rate_limit.redis_url).is_equal_to("")
    assert_that(settings.rate_limit.redis_prefix).is_not_empty()


def test_transcripts_settings_load_from_nested_env(monkeypatch: MonkeyPatch) -> None:
    """Nested ``PODEX_TRANSCRIPTS__*`` env vars populate artifact storage."""
    monkeypatch.setenv("PODEX_TRANSCRIPTS__STORAGE_BACKEND", "encrypted_s3")
    monkeypatch.setenv("PODEX_TRANSCRIPTS__STORAGE_PATH", "/tmp/podex-transcripts")
    monkeypatch.setenv("PODEX_TRANSCRIPTS__ENCRYPTION_KEY", "fernet-key")
    monkeypatch.setenv("PODEX_TRANSCRIPTS__S3_BUCKET", "private-transcripts")
    monkeypatch.setenv("PODEX_TRANSCRIPTS__S3_ENDPOINT_URL", "https://r2.example")
    monkeypatch.setenv("PODEX_TRANSCRIPTS__S3_REGION_NAME", "auto")
    monkeypatch.setenv("PODEX_TRANSCRIPTS__S3_ACCESS_KEY_ID", "key-id")
    monkeypatch.setenv("PODEX_TRANSCRIPTS__S3_SECRET_ACCESS_KEY", "secret")

    settings = Settings()

    assert_that(settings.transcripts.storage_backend).is_equal_to("encrypted_s3")
    assert_that(settings.transcripts.storage_path).is_equal_to(
        Path("/tmp/podex-transcripts"),
    )
    assert_that(settings.transcripts.encryption_key).is_equal_to("fernet-key")
    assert_that(settings.transcripts.s3_bucket).is_equal_to("private-transcripts")
    assert_that(settings.transcripts.s3_endpoint_url).is_equal_to(
        "https://r2.example",
    )
    assert_that(settings.transcripts.s3_region_name).is_equal_to("auto")
    assert_that(settings.transcripts.s3_access_key_id).is_equal_to("key-id")
    assert_that(settings.transcripts.s3_secret_access_key).is_equal_to("secret")
    assert_that(settings.transcripts).is_instance_of(TranscriptStorageSettings)


def test_flat_transcripts_env_name_is_ignored(monkeypatch: MonkeyPatch) -> None:
    """Old flat ``PODEX_TRANSCRIPT_ARTIFACT_*`` names are ignored."""
    monkeypatch.setenv("PODEX_TRANSCRIPT_ARTIFACT_ENCRYPTION_KEY", "flat-key")
    monkeypatch.setenv("PODEX_TRANSCRIPT_ARTIFACT_S3_BUCKET", "flat-bucket")
    monkeypatch.delenv("PODEX_TRANSCRIPTS__ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("PODEX_TRANSCRIPTS__S3_BUCKET", raising=False)

    settings = Settings()

    assert_that(settings.transcripts.encryption_key).is_equal_to("")
    assert_that(settings.transcripts.s3_bucket).is_equal_to("")


def test_auth_settings_load_from_nested_env(monkeypatch: MonkeyPatch) -> None:
    """Nested ``PODEX_AUTH__*`` env vars populate auth settings."""
    monkeypatch.setenv("PODEX_AUTH__MAGIC_LINK_TTL_MINUTES", "30")
    monkeypatch.setenv("PODEX_AUTH__SESSION_TTL_DAYS", "45")
    monkeypatch.setenv("PODEX_AUTH__SESSION_COOKIE_NAME", "podex_test_session")
    monkeypatch.setenv("PODEX_AUTH__SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("PODEX_AUTH__SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("PODEX_AUTH__SMTP_PORT", "2525")
    monkeypatch.setenv("PODEX_AUTH__SMTP_USERNAME", "smtp-user")
    monkeypatch.setenv("PODEX_AUTH__SMTP_PASSWORD", "smtp-password")
    monkeypatch.setenv("PODEX_AUTH__SMTP_FROM_EMAIL", "signin@example.com")
    monkeypatch.setenv("PODEX_AUTH__SMTP_STARTTLS", "false")
    monkeypatch.setenv("PODEX_AUTH__WORKOS_CLIENT_ID", "client_123")
    monkeypatch.setenv("PODEX_AUTH__WORKOS_API_KEY", "sk_test")
    monkeypatch.setenv("PODEX_AUTH__WORKOS_REDIRECT_URI", "https://app/callback")

    settings = Settings()

    assert_that(settings.auth.magic_link_ttl_minutes).is_equal_to(30)
    assert_that(settings.auth.session_ttl_days).is_equal_to(45)
    assert_that(settings.auth.session_cookie_name).is_equal_to("podex_test_session")
    assert_that(settings.auth.session_cookie_secure).is_false()
    assert_that(settings.auth.smtp_host).is_equal_to("smtp.example.com")
    assert_that(settings.auth.smtp_port).is_equal_to(2525)
    assert_that(settings.auth.smtp_username).is_equal_to("smtp-user")
    assert_that(settings.auth.smtp_password).is_equal_to("smtp-password")
    assert_that(settings.auth.smtp_from_email).is_equal_to("signin@example.com")
    assert_that(settings.auth.smtp_starttls).is_false()
    assert_that(settings.auth.workos_client_id).is_equal_to("client_123")
    assert_that(settings.auth.workos_api_key).is_equal_to("sk_test")
    assert_that(settings.auth.workos_redirect_uri).is_equal_to("https://app/callback")
    assert_that(settings.auth.workos_enabled).is_true()
    assert_that(settings.auth).is_instance_of(AuthSettings)


def test_flat_auth_env_name_is_ignored(monkeypatch: MonkeyPatch) -> None:
    """Old flat auth, SMTP, and WorkOS env names are ignored."""
    monkeypatch.setenv("PODEX_AUTH_MAGIC_LINK_TTL_MINUTES", "30")
    monkeypatch.setenv("PODEX_SMTP_HOST", "smtp-flat.example.com")
    monkeypatch.setenv("PODEX_WORKOS_CLIENT_ID", "client_flat")
    monkeypatch.delenv("PODEX_AUTH__MAGIC_LINK_TTL_MINUTES", raising=False)
    monkeypatch.delenv("PODEX_AUTH__SMTP_HOST", raising=False)
    monkeypatch.delenv("PODEX_AUTH__WORKOS_CLIENT_ID", raising=False)

    settings = Settings()

    assert_that(settings.auth.magic_link_ttl_minutes).is_equal_to(15)
    assert_that(settings.auth.smtp_host).is_equal_to("")
    assert_that(settings.auth.workos_client_id).is_equal_to("")


def test_billing_settings_load_from_nested_env(monkeypatch: MonkeyPatch) -> None:
    """Nested ``PODEX_BILLING__*`` env vars populate paid-tier settings."""
    monkeypatch.setenv("PODEX_BILLING__PAID_TIER_ENABLED", "true")
    monkeypatch.setenv("PODEX_BILLING__PAID_TIER_ENFORCED", "true")
    monkeypatch.setenv("PODEX_BILLING__PAID_API_REQUESTS_PER_MONTH", "1000")
    monkeypatch.setenv("PODEX_BILLING__PAID_LLM_REQUESTS_PER_MONTH", "50")
    monkeypatch.setenv("PODEX_BILLING__PROVIDER_NAME", "hosted-test")
    monkeypatch.setenv("PODEX_BILLING__CHECKOUT_URL", "https://billing.example")
    monkeypatch.setenv("PODEX_BILLING__PADDLE_API_KEY", "pdl_api")
    monkeypatch.setenv("PODEX_BILLING__PADDLE_WEBHOOK_SECRET", "whsec")
    monkeypatch.setenv("PODEX_BILLING__PADDLE_PRICE_ID", "pri_123")
    monkeypatch.setenv("PODEX_BILLING__PADDLE_CHECKOUT_URL", "https://buy.example")

    settings = Settings()

    assert_that(settings.billing.paid_tier_enabled).is_true()
    assert_that(settings.billing.paid_tier_enforced).is_true()
    assert_that(settings.billing.paid_api_requests_per_month).is_equal_to(1000)
    assert_that(settings.billing.paid_llm_requests_per_month).is_equal_to(50)
    assert_that(settings.billing.provider_name).is_equal_to("hosted-test")
    assert_that(settings.billing.checkout_url).is_equal_to("https://billing.example")
    assert_that(settings.billing.paddle_api_key).is_equal_to("pdl_api")
    assert_that(settings.billing.paddle_webhook_secret).is_equal_to("whsec")
    assert_that(settings.billing.paddle_price_id).is_equal_to("pri_123")
    assert_that(settings.billing.paddle_checkout_url).is_equal_to("https://buy.example")
    assert_that(settings.billing.paddle_checkout_enabled).is_true()
    assert_that(settings.billing).is_instance_of(BillingSettings)


def test_flat_billing_env_name_is_ignored(monkeypatch: MonkeyPatch) -> None:
    """Old flat paid-tier, billing, and Paddle env names are ignored."""
    monkeypatch.setenv("PODEX_PAID_TIER_ENABLED", "true")
    monkeypatch.setenv("PODEX_BILLING_PROVIDER_NAME", "flat-provider")
    monkeypatch.setenv("PODEX_PADDLE_CHECKOUT_URL", "https://flat-buy.example")
    monkeypatch.delenv("PODEX_BILLING__PAID_TIER_ENABLED", raising=False)
    monkeypatch.delenv("PODEX_BILLING__PROVIDER_NAME", raising=False)
    monkeypatch.delenv("PODEX_BILLING__PADDLE_CHECKOUT_URL", raising=False)

    settings = Settings()

    assert_that(settings.billing.paid_tier_enabled).is_false()
    assert_that(settings.billing.provider_name).is_equal_to("")
    assert_that(settings.billing.paddle_checkout_url).is_equal_to("")


def test_observability_settings_load_from_nested_env(
    monkeypatch: MonkeyPatch,
) -> None:
    """Nested ``PODEX_OBSERVABILITY__*`` env vars populate Sentry settings."""
    monkeypatch.setenv("PODEX_OBSERVABILITY__SENTRY_DSN", "https://key@sentry/1")
    monkeypatch.setenv("PODEX_OBSERVABILITY__SENTRY_ENVIRONMENT", "staging")

    settings = Settings()

    assert_that(settings.observability.sentry_dsn).is_equal_to("https://key@sentry/1")
    assert_that(settings.observability.sentry_environment).is_equal_to("staging")
    assert_that(settings.observability).is_instance_of(ObservabilitySettings)


def test_flat_observability_env_name_is_ignored(monkeypatch: MonkeyPatch) -> None:
    """Old flat ``PODEX_SENTRY_*`` env names are ignored."""
    monkeypatch.setenv("PODEX_SENTRY_DSN", "https://flat@sentry/1")
    monkeypatch.setenv("PODEX_SENTRY_ENVIRONMENT", "staging")
    monkeypatch.delenv("PODEX_OBSERVABILITY__SENTRY_DSN", raising=False)
    monkeypatch.delenv("PODEX_OBSERVABILITY__SENTRY_ENVIRONMENT", raising=False)

    settings = Settings()

    assert_that(settings.observability.sentry_dsn).is_equal_to("")
    assert_that(settings.observability.sentry_environment).is_equal_to("production")
