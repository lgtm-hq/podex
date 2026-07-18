"""Tests for application settings."""

import math

import pytest
from assertpy import assert_that
from pydantic import ValidationError

from podex.config import Settings, get_settings


def test_default_settings() -> None:
    """Defaults are sensible for local development."""
    settings = Settings()

    assert_that(settings.app_name).is_equal_to("podex")
    assert_that(settings.api_v2_prefix).is_equal_to("/api/v2")
    assert_that(settings.debug).is_false()


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
    assert_that(Settings(stats_cache_ttl_seconds=0).stats_cache_ttl_seconds).is_equal_to(
        0,
    )
    assert_that(
        Settings(stats_cache_ttl_seconds=45.5).stats_cache_ttl_seconds,
    ).is_equal_to(45.5)
