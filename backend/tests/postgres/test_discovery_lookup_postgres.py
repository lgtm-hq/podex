"""The discovery catalog lookup on real PostgreSQL (ADR 0020).

PostgreSQL lowercases every letter, where SQLite lowercases only ASCII. The
GUID rule must still hold exactly: surrounding whitespace and ASCII letter case
are ignored, every other character compares exactly. Each test runs discovery
over fixed feed records and observes which row was updated. These tests run in
the PostgreSQL CI lane and are skipped elsewhere.
"""

from datetime import UTC, datetime, timedelta

import pytest
from assertpy import assert_that
from sqlalchemy import select

from podex.models import Episode, Podcast
from podex.services import discovery_orchestrator
from podex.services.discovery import DiscoveredEpisode
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from tests.feed_identity_support import MARK, RecordedFeed, marked_record
from tests.postgres.conftest import PostgresDatabase

pytestmark = pytest.mark.postgres

_DAY = datetime(2026, 5, 4, 6, 0, tzinfo=UTC)


def _updated_title(
    postgres: PostgresDatabase,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, object]],
    record: DiscoveredEpisode,
) -> str | None:
    """Store ``rows``, discover ``record``; return the title of the row it updated.

    Returns None when discovery stored the record as a new row instead.
    """
    feed = RecordedFeed([record])
    monkeypatch.setattr(discovery_orchestrator, "RSSDiscovery", lambda: feed)
    with postgres.sessions() as session:
        podcast = Podcast(name="Feed Show", slug="feed-show", rss_url="https://f.ex")
        session.add(podcast)
        session.flush()
        existing = [
            Episode(podcast_id=podcast.id, title=f"Stored {index}", **fields)
            for index, fields in enumerate(rows)
        ]
        session.add_all(existing)
        session.commit()
        titles = {row.id: row.title for row in existing}
        with DiscoveryOrchestrator(session) as orchestrator:
            result = orchestrator.discover_for_podcast(podcast)
        assert_that(result.errors).is_empty()
        updated = session.scalars(
            select(Episode.id).where(
                Episode.podcast_id == podcast.id,
                Episode.duration_seconds == MARK,
            ),
        ).all()
    assert_that(updated).is_length(1)
    return titles.get(updated[0])


@pytest.mark.parametrize(
    ("stored", "reported", "found"),
    [
        ("  URN:Show:Return\t", "urn:show:return", True),
        ("URN:SHOW:É", "urn:show:É", True),
        ("urn:show:é", "urn:show:É", False),
        ("urn:a\\b%c_d", "URN:A\\B%C_D", True),
        ("urn:aXbYcZd", "urn:a\\b%c_d", False),
        ("urn:show:10", "urn:show:1", False),
    ],
    ids=[
        "whitespace-and-ascii-case",
        "ascii-case-around-non-ascii",
        "non-ascii-case-is-exact",
        "like-characters-literal",
        "like-characters-not-wildcards",
        "prefix-is-not-equal",
    ],
)
def test_guid_rule_holds_on_postgres(
    postgres: PostgresDatabase,
    monkeypatch: pytest.MonkeyPatch,
    stored: str,
    reported: str,
    found: bool,
) -> None:
    """A stored GUID is found only by the GUID rule, whatever PostgreSQL lowercases."""
    title = _updated_title(
        postgres,
        monkeypatch,
        [{"rss_guid": stored}],
        marked_record(rss_guid=reported),
    )

    assert_that(title).is_equal_to("Stored 0" if found else None)


def test_number_lookup_respects_the_season_on_postgres(
    postgres: PostgresDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Season 2 episode 5 skips a season 1 row and takes the seasonless one."""
    title = _updated_title(
        postgres,
        monkeypatch,
        [
            {"episode_number": 5, "season_number": 1},
            {"episode_number": 5, "season_number": None},
        ],
        marked_record(episode_number=5, season_number=2),
    )

    assert_that(title).is_equal_to("Stored 1")


@pytest.mark.parametrize(
    ("published_at", "row_published_at", "found"),
    [
        (_DAY, _DAY, True),
        (_DAY + timedelta(seconds=1), _DAY, False),
        (None, None, True),
        (None, _DAY, False),
    ],
    ids=["same-time", "other-time", "both-undated", "undated-record"],
)
def test_title_and_time_lookup_on_postgres(
    postgres: PostgresDatabase,
    monkeypatch: pytest.MonkeyPatch,
    published_at: datetime | None,
    row_published_at: datetime | None,
    found: bool,
) -> None:
    """A record without identifiers finds the row with its exact title and time."""
    title = _updated_title(
        postgres,
        monkeypatch,
        [{"published_at": row_published_at}],
        marked_record("Stored 0", published_at=published_at),
    )

    assert_that(title).is_equal_to("Stored 0" if found else None)
