"""Shared public media queries for v1 and v2 API endpoints."""

from typing import Any, Literal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.query import RowReturningQuery

from podex.api.query_helpers import (
    episode_count_by_media_subquery,
    mention_count_by_media_subquery,
)
from podex.models import Media, MediaType, Mention
from podex.schemas import (
    MediaDetail,
    MediaListResponse,
    MediaResponse,
    MentionWithEpisode,
)
from podex.schemas.episode import EpisodeBrief


def escape_like_pattern(value: str) -> str:
    """Escape special LIKE pattern characters.

    Args:
        value: User input string to escape.

    Returns:
        Escaped string safe for use in ILIKE patterns.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_media_count_subqueries(
    *,
    db: Session,
) -> tuple[RowReturningQuery[Any], RowReturningQuery[Any]]:
    """Create reusable media count subqueries.

    Args:
        db: Database session.

    Returns:
        Tuple of mention-count and episode-count subqueries.
    """
    return mention_count_by_media_subquery(db), episode_count_by_media_subquery(db)


def get_media_response(
    *,
    media: Media,
    mention_count: int = 0,
    episode_count: int = 0,
) -> MediaResponse:
    """Convert a media model to a list response item.

    Args:
        media: Media model instance.
        mention_count: Total mention count.
        episode_count: Total episode count.

    Returns:
        Media response payload.
    """
    return MediaResponse(
        id=media.id,
        type=MediaType(media.type),
        title=media.title,
        author=media.author,
        cover_url=media.cover_url,
        year=media.year,
        description=media.description,
        mention_count=mention_count,
        episode_count=episode_count,
        created_at=media.created_at,
    )


def list_media_with_stats(
    *,
    db: Session,
    page: int,
    per_page: int,
    media_types: list[MediaType] | None,
    sort: Literal["mention_count", "title", "created_at"],
    order: Literal["asc", "desc"],
) -> MediaListResponse:
    """List media with pagination, filtering, and sorting.

    Args:
        db: Database session.
        page: Requested page number.
        per_page: Number of items per page.
        media_types: Optional type filters.
        sort: Sort field.
        order: Sort direction.

    Returns:
        Paginated media response.
    """
    mention_counts, episode_counts = get_media_count_subqueries(db=db)

    query = (
        db.query(
            Media,
            func.coalesce(mention_counts.c.mention_count, 0).label("mention_count"),
            func.coalesce(episode_counts.c.episode_count, 0).label("episode_count"),
        )
        .outerjoin(mention_counts, Media.id == mention_counts.c.media_id)
        .outerjoin(episode_counts, Media.id == episode_counts.c.media_id)
    )

    if media_types:
        type_values = [media_type.value for media_type in media_types]
        query = query.filter(Media.type.in_(type_values))

    total = query.count()
    sort_col = mention_counts.c.mention_count
    if sort == "mention_count":
        query = query.order_by(
            func.coalesce(sort_col, 0).desc()
            if order == "desc"
            else func.coalesce(sort_col, 0).asc()
        )
    elif sort == "title":
        query = query.order_by(
            Media.title.desc() if order == "desc" else Media.title.asc()
        )
    elif sort == "created_at":
        query = query.order_by(
            Media.created_at.desc() if order == "desc" else Media.created_at.asc()
        )
    else:
        query = query.order_by(Media.id.desc())

    media_items = query.offset((page - 1) * per_page).limit(per_page).all()
    items = [
        get_media_response(
            media=media,
            mention_count=mention_count,
            episode_count=episode_count,
        )
        for media, mention_count, episode_count in media_items
    ]

    return MediaListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


def search_media_with_stats(
    *,
    db: Session,
    query_text: str,
    page: int,
    per_page: int,
    media_types: list[MediaType] | None,
) -> MediaListResponse:
    """Search media by title or author.

    Args:
        db: Database session.
        query_text: Search query.
        page: Requested page number.
        per_page: Number of items per page.
        media_types: Optional type filters.

    Returns:
        Paginated search results.
    """
    mention_counts, episode_counts = get_media_count_subqueries(db=db)
    escaped_query = escape_like_pattern(query_text)

    query = (
        db.query(
            Media,
            func.coalesce(mention_counts.c.mention_count, 0).label("mention_count"),
            func.coalesce(episode_counts.c.episode_count, 0).label("episode_count"),
        )
        .outerjoin(mention_counts, Media.id == mention_counts.c.media_id)
        .outerjoin(episode_counts, Media.id == episode_counts.c.media_id)
        .filter(
            or_(
                Media.title.ilike(f"%{escaped_query}%", escape="\\"),
                Media.author.ilike(f"%{escaped_query}%", escape="\\"),
            )
        )
    )

    if media_types:
        type_values = [media_type.value for media_type in media_types]
        query = query.filter(Media.type.in_(type_values))

    total = query.count()
    media_items = (
        query.order_by(Media.title).offset((page - 1) * per_page).limit(per_page).all()
    )
    items = [
        get_media_response(
            media=media,
            mention_count=mention_count,
            episode_count=episode_count,
        )
        for media, mention_count, episode_count in media_items
    ]

    return MediaListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


def get_top_media_with_stats(
    *,
    db: Session,
    limit: int,
    media_type: str | None,
) -> list[MediaResponse]:
    """Get top mentioned media.

    Args:
        db: Database session.
        limit: Maximum number of items to return.
        media_type: Optional media type filter.

    Returns:
        Ranked media responses.
    """
    mention_counts, episode_counts = get_media_count_subqueries(db=db)
    query = (
        db.query(
            Media,
            func.coalesce(mention_counts.c.mention_count, 0).label("mention_count"),
            func.coalesce(episode_counts.c.episode_count, 0).label("episode_count"),
        )
        .join(mention_counts, Media.id == mention_counts.c.media_id)
        .outerjoin(episode_counts, Media.id == episode_counts.c.media_id)
    )

    if media_type:
        query = query.filter(Media.type == media_type)

    media_items = (
        query.order_by(mention_counts.c.mention_count.desc()).limit(limit).all()
    )
    return [
        get_media_response(
            media=media,
            mention_count=mention_count,
            episode_count=episode_count,
        )
        for media, mention_count, episode_count in media_items
    ]


def build_external_urls(
    *,
    media: Media,
) -> dict[str, Any]:
    """Build external URLs from media identifiers.

    Args:
        media: Media model instance.

    Returns:
        Mapping of external URL field names to URLs.
    """
    urls: dict[str, Any] = {}

    if media.imdb_id:
        urls["imdb_url"] = f"https://www.imdb.com/title/{media.imdb_id}"
    if media.tmdb_id:
        urls["tmdb_url"] = (
            f"https://www.themoviedb.org/tv/{media.tmdb_id}"
            if media.type == "tv_show"
            else f"https://www.themoviedb.org/movie/{media.tmdb_id}"
        )
    if media.wikipedia_id:
        urls["wikipedia_url"] = f"https://en.wikipedia.org/wiki/{media.wikipedia_id}"
    if media.google_books_id:
        urls["google_books_url"] = (
            f"https://books.google.com/books?id={media.google_books_id}"
        )
    if media.open_library_id:
        urls["open_library_url"] = (
            f"https://openlibrary.org/works/{media.open_library_id}"
        )
    if media.doi:
        urls["doi_url"] = f"https://doi.org/{media.doi}"
    if media.pubmed_id:
        urls["pubmed_url"] = f"https://pubmed.ncbi.nlm.nih.gov/{media.pubmed_id}"
    if media.semantic_scholar_id:
        urls["semantic_scholar_url"] = (
            f"https://www.semanticscholar.org/paper/{media.semantic_scholar_id}"
        )

    return urls


def extract_metadata_fields(
    *,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Extract structured metadata fields from ``metadata_json``.

    Args:
        metadata: Raw metadata payload from the database.

    Returns:
        Mapping of structured metadata keys to values.
    """
    if not metadata:
        return {}

    fields: dict[str, Any] = {}
    for key in [
        "imdb_rating",
        "tmdb_rating",
        "rotten_tomatoes",
        "metacritic",
        "google_books_rating",
        "awards",
        "oscar_wins",
        "oscar_nominations",
        "runtime_minutes",
        "page_count",
        "genres",
        "cast",
        "directors",
        "journal",
        "authors",
        "citation_count",
        "publication_date",
        "mesh_terms",
        "fields_of_study",
        "open_access_pdf_url",
        "tagline",
        "budget",
        "revenue",
        "production_countries",
        "spoken_languages",
        "status",
        "networks",
        "seasons",
        "episodes",
        "publisher",
        "preview_link",
        "language",
        "explicit",
        "feed_url",
        "biography",
        "birthday",
        "known_for",
        "wikipedia_categories",
    ]:
        if key in metadata:
            fields[key] = metadata[key]

    if "isbn_13" in metadata:
        fields["isbn"] = metadata["isbn_13"]
    elif "isbn_10" in metadata:
        fields["isbn"] = metadata["isbn_10"]

    if "episode_count" in metadata:
        fields["podcast_episode_count"] = metadata["episode_count"]
    if "place_of_birth" in metadata:
        fields["birthplace"] = metadata["place_of_birth"]

    return fields


def get_media_mentions(
    *,
    db: Session,
    media_id: int,
) -> list[MentionWithEpisode]:
    """Get mention occurrences for a media item.

    Args:
        db: Database session.
        media_id: Internal media identifier.

    Returns:
        Mention occurrences with episode context.
    """
    mentions = (
        db.query(Mention)
        .options(joinedload(Mention.episode))
        .filter(Mention.media_id == media_id)
        .order_by(Mention.episode_id.desc())
        .all()
    )

    mention_responses: list[MentionWithEpisode] = []
    for mention in mentions:
        episode = mention.episode
        youtube_url = None
        if episode.youtube_id and mention.timestamp_seconds is not None:
            youtube_url = f"https://youtube.com/watch?v={episode.youtube_id}&t={mention.timestamp_seconds}"

        mention_responses.append(
            MentionWithEpisode(
                id=mention.id,
                episode=EpisodeBrief(
                    id=episode.id,
                    title=episode.title,
                    episode_number=episode.episode_number,
                    youtube_id=episode.youtube_id,
                    published_at=episode.published_at,
                    thumbnail_url=episode.thumbnail_url,
                ),
                timestamp_seconds=mention.timestamp_seconds,
                context=mention.context,
                confidence=mention.confidence,
                youtube_timestamp_url=youtube_url,
            )
        )

    return mention_responses


def get_media_detail_by_id(
    *,
    db: Session,
    media_id: int,
) -> MediaDetail | None:
    """Get media detail with mentions and enrichment fields.

    Args:
        db: Database session.
        media_id: Internal media identifier.

    Returns:
        Detailed media response when found, otherwise ``None``.
    """
    media = db.query(Media).filter(Media.id == media_id).first()
    if media is None:
        return None

    mention_responses = get_media_mentions(db=db, media_id=media_id)
    mention_count = len(mention_responses)
    episode_count = len({mention.episode.id for mention in mention_responses})
    external_urls = build_external_urls(media=media)
    metadata_fields = extract_metadata_fields(metadata=media.metadata_json)

    return MediaDetail(
        id=media.id,
        type=MediaType(media.type),
        title=media.title,
        author=media.author,
        cover_url=media.cover_url,
        year=media.year,
        description=media.description,
        mention_count=mention_count,
        episode_count=episode_count,
        created_at=media.created_at,
        mentions=mention_responses,
        google_books_id=media.google_books_id,
        open_library_id=media.open_library_id,
        imdb_id=media.imdb_id,
        tmdb_id=media.tmdb_id,
        wikipedia_id=media.wikipedia_id,
        doi=media.doi,
        pubmed_id=media.pubmed_id,
        semantic_scholar_id=media.semantic_scholar_id,
        metadata_json=media.metadata_json,
        enriched_at=media.enriched_at,
        enrichment_source=media.enrichment_source,
        verification_sources=media.verification_sources or [],
        doi_verified=media.doi_verified or False,
        **external_urls,
        **metadata_fields,
    )
