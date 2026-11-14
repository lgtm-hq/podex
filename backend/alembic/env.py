"""Alembic migration environment."""

from logging.config import fileConfig

from alembic import context  # type: ignore[attr-defined]
from sqlalchemy import engine_from_config, pool, text

from podex.config import get_settings
from podex.models import Base

config = context.config

if config.config_file_name is not None:
    # Keep loggers created before this point (the application's, when
    # migrations run in-process) enabled; the CLI output is unchanged.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

if not config.get_main_option("sqlalchemy.url"):
    # The option goes through ConfigParser interpolation, which would reject a
    # percent-encoded password (and print the whole URL); "%%" reads back "%".
    config.set_main_option(
        "sqlalchemy.url",
        get_settings().database.url.get_secret_value().replace("%", "%%"),
    )

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live database connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    section = config.get_section(config.config_ini_section, {}) or {}
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # As for the application engine: keep bound values out of errors.
        hide_parameters=True,
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "postgresql":
            # Migration 0010 creates a pgvector ``vector(1536)`` column, so
            # the extension must exist before the chain replays. Idempotent,
            # and runs on every upgrade so fresh databases (CI service
            # containers, new Neon branches) need no manual step.
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
