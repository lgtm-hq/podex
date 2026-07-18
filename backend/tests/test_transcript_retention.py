"""Tests for transcript retention policy primitives."""

from datetime import datetime, timedelta

from assertpy import assert_that

from podex.services.transcript_retention import (
    TranscriptAgeBucket,
    TranscriptConfidenceBand,
    TranscriptLifecycleTier,
    TranscriptRetentionBlocker,
    TranscriptRetentionPolicy,
    TranscriptRetentionSampleKey,
    TranscriptRetentionService,
    TranscriptRetentionState,
)


def test_classify_tier_transitions_from_hot_to_warm_to_cold() -> None:
    """Verify age-based transcript lifecycle tier transitions."""
    now = datetime(2026, 5, 10, 12, 0, 0)
    service = TranscriptRetentionService(
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=7),
            warm_retention_period=timedelta(days=30),
        ),
    )

    hot = service.classify_tier(
        state=TranscriptRetentionState(acquired_at=now - timedelta(days=1)),
        now=now,
    )
    warm = service.classify_tier(
        state=TranscriptRetentionState(acquired_at=now - timedelta(days=14)),
        now=now,
    )
    cold = service.classify_tier(
        state=TranscriptRetentionState(acquired_at=now - timedelta(days=45)),
        now=now,
    )

    assert_that(hot).is_equal_to(TranscriptLifecycleTier.HOT)
    assert_that(warm).is_equal_to(TranscriptLifecycleTier.WARM)
    assert_that(cold).is_equal_to(TranscriptLifecycleTier.COLD)


def test_source_opt_out_suppresses_raw_transcript_retention() -> None:
    """Verify source opt-out bypasses active raw transcript retention tiers."""
    now = datetime(2026, 5, 10, 12, 0, 0)
    service = TranscriptRetentionService(
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=30),
            warm_retention_period=timedelta(days=180),
            min_purge_confidence=0.85,
        ),
    )

    decision = service.evaluate(
        state=TranscriptRetentionState(
            acquired_at=now - timedelta(days=1),
            extraction_confidence=0.95,
            digest_created_at=now,
            source_retention_opt_out=True,
        ),
        now=now,
    )

    assert_that(decision.tier).is_equal_to(TranscriptLifecycleTier.COLD)
    assert_that(decision.retention_suppressed).is_true()
    assert_that(decision.purge_eligible).is_true()
    assert_that(decision.purge_blockers).is_empty()


def test_source_opt_out_does_not_wait_for_derivative_prerequisites() -> None:
    """Verify creator/source opt-out allows immediate raw retention suppression."""
    now = datetime(2026, 5, 10, 12, 0, 0)
    service = TranscriptRetentionService(
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=30),
            warm_retention_period=timedelta(days=180),
            min_purge_confidence=0.85,
        ),
    )

    decision = service.evaluate(
        state=TranscriptRetentionState(
            acquired_at=now - timedelta(days=1),
            source_retention_opt_out=True,
        ),
        now=now,
    )

    assert_that(decision.tier).is_equal_to(TranscriptLifecycleTier.COLD)
    assert_that(decision.purge_eligible).is_true()
    assert_that(decision.purge_blockers).is_empty()


def test_low_confidence_blocks_purge() -> None:
    """Verify cold transcripts still need enough extraction confidence to purge."""
    now = datetime(2026, 5, 10, 12, 0, 0)
    service = TranscriptRetentionService(
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=7),
            warm_retention_period=timedelta(days=30),
            min_purge_confidence=0.85,
        ),
    )

    decision = service.evaluate(
        state=TranscriptRetentionState(
            acquired_at=now - timedelta(days=45),
            extraction_confidence=0.6,
            digest_created_at=now,
        ),
        now=now,
    )

    assert_that(decision.tier).is_equal_to(TranscriptLifecycleTier.COLD)
    assert_that(decision.purge_eligible).is_false()
    assert_that(decision.purge_blockers).contains(
        TranscriptRetentionBlocker.LOW_CONFIDENCE,
    )


def test_digest_is_required_before_purge() -> None:
    """Verify purge eligibility waits for a transcript digest."""
    now = datetime(2026, 5, 10, 12, 0, 0)
    service = TranscriptRetentionService(
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=7),
            warm_retention_period=timedelta(days=30),
            min_purge_confidence=0.85,
        ),
    )

    decision = service.evaluate(
        state=TranscriptRetentionState(
            acquired_at=now - timedelta(days=45),
            extraction_confidence=0.9,
        ),
        now=now,
    )

    assert_that(decision.tier).is_equal_to(TranscriptLifecycleTier.COLD)
    assert_that(decision.purge_eligible).is_false()
    assert_that(decision.purge_blockers).contains(
        TranscriptRetentionBlocker.DIGEST_REQUIRED,
    )


def test_retention_exempt_sample_blocks_purge() -> None:
    """Verify retention-exempt samples are preserved from purge."""
    now = datetime(2026, 5, 10, 12, 0, 0)
    service = TranscriptRetentionService(
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=7),
            warm_retention_period=timedelta(days=30),
            min_purge_confidence=0.85,
        ),
    )

    decision = service.evaluate(
        state=TranscriptRetentionState(
            acquired_at=now - timedelta(days=45),
            extraction_confidence=0.9,
            digest_created_at=now,
            retention_exempt_sample=True,
        ),
        now=now,
    )

    assert_that(decision.tier).is_equal_to(TranscriptLifecycleTier.COLD)
    assert_that(decision.purge_eligible).is_false()
    assert_that(decision.purge_blockers).contains(
        TranscriptRetentionBlocker.RETENTION_EXEMPT_SAMPLE,
    )


def test_retention_sampling_returns_strata_and_exemption() -> None:
    """Verify deterministic retention sampling returns auditable strata."""
    now = datetime(2026, 5, 10, 12, 0, 0)
    service = TranscriptRetentionService(
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=7),
            warm_retention_period=timedelta(days=30),
            min_purge_confidence=0.85,
            retention_sample_rate=1.0,
            sample_version="test-sample-v1",
        ),
    )

    sample = service.choose_sample(
        sample_key=TranscriptRetentionSampleKey(
            transcript_key="episode-123",
            source_key="source-a",
            topic_key="books",
        ),
        acquired_at=now - timedelta(days=45),
        now=now,
        extraction_confidence=0.9,
    )

    assert_that(sample.retention_exempt).is_true()
    assert_that(sample.confidence_band).is_equal_to(TranscriptConfidenceBand.HIGH)
    assert_that(sample.age_bucket).is_equal_to(TranscriptAgeBucket.OLDER)
    assert_that(sample.sample_version).is_equal_to("test-sample-v1")
    assert_that(sample.score).is_between(0, 1)
