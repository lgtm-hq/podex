"""The migration environment reads a database URL without exposing it.

``alembic.ini`` leaves ``sqlalchemy.url`` empty, so ``alembic/env.py`` takes
the URL from settings and stores it as a ConfigParser option. A password
percent-encoded in that URL (``%40`` for ``@``) must survive interpolation;
otherwise the resulting error prints the whole URL.
"""

import io
from pathlib import Path
from secrets import token_hex

import pytest
from alembic.command import upgrade
from alembic.config import Config
from assertpy import assert_that
from pydantic import SecretStr

from podex.config import DatabaseSettings, Settings

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def test_offline_migrations_accept_percent_encoded_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offline SQL generation works and never prints the password."""
    password = f"pw%40{token_hex(6)}"
    url = f"postgresql+psycopg://podex:{password}@db.invalid:5432/podex"
    settings = Settings(database=DatabaseSettings(url=SecretStr(url)))
    monkeypatch.setattr("podex.config.get_settings", lambda: settings)
    output = io.StringIO()
    config = Config(str(_ALEMBIC_INI), output_buffer=output)

    upgrade(config, "head", sql=True)

    assert_that(config.get_main_option("sqlalchemy.url")).is_equal_to(url)
    assert_that(output.getvalue()).contains("CREATE TABLE")
    assert_that(output.getvalue()).does_not_contain(password)
    assert_that(output.getvalue()).does_not_contain(password.replace("%40", "@"))
