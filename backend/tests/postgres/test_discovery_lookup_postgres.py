"""The discovery catalog lookup on real PostgreSQL (ADR 0019).

PostgreSQL lowercases every letter and reads backslashes in LIKE patterns,
where SQLite lowercases only ASCII. The GUID rule must still hold exactly:
surrounding whitespace and ASCII letter case are ignored, every other
character compares exactly. These tests run in the PostgreSQL CI lane and are
skipped elsewhere.
"""

from datetime import UTC, datetime, timedelta

import pytest
from assertpy import assert_that

from podex.models import Episode, Podcast
from podex.services.discovery import DiscoveredEpisode
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from tests.postgres.conftest import PostgresDatabase

pytestmark = pytest.mark.postgres

_DAY = datetime(2026, 5, 4, 6, 0, tzinfo=UTC)


def _lookup(
    postgres: PostgresDatabase,
    stored: list[dict[str, object]],
    record: DiscoveredEpisode,
) -> str | None:
    """Store ``stored`` rows for one podcast; return the title the lookup finds."""
    with postgres.sessions() as session:
        podcast = Podcast(name="Feed Show", slug="feed-show")
        session.add(podcast)
        session.flush()
        session.add_all(
            Episode(podcast_id=podcast.id, title=f"Stored {index}", **fields)
            for index, fields in enumerate(stored)
        )
        session.commit()
        with DiscoveryOrchestrator(session) as orchestrator:
            # Direct unit coverage of the private catalog lookup.
            # pylint: disable-next=protected-access
            found = orchestrator._find_existing_episode(podcast, record)
        return found.title if found is not None else None


@pytest.mark.parametrize(
    ("stored", "reported", "found"),
    [
        ("  URN:Show:Return\t", "urn:show:return", True),
        ("URN:CAFÉ", "urn:calfÉ", True),
        ("urn:café", "urn:calfÉ", False),
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
    stored: str,
    reported: str,
    found: bool,
) -> None:
    """A stored GUID is found only by the GUID rule, whatever PostgreSQL lowercases."""
    title = _lookup(
        postgres,
        [{"rss_guid": stored}],
        DiscoveredEpisode(title="Unrelated", rss_guid=reported),
    )

    assert_that(title).is_equal_to("Stored 0" if found else None)


def test_number_lookup_respects_the_season_on_postgres(
    postgres: PostgresDatabase,
) -> None:
    """Season 2 episode 5 skips a season 1 row and takes the seasonless one."""
    title = _lookup(
        postgres,
        [
            {"episode_number": 5, "season_number": 1},
            {"episode_number": 5, "season_number": None},
        ],
        DiscoveredEpisode(title="New", episode_number=5, season_number=2),
    )

    assert_that(title).is_equal_to("Stored 1")


@pytest.mark.parametrize(
    ("published_at", "found"),
    [(_DAY, True), (_DAY + timedelta(seconds=1), False)],
    ids=["same-time", "other-time"],
)
def test_title_and_time_lookup_on_postgres(
    postgres: PostgresDatabase,
    published_at: datetime,
    found: bool,
) -> None:
    """A record without identifiers finds the row with its exact title and time."""
    with postgres.sessions() as session:
        podcast = Podcast(name="Feed Show", slug="feed-show")
        session.add(podcast)
        session.flush()
        session.add(
            Episode(podcast_id=podcast.id, title="Why We Started", published_at=_DAY),
        )
        session.commit()
        with DiscoveryOrchestrator(session) as orchestrator:
            # Direct unit coverage of the private catalog lookup.
            # pylint: disable-next=protected-access
            existing = orchestrator._find_existing_episode(
                podcast,
                DiscoveredEpisode(title="Why We Started", published_at=published_at),
            )

    assert_that(existing is not None).is_equal_to(found)
