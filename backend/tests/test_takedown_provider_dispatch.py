"""Authorising episode text and sending it to a provider is one serialised step.

Two sessions on separate connections to one SQLite file play the writer and
the approver. While the writer reads episode text and calls its provider, a
takedown of that episode cannot commit; once the writer's transaction ends the
takedown runs and removes what was written. A takedown committed first makes
the writer refuse before any text is read.
"""

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from assertpy import assert_that
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from podex.database import enable_sqlite_foreign_keys
from podex.models import (
    EMBEDDING_DIMENSIONS,
    Base,
    ContentSuppressedError,
    ContentSuppressionScope,
    Episode,
    EpisodeSummary,
    Media,
    MediaSummary,
    MediaType,
    SemanticChunk,
    TakedownSubjectType,
    Transcript,
)
from podex.services import content_suppression, derivative_summaries, semantic_chunks
from podex.services.derivative_summaries import (
    GeneratedSummaryData,
    generate_episode_summary,
    generate_media_summary,
)
from podex.services.semantic_chunks import (
    SemanticChunkingPolicy,
    sync_semantic_chunks_for_transcript,
)
from tests.conftest import SeededGraph, seed_catalog_graph
from tests.takedown_support import approve_takedown

_TEXT = "secret transcript one two three four five six"
_LOCK_TIMEOUT_SECONDS = 0.2


@dataclass(frozen=True, slots=True)
class _Sides:
    """Writer and approver sessions on one database file."""

    writer: Session
    approver: Session
    graph: SeededGraph


def _engine(path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": _LOCK_TIMEOUT_SECONDS},
    )
    enable_sqlite_foreign_keys(engine)
    return engine


@pytest.fixture(name="sides")
def sides_fixture(tmp_path: Path) -> Iterator[_Sides]:
    """Seed an episode with a transcript and open both sessions."""
    path = tmp_path / "dispatch.db"
    writer_engine = _engine(path)
    approver_engine = _engine(path)
    Base.metadata.create_all(writer_engine)
    writer = sessionmaker(bind=writer_engine, autoflush=False)()
    approver = sessionmaker(bind=approver_engine, autoflush=False)()
    graph = seed_catalog_graph(writer)
    writer.add(Transcript(episode_id=graph.episode_id, provider="p", raw_text=_TEXT))
    writer.commit()
    try:
        yield _Sides(writer=writer, approver=approver, graph=graph)
    finally:
        writer.close()
        approver.close()
        writer_engine.dispose()
        approver_engine.dispose()


def _provider() -> Mock:
    provider = Mock(model_name="model", dimensions=EMBEDDING_DIMENSIONS)
    provider.summarize.return_value = GeneratedSummaryData(summary_text="summary")
    provider.embed_texts.side_effect = lambda texts: [
        [0.1] * EMBEDDING_DIMENSIONS for _ in texts
    ]
    return provider


def _take_down(sides: _Sides) -> None:
    approve_takedown(
        sides.approver,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=sides.graph.episode_id,
        actions=["suppress_derivatives"],
    )


def _episode_summary(sides: _Sides, provider: Mock) -> None:
    generate_episode_summary(
        db=sides.writer,
        episode=sides.writer.query(Episode).one(),
        provider=provider,
        pipeline_version="v1",
        prompt_version="p1",
    )


def _chunks(sides: _Sides, provider: Mock) -> None:
    sync_semantic_chunks_for_transcript(
        db=sides.writer,
        transcript=sides.writer.query(Transcript).one(),
        embedding_provider=provider,
        policy=SemanticChunkingPolicy(max_words=20, overlap_words=0, min_words=1),
    )


def _media_summary(sides: _Sides, provider: Mock) -> None:
    generate_media_summary(
        db=sides.writer,
        media=sides.writer.query(Media).one(),
        provider=provider,
        pipeline_version="v1",
        prompt_version="p1",
    )


@dataclass(frozen=True, slots=True)
class _Lane:
    """A provider-calling writer, the text source it reads, and its output."""

    run: Callable[[_Sides, Mock], None]
    module: Any
    source: str
    output: type[Any]


_LANES = {
    "episode-summary": _Lane(
        _episode_summary,
        derivative_summaries,
        "build_episode_summary_source",
        EpisodeSummary,
    ),
    "chunks": _Lane(
        _chunks,
        semantic_chunks,
        "build_semantic_chunk_candidates",
        SemanticChunk,
    ),
    "media-summary": _Lane(
        _media_summary,
        derivative_summaries,
        "build_media_summary_source",
        MediaSummary,
    ),
}


@pytest.mark.parametrize("lane", _LANES.values(), ids=_LANES.keys())
def test_takedown_cannot_commit_while_text_is_being_dispatched(
    sides: _Sides,
    monkeypatch: pytest.MonkeyPatch,
    lane: _Lane,
) -> None:
    """The approver is blocked from reading the text until after the provider.

    1. The writer authorises the episode and starts reading its text.
    2. At that moment the approver tries to commit a takedown: it cannot.
    3. The provider is called (asserted) and the writer commits.
    4. The approver retries, commits, and removes the writer's output.
    """
    attempts: list[str] = []
    original = getattr(lane.module, lane.source)

    def approve_while_reading(**kwargs: Any) -> Any:
        try:
            _take_down(sides)
            attempts.append("committed")
        except OperationalError:
            sides.approver.rollback()
            attempts.append("blocked")
        return original(**kwargs)

    monkeypatch.setattr(lane.module, lane.source, approve_while_reading)
    provider = _provider()

    lane.run(sides, provider)
    sides.writer.commit()
    monkeypatch.setattr(lane.module, lane.source, original)
    _take_down(sides)

    assert_that(attempts).is_equal_to(["blocked"])
    assert_that(provider.mock_calls).is_not_empty()
    sides.approver.expire_all()
    assert_that(sides.approver.query(lane.output).count()).is_zero()


@pytest.mark.parametrize(
    "lane",
    [_LANES["episode-summary"], _LANES["chunks"]],
    ids=["episode-summary", "chunks"],
)
def test_takedown_committed_first_stops_the_writer_before_any_text(
    sides: _Sides,
    lane: _Lane,
) -> None:
    """A writer that starts after the takedown commits never calls its provider."""
    _take_down(sides)
    provider = _provider()

    with pytest.raises(ContentSuppressedError):
        lane.run(sides, provider)
    sides.writer.rollback()

    assert_that(provider.mock_calls).is_empty()


@pytest.mark.parametrize(
    "lane",
    [_LANES["episode-summary"], _LANES["chunks"]],
    ids=["episode-summary", "chunks"],
)
def test_refused_writer_does_not_keep_the_database_locked(
    sides: _Sides,
    lane: _Lane,
) -> None:
    """After a refusal, other connections can write without waiting for a rollback."""
    _take_down(sides)

    with pytest.raises(ContentSuppressedError):
        lane.run(sides, _provider())

    sides.approver.add(Media(type=MediaType.MOVIE, title="Written meanwhile"))
    sides.approver.commit()
    sides.writer.rollback()


def test_refusal_under_the_lock_releases_it(
    sides: _Sides,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A takedown committed between the two checks: the refusal frees the lock.

    1. The writer's first check, without a lock, finds nothing.
    2. The takedown commits in that gap.
    3. The writer takes the lock, checks again, refuses, and releases it, so
       another connection can write at once.
    """
    original = content_suppression.suppressed_episode_ids
    calls: list[int] = []

    def commit_takedown_after_first_check(
        *,
        db: Session,
        scope: ContentSuppressionScope,
        episode_ids: Iterable[int],
    ) -> set[int]:
        found: set[int] = original(db=db, scope=scope, episode_ids=episode_ids)
        calls.append(len(found))
        if len(calls) == 1:
            _take_down(sides)
        return found

    monkeypatch.setattr(
        content_suppression,
        "suppressed_episode_ids",
        commit_takedown_after_first_check,
    )

    with pytest.raises(ContentSuppressedError):
        _episode_summary(sides, _provider())

    sides.approver.add(Media(type=MediaType.MOVIE, title="Written meanwhile"))
    sides.approver.commit()
    sides.writer.rollback()
    assert_that(calls).is_equal_to([0, 1])
