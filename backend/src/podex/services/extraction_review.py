"""Persist extraction output into review-state mention candidates."""

from __future__ import annotations

import string
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy.orm import Session

from podex.models import (
    Episode,
    Media,
    Mention,
    MentionCandidate,
    MentionCandidateProvenance,
    MentionCandidateProvenanceEventType,
    MentionCandidateState,
    ReviewItem,
    ReviewItemStatus,
    ReviewPriority,
)
from podex.services.llm_extraction import ExtractedMedia


@dataclass(frozen=True, slots=True)
class PersistedExtractionReviewData:
    """Summary of persisted extraction review state.

    Attributes:
        candidates_created: Number of new mention candidates created.
        candidates_updated: Number of existing mention candidates updated.
        review_items_created: Number of review queue items created.
        skipped_low_confidence: Number of extracted items skipped for low confidence.
        skipped_existing_mentions: Number of extracted items skipped because a
            published mention already exists for the same episode and media.
    """

    candidates_created: int = 0
    candidates_updated: int = 0
    review_items_created: int = 0
    skipped_low_confidence: int = 0
    skipped_existing_mentions: int = 0


@dataclass(frozen=True, slots=True)
class CandidateSnapshotData:
    """Mutable candidate fields captured for provenance comparisons."""

    raw_title: str
    normalized_title: str | None
    suggested_author: str | None
    timestamp_seconds: int | None
    context: str | None
    confidence: float
    extraction_source: str | None
    source_job_id: int | None
    media_id: int | None
    metadata_json: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class SegmentContextMatch:
    """Matched transcript context for an extracted item.

    Attributes:
        timestamp_seconds: Approximate segment start time for the match.
        context: Review-friendly transcript context around the match.
    """

    timestamp_seconds: int | None = None
    context: str | None = None


def _normalize_value(*, value: str | None) -> str | None:
    """Normalize a free-text value for replay-safe comparisons.

    Args:
        value: Raw text value.

    Returns:
        Normalized value or ``None`` when the input is empty.
    """
    if value is None:
        return None

    normalized = " ".join(value.split()).strip().lower()
    if not normalized:
        return None

    translation = str.maketrans("", "", string.punctuation)
    normalized = normalized.translate(translation).strip()
    return normalized or None


def _priority_for_confidence(*, confidence: float) -> ReviewPriority:
    """Map extraction confidence to an operator review priority.

    Args:
        confidence: Extracted item confidence score.

    Returns:
        Review priority for the queue item.
    """
    if confidence < 0.65:
        return ReviewPriority.HIGH
    if confidence < 0.85:
        return ReviewPriority.MEDIUM
    return ReviewPriority.LOW


def _segment_text(*, segment: dict[str, Any]) -> str | None:
    """Extract normalized text content from a transcript segment.

    Args:
        segment: Raw transcript segment payload.

    Returns:
        Segment text when present.
    """
    text = segment.get("text")
    if not isinstance(text, str):
        return None

    stripped = text.strip()
    return stripped or None


def _segment_start_seconds(*, segment: dict[str, Any]) -> int | None:
    """Extract the segment start time in seconds.

    Args:
        segment: Raw transcript segment payload.

    Returns:
        Integer start seconds when available.
    """
    start = segment.get("start")
    if isinstance(start, int | float):
        return int(start)
    return None


def _build_segment_context(
    *,
    segments: Sequence[dict[str, Any]],
    center_index: int,
) -> str | None:
    """Build a compact context window around a matched segment.

    Args:
        segments: Ordered transcript segments.
        center_index: Index of the matched segment.

    Returns:
        Joined context string around the match.
    """
    context_parts: list[str] = []
    for offset in (-1, 0, 1):
        index = center_index + offset
        if index < 0 or index >= len(segments):
            continue

        text = _segment_text(segment=segments[index])
        if text is not None:
            context_parts.append(text)

    if not context_parts:
        return None

    return " ".join(context_parts)


def _find_segment_context(
    *,
    item: ExtractedMedia,
    segments: Sequence[dict[str, Any]] | None,
) -> SegmentContextMatch:
    """Find approximate timestamp and context for an extracted item.

    Args:
        item: Extracted media item.
        segments: Ordered transcript segments for the episode.

    Returns:
        Matched timestamp/context payload when a segment match is found.
    """
    if not segments:
        return SegmentContextMatch()

    normalized_title = _normalize_value(value=item.title)
    if normalized_title is None:
        return SegmentContextMatch()

    normalized_creator = _normalize_value(value=item.creator)
    best_index: int | None = None
    best_score = 0

    for index, segment in enumerate(segments):
        text = _segment_text(segment=segment)
        normalized_segment = _normalize_value(value=text)
        if normalized_segment is None:
            continue

        score = 0
        if normalized_title in normalized_segment:
            score += 2
        if normalized_creator is not None and normalized_creator in normalized_segment:
            score += 1

        if score > best_score:
            best_score = score
            best_index = index

    if best_index is None or best_score == 0:
        return SegmentContextMatch()

    return SegmentContextMatch(
        timestamp_seconds=_segment_start_seconds(segment=segments[best_index]),
        context=_build_segment_context(segments=segments, center_index=best_index),
    )


def _find_matching_media(
    *,
    db: Session,
    media_type: str,
    normalized_title: str | None,
) -> Media | None:
    """Resolve an extracted title against existing canonical media records.

    Args:
        db: Database session.
        media_type: Extracted media type value.
        normalized_title: Normalized extracted title.

    Returns:
        Matching canonical media record when found.
    """
    if normalized_title is None:
        return None

    candidates = cast(
        list[Media],
        db.query(Media).filter(Media.type == media_type).all(),
    )
    for media in candidates:
        if _normalize_value(value=media.title) == normalized_title:
            return media
    return None


def _find_matching_candidate(
    *,
    candidates: list[MentionCandidate],
    media_type: str,
    normalized_title: str | None,
    normalized_author: str | None,
    media_id: int | None,
) -> MentionCandidate | None:
    """Find an existing candidate representing the same extracted item.

    Args:
        candidates: Existing candidates for the episode.
        media_type: Extracted media type value.
        normalized_title: Normalized extracted title.
        normalized_author: Normalized extracted author/creator.
        media_id: Matched canonical media identifier, if any.

    Returns:
        Matching mention candidate when found.
    """
    for candidate in candidates:
        if candidate.media_type != media_type:
            continue

        if media_id is not None and candidate.media_id == media_id:
            return candidate

        if candidate.normalized_title != normalized_title:
            continue

        candidate_author = _normalize_value(value=candidate.suggested_author)
        if normalized_author is None or candidate_author in {None, normalized_author}:
            return candidate

    return None


def _candidate_metadata(*, item: ExtractedMedia) -> dict[str, int] | None:
    """Build structured metadata stored alongside a mention candidate.

    Args:
        item: Extracted media item.

    Returns:
        Candidate metadata payload.
    """
    if item.year is None:
        return None
    return {"year": item.year}


def _snapshot_candidate(*, candidate: MentionCandidate) -> CandidateSnapshotData:
    """Capture the mutable fields of a candidate for provenance diffs.

    Args:
        candidate: Candidate to snapshot.

    Returns:
        Comparable snapshot of candidate fields.
    """
    metadata_json = (
        None if candidate.metadata_json is None else dict(candidate.metadata_json)
    )
    return CandidateSnapshotData(
        raw_title=candidate.raw_title,
        normalized_title=candidate.normalized_title,
        suggested_author=candidate.suggested_author,
        timestamp_seconds=candidate.timestamp_seconds,
        context=candidate.context,
        confidence=candidate.confidence,
        extraction_source=candidate.extraction_source,
        source_job_id=candidate.source_job_id,
        media_id=candidate.media_id,
        metadata_json=metadata_json,
    )


def _build_change_summary(*, changed_fields: list[str]) -> str | None:
    """Build a review-friendly summary of changed candidate fields.

    Args:
        changed_fields: Names of changed fields.

    Returns:
        Human-readable change summary when fields changed.
    """
    if not changed_fields:
        return None
    return f"Updated from extraction rerun: {', '.join(changed_fields)}"


def _record_candidate_provenance(
    *,
    db: Session,
    candidate: MentionCandidate,
    event_type: MentionCandidateProvenanceEventType,
    change_summary: str | None,
    changed_fields: list[str],
) -> None:
    """Persist an immutable provenance snapshot for a candidate event.

    Args:
        db: Database session.
        candidate: Candidate to snapshot.
        event_type: Provenance event type.
        change_summary: Review-friendly event summary.
        changed_fields: Field names changed by the event.
    """
    metadata_json = dict(candidate.metadata_json or {})
    if changed_fields:
        metadata_json["changed_fields"] = changed_fields

    provenance = MentionCandidateProvenance(
        mention_candidate_id=candidate.id,
        source_job_id=candidate.source_job_id,
        media_id=candidate.media_id,
        event_type=event_type.value,
        change_summary=change_summary,
        raw_title=candidate.raw_title,
        normalized_title=candidate.normalized_title,
        suggested_author=candidate.suggested_author,
        timestamp_seconds=candidate.timestamp_seconds,
        context=candidate.context,
        confidence=candidate.confidence,
        extraction_source=candidate.extraction_source,
        metadata_json=metadata_json or None,
    )
    db.add(provenance)


def _changed_candidate_fields(
    *,
    before: CandidateSnapshotData,
    after: CandidateSnapshotData,
) -> list[str]:
    """Calculate which candidate fields changed during an update.

    Args:
        before: Snapshot before mutation.
        after: Snapshot after mutation.

    Returns:
        Ordered list of changed field names.
    """
    changed_fields: list[str] = []
    for field_name in CandidateSnapshotData.__dataclass_fields__:
        if getattr(before, field_name) != getattr(after, field_name):
            changed_fields.append(field_name)
    return changed_fields


def _update_candidate(
    *,
    candidate: MentionCandidate,
    item: ExtractedMedia,
    normalized_title: str | None,
    timestamp_seconds: int | None,
    context: str | None,
    media_id: int | None,
    source_job_id: int | None,
    extraction_source: str,
) -> None:
    """Refresh an existing candidate with the latest extraction output.

    Args:
        candidate: Mention candidate to update.
        item: Extracted media item.
        normalized_title: Normalized extracted title.
        timestamp_seconds: Matched transcript timestamp.
        context: Matched transcript context.
        media_id: Matched canonical media identifier, if any.
        source_job_id: Optional pipeline job identifier.
        extraction_source: Extraction provider identifier.
    """
    candidate.raw_title = item.title
    candidate.normalized_title = normalized_title
    candidate.suggested_author = item.creator
    candidate.confidence = item.confidence
    candidate.extraction_source = extraction_source
    candidate.timestamp_seconds = timestamp_seconds or candidate.timestamp_seconds
    candidate.context = context or candidate.context
    candidate.media_id = candidate.media_id or media_id
    candidate.source_job_id = source_job_id or candidate.source_job_id

    metadata = _candidate_metadata(item=item)
    if metadata is not None:
        candidate.metadata_json = {
            **(candidate.metadata_json or {}),
            **metadata,
        }


def _ensure_review_item(
    *,
    db: Session,
    candidate: MentionCandidate,
    confidence: float,
) -> bool:
    """Ensure a pending candidate is visible in the operator review queue.

    Args:
        db: Database session.
        candidate: Mention candidate to expose through review.
        confidence: Confidence score for priority derivation.

    Returns:
        ``True`` when a new review item is created.
    """
    if candidate.state != MentionCandidateState.PENDING_REVIEW.value:
        return False

    priority = _priority_for_confidence(confidence=confidence)

    if candidate.review_item is None:
        db.add(
            ReviewItem(
                mention_candidate_id=candidate.id,
                priority=priority.value,
            )
        )
        return True

    if candidate.review_item.status in {
        ReviewItemStatus.PENDING.value,
        ReviewItemStatus.IN_REVIEW.value,
    }:
        candidate.review_item.priority = priority.value

    return False


def persist_extracted_candidates(
    *,
    db: Session,
    episode: Episode,
    items: list[ExtractedMedia],
    segments: Sequence[dict[str, Any]] | None,
    min_confidence: float,
    extraction_source: str,
    source_job_id: int | None = None,
) -> PersistedExtractionReviewData:
    """Persist extraction results as replay-safe review candidates.

    Args:
        db: Database session.
        episode: Episode whose transcript was extracted.
        items: Extracted media items.
        segments: Ordered transcript segments for context matching.
        min_confidence: Minimum confidence threshold to persist.
        extraction_source: Extraction provider identifier.
        source_job_id: Optional pipeline job identifier.

    Returns:
        Summary of persisted candidate and review queue changes.
    """
    existing_candidates = (
        db.query(MentionCandidate)
        .filter(MentionCandidate.episode_id == episode.id)
        .all()
    )
    existing_media_mentions = {
        mention.media_id
        for mention in db.query(Mention).filter(Mention.episode_id == episode.id).all()
    }

    summary = PersistedExtractionReviewData()

    for item in items:
        if item.confidence < min_confidence:
            summary = PersistedExtractionReviewData(
                candidates_created=summary.candidates_created,
                candidates_updated=summary.candidates_updated,
                review_items_created=summary.review_items_created,
                skipped_low_confidence=summary.skipped_low_confidence + 1,
                skipped_existing_mentions=summary.skipped_existing_mentions,
            )
            continue

        normalized_title = _normalize_value(value=item.title)
        normalized_author = _normalize_value(value=item.creator)
        segment_match = _find_segment_context(item=item, segments=segments)
        matched_media = _find_matching_media(
            db=db,
            media_type=item.media_type.value,
            normalized_title=normalized_title,
        )

        if matched_media is not None and matched_media.id in existing_media_mentions:
            summary = PersistedExtractionReviewData(
                candidates_created=summary.candidates_created,
                candidates_updated=summary.candidates_updated,
                review_items_created=summary.review_items_created,
                skipped_low_confidence=summary.skipped_low_confidence,
                skipped_existing_mentions=summary.skipped_existing_mentions + 1,
            )
            continue

        candidate = _find_matching_candidate(
            candidates=existing_candidates,
            media_type=item.media_type.value,
            normalized_title=normalized_title,
            normalized_author=normalized_author,
            media_id=matched_media.id if matched_media is not None else None,
        )

        if candidate is None:
            candidate = MentionCandidate(
                episode_id=episode.id,
                media_id=matched_media.id if matched_media is not None else None,
                source_job_id=source_job_id,
                media_type=item.media_type.value,
                raw_title=item.title,
                normalized_title=normalized_title,
                suggested_author=item.creator,
                timestamp_seconds=segment_match.timestamp_seconds,
                context=segment_match.context,
                confidence=item.confidence,
                extraction_source=extraction_source,
                metadata_json=_candidate_metadata(item=item),
            )
            db.add(candidate)
            db.flush()
            _record_candidate_provenance(
                db=db,
                candidate=candidate,
                event_type=MentionCandidateProvenanceEventType.CREATED,
                change_summary="Created from extraction result",
                changed_fields=[],
            )
            existing_candidates.append(candidate)
            review_item_created = _ensure_review_item(
                db=db,
                candidate=candidate,
                confidence=item.confidence,
            )
            summary = PersistedExtractionReviewData(
                candidates_created=summary.candidates_created + 1,
                candidates_updated=summary.candidates_updated,
                review_items_created=summary.review_items_created
                + int(review_item_created),
                skipped_low_confidence=summary.skipped_low_confidence,
                skipped_existing_mentions=summary.skipped_existing_mentions,
            )
            continue

        before_snapshot = _snapshot_candidate(candidate=candidate)
        _update_candidate(
            candidate=candidate,
            item=item,
            normalized_title=normalized_title,
            timestamp_seconds=segment_match.timestamp_seconds,
            context=segment_match.context,
            media_id=matched_media.id if matched_media is not None else None,
            source_job_id=source_job_id,
            extraction_source=extraction_source,
        )
        after_snapshot = _snapshot_candidate(candidate=candidate)
        changed_fields = _changed_candidate_fields(
            before=before_snapshot,
            after=after_snapshot,
        )
        _record_candidate_provenance(
            db=db,
            candidate=candidate,
            event_type=MentionCandidateProvenanceEventType.UPDATED,
            change_summary=_build_change_summary(changed_fields=changed_fields),
            changed_fields=changed_fields,
        )
        review_item_created = _ensure_review_item(
            db=db,
            candidate=candidate,
            confidence=item.confidence,
        )
        summary = PersistedExtractionReviewData(
            candidates_created=summary.candidates_created,
            candidates_updated=summary.candidates_updated + 1,
            review_items_created=(
                summary.review_items_created + int(review_item_created)
            ),
            skipped_low_confidence=summary.skipped_low_confidence,
            skipped_existing_mentions=summary.skipped_existing_mentions,
        )

    return summary
