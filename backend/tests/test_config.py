"""Tests for application settings."""

import math

import pytest
from assertpy import assert_that
from pydantic import ValidationError
from pytest import MonkeyPatch

from podex.config import DatabaseSettings, Settings, get_settings


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

    assert_that(settings.rate_limit_enabled).is_true()
    assert_that(settings.rate_limit_max_requests).is_greater_than(0)
    assert_that(settings.rate_limit_window_seconds).is_greater_than(0)
    assert_that(settings.rate_limit_exempt_paths).contains("/health")


def test_get_settings_is_cached() -> None:
    """``get_settings`` returns a cached singleton."""
    assert_that(get_settings()).is_same_as(get_settings())


def test_stats_cache_ttl_rejects_non_finite_values() -> None:
    """``NaN`` and ``inf`` are refused so misconfig fails at startup."""
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError):
            Settings(stats_cache_ttl_seconds=bad)


def test_stats_cache_ttl_rejects_negative_values() -> None:
    """A negative TTL is refused (``ge=0`` guard)."""
    with pytest.raises(ValidationError):
        Settings(stats_cache_ttl_seconds=-1.0)


def test_stats_cache_ttl_accepts_zero_and_positive() -> None:
    """``0`` (disabled) and positive floats are accepted."""
    zero = Settings(stats_cache_ttl_seconds=0).stats_cache_ttl_seconds
    positive = Settings(stats_cache_ttl_seconds=45.5).stats_cache_ttl_seconds

    assert_that(zero).is_equal_to(0)
    assert_that(positive).is_equal_to(45.5)


def test_rate_limit_redis_url_defaults_to_disabled() -> None:
    """Default deployment stays on the in-memory backend (empty Redis URL)."""
    settings = Settings()

    assert_that(settings.rate_limit_redis_url).is_equal_to("")
    assert_that(settings.rate_limit_redis_prefix).is_not_empty()
