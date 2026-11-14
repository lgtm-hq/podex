"""PostgreSQL regression: a unique violation's DETAIL never reaches the logs.

PostgreSQL renders the duplicate value into the driver message
(``DETAIL:  Key (value)=(...) already exists.``), which ``hide_parameters``
does not cover. The test runs in its own migrated database (the shared
``postgres`` fixture) in the PostgreSQL lane and is skipped elsewhere.
"""

import logging
from secrets import token_hex

import pytest
from assertpy import assert_that
from pydantic import SecretStr
from sqlalchemy import Column, MetaData, String, Table, create_engine, insert
from sqlalchemy.exc import IntegrityError

from podex.config import DatabaseSettings, Settings
from podex.database import build_engine_kwargs
from podex.logging_config import configure_logging
from tests.postgres.conftest import PostgresDatabase

pytestmark = pytest.mark.postgres


def test_unique_violation_detail_is_withheld_from_logs(
    postgres: PostgresDatabase,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The scheduler's boundary logs the class and SQLSTATE only."""
    row_value = f"pg-row-secret-{token_hex(8)}"
    url = postgres.url.render_as_string(hide_password=False)
    settings = Settings(database=DatabaseSettings(url=SecretStr(url)))
    engine = create_engine(url, **build_engine_kwargs(settings))
    credentials = Table(
        "redaction_credentials",
        MetaData(),
        Column("value", String, unique=True),
    )
    configure_logging()
    caplog.set_level(logging.DEBUG)
    try:
        credentials.metadata.create_all(engine)
        with pytest.raises(IntegrityError) as caught:
            with engine.begin() as connection:
                connection.execute(insert(credentials), [{"value": row_value}])
                connection.execute(insert(credentials), [{"value": row_value}])
        assert_that(str(caught.value)).contains("DETAIL")
        assert_that(str(caught.value)).contains(row_value)

        logging.getLogger("podex.scheduler_runner").error(
            "scheduler_tick_failed",
            exc_info=caught.value,
        )
    finally:
        engine.dispose()

    assert_that(caplog.text).does_not_contain(row_value)
    assert_that(caplog.text).does_not_contain("DETAIL")
    assert_that(caplog.text).contains(
        "IntegrityError (UniqueViolation, SQLSTATE 23505) [driver message withheld]",
    )
