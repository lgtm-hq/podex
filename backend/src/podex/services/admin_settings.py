"""Shared admin settings services for runtime configuration surfaces."""

from dataclasses import dataclass

from podex.config import Settings


@dataclass(frozen=True, slots=True)
class AdminSettingsData:
    """Runtime configuration snapshot exposed to admin surfaces."""

    app_name: str
    debug: bool
    api_key_enabled: bool
    rate_limit_per_minute: int
    cors_origins: list[str]
    youtube_channel_id: str
    meilisearch_enabled: bool
    meilisearch_url: str


@dataclass(frozen=True, slots=True)
class UpdateAdminSettingsInputData:
    """Partial runtime configuration update payload for admin surfaces."""

    provided_fields: frozenset[str] = frozenset()
    debug: bool | None = None
    rate_limit_per_minute: int | None = None
    cors_origins: list[str] | None = None
    youtube_channel_id: str | None = None
    meilisearch_enabled: bool | None = None
    meilisearch_url: str | None = None


def _to_admin_settings_data(*, settings: Settings) -> AdminSettingsData:
    """Convert runtime settings to an admin-facing data payload.

    Args:
        settings: Runtime application settings.

    Returns:
        Admin settings payload.
    """
    return AdminSettingsData(
        app_name=settings.app_name,
        debug=settings.debug,
        api_key_enabled=bool(settings.api_key),
        rate_limit_per_minute=settings.rate_limit_per_minute,
        cors_origins=list(settings.cors_origins),
        youtube_channel_id=settings.youtube_channel_id,
        meilisearch_enabled=settings.meilisearch_enabled,
        meilisearch_url=settings.meilisearch_url,
    )


def get_admin_settings_snapshot(*, settings: Settings) -> AdminSettingsData:
    """Get the current runtime configuration snapshot for admin surfaces.

    Args:
        settings: Runtime application settings.

    Returns:
        Admin settings payload.
    """
    return _to_admin_settings_data(settings=settings)


def update_admin_settings(
    *,
    settings: Settings,
    payload: UpdateAdminSettingsInputData,
) -> AdminSettingsData:
    """Apply a partial runtime configuration update for admin surfaces.

    Args:
        settings: Runtime application settings.
        payload: Partial configuration update payload.

    Returns:
        Updated admin settings payload.
    """
    if "debug" in payload.provided_fields:
        settings.debug = bool(payload.debug)
    if "rate_limit_per_minute" in payload.provided_fields:
        if payload.rate_limit_per_minute is None:
            raise ValueError("rate_limit_per_minute cannot be cleared")
        settings.rate_limit_per_minute = int(payload.rate_limit_per_minute)
    if "cors_origins" in payload.provided_fields:
        settings.cors_origins = list(payload.cors_origins or [])
    if "youtube_channel_id" in payload.provided_fields:
        settings.youtube_channel_id = payload.youtube_channel_id or ""
    if "meilisearch_enabled" in payload.provided_fields:
        settings.meilisearch_enabled = bool(payload.meilisearch_enabled)
    if "meilisearch_url" in payload.provided_fields:
        settings.meilisearch_url = payload.meilisearch_url or ""

    return _to_admin_settings_data(settings=settings)
