"""Summary provider model names on the migrated PostgreSQL schema.

``source_model`` on episode and media summaries is ``varchar(120)`` here.
SQLite stores a longer name, so only PostgreSQL shows that an over-long name
would fail the flush.
"""

from unittest.mock import Mock

import pytest
from assertpy import assert_that
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from podex.models import (
    Episode,
    EpisodeSummary,
    Media,
    MediaSummary,
    MediaType,
    Mention,
    Podcast,
)
from podex.services.derivative_summaries import (
    GeneratedSummaryData,
    SummaryProvider,
    generate_episode_summary,
    generate_media_summary,
)
from podex.services.failure_codes import FailureReason, PipelineFailure
from tests.postgres.conftest import PostgresDatabase

pytestmark = pytest.mark.postgres


def _add_episode_and_media(db: Session) -> tuple[Episode, Media]:
    """Add and flush an episode that mentions one media item."""
    episode = Episode(
        podcast=Podcast(name="Show model-name", slug="model-name"),
        title="Episode model-name",
    )
    media = Media(type=MediaType.BOOK, title="Dune")
    db.add_all([episode, media])
    db.flush()
    db.add(Mention(episode_id=episode.id, media_id=media.id, context="a book"))
    db.flush()
    return episode, media


def _provider(model_name: str) -> Mock:
    provider = Mock(model_name=model_name)
    provider.summarize.return_value = GeneratedSummaryData(summary_text="summary")
    return provider


def _summarize(
    db: Session,
    episode: Episode,
    media: Media,
    provider: SummaryProvider,
) -> None:
    """Write an episode summary and a media summary with ``provider``."""
    generate_episode_summary(
        db=db,
        prompt_version="pg-summary-v1",
        pipeline_version="pg-derivatives-v1",
        provider=provider,
        episode=episode,
    )
    generate_media_summary(
        db=db,
        prompt_version="pg-summary-v1",
        pipeline_version="pg-derivatives-v1",
        provider=provider,
        media=media,
    )


def test_over_long_summary_model_name_is_refused_before_any_write(
    postgres: PostgresDatabase,
) -> None:
    """A 121-character name is never sent, bound or flushed; 120 is stored.

    The varchar(120) columns would refuse the longer name at flush, after the
    provider had been paid for. It is refused before the provider is called,
    for both summaries, and the session still commits.
    """
    longest = "m" * 120
    over_long = longest + "m"
    refused = _provider(over_long)
    parameters: list[str] = []
    failures: list[PipelineFailure] = []

    def record(*args: object) -> None:
        # before_cursor_execute(conn, cursor, statement, parameters, ...).
        parameters.append(repr(args[3]))

    with postgres.sessions() as db:
        episode, media = _add_episode_and_media(db)
        db.commit()
        event.listen(postgres.engine, "before_cursor_execute", record)
        try:
            with pytest.raises(PipelineFailure) as episode_failure:
                _summarize(db, episode, media, refused)
            failures.append(episode_failure.value)
            with pytest.raises(PipelineFailure) as media_failure:
                generate_media_summary(
                    db=db,
                    prompt_version="pg-summary-v1",
                    pipeline_version="pg-derivatives-v1",
                    provider=refused,
                    media=media,
                )
            failures.append(media_failure.value)
            db.commit()
        finally:
            event.remove(postgres.engine, "before_cursor_execute", record)
        _summarize(db, episode, media, _provider(longest))
        db.commit()

    with postgres.sessions() as db:
        stored = [
            *db.scalars(select(EpisodeSummary.source_model)),
            *db.scalars(select(MediaSummary.source_model)),
        ]

    assert_that([error.failure_reason for error in failures]).is_equal_to(
        [FailureReason.SUMMARY_INVALID_MODEL_NAME] * 2,
    )
    refused.summarize.assert_not_called()
    assert_that(parameters).is_not_empty()
    assert_that("\n".join(parameters)).does_not_contain(over_long)
    assert_that(stored).is_equal_to([longest] * 2)
