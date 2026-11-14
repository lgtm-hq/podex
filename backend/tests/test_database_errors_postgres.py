"""PostgreSQL regression: a unique violation's DETAIL never reaches the logs.

PostgreSQL renders the duplicate value into the driver message
(``DETAIL:  Key (value)=(...) already exists.``), which ``hide_parameters``
does not cover. These tests run when ``PODEX_DATABASE__URL`` points at
PostgreSQL and are skipped otherwise:

    PODEX_DATABASE__URL=postgresql+psycopg://podex@localhost:5432/podex \\
    uv run pytest tests/test_database_errors_postgres.py
"""

import logging
import os
import uuid
from secrets import token_hex

import pytest
from assertpy import assert_that
from pydantic import SecretStr
from sqlalchemy import Column, MetaData, String, Table, create_engine, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from podex.config import DatabaseSettings, Settings
from podex.database import build_engine_kwargs
from podex.logging_config import configure_logging

_DATABASE_URL = os.environ.get("PODEX_DATABASE__URL", "")

pytestmark = pytest.mark.skipif(
    not _DATABASE_URL.startswith("postgresql"),
    reason="PODEX_DATABASE__URL does not point at PostgreSQL",
)


def test_unique_violation_detail_is_withheld_from_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The scheduler-style boundary logs the class and SQLSTATE only."""
    row_value = f"pg-row-secret-{token_hex(8)}"
    settings = Settings(database=DatabaseSettings(url=SecretStr(_DATABASE_URL)))
    engine = create_engine(_DATABASE_URL, **build_engine_kwargs(settings))
    schema = f"podex_errors_{uuid.uuid4().hex[:12]}"
    credentials = Table(
        "credentials",
        MetaData(schema=schema),
        Column("value", String, unique=True),
    )
    configure_logging()
    caplog.set_level(logging.DEBUG)
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
            credentials.metadata.create_all(connection)
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
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()

    assert_that(caplog.text).does_not_contain(row_value)
    assert_that(caplog.text).does_not_contain("DETAIL")
    assert_that(caplog.text).contains(
        "IntegrityError (UniqueViolation, SQLSTATE 23505) [driver message withheld]",
    )
