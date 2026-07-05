"""Tests for application settings."""

from podex.config import Settings, get_settings


def test_default_settings() -> None:
    """Defaults are sensible for local development."""
    settings = Settings()

    assert settings.app_name == "podex"
    assert settings.api_v2_prefix == "/api/v2"
    assert settings.debug is False


def test_get_settings_is_cached() -> None:
    """``get_settings`` returns a cached singleton."""
    assert get_settings() is get_settings()
