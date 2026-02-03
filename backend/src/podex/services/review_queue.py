"""Shared review queue services for ops surfaces."""

import string
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeAlias, cast

from sqlalchemy.orm import Query, Session

from podex.models import (
    Episode,
    JobStatus,
    JobType,
    Media,
    Mention,
    MentionCandidate,
    MentionCandidateProvenance,
    MentionCandidateProvenanceEventType,
    MentionCandidateState,
    Podcast,
    ReviewItem,
    ReviewItemStatus,
    ReviewPriority,
    TranscriptionJob,
)
from podex.services.extraction_review import (
    _build_change_summary,
    _changed_candidate_fields,
    _record_candidate_provenance,
    _snapshot_candidate,
)

ReviewQueueRow: TypeAlias = tuple[
    ReviewItem,
    MentionCandidate,
    Episode,
    Podcast,
    TranscriptionJob | None,
]


def _candidate_year(*, candidate: MentionCandidate) -> int | None:
    """Extract a candidate year from structured metadata.

    Args:
        candidate: Mention candidate being published.

    Returns:
        Parsed year when available.
    """
    metadata: dict[str, Any] = candidate.metadata_json or {}
    year = metadata.get("year")
    if isinstance(year, int):
        return year
    return None


@dataclass(frozen=True, slots=True)
class ReviewQueueExtractionJobData:
    """Recent extraction job details exposed for review operations."""

    id: int
    status: JobStatus
    backend: str | None
    model: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    is_source_job: bool


@dataclass(frozen=True, slots=True)
class ReviewQueueCandidateData:
    """Candidate details exposed through the review queue."""

    id: int
    media_type: str
    raw_title: str
    normalized_title: str | None
    suggested_author: str | None
    timestamp_seconds: int | None
    context: str | None
    confidence: float
    extraction_source: str | None
    source_job_id: int | None
    source_job_status: str | None
    source_job_backend: str | None
    source_job_model: str | None
    source_job_created_at: datetime | None
    state: MentionCandidateState
    media_id: int | None
    mention_id: int | None
    created_at: datetime
    reviewed_at: datetime | None
    extraction_jobs: list[ReviewQueueExtractionJobData]
    provenance: list["ReviewQueueCandidateProvenanceData"]


@dataclass(frozen=True, slots=True)
class ReviewQueueCandidateProvenanceData:
    """Historical extraction provenance exposed for a review candidate."""

    id: int
    event_type: MentionCandidateProvenanceEventType
    change_summary: str | None
    raw_title: str
    normalized_title: str | None
    suggested_author: str | None
    timestamp_seconds: int | None
    context: str | None
    confidence: float
    extraction_source: str | None
    source_job_id: int | None
    source_job_status: str | None
    source_job_backend: str | None
    source_job_model: str | None
    source_job_created_at: datetime | None
    media_id: int | None
    changed_fields: list[str]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewQueueItemData:
    """Review item details exposed through ops review queue surfaces."""

    id: int
    status: ReviewItemStatus
    priority: ReviewPriority
    assigned_to: str | None
    decision_note: str | None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None
    episode_id: int
    episode_title: str
    podcast_id: int
    podcast_name: str
    podcast_slug: str
    target_media_id: int | None
    candidate: ReviewQueueCandidateData


@dataclass(frozen=True, slots=True)
class ReviewQueueListData:
    """Paginated review queue payload."""

    items: list[ReviewQueueItemData]
    total: int
    page: int
    per_page: int


@dataclass(frozen=True, slots=True)
class ReviewDecisionInputData:
    """Decision input for approving or rejecting a review item."""

    actor_name: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewMergeInputData(ReviewDecisionInputData):
    """Decision input for merging a review item into an existing media record."""

    target_media_id: int = 0


@dataclass(frozen=True, slots=True)
class ReviewReclassifyInputData(ReviewDecisionInputData):
    """Input for reclassifying a pending review candidate."""

    media_type: str | None = None
    raw_title: str | None = None
    normalized_title: str | None = None
    suggested_author: str | None = None
    clear_suggested_author: bool = False


def _normalize_candidate_title(*, value: str | None) -> str | None:
    """Normalize a candidate title for matching-oriented storage.

    Args:
        value: Raw title value.

    Returns:
        Normalized title or ``None`` when empty.
    """
    if value is None:
        return None

    normalized = " ".join(value.split()).strip().lower()
    if not normalized:
        return None

    translation = str.maketrans("", "", string.punctuation)
    normalized = normalized.translate(translation).strip()
    return normalized or None


def _to_review_queue_item_data(
    *,
    review_item: ReviewItem,
    candidate: MentionCandidate,
    episode: Episode,
    podcast: Podcast,
    source_job: TranscriptionJob | None,
    extraction_jobs: list[TranscriptionJob],
    provenance: list[ReviewQueueCandidateProvenanceData],
) -> ReviewQueueItemData:
    """Convert joined review queue rows into shared data.

    Args:
        review_item: Review item model.
        candidate: Mention candidate model.
        episode: Episode linked to the candidate.
        podcast: Podcast linked to the episode.
        source_job: Extraction job linked to the candidate, if any.
        extraction_jobs: Recent extraction jobs for the episode.
        provenance: Historical provenance events for the candidate.

    Returns:
        Shared review queue item payload.
    """
    return ReviewQueueItemData(
        id=review_item.id,
        status=ReviewItemStatus(review_item.status),
        priority=ReviewPriority(review_item.priority),
        assigned_to=review_item.assigned_to,
        decision_note=review_item.decision_note,
        created_at=review_item.created_at,
        updated_at=review_item.updated_at,
        decided_at=review_item.decided_at,
        episode_id=episode.id,
        episode_title=episode.title,
        podcast_id=podcast.id,
        podcast_name=podcast.name,
        podcast_slug=podcast.slug,
        target_media_id=review_item.target_media_id,
        candidate=ReviewQueueCandidateData(
            id=candidate.id,
            media_type=candidate.media_type,
            raw_title=candidate.raw_title,
            normalized_title=candidate.normalized_title,
            suggested_author=candidate.suggested_author,
            timestamp_seconds=candidate.timestamp_seconds,
            context=candidate.context,
            confidence=candidate.confidence,
            extraction_source=candidate.extraction_source,
            source_job_id=source_job.id if source_job is not None else None,
            source_job_status=source_job.status if source_job is not None else None,
            source_job_backend=(source_job.backend if source_job is not None else None),
            source_job_model=source_job.model if source_job is not None else None,
            source_job_created_at=(
                source_job.created_at if source_job is not None else None
            ),
            state=MentionCandidateState(candidate.state),
            media_id=candidate.media_id,
            mention_id=candidate.mention_id,
            created_at=candidate.created_at,
            reviewed_at=candidate.reviewed_at,
            extraction_jobs=[
                ReviewQueueExtractionJobData(
                    id=job.id,
                    status=JobStatus(job.status),
                    backend=job.backend,
                    model=job.model,
                    error_message=job.error_message,
                    created_at=job.created_at,
                    started_at=job.started_at,
                    completed_at=job.completed_at,
                    is_source_job=job.id == candidate.source_job_id,
                )
                for job in extraction_jobs
            ],
            provenance=provenance,
        ),
    )


def _to_review_queue_candidate_provenance_data(
    *,
    provenance: MentionCandidateProvenance,
    source_job: TranscriptionJob | None,
) -> ReviewQueueCandidateProvenanceData:
    """Convert a provenance row into shared review data.

    Args:
        provenance: Provenance event model.
        source_job: Linked extraction job, if any.

    Returns:
        Shared provenance payload.
    """
    metadata_json = provenance.metadata_json or {}
    changed_fields = metadata_json.get("changed_fields", [])
    if not isinstance(changed_fields, list):
        changed_fields = []

    return ReviewQueueCandidateProvenanceData(
        id=provenance.id,
        event_type=MentionCandidateProvenanceEventType(provenance.event_type),
        change_summary=provenance.change_summary,
        raw_title=provenance.raw_title,
        normalized_title=provenance.normalized_title,
        suggested_author=provenance.suggested_author,
        timestamp_seconds=provenance.timestamp_seconds,
        context=provenance.context,
        confidence=provenance.confidence,
        extraction_source=provenance.extraction_source,
        source_job_id=source_job.id if source_job is not None else None,
        source_job_status=source_job.status if source_job is not None else None,
        source_job_backend=source_job.backend if source_job is not None else None,
        source_job_model=source_job.model if source_job is not None else None,
        source_job_created_at=source_job.created_at if source_job is not None else None,
        media_id=provenance.media_id,
        changed_fields=[field for field in changed_fields if isinstance(field, str)],
        created_at=provenance.created_at,
    )


def _load_candidate_provenance_map(
    *,
    db: Session,
    candidate_ids: list[int],
    limit_per_candidate: int = 5,
) -> dict[int, list[ReviewQueueCandidateProvenanceData]]:
    """Load recent provenance history for the given candidates.

    Args:
        db: Database session.
        candidate_ids: Candidate identifiers to load provenance for.
        limit_per_candidate: Max provenance items to keep per candidate.

    Returns:
        Candidate id keyed provenance lists ordered by recency.
    """
    if not candidate_ids:
        return {}

    rows = (
        db.query(MentionCandidateProvenance, TranscriptionJob)
        .outerjoin(
            TranscriptionJob,
            TranscriptionJob.id == MentionCandidateProvenance.source_job_id,
        )
        .filter(MentionCandidateProvenance.mention_candidate_id.in_(candidate_ids))
        .order_by(
            MentionCandidateProvenance.created_at.desc(),
            MentionCandidateProvenance.id.desc(),
        )
        .all()
    )

    provenance_map: dict[int, list[ReviewQueueCandidateProvenanceData]] = {
        candidate_id: [] for candidate_id in candidate_ids
    }
    for provenance, source_job in rows:
        candidate_history = provenance_map.setdefault(
            provenance.mention_candidate_id,
            [],
        )
        if len(candidate_history) >= limit_per_candidate:
            continue
        candidate_history.append(
            _to_review_queue_candidate_provenance_data(
                provenance=provenance,
                source_job=source_job,
            )
        )

    return provenance_map


def _load_episode_extraction_jobs_map(
    *,
    db: Session,
    episode_ids: list[int],
    limit_per_episode: int = 5,
) -> dict[int, list[TranscriptionJob]]:
    """Load recent extraction jobs for the given episodes.

    Args:
        db: Database session.
        episode_ids: Episode identifiers to load extraction jobs for.
        limit_per_episode: Max extraction jobs to keep per episode.

    Returns:
        Episode id keyed extraction jobs ordered by recency.
    """
    if not episode_ids:
        return {}

    rows = (
        db.query(TranscriptionJob)
        .filter(
            TranscriptionJob.episode_id.in_(episode_ids),
            TranscriptionJob.job_type == JobType.EXTRACT.value,
        )
        .order_by(TranscriptionJob.created_at.desc(), TranscriptionJob.id.desc())
        .all()
    )

    extraction_jobs_map: dict[int, list[TranscriptionJob]] = {
        episode_id: [] for episode_id in episode_ids
    }
    for job in rows:
        episode_jobs = extraction_jobs_map.setdefault(job.episode_id, [])
        if len(episode_jobs) >= limit_per_episode:
            continue
        episode_jobs.append(job)

    return extraction_jobs_map


def _review_item_query(*, db: Session) -> Query[ReviewQueueRow]:
    """Build the base review item query used by review queue services.

    Args:
        db: Database session.

    Returns:
        SQLAlchemy query joining review items to candidate and episode context.
    """
    return (
        db.query(ReviewItem, MentionCandidate, Episode, Podcast, TranscriptionJob)
        .join(MentionCandidate, MentionCandidate.id == ReviewItem.mention_candidate_id)
        .join(Episode, Episode.id == MentionCandidate.episode_id)
        .join(Podcast, Podcast.id == Episode.podcast_id)
        .outerjoin(
            TranscriptionJob, TranscriptionJob.id == MentionCandidate.source_job_id
        )
    )


def _get_review_item_row(
    *,
    db: Session,
    review_item_id: int,
) -> ReviewQueueRow | None:
    """Load a single joined review item row.

    Args:
        db: Database session.
        review_item_id: Internal review item identifier.

    Returns:
        Joined review item row when found, otherwise ``None``.
    """
    return cast(
        ReviewQueueRow | None,
        _review_item_query(db=db).filter(ReviewItem.id == review_item_id).first(),
    )


def list_review_queue_items(
    *,
    db: Session,
    page: int,
    per_page: int,
    status: ReviewItemStatus | None = None,
    priority: ReviewPriority | None = None,
) -> ReviewQueueListData:
    """List review queue items for ops views.

    Args:
        db: Database session.
        page: Requested page number.
        per_page: Number of items per page.
        status: Optional review status filter.
        priority: Optional priority filter.

    Returns:
        Paginated review queue payload.
    """
    query = _review_item_query(db=db)

    if status is not None:
        query = query.filter(ReviewItem.status == status.value)
    if priority is not None:
        query = query.filter(ReviewItem.priority == priority.value)

    total = query.count()
    rows = (
        query.order_by(ReviewItem.created_at.desc(), ReviewItem.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    extraction_jobs_map = _load_episode_extraction_jobs_map(
        db=db,
        episode_ids=[
            episode.id for _review_item, _candidate, episode, _podcast, _job in rows
        ],
    )
    provenance_map = _load_candidate_provenance_map(
        db=db,
        candidate_ids=[
            candidate.id for _review_item, candidate, _episode, _podcast, _job in rows
        ],
    )

    return ReviewQueueListData(
        items=[
            _to_review_queue_item_data(
                review_item=review_item,
                candidate=candidate,
                episode=episode,
                podcast=podcast,
                source_job=source_job,
                extraction_jobs=extraction_jobs_map.get(episode.id, []),
                provenance=provenance_map.get(candidate.id, []),
            )
            for review_item, candidate, episode, podcast, source_job in rows
        ],
        total=total,
        page=page,
        per_page=per_page,
    )


def get_review_queue_item_by_id(
    *,
    db: Session,
    review_item_id: int,
) -> ReviewQueueItemData | None:
    """Load a single review queue item.

    Args:
        db: Database session.
        review_item_id: Internal review item identifier.

    Returns:
        Review queue item when found, otherwise ``None``.
    """
    row = _get_review_item_row(db=db, review_item_id=review_item_id)
    if row is None:
        return None

    review_item, candidate, episode, podcast, source_job = row
    extraction_jobs_map = _load_episode_extraction_jobs_map(
        db=db,
        episode_ids=[episode.id],
    )
    provenance_map = _load_candidate_provenance_map(
        db=db,
        candidate_ids=[candidate.id],
    )
    return _to_review_queue_item_data(
        review_item=review_item,
        candidate=candidate,
        episode=episode,
        podcast=podcast,
        source_job=source_job,
        extraction_jobs=extraction_jobs_map.get(episode.id, []),
        provenance=provenance_map.get(candidate.id, []),
    )


def _assert_review_item_is_open(*, review_item: ReviewItem) -> None:
    """Validate that a review item can still be decided.

    Args:
        review_item: Review item model.

    Raises:
        ValueError: If the review item was already decided.
    """
    if review_item.status in {
        ReviewItemStatus.APPROVED.value,
        ReviewItemStatus.REJECTED.value,
        ReviewItemStatus.MERGED.value,
    }:
        raise ValueError("Review item has already been decided")


def _ensure_published_mention(
    *,
    db: Session,
    candidate: MentionCandidate,
    media_id: int,
) -> Mention:
    """Ensure a published mention exists for an approved candidate.

    Args:
        db: Database session.
        candidate: Mention candidate being published.
        media_id: Internal media identifier to publish against.

    Returns:
        Persisted mention record.
    """
    if candidate.mention_id is not None:
        existing = cast(
            Mention | None,
            db.query(Mention).filter(Mention.id == candidate.mention_id).first(),
        )
        if existing is not None:
            return existing

    existing = cast(
        Mention | None,
        db.query(Mention)
        .filter(Mention.episode_id == candidate.episode_id)
        .filter(Mention.media_id == media_id)
        .filter(Mention.timestamp_seconds == candidate.timestamp_seconds)
        .filter(Mention.context == candidate.context)
        .first(),
    )
    if existing is not None:
        return existing

    mention = Mention(
        episode_id=candidate.episode_id,
        media_id=media_id,
        timestamp_seconds=candidate.timestamp_seconds,
        context=candidate.context,
        confidence=candidate.confidence,
    )
    db.add(mention)
    db.flush()
    return mention


def _ensure_candidate_media(
    *,
    db: Session,
    candidate: MentionCandidate,
) -> Media:
    """Ensure an approved candidate points to a canonical media record.

    Args:
        db: Database session.
        candidate: Mention candidate being approved.

    Returns:
        Canonical media record for the candidate.
    """
    candidate_year = _candidate_year(candidate=candidate)

    if candidate.media_id is not None:
        media = cast(
            Media | None,
            db.query(Media).filter(Media.id == candidate.media_id).first(),
        )
        if media is not None:
            if candidate.suggested_author and not media.author:
                media.author = candidate.suggested_author
            if candidate_year is not None and media.year is None:
                media.year = candidate_year
            if candidate.metadata_json is not None and media.metadata_json is None:
                media.metadata_json = candidate.metadata_json
            return media

    media = Media(
        type=candidate.media_type,
        title=candidate.normalized_title or candidate.raw_title,
        author=candidate.suggested_author,
        year=candidate_year,
        metadata_json=candidate.metadata_json,
    )
    db.add(media)
    db.flush()
    candidate.media = media
    return media


def approve_review_queue_item(
    *,
    db: Session,
    review_item_id: int,
    payload: ReviewDecisionInputData,
) -> ReviewQueueItemData | None:
    """Approve a review queue item and publish its candidate.

    Args:
        db: Database session.
        review_item_id: Internal review item identifier.
        payload: Decision payload.

    Returns:
        Updated review queue item when found, otherwise ``None``.

    """
    row = _get_review_item_row(db=db, review_item_id=review_item_id)
    if row is None:
        return None

    review_item, candidate, _episode, _podcast, _source_job = row
    _assert_review_item_is_open(review_item=review_item)

    media = _ensure_candidate_media(db=db, candidate=candidate)
    mention = _ensure_published_mention(db=db, candidate=candidate, media_id=media.id)

    now = datetime.now(UTC)
    candidate.media_id = media.id
    candidate.mention_id = mention.id
    candidate.state = MentionCandidateState.PUBLISHED.value
    candidate.reviewed_at = now
    review_item.approve(actor_name=payload.actor_name, note=payload.note)
    db.flush()

    return get_review_queue_item_by_id(db=db, review_item_id=review_item_id)


def reject_review_queue_item(
    *,
    db: Session,
    review_item_id: int,
    payload: ReviewDecisionInputData,
) -> ReviewQueueItemData | None:
    """Reject a review queue item.

    Args:
        db: Database session.
        review_item_id: Internal review item identifier.
        payload: Decision payload.

    Returns:
        Updated review queue item when found, otherwise ``None``.

    """
    row = _get_review_item_row(db=db, review_item_id=review_item_id)
    if row is None:
        return None

    review_item, candidate, _episode, _podcast, _source_job = row
    _assert_review_item_is_open(review_item=review_item)

    candidate.state = MentionCandidateState.REJECTED.value
    candidate.reviewed_at = datetime.now(UTC)
    review_item.reject(actor_name=payload.actor_name, note=payload.note)
    db.flush()

    return get_review_queue_item_by_id(db=db, review_item_id=review_item_id)


def merge_review_queue_item(
    *,
    db: Session,
    review_item_id: int,
    payload: ReviewMergeInputData,
) -> ReviewQueueItemData | None:
    """Merge a review queue item into an existing canonical media record.

    Args:
        db: Database session.
        review_item_id: Internal review item identifier.
        payload: Merge decision payload.

    Returns:
        Updated review queue item when found, otherwise ``None``.

    Raises:
        ValueError: If the review item has already been decided
            or the target media is missing.
    """
    row = _get_review_item_row(db=db, review_item_id=review_item_id)
    if row is None:
        return None

    review_item, candidate, _episode, _podcast, _source_job = row
    _assert_review_item_is_open(review_item=review_item)

    target_media = db.query(Media).filter(Media.id == payload.target_media_id).first()
    if target_media is None:
        raise ValueError("Target media not found")

    mention = _ensure_published_mention(
        db=db,
        candidate=candidate,
        media_id=target_media.id,
    )

    now = datetime.now(UTC)
    candidate.media_id = target_media.id
    candidate.mention_id = mention.id
    candidate.state = MentionCandidateState.MERGED.value
    candidate.reviewed_at = now
    review_item.mark_merged(
        actor_name=payload.actor_name,
        note=payload.note,
        target_media_id=target_media.id,
    )
    db.flush()

    return get_review_queue_item_by_id(db=db, review_item_id=review_item_id)


def reclassify_review_queue_item(
    *,
    db: Session,
    review_item_id: int,
    payload: ReviewReclassifyInputData,
) -> ReviewQueueItemData | None:
    """Reclassify a pending review queue item before approval.

    Args:
        db: Database session.
        review_item_id: Internal review item identifier.
        payload: Reclassification payload.

    Returns:
        Updated review queue item when found, otherwise ``None``.

    Raises:
        ValueError: If the review item has already been decided or no changes were
            provided.
    """
    row = _get_review_item_row(db=db, review_item_id=review_item_id)
    if row is None:
        return None

    review_item, candidate, _episode, _podcast, _source_job = row
    _assert_review_item_is_open(review_item=review_item)

    before_snapshot = _snapshot_candidate(candidate=candidate)

    if payload.media_type is not None:
        candidate.media_type = payload.media_type

    if payload.raw_title is not None:
        raw_title = payload.raw_title.strip()
        if not raw_title:
            raise ValueError("Raw title cannot be empty")
        candidate.raw_title = raw_title
        if payload.normalized_title is None:
            candidate.normalized_title = _normalize_candidate_title(value=raw_title)

    if payload.normalized_title is not None:
        candidate.normalized_title = _normalize_candidate_title(
            value=payload.normalized_title,
        )

    if payload.clear_suggested_author:
        candidate.suggested_author = None
    elif payload.suggested_author is not None:
        candidate.suggested_author = payload.suggested_author.strip() or None

    if (
        payload.media_type is not None
        or payload.raw_title is not None
        or payload.normalized_title is not None
    ):
        candidate.media_id = None

    now = datetime.now(UTC)
    if payload.actor_name is not None:
        review_item.assigned_to = payload.actor_name
    if payload.note is not None:
        review_item.decision_note = payload.note
    review_item.updated_at = now

    after_snapshot = _snapshot_candidate(candidate=candidate)
    changed_fields = _changed_candidate_fields(
        before=before_snapshot, after=after_snapshot
    )
    if not changed_fields:
        raise ValueError("No candidate changes were provided")

    _record_candidate_provenance(
        db=db,
        candidate=candidate,
        event_type=MentionCandidateProvenanceEventType.UPDATED,
        change_summary=_build_change_summary(changed_fields=changed_fields),
        changed_fields=changed_fields,
    )
    db.flush()

    return get_review_queue_item_by_id(db=db, review_item_id=review_item_id)
