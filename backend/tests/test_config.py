"""Tests for application settings."""

from assertpy import assert_that

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
