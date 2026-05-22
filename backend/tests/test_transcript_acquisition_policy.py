"""Tests for retention-aware transcript acquisition policy hooks."""

from datetime import UTC, datetime

from assertpy import assert_that

from podex.models import Episode, Podcast
from podex.services.transcript_source import (
    TranscriptAcquirer,
    TranscriptAcquisitionPolicy,
    TranscriptAcquisitionResult,
    TranscriptSource,
)
from podex.services.whisper_transcriber import TranscriptResult


class FakeTranscriptSource(TranscriptSource):
    """Test transcript source with configurable generation behavior."""

    def __init__(
        self,
        *,
        name: str,
        produces_new_raw_transcript: bool,
    ) -> None:
        self._name = name
        self._produces_new_raw_transcript = produces_new_raw_transcript
        self.attempts = 0

    @property
    def name(self) -> str:
        """Name of this transcript source."""
        return self._name

    @property
    def produces_new_raw_transcript(self) -> bool:
        """Whether this source generates raw transcript text."""
        return self._produces_new_raw_transcript

    def supports(self, episode: Episode) -> bool:
        """Return whether this source supports the episode."""
        return True

    def get_transcript(self, episode: Episode) -> TranscriptAcquisitionResult:
        """Return a successful transcript result."""
        self.attempts += 1
        return TranscriptAcquisitionResult(
            success=True,
            result=TranscriptResult(
                provider=self.name,
                raw_text="hello world",
                segments=[],
                fetched_at=datetime(2026, 5, 12, 12, 0, tzinfo=UTC),
            ),
            source=self.name,
        )


def test_retention_policy_skips_generated_transcripts_after_source_opt_out() -> None:
    """Verify source opt-out skips generation sources and suppresses raw storage."""
    generated = FakeTranscriptSource(
        name="generated",
        produces_new_raw_transcript=True,
    )
    external = FakeTranscriptSource(
        name="external",
        produces_new_raw_transcript=False,
    )
    acquirer = TranscriptAcquirer(
        sources=[generated, external],
        acquisition_policy=TranscriptAcquisitionPolicy(
            source_retention_opt_out=True,
        ),
    )

    result = acquirer.acquire(
        Episode(podcast=Podcast(name="Pod", slug="pod"), title="Ep")
    )

    assert_that(generated.attempts).is_zero()
    assert_that(external.attempts).is_equal_to(1)
    assert_that(result.success).is_true()
    assert_that(result.source).is_equal_to("external")
    assert_that(result.should_store_raw).is_false()
    assert_that(result.source_retention_opt_out).is_true()


def test_retention_policy_can_allow_generated_reacquisition() -> None:
    """Verify policy can explicitly allow generated transcript reacquisition."""
    generated = FakeTranscriptSource(
        name="generated",
        produces_new_raw_transcript=True,
    )
    acquirer = TranscriptAcquirer(
        sources=[generated],
        acquisition_policy=TranscriptAcquisitionPolicy(
            source_retention_opt_out=True,
            allow_generated_after_opt_out=True,
        ),
    )

    result = acquirer.acquire(
        Episode(podcast=Podcast(name="Pod", slug="pod"), title="Ep")
    )

    assert_that(generated.attempts).is_equal_to(1)
    assert_that(result.success).is_true()
    assert_that(result.should_store_raw).is_false()
