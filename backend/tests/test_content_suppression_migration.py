"""Revision 0022 backfills suppressions from takedowns that already ran."""

import json
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from assertpy import assert_that
from sqlalchemy import Engine, create_engine, inspect, text

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
_EXECUTED = {"executed_at": "2026-07-01T00:00:00+00:00"}


def _migrate(db_url: str, revision: str, *, down: bool = False) -> None:
    """Upgrade, or downgrade, the database to ``revision``."""
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", db_url)
    if down:
        downgrade(config, revision)
    else:
        upgrade(config, revision)


def _seed_cases(engine: Engine) -> None:
    """Insert catalog rows and takedown cases at revision 0021."""
    cases = [
        # id, subject_type, subject_id, actions, status, metadata
        (1, "episode", 10, ["unpublish_mentions", "suppress_derivatives"], _EXECUTED),
        (2, "podcast", 1, ["suppress_raw_transcript", "unpublish_mentions"], _EXECUTED),
        (3, "mention", 500, ["unpublish_mentions", "suppress_derivatives"], _EXECUTED),
        (4, "mention", 501, ["unpublish_mentions"], _EXECUTED),
        (5, "episode", 10, ["suppress_derivatives"], None),
        (6, "episode", 99, ["suppress_derivatives"], _EXECUTED),
        (7, "mention", 502, ["unpublish_mentions"], _EXECUTED),
        (8, "episode", 11, ["suppress_raw_transcript"], _EXECUTED),
        (9, "episode", 11, ["unpublish_mentions"], _EXECUTED),
    ]
    audit_results = {
        3: {"episode_ids": [11], "media_ids": [21], "mentions_unpublished": 1},
        7: {"episode_ids": [], "media_ids": [], "mentions_unpublished": 0},
    }
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO podcasts (id, name, slug) VALUES (1, 'Show', 'show')"),
        )
        connection.execute(
            text(
                "INSERT INTO episodes (id, podcast_id, title) VALUES"
                " (10, 1, 'One'), (11, 1, 'Two')",
            ),
        )
        connection.execute(
            text(
                "INSERT INTO media (id, type, title) VALUES"
                " (20, 'book', 'Dune'), (21, 'book', 'Arrival')",
            ),
        )
        for case_id, subject_type, subject_id, actions, metadata in cases:
            connection.execute(
                text(
                    "INSERT INTO takedown_requests (id, subject_type, subject_id,"
                    " requester_type, requester_name, requester_email, basis,"
                    " requested_actions_json, status, metadata_json, created_at,"
                    " updated_at) VALUES (:id, :subject_type, :subject_id,"
                    " 'rights_holder', 'Rights Holder', 'rights@example.com',"
                    " 'Synthetic basis.', :actions, :status, :metadata,"
                    " '2026-07-01', '2026-07-01')",
                ),
                {
                    "id": case_id,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "actions": json.dumps(actions),
                    "status": "approved" if case_id != 5 else "pending",
                    "metadata": json.dumps(metadata) if metadata else None,
                },
            )
        for case_id, result in audit_results.items():
            connection.execute(
                text(
                    "INSERT INTO audit_logs (action, resource_type, resource_id,"
                    " summary, metadata_json, created_at) VALUES"
                    " ('decide_takedown_request', 'takedown_request', :id,"
                    " 'decided', :metadata, '2026-07-01')",
                ),
                {"id": case_id, "metadata": json.dumps(result)},
            )


def test_backfill_records_resolvable_cases_and_reports_the_rest(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Executed cases get rows; unresolvable ones are reported, not guessed."""
    db_url = f"sqlite:///{tmp_path / 'backfill.db'}"
    _migrate(db_url, "0021")
    engine = create_engine(db_url)
    _seed_cases(engine)

    capfd.readouterr()
    _migrate(db_url, "head")
    output = capfd.readouterr().err

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT takedown_request_id, suppression_key FROM content_suppressions"
                " ORDER BY id",
            ),
        ).all()
    assert_that([tuple(row) for row in rows]).is_equal_to(
        [
            (1, "episode:10:mentions"),
            (1, "episode:10:derivatives"),
            (2, "podcast:1:mentions"),
            (3, "episode:11:derivatives"),
            (3, "mention_pair:11:21:mentions"),
            (9, "episode:11:mentions"),
        ],
    )
    assert_that(output).contains(
        "could not resolve takedown cases [4]",
        "skipped takedown cases [6, 7]",
    )
    engine.dispose()


def test_downgrade_drops_the_table_and_upgrade_replays(tmp_path: Path) -> None:
    """The revision downgrades cleanly and can be applied again."""
    db_url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    _migrate(db_url, "head")

    _migrate(db_url, "0021", down=True)
    engine = create_engine(db_url)
    assert_that(inspect(engine).get_table_names()).does_not_contain(
        "content_suppressions",
    )
    engine.dispose()

    _migrate(db_url, "head")
    engine = create_engine(db_url)
    assert_that(inspect(engine).get_table_names()).contains("content_suppressions")
    engine.dispose()
