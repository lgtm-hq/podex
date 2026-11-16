"""Takedown intake and privileged decision services."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressionScope,
    ContentSuppressionSubjectType,
    DerivativeGenerationRun,
    Episode,
    EpisodeSummary,
    GraphTriple,
    MediaSummary,
    Mention,
    MentionCandidate,
    Podcast,
    SemanticChunk,
    TakedownRequest,
    TakedownRequesterType,
    TakedownRequestStatus,
    TakedownSubjectType,
    Transcript,
)
from podex.services.content_suppression import (
    lock_media_for_takedown,
    lock_podcasts_for_takedown,
    record_suppression,
)
from podex.services.transcript_artifacts import (
    TranscriptArtifactStore,
    list_retained_artifacts,
)
from podex.services.transcript_retention import TranscriptRetentionPolicy
from podex.services.transcript_retention_commands import (
    create_transcript_digest,
    resolve_column_proof_source,
)
from podex.services.transcript_retention_policies import (
    get_transcript_source_retention_policy,
    to_retention_policy,
    upsert_transcript_source_retention_policy,
)

SUPPRESSION_SCOPES_BY_ACTION: dict[str, ContentSuppressionScope] = {
    "unpublish_mentions": ContentSuppressionScope.MENTIONS,
    "suppress_derivatives": ContentSuppressionScope.DERIVATIVES,
}
"""Requested actions that leave a durable suppression, and its scope.

Raw retention actions (``suppress_raw_transcript``, ``register_source_opt_out``)
keep their storage and opt-out semantics and record no suppression.
"""

_SuppressedSubject = tuple[ContentSuppressionSubjectType, tuple[int, ...]]
"""What one suppression row covers: its subject type and ids."""


@dataclass(frozen=True, slots=True)
class TakedownRequestInputData:
    """Validated input for one takedown submission."""

    subject_type: TakedownSubjectType
    subject_id: int
    requester_type: TakedownRequesterType
    requester_name: str
    requester_email: str
    basis: str
    requested_actions: list[str]


@dataclass(frozen=True, slots=True)
class TakedownDecisionInputData:
    """Operator decision for one submitted request."""

    status: TakedownRequestStatus
    actor_name: str | None
    note: str


@dataclass(frozen=True, slots=True)
class TakedownExecutionResultData:
    """Counts and public projection identifiers affected by suppression."""

    episode_ids: tuple[int, ...]
    media_ids: tuple[int, ...]
    transcripts_suppressed: int
    derivatives_suppressed: int
    mentions_unpublished: int
    source_opt_outs_registered: int


def create_takedown_request(
    *,
    db: Session,
    payload: TakedownRequestInputData,
) -> TakedownRequest:
    """Persist a newly submitted takedown case in pending review."""
    request = TakedownRequest(
        subject_type=payload.subject_type.value,
        subject_id=payload.subject_id,
        requester_type=payload.requester_type.value,
        requester_name=payload.requester_name,
        requester_email=payload.requester_email,
        basis=payload.basis,
        requested_actions_json=payload.requested_actions,
        status=TakedownRequestStatus.PENDING.value,
    )
    db.add(request)
    db.flush()
    return request


def list_takedown_requests(
    *,
    db: Session,
    status: TakedownRequestStatus | None = None,
    limit: int = 50,
) -> list[TakedownRequest]:
    """List submitted takedown cases for privileged operator review."""
    query = db.query(TakedownRequest)
    if status is not None:
        query = query.filter(TakedownRequest.status == status.value)
    return list(
        query.order_by(TakedownRequest.created_at.desc(), TakedownRequest.id.desc())
        .limit(limit)
        .all(),
    )


def decide_takedown_request(
    *,
    db: Session,
    request_id: int,
    payload: TakedownDecisionInputData,
    now: datetime | None = None,
) -> TakedownRequest | None:
    """Record approval or rejection for one pending takedown request."""
    request = db.query(TakedownRequest).filter(TakedownRequest.id == request_id).first()
    if request is None:
        return None
    if request.status != TakedownRequestStatus.PENDING.value:
        raise ValueError("Takedown request has already been decided")
    request.status = payload.status.value
    request.decided_by = payload.actor_name
    request.decision_note = payload.note
    request.decided_at = now or datetime.now(UTC)
    db.flush()
    return request


def execute_approved_takedown_request(
    *,
    db: Session,
    request: TakedownRequest,
    artifact_store: TranscriptArtifactStore | None,
    now: datetime | None = None,
) -> TakedownExecutionResultData:
    """Execute data removal and opt-out actions for an approved request."""
    if request.status != TakedownRequestStatus.APPROVED.value:
        raise ValueError("Only approved takedown requests can be executed")

    effective_now = now or datetime.now(UTC)
    actions = set(request.requested_actions_json)
    summary_media_ids = _lock_takedown_subject(db=db, request=request, actions=actions)
    episode_ids = _episode_ids_for_request(db=db, request=request)
    _record_suppressions(db=db, request=request, actions=actions)
    transcripts = (
        db.query(Transcript).filter(Transcript.episode_id.in_(episode_ids)).all()
        if episode_ids
        else []
    )
    transcripts_suppressed = 0
    derivatives_suppressed = 0
    mentions_unpublished = 0
    source_opt_outs_registered = 0
    media_ids: tuple[int, ...] = ()

    if "suppress_raw_transcript" in actions:
        transcripts_suppressed = _suppress_raw_transcripts(
            db=db,
            transcripts=transcripts,
            artifact_store=artifact_store,
            suppressed_at=effective_now,
        )
    if "suppress_derivatives" in actions:
        derivatives_suppressed = _suppress_derivatives(
            db=db,
            episode_ids=episode_ids,
        )
    if "unpublish_mentions" in actions:
        mentions_unpublished, media_ids = _unpublish_mentions(
            db=db,
            request=request,
            episode_ids=episode_ids,
        )
    if "register_source_opt_out" in actions:
        if request.requester_type != TakedownRequesterType.CREATOR.value:
            raise ValueError("Source opt-out registration requires a creator request")
        source_opt_outs_registered = _register_source_opt_outs(
            db=db,
            transcripts=transcripts,
        )

    result = TakedownExecutionResultData(
        episode_ids=episode_ids,
        media_ids=media_ids,
        transcripts_suppressed=transcripts_suppressed,
        derivatives_suppressed=derivatives_suppressed,
        mentions_unpublished=mentions_unpublished,
        source_opt_outs_registered=source_opt_outs_registered,
    )
    # The last destructive step drops media summaries that may quote the
    # covered text; like every other step it runs after the suppressions exist.
    request.metadata_json = {
        **(request.metadata_json or {}),
        "executed_at": effective_now.isoformat(),
        "transcripts_suppressed": result.transcripts_suppressed,
        "derivatives_suppressed": result.derivatives_suppressed,
        "mentions_unpublished": result.mentions_unpublished,
        "source_opt_outs_registered": result.source_opt_outs_registered,
        "media_summaries_dropped": _drop_media_summaries(
            db=db,
            media_ids=summary_media_ids,
        ),
    }
    db.flush()
    return result


def _lock_takedown_subject(
    *,
    db: Session,
    request: TakedownRequest,
    actions: set[str],
) -> tuple[int, ...]:
    """Take the takedown's locks in the shared order, media before podcasts.

    The media records whose summaries quote the covered text (which include a
    mention subject's media) are locked first, as a media merge locks its
    records first, then the covered podcast rows. Mentions published between
    the two reads would need a media lock taken out of order, so the
    takedown refuses and the operator retries.

    Returns:
        Media records whose summaries the takedown drops.

    Raises:
        ValueError: If covered mentions changed while the locks were taken.
    """
    media_ids = _summary_media_ids(db=db, request=request, actions=actions)
    lock_media_for_takedown(db=db, media_ids=media_ids)
    lock_podcasts_for_takedown(
        db=db,
        podcast_ids=_podcast_ids_for_request(db=db, request=request),
    )
    current = _summary_media_ids(db=db, request=request, actions=actions)
    if not set(current) <= set(media_ids):
        raise ValueError(
            "Covered mentions changed while the takedown was executing; retry",
        )
    return current


def _summary_media_ids(
    *,
    db: Session,
    request: TakedownRequest,
    actions: set[str],
) -> tuple[int, ...]:
    """Resolve the media records whose summaries may quote the covered text.

    A mention-only takedown of one mention covers its own media record; any
    other suppression covers every record mentioned in the covered episodes.
    Raw retention actions cover none.
    """
    if not actions & set(SUPPRESSION_SCOPES_BY_ACTION):
        return ()
    if request.subject_type == TakedownSubjectType.MENTION.value:
        mention = db.query(Mention).filter(Mention.id == request.subject_id).first()
        if mention is None:
            return ()
        if "suppress_derivatives" not in actions:
            return (mention.media_id,)
        episodes = select(Episode.id).where(Episode.id == mention.episode_id)
    elif request.subject_type == TakedownSubjectType.EPISODE.value:
        episodes = select(Episode.id).where(Episode.id == request.subject_id)
    else:
        episodes = select(Episode.id).where(Episode.podcast_id == request.subject_id)
    rows = db.execute(
        select(Mention.media_id)
        .where(Mention.episode_id.in_(episodes))
        .distinct()
        .order_by(Mention.media_id),
    ).all()
    return tuple(int(row[0]) for row in rows)


def _drop_media_summaries(*, db: Session, media_ids: tuple[int, ...]) -> int:
    """Delete media summaries that may quote the covered text.

    The media records come from ``_summary_media_ids``. A summary is kept only
    when its stored provenance proves it quoted no mention context: our source
    builder records ``mention_context_count`` for ``media_metadata_mentions``
    sources. A summary without that record could quote the covered text and
    is dropped. Rows are deleted through the session, so loaded objects do not
    go stale. The next summary generation reads only unsuppressed mentions.

    Returns:
        The number of summaries deleted.
    """
    if not media_ids:
        return 0
    dropped = [
        summary
        for summary in db.query(MediaSummary)
        .filter(MediaSummary.media_id.in_(media_ids))
        .order_by(MediaSummary.id)
        if not _quotes_no_mention_context(summary)
    ]
    for summary in dropped:
        db.delete(summary)
    db.flush()
    return len(dropped)


def _quotes_no_mention_context(summary: MediaSummary) -> bool:
    """Return whether a summary's provenance proves it quoted no mention."""
    metadata = summary.metadata_json or {}
    return (
        metadata.get("source") == "media_metadata_mentions"
        and metadata.get("mention_context_count") == 0
    )


def _podcast_ids_for_request(
    *,
    db: Session,
    request: TakedownRequest,
) -> tuple[int, ...]:
    """Resolve the podcast whose rows serialize this takedown with writers."""
    if request.subject_type == TakedownSubjectType.PODCAST.value:
        return (request.subject_id,)
    if request.subject_type == TakedownSubjectType.EPISODE.value:
        statement = select(Episode.podcast_id).where(
            Episode.id == request.subject_id,
        )
    else:
        statement = (
            select(Episode.podcast_id)
            .join(Mention, Mention.episode_id == Episode.id)
            .where(Mention.id == request.subject_id)
        )
    return tuple(int(row[0]) for row in db.execute(statement).all())


def _record_suppressions(
    *,
    db: Session,
    request: TakedownRequest,
    actions: set[str],
) -> None:
    """Record a durable suppression per requested scope before any deletion.

    A mention subject is stored as its ``(episode, media)`` pair, because the
    ``Mention`` row it names is deleted. Its derivative scope covers the whole
    episode, as execution does. A subject that no longer exists has nothing
    left to suppress and records nothing, as execution removes nothing.
    """
    scopes = [
        scope
        for action, scope in SUPPRESSION_SCOPES_BY_ACTION.items()
        if action in actions
    ]
    if not scopes:
        return
    subjects: dict[ContentSuppressionScope, _SuppressedSubject]
    if request.subject_type == TakedownSubjectType.PODCAST.value:
        if db.get(Podcast, request.subject_id) is None:
            return
        subjects = dict.fromkeys(
            scopes,
            (ContentSuppressionSubjectType.PODCAST, (request.subject_id,)),
        )
    elif request.subject_type == TakedownSubjectType.EPISODE.value:
        if db.get(Episode, request.subject_id) is None:
            return
        subjects = dict.fromkeys(
            scopes,
            (ContentSuppressionSubjectType.EPISODE, (request.subject_id,)),
        )
    else:
        mention = db.query(Mention).filter(Mention.id == request.subject_id).first()
        if mention is None:
            return
        subjects = {
            ContentSuppressionScope.MENTIONS: (
                ContentSuppressionSubjectType.MENTION_PAIR,
                (mention.episode_id, mention.media_id),
            ),
            ContentSuppressionScope.DERIVATIVES: (
                ContentSuppressionSubjectType.EPISODE,
                (mention.episode_id,),
            ),
        }
    for scope in scopes:
        subject_type, subject_ids = subjects[scope]
        record_suppression(
            db=db,
            takedown_request_id=request.id,
            subject_type=subject_type,
            scope=scope,
            subject_ids=subject_ids,
        )


def _episode_ids_for_request(
    *,
    db: Session,
    request: TakedownRequest,
) -> tuple[int, ...]:
    """Resolve the episodes covered by a catalog subject."""
    if request.subject_type == TakedownSubjectType.PODCAST.value:
        rows = (
            db.query(Episode.id).filter(Episode.podcast_id == request.subject_id).all()
        )
        return tuple(row[0] for row in rows)
    if request.subject_type == TakedownSubjectType.EPISODE.value:
        return (request.subject_id,)
    mention = db.query(Mention).filter(Mention.id == request.subject_id).first()
    return (mention.episode_id,) if mention is not None else ()


def _suppress_raw_transcripts(
    *,
    db: Session,
    transcripts: list[Transcript],
    artifact_store: TranscriptArtifactStore | None,
    suppressed_at: datetime,
) -> int:
    """Immediately delete retained raw payloads covered by an approval."""
    suppressed = 0
    for transcript in transcripts:
        artifacts = list_retained_artifacts(db=db, transcript_id=transcript.id)
        if (
            not artifacts
            and transcript.raw_text is None
            and transcript.segments_json is None
        ):
            continue
        if artifacts and artifact_store is None:
            raise ValueError("Encrypted transcript artifact storage is not configured")
        create_transcript_digest(
            db=db,
            transcript=transcript,
            source=resolve_column_proof_source(transcript),
            purged_at=suppressed_at,
        )
        for artifact in artifacts:
            if artifact_store is None:  # pragma: no cover - guarded above
                raise AssertionError
            artifact_store.delete(storage_key=artifact.storage_key)
            artifact.purged_at = suppressed_at
        transcript.raw_text = None
        transcript.segments_json = None
        transcript.purged_at = suppressed_at
        transcript.retention_tier = "purged"
        suppressed += 1
    return suppressed


def _suppress_derivatives(*, db: Session, episode_ids: tuple[int, ...]) -> int:
    """Delete generated summaries and semantic retrieval content."""
    if not episode_ids:
        return 0
    run_count = (
        db.query(DerivativeGenerationRun)
        .filter(DerivativeGenerationRun.episode_id.in_(episode_ids))
        .delete(synchronize_session=False)
    )
    chunk_count = (
        db.query(SemanticChunk)
        .filter(SemanticChunk.episode_id.in_(episode_ids))
        .delete(synchronize_session=False)
    )
    summary_count = (
        db.query(EpisodeSummary)
        .filter(EpisodeSummary.episode_id.in_(episode_ids))
        .delete(synchronize_session=False)
    )
    db.query(Transcript).filter(Transcript.episode_id.in_(episode_ids)).update(
        {
            Transcript.cleaned_text: None,
            Transcript.cleaned_at: None,
            Transcript.digest_text: None,
            Transcript.digest_created_at: None,
        },
        synchronize_session=False,
    )
    return int(run_count + chunk_count + summary_count)


def _unpublish_mentions(
    *,
    db: Session,
    request: TakedownRequest,
    episode_ids: tuple[int, ...],
) -> tuple[int, tuple[int, ...]]:
    """Remove published mentions and detach retained provenance references."""
    query = db.query(Mention)
    if request.subject_type == TakedownSubjectType.MENTION.value:
        query = query.filter(Mention.id == request.subject_id)
    else:
        query = query.filter(Mention.episode_id.in_(episode_ids))
    mentions = query.all()
    if not mentions:
        return 0, ()
    mention_ids = [mention.id for mention in mentions]
    media_ids = tuple(sorted({mention.media_id for mention in mentions}))
    db.query(MentionCandidate).filter(
        MentionCandidate.mention_id.in_(mention_ids)
    ).update({MentionCandidate.mention_id: None}, synchronize_session=False)
    db.query(GraphTriple).filter(
        GraphTriple.provenance_mention_id.in_(mention_ids)
    ).update({GraphTriple.provenance_mention_id: None}, synchronize_session=False)
    for mention in mentions:
        db.delete(mention)
    db.flush()
    return len(mentions), media_ids


def _register_source_opt_outs(*, db: Session, transcripts: list[Transcript]) -> int:
    """Persist future-acquisition raw retention opt-outs for known sources."""
    known_sources: set[tuple[int, str]] = set()
    for transcript in transcripts:
        source_key = (transcript.episode.podcast_id, transcript.provider)
        if source_key in known_sources:
            continue
        stored_policy = get_transcript_source_retention_policy(
            db=db,
            transcript=transcript,
        )
        upsert_transcript_source_retention_policy(
            db=db,
            transcript=transcript,
            policy=(
                to_retention_policy(record=stored_policy)
                if stored_policy is not None
                else TranscriptRetentionPolicy()
            ),
            source_retention_opt_out=True,
        )
        known_sources.add(source_key)
    for transcript in transcripts:
        transcript.source_retention_opt_out = True
    return len(known_sources)
