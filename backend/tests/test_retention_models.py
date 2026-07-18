"""Round-trip tests for transcript retention models and columns."""

from datetime import UTC, datetime

from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import (
    Transcript,
    TranscriptArtifact,
    TranscriptDigest,
    TranscriptSourceRetentionPolicy,
)
from tests.conftest import seed_catalog_graph

_NOW = datetime(2026, 7, 19, tzinfo=UTC)


def test_transcript_retention_columns_round_trip(db_session: Session) -> None:
    """Retention tier, sampling, and purge metadata persist."""
    graph = seed_catalog_graph(db_session)
    transcript = Transcript(
        episode_id=graph.episode_id,
        provider="podscripts",
        raw_text="raw",
        retention_tier="cold",
        retention_policy_version="rp1",
        retention_exempt_sample=True,
        retention_sample_rate=0.05,
        retention_sample_score=0.42,
        retention_sample_strata_json={"provider": "podscripts"},
        retention_blockers_json=["pending_review"],
        purge_eligible_at=_NOW,
    )
    db_session.add(transcript)
    db_session.commit()

    stored = db_session.execute(select(Transcript)).scalar_one()
    assert_that(stored.retention_tier).is_equal_to("cold")
    assert_that(stored.retention_exempt_sample).is_true()
    assert_that(stored.retention_blockers_json).contains("pending_review")
    assert_that(stored.purged_at).is_none()


def test_digest_artifact_and_policy_round_trip(db_session: Session) -> None:
    """Digests, artifacts, and per-source policies persist and link."""
    graph = seed_catalog_graph(db_session)
    transcript = Transcript(
        episode_id=graph.episode_id,
        provider="podscripts",
        raw_text="raw",
    )
    db_session.add(transcript)
    db_session.commit()

    digest = TranscriptDigest(
        transcript_id=transcript.id,
        episode_id=graph.episode_id,
        digest_key="t1:v1",
        source_text_hash="a" * 64,
        provider="podscripts",
        summary_text="A digest of the purged transcript.",
        generated_at=_NOW,
        purged_at=_NOW,
    )
    db_session.add(digest)
    db_session.commit()

    artifact = TranscriptArtifact(
        transcript_id=transcript.id,
        episode_id=graph.episode_id,
        reacquired_from_digest_id=digest.id,
        storage_key="artifacts/t1.json.enc",
        storage_backend="s3",
        encryption_key_id="key-1",
        provider="podscripts",
        source_text_hash="a" * 64,
        content_type="application/json",
        byte_size=1024,
        stored_at=_NOW,
    )
    policy = TranscriptSourceRetentionPolicy(
        podcast_id=graph.podcast_id,
        source_key="podscripts",
        policy_version="rp1",
        hot_days=30,
        warm_days=180,
        min_purge_confidence=0.9,
        source_retention_opt_out=True,
    )
    db_session.add_all([artifact, policy])
    db_session.commit()

    stored_digest = db_session.execute(select(TranscriptDigest)).scalar_one()
    stored_artifact = db_session.execute(
        select(TranscriptArtifact),
    ).scalar_one()
    stored_policy = db_session.execute(
        select(TranscriptSourceRetentionPolicy),
    ).scalar_one()
    assert_that(stored_digest.transcript.digests).contains(stored_digest)
    assert_that(stored_artifact.transcript.artifacts).contains(stored_artifact)
    assert_that(stored_artifact.reacquired_from_digest_id).is_equal_to(
        digest.id,
    )
    assert_that(stored_policy.source_retention_opt_out).is_true()
