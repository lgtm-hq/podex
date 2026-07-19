"""Tests for ops podcast management queries, commands, and audit log."""

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import AuditAction, Podcast, PodcastStatus
from podex.services.audit_log import list_audit_logs, record_audit_log
from podex.services.ops_podcast_commands import (
    CreateOpsPodcastInputData,
    OpsPodcastSourceInputData,
    UpdateOpsPodcastInputData,
    archive_ops_podcast,
    create_ops_podcast,
    update_ops_podcast,
)
from podex.services.ops_podcast_queries import (
    PodcastSourceType,
    get_ops_podcast_by_id,
    list_ops_podcasts,
)
from tests.conftest import seed_catalog_graph


def test_create_and_get_ops_podcast(db_session: Session) -> None:
    """Creation persists sources and reload returns aggregated counts."""
    created = create_ops_podcast(
        db=db_session,
        payload=CreateOpsPodcastInputData(
            name="The Example Show",
            slug="example-show",
            status=PodcastStatus.ACTIVE,
            description="A show about examples.",
            cover_url="https://cdn.example/cover.jpg",
            discovery_source="rss",
            sources=OpsPodcastSourceInputData(rss_url="https://example.com/feed.xml"),
        ),
    )
    db_session.commit()

    assert_that(created.slug).is_equal_to("example-show")
    assert_that(created.status).is_equal_to(PodcastStatus.ACTIVE)
    assert_that(created.sources.rss_url).is_equal_to("https://example.com/feed.xml")
    assert_that(created.episode_count).is_equal_to(0)
    loaded = get_ops_podcast_by_id(db=db_session, podcast_id=created.id)
    assert_that(loaded).is_not_none()
    assert_that(get_ops_podcast_by_id(db=db_session, podcast_id=999_999)).is_none()


def test_list_ops_podcasts_filters_and_sorts(db_session: Session) -> None:
    """Listing supports status filters, source filters, and count sorting."""
    graph = seed_catalog_graph(db_session)
    podcast = db_session.query(Podcast).filter(Podcast.id == graph.podcast_id).one()
    podcast.rss_url = "https://example.com/feed.xml"
    create_ops_podcast(
        db=db_session,
        payload=CreateOpsPodcastInputData(
            name="Quiet Show",
            slug="quiet-show",
            status=PodcastStatus.PAUSED,
        ),
    )
    db_session.commit()

    everything = list_ops_podcasts(db=db_session, page=1, per_page=10)
    assert_that(everything.total).is_equal_to(2)

    paused = list_ops_podcasts(
        db=db_session,
        page=1,
        per_page=10,
        status=PodcastStatus.PAUSED,
    )
    assert_that(paused.total).is_equal_to(1)
    assert_that(paused.items[0].slug).is_equal_to("quiet-show")

    rss_only = list_ops_podcasts(
        db=db_session,
        page=1,
        per_page=10,
        source=PodcastSourceType.RSS,
    )
    assert_that(rss_only.total).is_equal_to(1)
    assert_that(rss_only.items[0].slug).is_equal_to("example-show")

    by_mentions = list_ops_podcasts(
        db=db_session,
        page=1,
        per_page=10,
        sort="mention_count",
        order="desc",
    )
    assert_that(by_mentions.items[0].slug).is_equal_to("example-show")
    assert_that(by_mentions.items[0].mention_count).is_equal_to(1)


def test_update_and_archive_ops_podcast(db_session: Session) -> None:
    """Partial updates only touch provided fields; archive pauses processing."""
    created = create_ops_podcast(
        db=db_session,
        payload=CreateOpsPodcastInputData(
            name="The Example Show",
            slug="example-show",
            status=PodcastStatus.ACTIVE,
            description="Original description.",
        ),
    )
    db_session.commit()

    updated = update_ops_podcast(
        db=db_session,
        podcast_id=created.id,
        payload=UpdateOpsPodcastInputData(
            provided_fields=frozenset({"name", "description"}),
            source_fields=frozenset({"spotify_id"}),
            name="The Example Show — Renamed",
            description=None,
            sources=OpsPodcastSourceInputData(spotify_id="abc123"),
        ),
    )
    db_session.commit()

    assert_that(updated).is_not_none()
    if updated is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(updated.name).is_equal_to("The Example Show — Renamed")
    assert_that(updated.description).is_none()
    assert_that(updated.slug).is_equal_to("example-show")
    assert_that(updated.sources.spotify_id).is_equal_to("abc123")
    assert_that(
        update_ops_podcast(
            db=db_session,
            podcast_id=999_999,
            payload=UpdateOpsPodcastInputData(),
        ),
    ).is_none()

    archived = archive_ops_podcast(db=db_session, podcast_id=created.id)
    db_session.commit()
    assert_that(archived).is_not_none()
    if archived is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(archived.status).is_equal_to(PodcastStatus.PAUSED)
    assert_that(archive_ops_podcast(db=db_session, podcast_id=999_999)).is_none()


def test_audit_log_records_and_lists(db_session: Session) -> None:
    """Audit entries persist immutably and list newest first with paging."""
    record_audit_log(
        db=db_session,
        action=AuditAction.CREATE_PODCAST,
        resource_type="podcast",
        resource_id=1,
        resource_identifier="example-show",
        actor_name="operator",
        summary="Created podcast example-show",
        metadata_json={"slug": "example-show"},
    )
    record_audit_log(
        db=db_session,
        action=AuditAction.ARCHIVE_PODCAST,
        resource_type="podcast",
        resource_id=1,
        summary="Archived podcast example-show",
    )
    db_session.commit()

    listed = list_audit_logs(db=db_session, page=1, per_page=10)
    assert_that(listed.total).is_equal_to(2)
    assert_that(listed.items[0].action).is_equal_to(AuditAction.ARCHIVE_PODCAST)
    assert_that(listed.items[1].metadata_json).is_equal_to({"slug": "example-show"})

    filtered = list_audit_logs(
        db=db_session,
        page=1,
        per_page=10,
        action=AuditAction.CREATE_PODCAST,
    )
    assert_that(filtered.total).is_equal_to(1)

    paged = list_audit_logs(db=db_session, page=2, per_page=1)
    assert_that(paged.items).is_length(1)
    assert_that(paged.items[0].action).is_equal_to(AuditAction.CREATE_PODCAST)
