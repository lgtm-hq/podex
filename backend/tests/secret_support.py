"""Synthetic credentials for every secret setting.

Each value is generated at import time, so it never appears in test source:
a reported stack frame's source context cannot contain it, and a match in a
serialised report or a log can only come from the value itself.
"""

from secrets import token_hex

import pytest

from podex.config import Settings

DATABASE_PASSWORD = f"db-pw-{token_hex(8)}"
REDIS_PASSWORD = f"redis-pw-{token_hex(8)}"

SECRET_ENVIRONMENT: dict[str, str] = {
    "PODEX_DATABASE__URL": (
        f"postgresql+psycopg://podex:{DATABASE_PASSWORD}@db.invalid/podex"
    ),
    "PODEX_RATE_LIMIT__REDIS_URL": f"redis://:{REDIS_PASSWORD}@redis.invalid:6379/0",
    "PODEX_TRANSCRIPTS__ENCRYPTION_KEY": f"fernet-{token_hex(16)}",
    "PODEX_TRANSCRIPTS__S3_ACCESS_KEY_ID": f"s3-id-{token_hex(8)}",
    "PODEX_TRANSCRIPTS__S3_SECRET_ACCESS_KEY": f"s3-secret-{token_hex(8)}",
    "PODEX_AUTH__SMTP_PASSWORD": f"smtp-pw-{token_hex(8)}",
    "PODEX_AUTH__WORKOS_API_KEY": f"workos-{token_hex(8)}",
    "PODEX_BILLING__PADDLE_API_KEY": f"paddle-api-{token_hex(8)}",
    "PODEX_BILLING__PADDLE_WEBHOOK_SECRET": f"paddle-whsec-{token_hex(8)}",
    "PODEX_ENRICHMENT__TMDB__API_KEY": f"tmdb-{token_hex(8)}",
    "PODEX_ENRICHMENT__OMDB__API_KEY": f"omdb-{token_hex(8)}",
    "PODEX_ENRICHMENT__GOOGLE_BOOKS__API_KEY": f"books-{token_hex(8)}",
    "PODEX_ENRICHMENT__PUBMED__API_KEY": f"ncbi-{token_hex(8)}",
    "PODEX_ENRICHMENT__SEMANTIC_SCHOLAR__API_KEY": f"s2-{token_hex(8)}",
    "PODEX_OPS_API_KEY": f"ops-{token_hex(8)}",
}
"""One synthetic value per secret setting, keyed by environment variable."""

SECRET_VALUES: tuple[str, ...] = (
    DATABASE_PASSWORD,
    REDIS_PASSWORD,
    *(
        value
        for name, value in SECRET_ENVIRONMENT.items()
        if name not in {"PODEX_DATABASE__URL", "PODEX_RATE_LIMIT__REDIS_URL"}
    ),
)
"""The values that must never appear in a report or log."""

REPORTING_DSN = "https://public@sentry.invalid/1"


def load_secret_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Load settings from an environment carrying every synthetic secret.

    Args:
        monkeypatch: Fixture used to set the environment for this test.

    Returns:
        Settings: Settings loaded as the application loads them, with the
            reporter enabled.
    """
    for name, value in SECRET_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PODEX_OBSERVABILITY__SENTRY_DSN", REPORTING_DSN)
    return Settings(_env_file=None)


def leaked(text: str) -> list[str]:
    """Return the synthetic secrets that occur in ``text``.

    Args:
        text: A serialised report, log output or rendered object.

    Returns:
        list[str]: Each secret found, so a failure names what leaked.
    """
    return [secret for secret in SECRET_VALUES if secret in text]
