"""Tests for database engine option selection."""

from assertpy import assert_that

from podex.config import Settings
from podex.database import build_engine_kwargs


def test_sqlite_engine_kwargs_keep_thread_connect_args() -> None:
    """SQLite URLs keep ``check_same_thread=False`` and no pool tuning."""
    settings = Settings(database_url="sqlite:///./podex.db")

    kwargs = build_engine_kwargs(settings)

    assert_that(kwargs).is_equal_to(
        {"connect_args": {"check_same_thread": False}},
    )


def test_postgres_engine_kwargs_enable_pooling() -> None:
    """Server-backed URLs get pre-ping plus settings-driven pool sizing."""
    settings = Settings(
        database_url="postgresql+psycopg://podex:podex@db.example.internal/podex",
        database_pool_size=7,
        database_max_overflow=3,
        database_pool_recycle_seconds=120,
    )

    kwargs = build_engine_kwargs(settings)

    assert_that(kwargs).is_equal_to(
        {
            "pool_pre_ping": True,
            "pool_size": 7,
            "max_overflow": 3,
            "pool_recycle": 120,
        },
    )


def test_pool_defaults_are_sized_for_managed_postgres() -> None:
    """Default pool tuning matches the Railway + Neon deployment contract."""
    settings = Settings()

    assert_that(settings.database_pool_size).is_equal_to(5)
    assert_that(settings.database_max_overflow).is_equal_to(5)
    assert_that(settings.database_pool_recycle_seconds).is_equal_to(300)
