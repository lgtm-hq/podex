"""Tests for application settings."""

from assertpy import assert_that

from podex.config import Settings, get_settings


def test_default_settings() -> None:
    """Defaults are sensible for local development."""
    settings = Settings()

    assert_that(settings.app_name).is_equal_to("podex")
    assert_that(settings.api_v2_prefix).is_equal_to("/api/v2")
    assert_that(settings.debug).is_false()


def test_get_settings_is_cached() -> None:
    """``get_settings`` returns a cached singleton."""
    assert_that(get_settings()).is_same_as(get_settings())
