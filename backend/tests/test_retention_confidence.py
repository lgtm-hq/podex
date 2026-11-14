"""Which extraction confidence gates retention, across sweeps and ops commands."""

from datetime import UTC, datetime, timedelta

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.config import RetentionSettings
from podex.models import Episode, MentionCandidate, Podcast, Transcript
from podex.services.ops_retention_commands import (
    OpsTranscriptRetentionPreviewData,
    apply_ops_transcript_retention,
    preview_ops_transcript_retention,
)
from podex.services.recurring_notifications import run_due_retention_work
from podex.services.transcript_retention import (
    TranscriptRetentionBlocker,
    TranscriptRetentionPolicy,
)
from tests.conftest import plan_notification_work

_NOW = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
# Old enough to be cold and never sampled, so confidence decides eligibility.
_POLICY = TranscriptRetentionPolicy(
    hot_retention_period=timedelta(days=7),
    warm_retention_period=timedelta(days=30),
    retention_sample_rate=0,
)
_ENABLED = RetentionSettings(purge_enabled=True)


def _episode(db_session: Session) -> Episode:
    """Persist an episode to hold transcripts and extraction candidates."""
    podcast = Podcast(name="Confidence Podcast", slug="confidence-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Confidence Episode")
    db_session.add(episode)
    db_session.flush()
    return episode


def _transcript(
    db_session: Session,
    *,
    episode: Episode,
    acquired_days_ago: int = 45,
    source_retention_opt_out: bool = False,
) -> Transcript:
    """Persist a cold, digested transcript for the episode."""
    transcript = Transcript(
        episode_id=episode.id,
        provider="podscripts",
        raw_text=f"raw transcript acquired {acquired_days_ago} days ago",
        fetched_at=_NOW - timedelta(days=acquired_days_ago),
        digest_text="digest",
        digest_created_at=_NOW - timedelta(days=40),
        source_retention_opt_out=source_retention_opt_out,
    )
    db_session.add(transcript)
    db_session.flush()
    return transcript


def _add_candidates(
    db_session: Session,
    *,
    episode: Episode,
    confidences: tuple[float, ...],
) -> None:
    """Store extraction candidates the way extraction persists them, per episode."""
    for index, confidence in enumerate(confidences):
        db_session.add(
            MentionCandidate(
                episode_id=episode.id,
                media_type="book",
                raw_title=f"Candidate {index}",
                confidence=confidence,
                extraction_source="llm",
            ),
        )
    db_session.flush()


def _preview(
    db_session: Session,
    transcript: Transcript,
) -> OpsTranscriptRetentionPreviewData:
    """Dry-run the shared test policy for one transcript."""
    preview = preview_ops_transcript_retention(
        db=db_session,
        transcript_id=transcript.id,
        policy=_POLICY,
        now=_NOW,
    )
    if preview is None:  # pragma: no cover - transcripts exist in these tests
        raise AssertionError
    return preview


def _sweep(db_session: Session, *, now: datetime, expected_evaluated: int) -> None:
    """Run one scheduled retention work item over the expected transcripts."""
    plan_notification_work(db=db_session, now=now)
    results = run_due_retention_work(
        db=db_session,
        policy=_POLICY,
        now=now,
        retention=_ENABLED,
    )
    db_session.commit()
    assert_that(results).is_length(1)
    assert_that(results[0].transcripts_evaluated).is_equal_to(expected_evaluated)


def _persisted_gate(transcript: Transcript) -> tuple[bool, list[str]]:
    """Return the persisted purge eligibility and blockers of a transcript."""
    return (
        transcript.purge_eligible_at is not None,
        list(transcript.retention_blockers_json or []),
    )


def test_ops_retention_ignores_other_transcript_confidence(
    db_session: Session,
) -> None:
    """A newer transcript never inherits confidence extracted from another one."""
    episode = _episode(db_session)
    _transcript(db_session, episode=episode, acquired_days_ago=60)
    # Only the older transcript's extraction produced candidates.
    _add_candidates(db_session, episode=episode, confidences=(0.99,))
    newer = _transcript(db_session, episode=episode, acquired_days_ago=45)

    preview = _preview(db_session, newer)

    assert_that(preview.extraction_confidence).is_none()
    assert_that(preview.decision.purge_eligible).is_false()
    assert_that(preview.decision.purge_blockers).is_equal_to(
        (TranscriptRetentionBlocker.MISSING_CONFIDENCE,),
    )


@pytest.mark.parametrize(
    "confidences",
    [(), (0.99,), (0.3, 0.99)],
    ids=["zero-candidates", "high-candidate", "mixed-low-high"],
)
def test_ops_apply_treats_candidates_without_generation_as_unknown(
    db_session: Session,
    confidences: tuple[float, ...],
) -> None:
    """Episode candidates carry no generation, so they never supply confidence."""
    episode = _episode(db_session)
    transcript = _transcript(db_session, episode=episode)
    _add_candidates(db_session, episode=episode, confidences=confidences)

    applied = apply_ops_transcript_retention(
        db=db_session,
        transcript_id=transcript.id,
        policy=_POLICY,
        now=_NOW,
    )

    assert_that(applied).is_not_none()
    if applied is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(applied.extraction_confidence).is_none()
    assert_that(_persisted_gate(transcript)).is_equal_to(
        (False, ["missing_confidence"]),
    )


@pytest.mark.parametrize(
    "source_retention_opt_out",
    [False, True],
    ids=["retained-source", "opted-out-source"],
)
def test_retention_sweep_does_not_undo_operator_apply(
    db_session: Session,
    source_retention_opt_out: bool,
) -> None:
    """A later sweep reaches the same purge decision an operator applied."""
    episode = _episode(db_session)
    transcript = _transcript(
        db_session,
        episode=episode,
        source_retention_opt_out=source_retention_opt_out,
    )
    _add_candidates(db_session, episode=episode, confidences=(0.99,))
    apply_ops_transcript_retention(
        db=db_session,
        transcript_id=transcript.id,
        policy=_POLICY,
        now=_NOW,
    )
    db_session.commit()
    applied = _persisted_gate(transcript)

    _sweep(db_session, now=_NOW + timedelta(hours=1), expected_evaluated=1)

    assert_that(_persisted_gate(transcript)).is_equal_to(applied)
    # Only a source opt-out is eligible without generation-bound confidence.
    assert_that(applied[0]).is_equal_to(source_retention_opt_out)


def test_retention_sweep_and_ops_preview_reach_the_same_decision(
    db_session: Session,
) -> None:
    """Sweep and operator evaluation agree for every transcript of an episode."""
    episode = _episode(db_session)
    transcripts = [
        _transcript(db_session, episode=episode, acquired_days_ago=60),
        _transcript(db_session, episode=episode, acquired_days_ago=45),
        _transcript(
            db_session,
            episode=episode,
            acquired_days_ago=40,
            source_retention_opt_out=True,
        ),
    ]
    _add_candidates(db_session, episode=episode, confidences=(0.2, 0.9, 0.99))
    db_session.commit()
    previews = {
        transcript.id: _preview(db_session, transcript) for transcript in transcripts
    }

    _sweep(db_session, now=_NOW, expected_evaluated=len(transcripts))

    for transcript in transcripts:
        preview = previews[transcript.id]
        assert_that(preview.extraction_confidence).is_none()
        assert_that(_persisted_gate(transcript)).is_equal_to(
            (
                preview.decision.purge_eligible,
                [blocker.value for blocker in preview.decision.purge_blockers],
            ),
        )
