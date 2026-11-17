"""Derivative generation with a summary provider on PostgreSQL.

Listing an episode's media once compared whole ``media`` rows with
``DISTINCT``; PostgreSQL has no equality operator for their JSON columns and
rejected the statement, while SQLite accepted it. This runs the full call on
the migrated PostgreSQL schema for an episode that mentions several media
records more than once.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from assertpy import assert_that
from sqlalchemy import select

from podex.models import (
    DerivativeGenerationRun,
    DerivativeGenerationRunStatus,
    Episode,
    EpisodeSummary,
    MediaSummary,
    Mention,
    Podcast,
    Transcript,
)
from podex.services.derivative_generation import generate_episode_derivatives
from podex.services.semantic_chunks import SemanticChunkingPolicy
from tests.postgres.conftest import PostgresDatabase
from tests.postgres.test_semantic_chunks_postgres import (
    FullDimensionEmbeddingProvider,
)
from tests.test_derivative_generation import (
    RecordingSummaryProvider,
    media_with_json,
)

pytestmark = pytest.mark.postgres

_NOW = datetime(2026, 11, 17, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Seeded:
    """Identifiers of the seeded episode and its media records."""

    episode_id: int
    media_ids: tuple[int, int]
    elsewhere_media_id: int


def _seed(postgres: PostgresDatabase) -> _Seeded:
    """Commit an episode with a transcript and repeated mentions of two media.

    The record with the higher id is mentioned first; a third record is
    mentioned only in another episode. Every record carries JSON values.

    Returns:
        The episode id, its media ids in ascending order, and the other id.
    """
    with postgres.sessions() as db:
        podcast = Podcast(name="Derivative Show", slug="derivative-show")
        episode = Episode(podcast=podcast, title="Episode 1")
        elsewhere = Episode(podcast=podcast, title="Episode 2")
        first, second, other = (
            media_with_json(title) for title in ("Dune", "Foundation", "Hyperion")
        )
        text_value = " ".join(f"word{index}" for index in range(12))
        db.add_all(
            [
                Transcript(
                    episode=episode,
                    provider="podscripts",
                    raw_text=text_value,
                    cleaned_text=text_value,
                ),
                elsewhere,
                first,
                second,
                other,
            ],
        )
        db.flush()
        db.add_all(
            [
                Mention(episode=episode, media=second, context="Foundation first."),
                Mention(episode=episode, media=first, context="Then Dune."),
                Mention(episode=episode, media=second, context="Foundation again."),
                Mention(episode=episode, media=first, context="Dune again."),
                Mention(episode=elsewhere, media=other, context="Hyperion."),
            ],
        )
        db.commit()
        return _Seeded(
            episode_id=episode.id,
            media_ids=(first.id, second.id),
            elsewhere_media_id=other.id,
        )


def test_generate_with_summary_provider_completes(
    postgres: PostgresDatabase,
) -> None:
    """The run completes with one episode summary and one per media record."""
    seeded = _seed(postgres)
    provider = RecordingSummaryProvider()
    with postgres.sessions() as db:
        episode = db.get_one(Episode, seeded.episode_id)
        run = generate_episode_derivatives(
            db=db,
            episode=episode,
            embedding_provider=FullDimensionEmbeddingProvider(),
            summary_provider=provider,
            chunking_policy=SemanticChunkingPolicy(
                max_words=4,
                overlap_words=0,
                min_words=2,
            ),
            now=_NOW,
        )
        db.commit()
        run_id = run.id

    with postgres.sessions() as db:
        stored = db.get_one(DerivativeGenerationRun, run_id)
        summary = db.execute(
            select(EpisodeSummary).where(
                EpisodeSummary.episode_id == seeded.episode_id,
            ),
        ).scalar_one()
        summarised = list(
            db.scalars(select(MediaSummary.media_id).order_by(MediaSummary.media_id)),
        )

    media_sources = [
        resource_id for kind, resource_id in provider.sources if kind == "media"
    ]
    assert_that(media_sources).is_equal_to(list(seeded.media_ids))
    assert_that(stored.status).is_equal_to(
        DerivativeGenerationRunStatus.COMPLETED.value,
    )
    assert_that(stored.summary_model).is_equal_to(provider.model_name)
    assert_that(stored.episode_summary_id).is_equal_to(summary.id)
    assert_that(stored.media_summaries_generated).is_equal_to(2)
    assert_that(stored.semantic_chunks_created).is_equal_to(3)
    assert_that(stored.semantic_chunks_embedded).is_equal_to(3)
    assert_that(stored.semantic_chunks_failed).is_zero()
    assert_that(stored.completed_at).is_equal_to(_NOW)
    assert_that(stored.error_message).is_none()
    assert_that(summarised).is_equal_to(list(seeded.media_ids))
    assert_that(summarised).does_not_contain(seeded.elsewhere_media_id)
