"""Stratified transcript retention sampling for the calibration corpus."""

from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil

from sqlalchemy import and_, or_
from sqlalchemy.orm import Query, Session

from podex.models import (
    Episode,
    MentionCandidate,
    Podcast,
    Transcript,
    TranscriptArtifact,
)
from podex.services.transcript_retention import (
    TranscriptRetentionPolicy,
    TranscriptRetentionSampleKey,
    TranscriptRetentionService,
)


@dataclass(frozen=True, slots=True)
class RetentionSampleStratumData:
    """Coverage for one source/topic/confidence/age sample stratum."""

    source: str
    topic: str
    confidence_band: str
    age_bucket: str
    eligible_count: int
    sampled_count: int
    target_count: int


@dataclass(frozen=True, slots=True)
class RetentionSamplingReportData:
    """Coverage report for the permanent transcript calibration corpus."""

    policy_version: str
    sample_rate: float
    eligible_count: int
    sampled_count: int
    target_count: int
    strata: list[RetentionSampleStratumData]


@dataclass(frozen=True, slots=True)
class _CandidateTranscriptData:
    """Eligible transcript plus its stable stratification dimensions."""

    transcript: Transcript
    source: str
    topic: str
    confidence_band: str
    age_bucket: str
    score: float


def recalculate_retention_sample(
    *,
    db: Session,
    policy: TranscriptRetentionPolicy,
    now: datetime | None = None,
) -> RetentionSamplingReportData:
    """Assign deterministic retention exemptions across all populated strata."""
    effective_now = now or datetime.now(UTC)
    service = TranscriptRetentionService(policy=policy)
    candidates = _eligible_candidates(db=db, service=service, now=effective_now)
    grouped: dict[tuple[str, str, str, str], list[_CandidateTranscriptData]] = {}
    for candidate in candidates:
        key = (
            candidate.source,
            candidate.topic,
            candidate.confidence_band,
            candidate.age_bucket,
        )
        grouped.setdefault(key, []).append(candidate)

    strata: list[RetentionSampleStratumData] = []
    for key, group in sorted(grouped.items()):
        selected_count = (
            max(1, ceil(len(group) * policy.retention_sample_rate))
            if policy.retention_sample_rate > 0
            else 0
        )
        selected_ids = {
            item.transcript.id
            for item in sorted(
                group, key=lambda item: (item.score, item.transcript.id)
            )[:selected_count]
        }
        for item in group:
            item.transcript.retention_exempt_sample = item.transcript.id in selected_ids
            item.transcript.retention_policy_version = policy.sample_version
            item.transcript.retention_sample_rate = policy.retention_sample_rate
            item.transcript.retention_sample_score = item.score
            item.transcript.retention_sample_strata_json = {
                "source": item.source,
                "topic": item.topic,
                "confidence_band": item.confidence_band,
                "age_bucket": item.age_bucket,
            }
        strata.append(
            RetentionSampleStratumData(
                source=key[0],
                topic=key[1],
                confidence_band=key[2],
                age_bucket=key[3],
                eligible_count=len(group),
                sampled_count=selected_count,
                target_count=selected_count,
            )
        )

    db.flush()
    return _to_report(policy=policy, candidates=candidates, strata=strata)


def get_retention_sampling_report(
    *,
    db: Session,
    policy: TranscriptRetentionPolicy | None = None,
) -> RetentionSamplingReportData:
    """Build coverage metrics for currently persisted sample assignments."""
    effective_policy = policy or TranscriptRetentionPolicy()
    transcripts = (
        _with_retained_raw_payload(db.query(Transcript))
        .filter(Transcript.purged_at.is_(None))
        .order_by(Transcript.id.asc())
        .all()
    )
    grouped: dict[tuple[str, str, str, str], list[Transcript]] = {}
    for transcript in transcripts:
        strata = transcript.retention_sample_strata_json or {}
        key = (
            strata.get("source", "unassigned"),
            strata.get("topic", "unassigned"),
            strata.get("confidence_band", "unassigned"),
            strata.get("age_bucket", "unassigned"),
        )
        grouped.setdefault(key, []).append(transcript)
    persisted_version = next(
        (
            transcript.retention_policy_version
            for transcript in transcripts
            if transcript.retention_policy_version is not None
        ),
        effective_policy.sample_version,
    )
    persisted_rate = next(
        (
            transcript.retention_sample_rate
            for transcript in transcripts
            if transcript.retention_sample_rate is not None
        ),
        effective_policy.retention_sample_rate,
    )
    report_strata = [
        RetentionSampleStratumData(
            source=key[0],
            topic=key[1],
            confidence_band=key[2],
            age_bucket=key[3],
            eligible_count=len(group),
            sampled_count=sum(
                1 for transcript in group if transcript.retention_exempt_sample
            ),
            target_count=(
                max(1, ceil(len(group) * persisted_rate)) if persisted_rate > 0 else 0
            ),
        )
        for key, group in sorted(grouped.items())
    ]
    return RetentionSamplingReportData(
        policy_version=persisted_version,
        sample_rate=persisted_rate,
        eligible_count=len(transcripts),
        sampled_count=sum(
            1 for transcript in transcripts if transcript.retention_exempt_sample
        ),
        target_count=sum(item.target_count for item in report_strata),
        strata=report_strata,
    )


def _eligible_candidates(
    *,
    db: Session,
    service: TranscriptRetentionService,
    now: datetime,
) -> list[_CandidateTranscriptData]:
    """Load eligible transcripts and derive their sample strata."""
    transcripts = (
        _with_retained_raw_payload(db.query(Transcript))
        .join(Episode, Episode.id == Transcript.episode_id)
        .join(Podcast, Podcast.id == Episode.podcast_id)
        .filter(Transcript.purged_at.is_(None))
        .order_by(Transcript.id.asc())
        .all()
    )
    candidates: list[_CandidateTranscriptData] = []
    for transcript in transcripts:
        episode = transcript.episode
        extraction = (
            db.query(MentionCandidate)
            .filter(MentionCandidate.episode_id == episode.id)
            .order_by(MentionCandidate.confidence.desc(), MentionCandidate.id.asc())
            .first()
        )
        confidence = extraction.confidence if extraction is not None else None
        topic = extraction.media_type if extraction is not None else "unknown"
        source = episode.podcast.slug
        acquired_at = transcript.fetched_at or transcript.created_at
        sample = service.choose_sample(
            sample_key=TranscriptRetentionSampleKey(
                transcript_key=f"transcript:{transcript.id}",
                source_key=source,
                topic_key=topic,
            ),
            acquired_at=acquired_at,
            now=now,
            extraction_confidence=confidence,
        )
        candidates.append(
            _CandidateTranscriptData(
                transcript=transcript,
                source=source,
                topic=topic,
                confidence_band=sample.confidence_band.value,
                age_bucket=sample.age_bucket.value,
                score=sample.score,
            )
        )
    return candidates


def _with_retained_raw_payload(query: Query[Transcript]) -> Query[Transcript]:
    """Include legacy inline inputs and encrypted artifact-backed inputs."""
    return (
        query.outerjoin(
            TranscriptArtifact,
            and_(
                TranscriptArtifact.transcript_id == Transcript.id,
                TranscriptArtifact.purged_at.is_(None),
            ),
        )
        .filter(
            or_(
                Transcript.raw_text.is_not(None),
                TranscriptArtifact.id.is_not(None),
            ),
        )
        .distinct()
    )


def _to_report(
    *,
    policy: TranscriptRetentionPolicy,
    candidates: list[_CandidateTranscriptData],
    strata: list[RetentionSampleStratumData],
) -> RetentionSamplingReportData:
    """Build a report from a completed recalculation batch."""
    return RetentionSamplingReportData(
        policy_version=policy.sample_version,
        sample_rate=policy.retention_sample_rate,
        eligible_count=len(candidates),
        sampled_count=sum(item.sampled_count for item in strata),
        target_count=sum(item.target_count for item in strata),
        strata=strata,
    )
