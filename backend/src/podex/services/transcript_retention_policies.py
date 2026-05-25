"""Persistence services for source-scoped transcript retention policy."""

from dataclasses import replace
from datetime import timedelta

from sqlalchemy.orm import Session

from podex.models import Transcript, TranscriptSourceRetentionPolicy
from podex.services.transcript_retention import TranscriptRetentionPolicy
from podex.services.transcript_source import TranscriptAcquisitionResult


def upsert_transcript_source_retention_policy(
    *,
    db: Session,
    transcript: Transcript,
    policy: TranscriptRetentionPolicy,
    source_retention_opt_out: bool,
) -> TranscriptSourceRetentionPolicy:
    """Save lifecycle configuration for a transcript's podcast and source."""
    record = (
        db.query(TranscriptSourceRetentionPolicy)
        .filter(
            TranscriptSourceRetentionPolicy.podcast_id == transcript.episode.podcast_id,
            TranscriptSourceRetentionPolicy.source_key == transcript.provider,
        )
        .first()
    )
    if record is None:
        record = TranscriptSourceRetentionPolicy(
            podcast_id=transcript.episode.podcast_id,
            source_key=transcript.provider,
            policy_version=policy.sample_version,
            hot_days=policy.hot_retention_period.days,
            warm_days=policy.warm_retention_period.days,
            min_purge_confidence=policy.min_purge_confidence,
            source_retention_opt_out=source_retention_opt_out,
        )
        db.add(record)
    else:
        record.policy_version = policy.sample_version
        record.hot_days = policy.hot_retention_period.days
        record.warm_days = policy.warm_retention_period.days
        record.min_purge_confidence = policy.min_purge_confidence
        record.source_retention_opt_out = source_retention_opt_out
    db.flush()
    return record


def get_transcript_source_retention_policy(
    *,
    db: Session,
    transcript: Transcript,
) -> TranscriptSourceRetentionPolicy | None:
    """Get persisted lifecycle configuration for a transcript source."""
    return (
        db.query(TranscriptSourceRetentionPolicy)
        .filter(
            TranscriptSourceRetentionPolicy.podcast_id == transcript.episode.podcast_id,
            TranscriptSourceRetentionPolicy.source_key == transcript.provider,
        )
        .first()
    )


def to_retention_policy(
    *,
    record: TranscriptSourceRetentionPolicy,
) -> TranscriptRetentionPolicy:
    """Convert stored thresholds into the pure retention policy contract."""
    return TranscriptRetentionPolicy(
        hot_retention_period=timedelta(days=record.hot_days),
        warm_retention_period=timedelta(days=record.warm_days),
        min_purge_confidence=record.min_purge_confidence,
        retention_sample_rate=0,
        sample_version=record.policy_version,
    )


def apply_stored_acquisition_opt_out(
    *,
    db: Session,
    podcast_id: int,
    source_key: str,
    acquisition: TranscriptAcquisitionResult,
) -> TranscriptAcquisitionResult:
    """Apply a saved opt-out to a new payload from the same source."""
    record = (
        db.query(TranscriptSourceRetentionPolicy)
        .filter(
            TranscriptSourceRetentionPolicy.podcast_id == podcast_id,
            TranscriptSourceRetentionPolicy.source_key == source_key,
        )
        .first()
    )
    if record is None or not record.source_retention_opt_out:
        return acquisition
    return replace(
        acquisition,
        should_store_raw=False,
        source_retention_opt_out=True,
    )
