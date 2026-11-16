"""One isolated write per guarded model and field, run after a takedown.

Each refused case exercises a single branch of the flush guard, so removing
one protection fails its own case even when another protection would catch a
broader write. The allowed cases pin what the guard deliberately lets through:
removing content, detaching provenance, and media identity maintenance.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressedError,
    ContentSuppression,
    DerivativeGenerationRun,
    Episode,
    EpisodeSummary,
    GraphTriple,
    Media,
    MediaRelation,
    MediaRelationType,
    MediaType,
    Mention,
    SemanticChunk,
    TakedownSubjectType,
    Transcript,
)
from podex.services.graph_relations import (
    GraphTripleInputData,
    upsert_graph_triple,
    upsert_media_relation,
)
from podex.services.ops_media_commands import SplitOpsMediaInputData, split_ops_media
from podex.services.takedown_requests import execute_approved_takedown_request
from tests.conftest import seed_catalog_graph
from tests.takedown_support import approve_takedown, submit_takedown, take_down_mention

_AT = datetime(2026, 7, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _World:
    """A suppressed episode, an open one in the same podcast, and two media."""

    db: Session
    suppressed: int
    open: int
    media: int
    other_media: int


def _world(
    db: Session,
    before_takedown: Callable[[_World], None] | None = None,
) -> _World:
    """Seed the catalog and suppress one episode for mentions and derivatives.

    ``before_takedown`` writes rows that exist when the takedown runs; rows a
    takedown does not delete (graph triples, media relations) keep pointing
    at the suppressed episode, as they do in production.
    """
    graph = seed_catalog_graph(db)
    other_media = Media(type=MediaType.MOVIE, title="Dune (film)")
    open_episode = Episode(podcast_id=graph.podcast_id, title="Open")
    db.add_all([other_media, open_episode])
    db.commit()
    world = _World(
        db=db,
        suppressed=graph.episode_id,
        open=open_episode.id,
        media=graph.media_id,
        other_media=other_media.id,
    )
    if before_takedown is not None:
        before_takedown(world)
        db.commit()
    approve_takedown(
        db,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=graph.episode_id,
        actions=["unpublish_mentions", "suppress_derivatives"],
    )
    return world


def _committed(world: _World, row: object) -> object:
    """Persist a row on the open episode and return it."""
    world.db.add(row)
    world.db.commit()
    return row


def _chunk(episode_id: int, transcript_id: int, key: str) -> SemanticChunk:
    return SemanticChunk(
        episode_id=episode_id,
        transcript_id=transcript_id,
        chunk_key=key,
        chunk_index=0,
        pipeline_version="v1",
        source_text_hash="c" * 64,
        text="chunk text",
        context_snippet="chunk",
        token_count=2,
    )


def _summary(episode_id: int, key: str) -> EpisodeSummary:
    return EpisodeSummary(
        episode_id=episode_id,
        summary_key=key,
        summary_kind="overview",
        pipeline_version="v1",
        prompt_version="p1",
        source_text_hash="d" * 64,
        source_model="model",
        summary_text="summary text",
        generated_at=_AT,
    )


def _run(episode_id: int, key: str) -> DerivativeGenerationRun:
    return DerivativeGenerationRun(
        run_key=key,
        episode_id=episode_id,
        pipeline_version="v1",
        chunk_pipeline_version="c1",
        summary_prompt_version="p1",
        source_text_hash="e" * 64,
        started_at=_AT,
    )


def _transcript(episode_id: int, **fields: object) -> Transcript:
    return Transcript(episode_id=episode_id, provider="podscripts", **fields)


def _triple(world: _World, *, provenance: int | None, evidence: str) -> GraphTriple:
    return upsert_graph_triple(
        db=world.db,
        payload=GraphTripleInputData(
            subject_media_id=world.media,
            predicate="discussed",
            object_value="same object",
            source="rebuild",
            provenance_episode_id=provenance,
            evidence_text=evidence,
        ),
    )


def _relation(world: _World, *, provenance: int | None, evidence: str) -> MediaRelation:
    return upsert_media_relation(
        db=world.db,
        subject_media_id=world.other_media,
        object_media_id=world.media,
        relation_type=MediaRelationType.ADAPTED_FROM,
        source="rebuild",
        evidence_text=evidence,
        provenance_episode_id=provenance,
    )


def _transcript_on_open(world: _World) -> Transcript:
    row = _committed(world, _transcript(world.open, cleaned_text="cleaned"))
    if not isinstance(row, Transcript):
        raise TypeError(type(row))
    return row


def _mention_insert(world: _World) -> None:
    world.db.add(Mention(episode_id=world.suppressed, media_id=world.other_media))


def _mention_episode_move(world: _World) -> None:
    mention = Mention(episode_id=world.open, media_id=world.media)
    _committed(world, mention)
    mention.episode_id = world.suppressed


def _mention_relationship_move(world: _World) -> None:
    mention = Mention(episode_id=world.open, media_id=world.media)
    _committed(world, mention)
    mention.episode = world.db.get(Episode, world.suppressed)


def _chunk_insert(world: _World) -> None:
    transcript = _transcript_on_open(world)
    world.db.add(_chunk(world.suppressed, transcript.id, "new"))


def _chunk_reassign(world: _World) -> None:
    transcript = _transcript_on_open(world)
    chunk = _chunk(world.open, transcript.id, "moved")
    _committed(world, chunk)
    chunk.episode_id = world.suppressed


def _summary_insert(world: _World) -> None:
    world.db.add(_summary(world.suppressed, "new"))


def _summary_reassign(world: _World) -> None:
    summary = _summary(world.open, "moved")
    _committed(world, summary)
    summary.episode_id = world.suppressed


def _run_insert(world: _World) -> None:
    world.db.add(_run(world.suppressed, "new"))


def _run_reassign(world: _World) -> None:
    run = _run(world.open, "moved")
    _committed(world, run)
    run.episode_id = world.suppressed


def _triple_insert(world: _World) -> None:
    _triple(world, provenance=world.suppressed, evidence="regenerated")


def _relation_insert(world: _World) -> None:
    _relation(world, provenance=world.suppressed, evidence="regenerated")


def _seed_triple(world: _World) -> None:
    _triple(world, provenance=world.suppressed, evidence="old")


def _seed_relation(world: _World) -> None:
    _relation(world, provenance=world.suppressed, evidence="old")


def _triple_new_evidence(world: _World) -> None:
    _triple(world, provenance=world.suppressed, evidence="rewritten evidence")


def _relation_new_evidence(world: _World) -> None:
    _relation(world, provenance=world.suppressed, evidence="rewritten evidence")


def _triple_set_provenance(world: _World) -> None:
    triple = _triple(world, provenance=None, evidence="independent fact")
    world.db.commit()
    triple.provenance_episode_id = world.suppressed


def _transcript_cleaned_insert(world: _World) -> None:
    world.db.add(_transcript(world.suppressed, cleaned_text="regenerated"))


def _transcript_cleaned_update(world: _World) -> None:
    transcript = _committed(world, _transcript(world.suppressed))
    if not isinstance(transcript, Transcript):
        raise TypeError(type(transcript))
    transcript.cleaned_text = "regenerated"


def _transcript_digest_update(world: _World) -> None:
    transcript = _committed(world, _transcript(world.suppressed))
    if not isinstance(transcript, Transcript):
        raise TypeError(type(transcript))
    transcript.digest_text = "regenerated digest"


def _transcript_fk_reassign(world: _World) -> None:
    _transcript_on_open(world).episode_id = world.suppressed


def _transcript_relationship_reassign(world: _World) -> None:
    _transcript_on_open(world).episode = world.db.get(Episode, world.suppressed)


_REFUSED: dict[str, Callable[[_World], None]] = {
    "mention-insert": _mention_insert,
    "mention-episode-move": _mention_episode_move,
    "mention-relationship-move": _mention_relationship_move,
    "chunk-insert": _chunk_insert,
    "chunk-reassign": _chunk_reassign,
    "summary-insert": _summary_insert,
    "summary-reassign": _summary_reassign,
    "run-insert": _run_insert,
    "run-reassign": _run_reassign,
    "triple-insert": _triple_insert,
    "triple-set-provenance": _triple_set_provenance,
    "relation-insert": _relation_insert,
    "transcript-cleaned-insert": _transcript_cleaned_insert,
    "transcript-cleaned-update": _transcript_cleaned_update,
    "transcript-digest-update": _transcript_digest_update,
    "transcript-fk-reassign": _transcript_fk_reassign,
    "transcript-relationship-reassign": _transcript_relationship_reassign,
}


_REFUSED_AFTER_SEED: dict[
    str,
    tuple[Callable[[_World], None], Callable[[_World], None]],
] = {
    "triple-new-evidence": (_seed_triple, _triple_new_evidence),
    "relation-new-evidence": (_seed_relation, _relation_new_evidence),
}


@pytest.mark.parametrize("write", _REFUSED.values(), ids=_REFUSED.keys())
def test_guarded_write_is_refused(
    db_session: Session,
    write: Callable[[_World], None],
) -> None:
    """Each guarded model and field refuses its own write on its own."""
    world = _world(db_session)

    with pytest.raises(ContentSuppressedError):
        write(world)
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize(
    ("seed", "write"),
    _REFUSED_AFTER_SEED.values(),
    ids=_REFUSED_AFTER_SEED.keys(),
)
def test_rewriting_content_kept_by_the_takedown_is_refused(
    db_session: Session,
    seed: Callable[[_World], None],
    write: Callable[[_World], None],
) -> None:
    """An idempotent upsert cannot put new evidence on a suppressed row."""
    world = _world(db_session, before_takedown=seed)

    with pytest.raises(ContentSuppressedError):
        write(world)
        db_session.commit()
    db_session.rollback()


def _clear_triple_evidence(world: _World) -> None:
    triple = world.db.query(GraphTriple).one()
    triple.evidence_text = None
    triple.provenance_episode_id = None


def _repoint_relation_media(world: _World) -> None:
    relation = world.db.query(MediaRelation).one()
    replacement = Media(type=MediaType.MOVIE, title="Dune (1984)")
    world.db.add(replacement)
    world.db.flush()
    relation.subject_media_id = replacement.id
    relation.relation_key = "re-keyed by a merge"


def _clear_transcript_text(world: _World) -> None:
    transcript = _committed(world, _transcript(world.open, cleaned_text="x"))
    if not isinstance(transcript, Transcript):
        raise TypeError(type(transcript))
    world.db.query(Transcript).filter(Transcript.id == transcript.id).update(
        {Transcript.episode_id: world.suppressed},
    )
    world.db.commit()
    world.db.expire_all()
    stored = world.db.get(Transcript, transcript.id)
    if stored is None:
        raise AssertionError(transcript.id)
    stored.cleaned_text = None


def _no_seed(_world: _World) -> None:
    """Seed nothing before the takedown."""


_ALLOWED: dict[str, tuple[Callable[[_World], None], Callable[[_World], None]]] = {
    "triple-evidence-cleared": (_seed_triple, _clear_triple_evidence),
    "relation-media-repointed": (_seed_relation, _repoint_relation_media),
    "transcript-text-cleared": (_no_seed, _clear_transcript_text),
}


@pytest.mark.parametrize(("seed", "write"), _ALLOWED.values(), ids=_ALLOWED.keys())
def test_removing_or_reidentifying_content_is_allowed(
    db_session: Session,
    seed: Callable[[_World], None],
    write: Callable[[_World], None],
) -> None:
    """Writes that only remove content or re-identify media still commit."""
    world = _world(db_session, before_takedown=seed)

    write(world)
    db_session.commit()


def test_split_cannot_move_a_hidden_mention(
    client: TestClient,
    db_session: Session,
) -> None:
    """Splitting a suppressed pair's surviving mention does not publish it."""
    graph = seed_catalog_graph(db_session)
    hidden = Mention(
        episode_id=graph.episode_id,
        media_id=graph.media_id,
        context="hidden same-work text",
    )
    db_session.add(hidden)
    db_session.commit()
    hidden_id = hidden.id
    take_down_mention(db_session, graph.mention_id)

    with pytest.raises(ContentSuppressedError):
        split_ops_media(
            db=db_session,
            media_id=graph.media_id,
            payload=SplitOpsMediaInputData(
                mention_ids=(hidden_id,),
                type=MediaType.BOOK,
                title="Dune",
            ),
        )
        db_session.commit()
    db_session.rollback()

    listed = client.get(f"/api/v2/episodes/{graph.episode_id}/mentions").json()
    assert_that(listed["total"]).is_zero()
    stored = db_session.get(Mention, hidden_id)
    assert_that(stored).is_not_none()
    if stored is None:
        raise AssertionError(hidden_id)
    assert_that(stored.media_id).is_equal_to(graph.media_id)


def test_suppression_recorded_in_this_transaction_overrides_earlier_checks(
    db_session: Session,
) -> None:
    """A check cached before a same-transaction takedown is not reused."""
    graph = seed_catalog_graph(db_session)
    db_session.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))
    db_session.flush()
    request = submit_takedown(
        db_session,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=graph.episode_id,
        actions=["unpublish_mentions"],
    )
    execute_approved_takedown_request(
        db=db_session,
        request=request,
        artifact_store=None,
    )
    db_session.flush()
    db_session.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))

    with pytest.raises(ContentSuppressedError):
        db_session.commit()
    db_session.rollback()


def test_suppression_moved_in_this_transaction_overrides_earlier_checks(
    db_session: Session,
) -> None:
    """A pair suppression relocated by a merge in this transaction applies."""
    graph = seed_catalog_graph(db_session)
    duplicate = Media(type=MediaType.BOOK, title="Dune (dupe)")
    second = Episode(podcast_id=graph.podcast_id, title="Second")
    db_session.add_all([duplicate, second])
    db_session.commit()
    taken_down = Mention(episode_id=second.id, media_id=duplicate.id)
    db_session.add(taken_down)
    db_session.commit()
    take_down_mention(db_session, taken_down.id)
    db_session.add(Mention(episode_id=second.id, media_id=graph.media_id))
    db_session.flush()
    db_session.query(Mention).filter(
        Mention.episode_id == second.id,
        Mention.media_id == graph.media_id,
    ).delete()
    suppression = db_session.query(ContentSuppression).one()
    suppression.media_id = graph.media_id
    suppression.suppression_key = f"mention_pair:{second.id}:{graph.media_id}:mentions"
    db_session.flush()
    db_session.add(Mention(episode_id=second.id, media_id=graph.media_id))

    with pytest.raises(ContentSuppressedError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize("count", [1, 100, 1000])
def test_guard_queries_do_not_grow_with_the_flush(
    db_session: Session,
    count: int,
) -> None:
    """One flush of many distinct pairs runs a bounded number of guard queries."""
    graph = seed_catalog_graph(db_session)
    media = [
        Media(type=MediaType.BOOK, title=f"Work {index}") for index in range(count)
    ]
    db_session.add_all(media)
    db_session.commit()
    engine = db_session.get_bind()
    selects: list[str] = []

    def record(*args: object) -> None:
        statement = str(args[2])
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    db_session.add_all(
        Mention(episode_id=graph.episode_id, media_id=item.id) for item in media
    )
    event.listen(engine, "before_cursor_execute", record)
    try:
        db_session.flush()
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert_that(len(selects)).is_less_than_or_equal_to(6)
