"""Tests for ops podcast management queries, commands, and audit log."""

from typing import Any, NoReturn

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from podex.models import AuditAction, AuditLog, DiscoverySource, Podcast, PodcastStatus
from podex.services.audit_log import list_audit_logs, record_audit_log
from podex.services.ops_podcast_commands import (
    CreateOpsPodcastInputData,
    OpsPodcastSourceInputData,
    OpsPodcastValidationError,
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
from tests.conftest import enable_ops, seed_catalog_graph

_OPS_KEY = "ops-podcasts-test-key"
_SOURCE_FIELDS = (
    "rss_url",
    "spotify_id",
    "apple_id",
    "youtube_channel_id",
    "podscripts_slug",
)


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


def _stored_row(db: Session, podcast_id: int) -> dict[str, Any]:
    """Read the podcast row with SQL, bypassing the session's pending changes."""
    row = db.execute(
        text(
            "SELECT name, slug, status, description, discovery_source, cover_url, "
            "rss_url, spotify_id, apple_id, youtube_channel_id, podscripts_slug "
            "FROM podcasts WHERE id = :podcast_id",
        ),
        {"podcast_id": podcast_id},
    ).mappings()
    return dict(row.one())


def _podcast_count(db: Session) -> int:
    """Return the number of podcast rows."""
    count: int | None = db.scalar(select(func.count()).select_from(Podcast))
    return count or 0


def _existing_podcast(db: Session) -> int:
    """Create and commit one fully populated podcast; return its id."""
    created = create_ops_podcast(
        db=db,
        payload=CreateOpsPodcastInputData(
            name="The Example Show",
            slug="example-show",
            status=PodcastStatus.ACTIVE,
            description="Original description.",
            cover_url="https://cdn.example/cover.jpg",
            discovery_source="rss",
            sources=OpsPodcastSourceInputData(
                rss_url="https://example.com/feed.xml",
                spotify_id="spotify-original",
                apple_id="apple-original",
                youtube_channel_id="youtube-original",
                podscripts_slug="podscripts-original",
            ),
        ),
    )
    db.commit()
    podcast_id: int = created.id
    return podcast_id


def test_create_rejects_unknown_discovery_source_without_writing(
    db_session: Session,
) -> None:
    """An unknown discovery source fails creation before any row is added."""
    with pytest.raises(ValueError, match="'bogus' is not a valid DiscoverySource"):
        create_ops_podcast(
            db=db_session,
            payload=CreateOpsPodcastInputData(
                name="The Example Show",
                slug="example-show",
                status=PodcastStatus.WATCHLIST,
                discovery_source="bogus",
            ),
        )

    assert_that(db_session.new).is_empty()
    assert_that(_podcast_count(db_session)).is_zero()


def test_create_stores_description_and_cover(db_session: Session) -> None:
    """Description and cover URL given on creation are stored and returned."""
    podcast_id = _existing_podcast(db_session)

    row = _stored_row(db_session, podcast_id)
    created = get_ops_podcast_by_id(db=db_session, podcast_id=podcast_id)

    assert_that((row["description"], row["cover_url"])).is_equal_to(
        ("Original description.", "https://cdn.example/cover.jpg"),
    )
    if created is None:
        raise AssertionError("expected the podcast to be found")
    assert_that((created.description, created.cover_url)).is_equal_to(
        ("Original description.", "https://cdn.example/cover.jpg"),
    )


def test_create_leaves_the_transaction_to_the_caller(db_session: Session) -> None:
    """Creation only flushes; a caller that rolls back keeps no row."""
    created = create_ops_podcast(
        db=db_session,
        payload=CreateOpsPodcastInputData(
            name="The Example Show",
            slug="example-show",
            status=PodcastStatus.WATCHLIST,
        ),
    )
    assert_that(created.id).is_not_none()

    db_session.rollback()

    assert_that(_podcast_count(db_session)).is_zero()


def test_update_leaves_the_transaction_to_the_caller(db_session: Session) -> None:
    """An update only flushes; a caller that rolls back keeps the stored row."""
    podcast_id = _existing_podcast(db_session)
    before = _stored_row(db_session, podcast_id)

    updated = update_ops_podcast(
        db=db_session,
        podcast_id=podcast_id,
        payload=UpdateOpsPodcastInputData(
            provided_fields=frozenset({"name"}),
            source_fields=frozenset({"rss_url"}),
            name="Renamed Show",
            sources=OpsPodcastSourceInputData(rss_url="https://example.com/new.xml"),
        ),
    )
    assert_that(updated.name if updated else None).is_equal_to("Renamed Show")

    db_session.rollback()

    assert_that(_stored_row(db_session, podcast_id)).is_equal_to(before)


def test_create_without_discovery_source_stores_none(db_session: Session) -> None:
    """Omitting the discovery source stores no source and no identifiers."""
    created = create_ops_podcast(
        db=db_session,
        payload=CreateOpsPodcastInputData(
            name="The Example Show",
            slug="example-show",
            status=PodcastStatus.WATCHLIST,
        ),
    )
    db_session.commit()

    assert_that(created.status).is_equal_to(PodcastStatus.WATCHLIST)
    assert_that(_stored_row(db_session, created.id)["discovery_source"]).is_none()
    assert_that(created.sources.rss_url).is_none()


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("name", "name cannot be cleared"),
        ("slug", "slug cannot be cleared"),
        ("status", "status cannot be cleared"),
    ],
)
def test_update_rejects_clearing_a_required_field(
    db_session: Session,
    field: str,
    message: str,
) -> None:
    """Clearing name, slug or status is rejected and no row changes."""
    podcast_id = _existing_podcast(db_session)
    before = _stored_row(db_session, podcast_id)

    with pytest.raises(ValueError) as caught:
        update_ops_podcast(
            db=db_session,
            podcast_id=podcast_id,
            payload=UpdateOpsPodcastInputData(provided_fields=frozenset({field})),
        )

    assert_that(str(caught.value)).is_equal_to(message)
    assert_that(_stored_row(db_session, podcast_id)).is_equal_to(before)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            UpdateOpsPodcastInputData(
                provided_fields=frozenset({"name", "slug", "cover_url"}),
                name="Renamed Show",
                slug=None,
                cover_url=None,
            ),
            "^slug cannot be cleared$",
        ),
        (
            UpdateOpsPodcastInputData(
                provided_fields=frozenset({"name", "status"}),
                name="Renamed Show",
                status=None,
            ),
            "^status cannot be cleared$",
        ),
        (
            UpdateOpsPodcastInputData(
                provided_fields=frozenset(
                    {"name", "slug", "status", "description", "discovery_source"},
                ),
                source_fields=frozenset({"rss_url"}),
                name="Renamed Show",
                slug="example-show-renamed",
                status=PodcastStatus.PAUSED,
                description="Replaced description.",
                discovery_source="bogus",
                sources=OpsPodcastSourceInputData(
                    rss_url="https://example.com/new.xml",
                ),
            ),
            "'bogus' is not a valid DiscoverySource",
        ),
    ],
    ids=["cleared-slug", "cleared-status", "unknown-discovery-source"],
)
def test_rejected_update_leaves_the_loaded_podcast_untouched(
    db_session: Session,
    payload: UpdateOpsPodcastInputData,
    message: str,
) -> None:
    """A rejected update assigns none of its valid fields, even in memory."""
    podcast_id = _existing_podcast(db_session)
    before = _stored_row(db_session, podcast_id)

    with pytest.raises(ValueError, match=message):
        update_ops_podcast(db=db_session, podcast_id=podcast_id, payload=payload)

    assert_that(db_session.dirty).is_empty()
    loaded = db_session.get(Podcast, podcast_id)
    if loaded is None:
        raise AssertionError("expected the podcast to be loaded")
    assert_that(db_session.is_modified(loaded)).is_false()
    assert_that(loaded.name).is_equal_to("The Example Show")
    assert_that(_stored_row(db_session, podcast_id)).is_equal_to(before)


@pytest.mark.parametrize(
    ("provided_fields", "message"),
    [
        (
            frozenset({"name", "slug", "status", "discovery_source"}),
            "^name cannot be cleared$",
        ),
        (
            frozenset({"slug", "status", "discovery_source"}),
            "^slug cannot be cleared$",
        ),
        (
            frozenset({"status", "discovery_source"}),
            "^status cannot be cleared$",
        ),
    ],
    ids=["name-first", "slug-before-status", "status-before-discovery-source"],
)
def test_update_reports_the_first_invalid_field_in_a_fixed_order(
    db_session: Session,
    provided_fields: frozenset[str],
    message: str,
) -> None:
    """Name, slug and status are checked in that order, before discovery source."""
    podcast_id = _existing_podcast(db_session)

    with pytest.raises(ValueError, match=message):
        update_ops_podcast(
            db=db_session,
            podcast_id=podcast_id,
            payload=UpdateOpsPodcastInputData(
                provided_fields=provided_fields,
                name=None,
                slug=None,
                status=None,
                discovery_source="bogus",
            ),
        )


def test_update_rejects_unknown_discovery_source(db_session: Session) -> None:
    """An unknown discovery source is rejected and the stored source is kept."""
    podcast_id = _existing_podcast(db_session)

    with pytest.raises(ValueError, match="'bogus' is not a valid DiscoverySource"):
        update_ops_podcast(
            db=db_session,
            podcast_id=podcast_id,
            payload=UpdateOpsPodcastInputData(
                provided_fields=frozenset({"discovery_source"}),
                discovery_source="bogus",
            ),
        )

    # The enum column stores member names.
    assert_that(_stored_row(db_session, podcast_id)["discovery_source"]).is_equal_to(
        DiscoverySource.RSS.name,
    )


@pytest.mark.parametrize("value", ["spotify", None])
def test_update_sets_or_clears_cover_and_discovery_source(
    db_session: Session,
    value: str | None,
) -> None:
    """Provided cover and discovery source fields are written, including None."""
    podcast_id = _existing_podcast(db_session)
    cover = None if value is None else "https://cdn.example/new.jpg"

    updated = update_ops_podcast(
        db=db_session,
        podcast_id=podcast_id,
        payload=UpdateOpsPodcastInputData(
            provided_fields=frozenset({"cover_url", "discovery_source"}),
            cover_url=cover,
            discovery_source=value,
        ),
    )
    db_session.commit()

    assert_that(updated).is_not_none()
    row = _stored_row(db_session, podcast_id)
    assert_that(row["cover_url"]).is_equal_to(cover)
    assert_that(row["discovery_source"]).is_equal_to(
        None if value is None else DiscoverySource(value).name,
    )
    assert_that(row["name"]).is_equal_to("The Example Show")


def test_update_writes_status_and_slug(db_session: Session) -> None:
    """Provided status and slug values replace the stored ones."""
    podcast_id = _existing_podcast(db_session)

    updated = update_ops_podcast(
        db=db_session,
        podcast_id=podcast_id,
        payload=UpdateOpsPodcastInputData(
            provided_fields=frozenset({"status", "slug"}),
            status=PodcastStatus.PAUSED,
            slug="example-show-renamed",
        ),
    )
    db_session.commit()

    if updated is None:
        raise AssertionError("expected the podcast to be found")
    assert_that(updated.status).is_equal_to(PodcastStatus.PAUSED)
    assert_that(updated.slug).is_equal_to("example-show-renamed")
    assert_that(updated.description).is_equal_to("Original description.")
    assert_that(_stored_row(db_session, podcast_id)["description"]).is_equal_to(
        "Original description.",
    )
    assert_that(_stored_row(db_session, podcast_id)["status"]).is_equal_to(
        PodcastStatus.PAUSED.name,
    )


@pytest.mark.parametrize("field", _SOURCE_FIELDS)
def test_update_changes_only_the_listed_source_field(
    db_session: Session,
    field: str,
) -> None:
    """Only the source fields named in ``source_fields`` are written."""
    podcast_id = _existing_podcast(db_session)
    replacement = OpsPodcastSourceInputData(
        **{name: f"{name}-replacement" for name in _SOURCE_FIELDS},
    )

    updated = update_ops_podcast(
        db=db_session,
        podcast_id=podcast_id,
        payload=UpdateOpsPodcastInputData(
            source_fields=frozenset({field}),
            sources=replacement,
        ),
    )
    db_session.commit()

    if updated is None:
        raise AssertionError("expected the podcast to be found")
    sources = updated.sources
    original = {
        "rss_url": "https://example.com/feed.xml",
        "spotify_id": "spotify-original",
        "apple_id": "apple-original",
        "youtube_channel_id": "youtube-original",
        "podscripts_slug": "podscripts-original",
    }
    expected = {**original, field: f"{field}-replacement"}
    assert_that({name: getattr(sources, name) for name in _SOURCE_FIELDS}).is_equal_to(
        expected,
    )


def test_update_clears_every_listed_source_field(db_session: Session) -> None:
    """Listing every source field with empty values clears all identifiers."""
    podcast_id = _existing_podcast(db_session)

    updated = update_ops_podcast(
        db=db_session,
        podcast_id=podcast_id,
        payload=UpdateOpsPodcastInputData(
            source_fields=frozenset(_SOURCE_FIELDS),
            sources=OpsPodcastSourceInputData(),
        ),
    )
    db_session.commit()

    if updated is None:
        raise AssertionError("expected the podcast to be found")
    assert_that(
        [getattr(updated.sources, name) for name in _SOURCE_FIELDS],
    ).is_equal_to([None] * len(_SOURCE_FIELDS))


def test_source_field_names_without_sources_change_nothing(
    db_session: Session,
) -> None:
    """Naming source fields without a sources payload leaves identifiers intact."""
    podcast_id = _existing_podcast(db_session)
    before = _stored_row(db_session, podcast_id)

    updated = update_ops_podcast(
        db=db_session,
        podcast_id=podcast_id,
        payload=UpdateOpsPodcastInputData(source_fields=frozenset(_SOURCE_FIELDS)),
    )
    db_session.commit()

    if updated is None:
        raise AssertionError("expected the podcast to be found")
    assert_that(updated.id).is_equal_to(podcast_id)
    assert_that(
        {name: getattr(updated.sources, name) for name in _SOURCE_FIELDS},
    ).is_equal_to(
        {
            "rss_url": "https://example.com/feed.xml",
            "spotify_id": "spotify-original",
            "apple_id": "apple-original",
            "youtube_channel_id": "youtube-original",
            "podscripts_slug": "podscripts-original",
        },
    )
    assert_that(_stored_row(db_session, podcast_id)).is_equal_to(before)


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"name": None}, "name cannot be cleared"),
        ({"name": "Renamed Show", "slug": None}, "slug cannot be cleared"),
        ({"status": None}, "status cannot be cleared"),
        ({"discovery_source": "bogus"}, "'bogus' is not a valid DiscoverySource"),
    ],
    ids=["name", "slug-with-rename", "status", "discovery-source"],
)
def test_api_update_validation_returns_422_and_changes_nothing(
    client: TestClient,
    db_session: Session,
    body: dict[str, Any],
    detail: str,
) -> None:
    """The ops endpoint reports the service's validation message as a 422."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _existing_podcast(db_session)
    before = _stored_row(db_session, podcast_id)

    response = client.patch(
        f"/api/v2/ops/podcasts/{podcast_id}",
        json=body,
        headers={"X-Ops-Key": _OPS_KEY},
    )

    assert_that(response.status_code).is_equal_to(422)
    assert_that(response.json()["detail"]).is_equal_to(detail)
    assert_that(_stored_row(db_session, podcast_id)).is_equal_to(before)
    assert_that(
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    ).is_zero()


_CREATE_BODY: dict[str, Any] = {"name": "Example", "slug": "example"}


def _error_body(response: Response) -> dict[str, Any]:
    """Return an error body without its per-request correlation id."""
    body: dict[str, Any] = response.json()
    return {key: value for key, value in body.items() if key != "request_id"}


def test_api_create_rejects_unknown_discovery_source_like_update(
    client: TestClient,
    db_session: Session,
) -> None:
    """Create answers an unknown discovery source with update's 422 body."""
    enable_ops(client, api_key=_OPS_KEY)

    created = client.post(
        "/api/v2/ops/podcasts",
        json={**_CREATE_BODY, "discovery_source": "bogus"},
        headers={"X-Ops-Key": _OPS_KEY},
    )

    assert_that(created.status_code).is_equal_to(422)
    assert_that(db_session.new).is_empty()
    db_session.expire_all()
    assert_that(_podcast_count(db_session)).is_zero()
    assert_that(
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    ).is_zero()

    podcast_id = _existing_podcast(db_session)
    updated = client.patch(
        f"/api/v2/ops/podcasts/{podcast_id}",
        json={"discovery_source": "bogus"},
        headers={"X-Ops-Key": _OPS_KEY},
    )

    assert_that(updated.status_code).is_equal_to(422)
    assert_that(_error_body(created)).is_equal_to(_error_body(updated))
    assert_that(created.json()["detail"]).is_equal_to(
        "'bogus' is not a valid DiscoverySource",
    )


@pytest.mark.parametrize("field", ["status", "discovery_source"])
def test_api_create_rejects_unknown_enum_values_without_writing(
    client: TestClient,
    db_session: Session,
    field: str,
) -> None:
    """Unknown status or discovery source values on create answer 422."""
    enable_ops(client, api_key=_OPS_KEY)

    response = client.post(
        "/api/v2/ops/podcasts",
        json={**_CREATE_BODY, field: "bogus"},
        headers={"X-Ops-Key": _OPS_KEY},
    )

    assert_that(response.status_code).is_equal_to(422)
    db_session.expire_all()
    assert_that(_podcast_count(db_session)).is_zero()
    assert_that(
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    ).is_zero()


@pytest.mark.parametrize("field", ["status", "discovery_source"])
def test_api_update_rejects_unknown_enum_values_without_writing(
    client: TestClient,
    db_session: Session,
    field: str,
) -> None:
    """Unknown status or discovery source values on update answer 422."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _existing_podcast(db_session)
    before = _stored_row(db_session, podcast_id)

    response = client.patch(
        f"/api/v2/ops/podcasts/{podcast_id}",
        json={"name": "Renamed Show", field: "bogus"},
        headers={"X-Ops-Key": _OPS_KEY},
    )

    assert_that(response.status_code).is_equal_to(422)
    assert_that(_stored_row(db_session, podcast_id)).is_equal_to(before)
    assert_that(
        db_session.scalar(select(func.count()).select_from(AuditLog)),
    ).is_zero()


def test_api_create_and_update_reject_unknown_status_with_the_same_body(
    client: TestClient,
    db_session: Session,
) -> None:
    """Both routes report an unknown status as the same request validation error."""
    enable_ops(client, api_key=_OPS_KEY)
    podcast_id = _existing_podcast(db_session)

    created = client.post(
        "/api/v2/ops/podcasts",
        json={**_CREATE_BODY, "status": "bogus"},
        headers={"X-Ops-Key": _OPS_KEY},
    )
    updated = client.patch(
        f"/api/v2/ops/podcasts/{podcast_id}",
        json={"status": "bogus"},
        headers={"X-Ops-Key": _OPS_KEY},
    )

    assert_that((created.status_code, updated.status_code)).is_equal_to((422, 422))
    assert_that(_error_body(created)).is_equal_to(_error_body(updated))
    assert_that(created.json()["errors"][0]["loc"]).is_equal_to(["body", "status"])


def _create_then_reject(
    *,
    db: Session,
    payload: CreateOpsPodcastInputData,
) -> NoReturn:
    """Write a podcast to the session, then fail as an invalid payload would."""
    db.add(
        Podcast(
            name=payload.name,
            slug=payload.slug,
            status=payload.status.value,
        ),
    )
    db.flush()
    raise OpsPodcastValidationError("'bogus' is not a valid DiscoverySource")


def test_api_create_rolls_back_writes_made_before_a_rejection(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected create discards what the service flushed before it raised."""
    enable_ops(client, api_key=_OPS_KEY)
    monkeypatch.setattr("podex.api.v2.ops.create_ops_podcast", _create_then_reject)

    response = client.post(
        "/api/v2/ops/podcasts",
        json=_CREATE_BODY,
        headers={"X-Ops-Key": _OPS_KEY},
    )

    assert_that(response.status_code).is_equal_to(422)
    assert_that(response.json()["detail"]).is_equal_to(
        "'bogus' is not a valid DiscoverySource",
    )
    assert_that(db_session.in_transaction()).is_false()
    assert_that(_podcast_count(db_session)).is_zero()
    db_session.rollback()
    assert_that(_podcast_count(db_session)).is_zero()


def test_api_create_does_not_map_an_audit_failure_to_422(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ValueError raised after creation succeeded is not a validation error."""
    enable_ops(client, api_key=_OPS_KEY)

    def _failing_audit(**_: object) -> NoReturn:
        """Fail the way an audit write error would."""
        raise ValueError("audit failure")

    monkeypatch.setattr("podex.api.v2.ops.record_audit_log", _failing_audit)

    with pytest.raises(ValueError, match="^audit failure$"):
        client.post(
            "/api/v2/ops/podcasts",
            json=_CREATE_BODY,
            headers={"X-Ops-Key": _OPS_KEY},
        )


def test_api_create_does_not_map_other_errors_to_422(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a ValueError from the service is reported as a 422."""
    enable_ops(client, api_key=_OPS_KEY)

    def _failing_create(**_: object) -> NoReturn:
        """Fail with an error that is not a validation error."""
        raise RuntimeError("Created podcast could not be reloaded")

    monkeypatch.setattr("podex.api.v2.ops.create_ops_podcast", _failing_create)

    with pytest.raises(RuntimeError, match="^Created podcast could not be reloaded$"):
        client.post(
            "/api/v2/ops/podcasts",
            json=_CREATE_BODY,
            headers={"X-Ops-Key": _OPS_KEY},
        )
