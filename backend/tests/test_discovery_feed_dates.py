"""Feed dates with a UTC offset find their rows again (ADR 0020).

The reader reports every publication time in UTC, so the stored value and the
value the title-and-time lookup compares are the same instant on SQLite, which
keeps only the wall-clock time, and on PostgreSQL. Each test reads synthetic
feeds through the real reader and orchestrator over six runs.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Podcast
from tests.feed_identity_support import (
    DAY,
    SITE,
    FeedItem,
    Round,
    counts,
    feed_reader,
    feed_xml,
    show,
    store,
)

_RUNS = 6
_TITLES = ("Why We Started", "Listener Mail", "A Long Goodbye")

_OFFSETS = pytest.mark.parametrize(
    "offset",
    [timedelta(hours=-7), timedelta(hours=2), timedelta(hours=5, minutes=30)],
    ids=["-0700", "+0200", "+0530"],
)


def _when(offset: timedelta, index: int) -> datetime:
    """Return item ``index``'s publication time, written with ``offset``."""
    return (DAY + timedelta(days=index)).astimezone(timezone(offset))


@dataclass(frozen=True, slots=True)
class _Shape:
    """A feed over six runs, and the rows an earlier release stored for it.

    ``main_rows`` are the rows that release left after reading the same items,
    each dated with the feed's wall-clock time, as SQLite kept it;
    ``after_main`` is the row count after each run on top of them.
    """

    runs: Callable[[timedelta], list[Round]]
    main_rows: Callable[[timedelta], list[dict[str, object]]]
    items: int
    after_main: list[int]


def _homepage_items(offset: timedelta, *, guid: str | None = None) -> list[FeedItem]:
    """Return the three items linking to the homepage, optionally with one GUID."""
    return [
        FeedItem(title, guid=guid, link=SITE, published_at=_when(offset, index))
        for index, title in enumerate(_TITLES)
    ]


def _wall_clock(offset: timedelta, index: int) -> datetime:
    """Return item ``index``'s time as SQLite kept it: the local time, no offset."""
    return _when(offset, index).replace(tzinfo=None)


def _own_then_shared(offset: timedelta) -> list[Round]:
    """Two items with their own GUIDs for two runs, then one shared GUID."""
    own = [
        FeedItem(
            title,
            guid=f"urn:show:{index}",
            link=SITE,
            published_at=_when(offset, index),
        )
        for index, title in enumerate(_TITLES[:2])
    ]
    shared = [
        FeedItem(
            title, guid="urn:show:feed", link=SITE, published_at=_when(offset, index)
        )
        for index, title in enumerate(_TITLES[:2])
    ]
    return [Round(items=own)] * 2 + [Round(items=shared)] * (_RUNS - 2)


def _shared_then_unique_link(offset: timedelta) -> list[Round]:
    """Three items sharing one link for three runs, then each with its own."""

    def items(links: list[str]) -> list[FeedItem]:
        """Return the three items with these links."""
        return [
            FeedItem(title, link=link, published_at=_when(offset, index))
            for index, (title, link) in enumerate(zip(_TITLES, links, strict=True))
        ]

    shared = items([f"{SITE}listen"] * 3)
    unique = items([f"{SITE}{index}" for index in range(3)])
    return [Round(items=shared)] * 3 + [Round(items=unique)] * (_RUNS - 3)


_SHAPES = {
    "homepage-links-no-guids": _Shape(
        runs=lambda offset: [Round(items=_homepage_items(offset))] * _RUNS,
        main_rows=lambda offset: [
            {
                "title": _TITLES[0],
                "episode_url": SITE,
                "published_at": _wall_clock(offset, 0),
            },
        ],
        items=3,
        after_main=[4] * _RUNS,
    ),
    "one-guid-and-link": _Shape(
        runs=lambda offset: [
            Round(items=_homepage_items(offset, guid="urn:show:feed")[:2]),
        ]
        * _RUNS,
        main_rows=lambda offset: [
            {
                "title": _TITLES[0],
                "episode_url": SITE,
                "rss_guid": "urn:show:feed",
                "published_at": _wall_clock(offset, 0),
            },
        ],
        items=2,
        after_main=[3] * _RUNS,
    ),
    "own-guids-then-shared": _Shape(
        runs=_own_then_shared,
        main_rows=lambda offset: [
            {
                "title": title,
                "episode_url": SITE,
                "rss_guid": f"urn:show:{index}",
                "published_at": _wall_clock(offset, index),
            }
            for index, title in enumerate(_TITLES[:2])
        ],
        items=2,
        # Found by their own GUIDs until the GUID becomes shared.
        after_main=[2, 2, 4, 4, 4, 4],
    ),
    "shared-link-becomes-unique": _Shape(
        runs=_shared_then_unique_link,
        main_rows=lambda offset: [
            {
                "title": _TITLES[0],
                "episode_url": f"{SITE}listen",
                "published_at": _wall_clock(offset, 0),
            },
        ],
        items=3,
        after_main=[4] * _RUNS,
    ),
}
_SHAPE = pytest.mark.parametrize("shape", list(_SHAPES))


@_OFFSETS
@_SHAPE
def test_items_dated_with_an_offset_keep_one_row_each(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    offset: timedelta,
    shape: str,
) -> None:
    """Items found again by title and time stay one row each over six runs."""
    podcast = show(db_session)
    feed = _SHAPES[shape]

    row_counts = counts(db_session, monkeypatch, podcast, feed.runs(offset))

    assert_that(row_counts).is_equal_to([feed.items] * _RUNS)


@_OFFSETS
@_SHAPE
def test_rows_an_earlier_release_dated_by_wall_clock_add_one_stable_row_each(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    offset: timedelta,
    shape: str,
) -> None:
    """A row stored with the feed's wall-clock time is not found by time again.

    Once its item is identified by neither GUID nor URL, each such row stays
    as it is, its item gets one row of its own, and the count is then stable
    (ADR 0020, not covered).
    """
    podcast = show(db_session)
    feed = _SHAPES[shape]
    for fields in feed.main_rows(offset):
        store(db_session, podcast, **fields)

    row_counts = counts(db_session, monkeypatch, podcast, feed.runs(offset))

    assert_that(row_counts).is_equal_to(feed.after_main)


def _stub_podcast() -> Podcast:
    """Return an unsaved podcast that only carries a feed URL."""
    return Podcast(name="Feed Show", slug="feed-show", rss_url="https://f.example")


@_OFFSETS
def test_the_reader_reports_publication_times_in_utc(offset: timedelta) -> None:
    """A ``pubDate`` with an offset is reported as the same instant in UTC."""
    xml = feed_xml([FeedItem("One", published_at=_when(offset, 0))])

    episodes = feed_reader(xml).discover_episodes(_stub_podcast())

    published_at = episodes[0].published_at
    assert_that(published_at).is_equal_to(DAY)
    assert_that(published_at and published_at.utcoffset()).is_equal_to(timedelta(0))
    assert_that(published_at and published_at.tzinfo).is_equal_to(UTC)
