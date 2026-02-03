"""Public v2 API endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from podex.api.v2.identifiers import (
    decode_episode_id,
    decode_media_id,
    decode_podcast_id,
    encode_episode_id,
    encode_media_id,
    encode_mention_id,
    encode_podcast_id,
)
from podex.api.v2.schemas import (
    PodcastEpisodeListResponse,
    PublicEpisodeDetail,
    PublicEpisodeListResponse,
    PublicEpisodeMediaMention,
    PublicEpisodeReference,
    PublicEpisodeSummary,
    PublicGlobalSearchResponse,
    PublicMediaDetail,
    PublicMediaListResponse,
    PublicMediaReference,
    PublicMediaSummary,
    PublicMentionOccurrence,
    PublicPodcastDetail,
    PublicPodcastSummary,
    PublicSearchResultGroup,
    PublicSearchResultItem,
    PublicTrendingMediaItem,
    PublicTrendsByTypeSummary,
    PublicTrendsOverview,
    PublicTrendsResponse,
)
from podex.database import get_db
from podex.models import MediaType
from podex.schemas import (
    EpisodeBrief,
    EpisodeWithStats,
    MediaDetail,
    MediaResponse,
    MentionWithEpisode,
    MentionWithMedia,
    PodcastWithStats,
)
from podex.services.public_catalog import (
    get_podcast_with_stats,
    list_podcast_episodes_with_stats,
    list_podcasts_with_stats,
)
from podex.services.public_episodes import (
    EpisodeDetailData,
    get_episode_detail_by_id,
    get_episode_mentions,
    list_episodes_with_stats,
)
from podex.services.public_media import (
    get_media_detail_by_id,
    get_media_mentions,
    list_media_with_stats,
)
from podex.services.public_search import (
    GlobalSearchResult,
)
from podex.services.public_search import (
    SearchResultGroup as ServiceSearchResultGroup,
)
from podex.services.public_search import (
    SearchResultItem as ServiceSearchResultItem,
)
from podex.services.public_search import (
    global_search as run_global_search,
)
from podex.services.search import get_search_client
from podex.services.trend_queries import (
    MediaTypeStatsData,
    OverviewStatsData,
    PublicTrendsData,
    TopMentionedMediaData,
    get_public_trends,
)

router = APIRouter(tags=["v2-public"])


def _to_public_trends_overview(
    *,
    overview: OverviewStatsData,
) -> PublicTrendsOverview:
    """Convert shared overview stats to the public trends schema.

    Args:
        overview: Shared overview statistics.

    Returns:
        Public trends overview payload.
    """
    return PublicTrendsOverview(
        total_podcasts=overview.total_podcasts,
        total_episodes=overview.total_episodes,
        total_media=overview.total_media,
        total_mentions=overview.total_mentions,
        total_books=overview.total_books,
        total_movies=overview.total_movies,
    )


def _to_public_trends_by_type_summary(
    *,
    item: MediaTypeStatsData,
) -> PublicTrendsByTypeSummary:
    """Convert shared media-type stats to the public trends schema.

    Args:
        item: Shared media-type statistics.

    Returns:
        Public trends by-type summary.
    """
    return PublicTrendsByTypeSummary(
        type=item.type,
        count=item.count,
        mention_count=item.mention_count,
    )


def _to_public_trending_media_item(
    *,
    item: TopMentionedMediaData,
) -> PublicTrendingMediaItem:
    """Convert shared top-mentioned media to the public trends schema.

    Args:
        item: Shared top-mentioned media summary.

    Returns:
        Public trending media item.
    """
    return PublicTrendingMediaItem(
        id=encode_media_id(media_id=item.id),
        type=item.type,
        title=item.title,
        author=item.author,
        mention_count=item.mention_count,
    )


def _to_public_trends_response(*, trends: PublicTrendsData) -> PublicTrendsResponse:
    """Convert shared trend data to the public v2 schema.

    Args:
        trends: Shared trend data.

    Returns:
        Public v2 trends response.
    """
    return PublicTrendsResponse(
        overview=_to_public_trends_overview(overview=trends.overview),
        by_type=[
            _to_public_trends_by_type_summary(item=item) for item in trends.by_type
        ],
        top_mentioned=[
            _to_public_trending_media_item(item=item) for item in trends.top_mentioned
        ],
    )


def _to_public_podcast_summary(*, podcast: PodcastWithStats) -> PublicPodcastSummary:
    """Convert a podcast payload to a v2 summary.

    Args:
        podcast: Podcast payload with stats.

    Returns:
        Public podcast summary.
    """
    return PublicPodcastSummary(
        id=encode_podcast_id(podcast_id=podcast.id),
        name=podcast.name,
        slug=podcast.slug,
        description=podcast.description,
        cover_url=podcast.cover_url,
        created_at=podcast.created_at,
        episode_count=podcast.episode_count,
        mention_count=podcast.mention_count,
    )


def _to_public_episode_summary(*, episode: EpisodeWithStats) -> PublicEpisodeSummary:
    """Convert an episode payload to a v2 summary.

    Args:
        episode: Episode payload with stats.

    Returns:
        Public episode summary.
    """
    return PublicEpisodeSummary(
        id=encode_episode_id(episode_id=episode.id),
        podcast_id=encode_podcast_id(podcast_id=episode.podcast_id),
        title=episode.title,
        episode_number=episode.episode_number,
        youtube_id=episode.youtube_id,
        published_at=episode.published_at,
        duration_seconds=episode.duration_seconds,
        thumbnail_url=episode.thumbnail_url,
        transcript_status=episode.transcript_status,
        created_at=episode.created_at,
        mention_count=episode.mention_count,
    )


def _to_public_episode_reference(*, episode: EpisodeBrief) -> PublicEpisodeReference:
    """Convert a nested episode object to a public reference.

    Args:
        episode: EpisodeBrief-compatible object.

    Returns:
        Public episode reference.
    """
    return PublicEpisodeReference(
        id=encode_episode_id(episode_id=episode.id),
        title=episode.title,
        episode_number=episode.episode_number,
        youtube_id=episode.youtube_id,
        published_at=episode.published_at,
        thumbnail_url=episode.thumbnail_url,
    )


def _to_public_mention_occurrence(
    *,
    mention: MentionWithEpisode,
) -> PublicMentionOccurrence:
    """Convert a mention occurrence to the public v2 shape.

    Args:
        mention: Mention occurrence with nested episode context.

    Returns:
        Public mention occurrence.
    """
    return PublicMentionOccurrence(
        id=encode_mention_id(mention_id=mention.id),
        episode=_to_public_episode_reference(episode=mention.episode),
        timestamp_seconds=mention.timestamp_seconds,
        context=mention.context,
        confidence=mention.confidence,
        youtube_timestamp_url=mention.youtube_timestamp_url,
    )


def _to_public_media_summary(*, media: MediaResponse) -> PublicMediaSummary:
    """Convert a media payload to a v2 summary.

    Args:
        media: Media payload.

    Returns:
        Public media summary.
    """
    return PublicMediaSummary(
        id=encode_media_id(media_id=media.id),
        type=media.type,
        title=media.title,
        author=media.author,
        cover_url=media.cover_url,
        year=media.year,
        description=media.description,
        mention_count=media.mention_count,
        episode_count=media.episode_count,
        created_at=media.created_at,
    )


def _to_public_media_reference(*, mention: MentionWithMedia) -> PublicMediaReference:
    """Convert a mention payload into a nested public media reference.

    Args:
        mention: Mention payload with media context.

    Returns:
        Nested media reference.
    """
    return PublicMediaReference(
        id=encode_media_id(media_id=mention.media_id),
        type=mention.media_type,
        title=mention.media_title,
        author=mention.media_author,
    )


def _to_public_episode_media_mention(
    *,
    mention: MentionWithMedia,
) -> PublicEpisodeMediaMention:
    """Convert an episode mention payload to the public v2 shape.

    Args:
        mention: Mention payload with media context.

    Returns:
        Public episode mention occurrence.
    """
    return PublicEpisodeMediaMention(
        id=encode_mention_id(mention_id=mention.id),
        media=_to_public_media_reference(mention=mention),
        timestamp_seconds=mention.timestamp_seconds,
        context=mention.context,
        confidence=mention.confidence,
        youtube_timestamp_url=mention.youtube_timestamp_url,
    )


def _to_public_media_detail(*, media: MediaDetail) -> PublicMediaDetail:
    """Convert a detailed media payload to the v2 public shape.

    Args:
        media: Detailed media payload.

    Returns:
        Public media detail.
    """
    summary = _to_public_media_summary(media=media)
    return PublicMediaDetail(
        **summary.model_dump(),
        mentions=[
            _to_public_mention_occurrence(mention=mention) for mention in media.mentions
        ],
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
        verification_sources=media.verification_sources,
        doi_verified=media.doi_verified,
        imdb_url=media.imdb_url,
        tmdb_url=media.tmdb_url,
        wikipedia_url=media.wikipedia_url,
        google_books_url=media.google_books_url,
        open_library_url=media.open_library_url,
        doi_url=media.doi_url,
        pubmed_url=media.pubmed_url,
        semantic_scholar_url=media.semantic_scholar_url,
    )


def _to_public_episode_detail(
    *,
    episode: EpisodeDetailData,
    mentions: list[MentionWithMedia],
) -> PublicEpisodeDetail:
    """Convert detailed episode data into the v2 public shape.

    Args:
        episode: Detailed episode data.
        mentions: Mention occurrences for the episode.

    Returns:
        Public episode detail.
    """
    return PublicEpisodeDetail(
        id=encode_episode_id(episode_id=episode.id),
        podcast_id=encode_podcast_id(podcast_id=episode.podcast_id),
        podcast_name=episode.podcast_name,
        podcast_slug=episode.podcast_slug,
        title=episode.title,
        episode_number=episode.episode_number,
        youtube_id=episode.youtube_id,
        published_at=episode.published_at,
        duration_seconds=episode.duration_seconds,
        thumbnail_url=episode.thumbnail_url,
        transcript_status=episode.transcript_status,
        extraction_status=episode.extraction_status,
        cleanup_status=episode.cleanup_status,
        created_at=episode.created_at,
        mention_count=episode.mention_count,
        mentions=[
            _to_public_episode_media_mention(mention=mention) for mention in mentions
        ],
    )


def _build_public_search_url(*, item: ServiceSearchResultItem) -> str:
    """Build a future-facing public URL for a v2 search result.

    Args:
        item: Shared search result item.

    Returns:
        Public resource URL.
    """
    if item.type == "media":
        return f"/media/{encode_media_id(media_id=item.id)}"
    if item.type == "episode":
        return f"/episodes/{encode_episode_id(episode_id=item.id)}"

    slug = item.slug if item.slug is not None else str(item.id)
    return f"/podcasts/{slug}"


def _to_public_search_item(*, item: ServiceSearchResultItem) -> PublicSearchResultItem:
    """Convert a shared search item into the v2 response shape.

    Args:
        item: Shared search result item.

    Returns:
        Public search result item.
    """
    if item.type == "media":
        public_id = encode_media_id(media_id=item.id)
    elif item.type == "episode":
        public_id = encode_episode_id(episode_id=item.id)
    else:
        public_id = encode_podcast_id(podcast_id=item.id)

    return PublicSearchResultItem(
        id=public_id,
        type=item.type,
        title=item.title,
        subtitle=item.subtitle,
        cover_url=item.cover_url,
        url=_build_public_search_url(item=item),
    )


def _to_public_search_group(
    *,
    group: ServiceSearchResultGroup,
) -> PublicSearchResultGroup:
    """Convert a shared search group into the v2 response shape.

    Args:
        group: Shared search result group.

    Returns:
        Public search result group.
    """
    return PublicSearchResultGroup(
        type=group.type,
        hits=[_to_public_search_item(item=item) for item in group.hits],
        total=group.total,
    )


def _to_public_global_search_response(
    *,
    result: GlobalSearchResult,
) -> PublicGlobalSearchResponse:
    """Convert a shared global search result into the v2 response shape.

    Args:
        result: Shared global search result.

    Returns:
        Public global search response.
    """
    return PublicGlobalSearchResponse(
        query=result.query,
        results=[_to_public_search_group(group=group) for group in result.results],
        processing_time_ms=result.processing_time_ms,
    )


@router.get("/search", response_model=PublicGlobalSearchResponse)
def search_public_catalog(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    limit: int = Query(10, ge=1, le=50, description="Results per type"),
) -> PublicGlobalSearchResponse:
    """Run grouped public search across media, episodes, and podcasts.

    Args:
        q: Search query.
        limit: Maximum results per resource type.

    Returns:
        Grouped public search response.
    """
    return _to_public_global_search_response(
        result=run_global_search(
            client=get_search_client(),
            query_text=q,
            limit=limit,
        )
    )


@router.get("/trends", response_model=PublicTrendsResponse)
def get_public_trends_view(
    limit: int = Query(10, ge=1, le=50),
    type: MediaType | None = Query(None),
    db: Session = Depends(get_db),
) -> PublicTrendsResponse:
    """Get aggregated discovery trends on the v2 API.

    Args:
        limit: Maximum number of top-mentioned items to return.
        type: Optional media type filter for the top-mentioned list.
        db: Database session.

    Returns:
        Combined discovery trends payload.
    """
    trends = get_public_trends(
        db=db,
        limit=limit,
        media_type=type,
    )
    return _to_public_trends_response(trends=trends)


@router.get("/podcasts", response_model=list[PublicPodcastSummary])
def list_public_podcasts(
    db: Session = Depends(get_db),
) -> list[PublicPodcastSummary]:
    """List public podcasts on the v2 API.

    Args:
        db: Database session.

    Returns:
        Public podcast summaries.
    """
    podcasts = list_podcasts_with_stats(db=db)
    return [_to_public_podcast_summary(podcast=podcast) for podcast in podcasts]


@router.get("/podcasts/{slug}", response_model=PublicPodcastDetail)
def get_public_podcast(
    slug: str,
    db: Session = Depends(get_db),
) -> PublicPodcastDetail:
    """Get a podcast by slug on the v2 API.

    Args:
        slug: Podcast slug.
        db: Database session.

    Returns:
        Detailed public podcast response.

    Raises:
        HTTPException: If the podcast does not exist.
    """
    podcast = get_podcast_with_stats(db=db, slug=slug)
    if podcast is None:
        raise HTTPException(status_code=404, detail="Podcast not found")

    return PublicPodcastDetail(
        **_to_public_podcast_summary(podcast=podcast).model_dump(),
    )


@router.get("/podcasts/{slug}/episodes", response_model=PodcastEpisodeListResponse)
def get_public_podcast_episodes(
    slug: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PodcastEpisodeListResponse:
    """Get paginated episodes for a podcast on the v2 API.

    Args:
        slug: Podcast slug.
        page: Requested page number.
        per_page: Number of items per page.
        db: Database session.

    Returns:
        Paginated episode summaries.

    Raises:
        HTTPException: If the podcast does not exist.
    """
    episodes = list_podcast_episodes_with_stats(
        db=db,
        slug=slug,
        page=page,
        per_page=per_page,
    )
    if episodes is None:
        raise HTTPException(status_code=404, detail="Podcast not found")

    return PodcastEpisodeListResponse(
        items=[_to_public_episode_summary(episode=item) for item in episodes.items],
        total=episodes.total,
        page=episodes.page,
        per_page=episodes.per_page,
    )


@router.get("/episodes", response_model=PublicEpisodeListResponse)
def list_public_episodes(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    podcast_id: str | None = Query(None),
    db: Session = Depends(get_db),
) -> PublicEpisodeListResponse:
    """List public episodes on the v2 API.

    Args:
        page: Requested page number.
        per_page: Number of items per page.
        podcast_id: Optional opaque podcast identifier filter.
        db: Database session.

    Returns:
        Paginated public episode response.

    Raises:
        HTTPException: If the podcast identifier is malformed.
    """
    internal_podcast_id = None
    if podcast_id is not None:
        try:
            internal_podcast_id = decode_podcast_id(podcast_id=podcast_id)
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail="Invalid podcast identifier",
            ) from error

    episodes = list_episodes_with_stats(
        db=db,
        page=page,
        per_page=per_page,
        podcast_id=internal_podcast_id,
    )
    return PublicEpisodeListResponse(
        items=[_to_public_episode_summary(episode=item) for item in episodes.items],
        total=episodes.total,
        page=episodes.page,
        per_page=episodes.per_page,
    )


@router.get("/episodes/{episode_id}", response_model=PublicEpisodeDetail)
def get_public_episode(
    episode_id: str,
    db: Session = Depends(get_db),
) -> PublicEpisodeDetail:
    """Get episode detail by opaque episode ID on the v2 API.

    Args:
        episode_id: Opaque episode identifier.
        db: Database session.

    Returns:
        Public episode detail.

    Raises:
        HTTPException: If the episode does not exist.
    """
    try:
        internal_episode_id = decode_episode_id(episode_id=episode_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Episode not found") from error

    episode = get_episode_detail_by_id(db=db, episode_id=internal_episode_id)
    mentions = get_episode_mentions(db=db, episode_id=internal_episode_id)
    if episode is None or mentions is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    return _to_public_episode_detail(episode=episode, mentions=mentions)


@router.get(
    "/episodes/{episode_id}/mentions",
    response_model=list[PublicEpisodeMediaMention],
)
def get_public_episode_mentions(
    episode_id: str,
    db: Session = Depends(get_db),
) -> list[PublicEpisodeMediaMention]:
    """Get episode mention occurrences by opaque episode ID.

    Args:
        episode_id: Opaque episode identifier.
        db: Database session.

    Returns:
        Public episode mention occurrences.

    Raises:
        HTTPException: If the episode does not exist.
    """
    try:
        internal_episode_id = decode_episode_id(episode_id=episode_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Episode not found") from error

    mentions = get_episode_mentions(db=db, episode_id=internal_episode_id)
    if mentions is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    return [_to_public_episode_media_mention(mention=mention) for mention in mentions]


@router.get("/media", response_model=PublicMediaListResponse)
def list_public_media(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    type: list[MediaType] | None = Query(None),
    sort: Literal["mention_count", "title", "created_at"] = "mention_count",
    order: Literal["asc", "desc"] = "desc",
    db: Session = Depends(get_db),
) -> PublicMediaListResponse:
    """List public media on the v2 API.

    Args:
        page: Requested page number.
        per_page: Number of items per page.
        type: Optional media type filters.
        sort: Sort field.
        order: Sort direction.
        db: Database session.

    Returns:
        Paginated public media response.
    """
    media = list_media_with_stats(
        db=db,
        page=page,
        per_page=per_page,
        media_types=type,
        sort=sort,
        order=order,
    )
    return PublicMediaListResponse(
        items=[_to_public_media_summary(media=item) for item in media.items],
        total=media.total,
        page=media.page,
        per_page=media.per_page,
    )


@router.get("/media/{media_id}", response_model=PublicMediaDetail)
def get_public_media(
    media_id: str,
    db: Session = Depends(get_db),
) -> PublicMediaDetail:
    """Get media detail by opaque media ID on the v2 API.

    Args:
        media_id: Opaque media identifier.
        db: Database session.

    Returns:
        Public media detail.

    Raises:
        HTTPException: If the media item does not exist.
    """
    try:
        internal_media_id = decode_media_id(media_id=media_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Media not found") from error

    media = get_media_detail_by_id(db=db, media_id=internal_media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")

    return _to_public_media_detail(media=media)


@router.get("/media/{media_id}/mentions", response_model=list[PublicMentionOccurrence])
def get_public_media_mentions(
    media_id: str,
    db: Session = Depends(get_db),
) -> list[PublicMentionOccurrence]:
    """Get media mention occurrences by opaque media ID.

    Args:
        media_id: Opaque media identifier.
        db: Database session.

    Returns:
        Public mention occurrences.

    Raises:
        HTTPException: If the media item does not exist.
    """
    try:
        internal_media_id = decode_media_id(media_id=media_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Media not found") from error

    media = get_media_detail_by_id(db=db, media_id=internal_media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")

    mentions = get_media_mentions(db=db, media_id=internal_media_id)
    return [_to_public_mention_occurrence(mention=mention) for mention in mentions]
