"""Ports and default implementation for derivative-backed retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import Protocol

from sqlalchemy.orm import Session

from podex.models import (
    DerivativeSummaryStatus,
    EpisodeSummary,
    GraphTriple,
    MediaSummary,
    SemanticChunk,
    SemanticChunkEmbeddingStatus,
)


class RetrievalHitKind(StrEnum):
    """Kinds of derivative retrieval hits."""

    EPISODE_SUMMARY = auto()
    GRAPH_TRIPLE = auto()
    MEDIA_SUMMARY = auto()
    SEMANTIC_CHUNK = auto()


@dataclass(frozen=True, slots=True)
class RetrievalQueryData:
    """Input query for a retrieval port."""

    query: str
    limit: int = 10
    episode_ids: tuple[int, ...] = ()
    media_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalHitData:
    """Single derivative retrieval hit."""

    hit_kind: RetrievalHitKind
    resource_id: int
    score: float
    title: str
    snippet: str
    metadata_json: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievalResultData:
    """Ranked retrieval results."""

    query: str
    hits: tuple[RetrievalHitData, ...]


class HybridRetrievalPort(Protocol):
    """Port for graph-plus-vector retrieval implementations."""

    def search(
        self,
        query: RetrievalQueryData,
    ) -> RetrievalResultData:
        """Run a hybrid retrieval query.

        Args:
            query: Retrieval query shape.

        Returns:
            Ranked retrieval hits.
        """


class PostgresDerivativeRetrievalPort:
    """Derivative-backed retrieval over summaries, graph triples, and chunks."""

    def __init__(
        self,
        db: Session,
    ) -> None:
        self._db = db

    def search(
        self,
        query: RetrievalQueryData,
    ) -> RetrievalResultData:
        """Run a lightweight hybrid retrieval query over persisted derivatives.

        Args:
            query: Retrieval query shape.

        Returns:
            Ranked retrieval hits.
        """
        normalized_query = _normalize_query(query.query)
        if not normalized_query:
            return RetrievalResultData(query=query.query, hits=())
        hits = [
            *self._episode_summary_hits(query=query, normalized_query=normalized_query),
            *self._media_summary_hits(query=query, normalized_query=normalized_query),
            *self._semantic_chunk_hits(query=query, normalized_query=normalized_query),
            *self._graph_triple_hits(query=query, normalized_query=normalized_query),
        ]
        ranked_hits = sorted(
            hits,
            key=lambda hit: (-hit.score, hit.hit_kind.value, hit.resource_id),
        )
        return RetrievalResultData(
            query=query.query,
            hits=tuple(ranked_hits[: query.limit]),
        )

    def _episode_summary_hits(
        self,
        *,
        query: RetrievalQueryData,
        normalized_query: str,
    ) -> list[RetrievalHitData]:
        """Return episode summary hits."""
        summaries = self._db.query(EpisodeSummary).filter(
            EpisodeSummary.status == DerivativeSummaryStatus.READY.value,
        )
        if query.episode_ids:
            summaries = summaries.filter(
                EpisodeSummary.episode_id.in_(query.episode_ids)
            )
        return [
            RetrievalHitData(
                hit_kind=RetrievalHitKind.EPISODE_SUMMARY,
                resource_id=summary.episode_id,
                score=_score_text(normalized_query, summary.summary_text),
                title=f"Episode {summary.episode_id} summary",
                snippet=_snippet(summary.summary_text),
                metadata_json={
                    "summary_id": summary.id,
                    "summary_kind": summary.summary_kind,
                },
            )
            for summary in summaries.all()
            if _score_text(normalized_query, summary.summary_text) > 0
        ]

    def _media_summary_hits(
        self,
        *,
        query: RetrievalQueryData,
        normalized_query: str,
    ) -> list[RetrievalHitData]:
        """Return media summary hits."""
        summaries = self._db.query(MediaSummary).filter(
            MediaSummary.status == DerivativeSummaryStatus.READY.value,
        )
        if query.media_ids:
            summaries = summaries.filter(MediaSummary.media_id.in_(query.media_ids))
        return [
            RetrievalHitData(
                hit_kind=RetrievalHitKind.MEDIA_SUMMARY,
                resource_id=summary.media_id,
                score=_score_text(normalized_query, summary.summary_text),
                title=f"Media {summary.media_id} summary",
                snippet=_snippet(summary.summary_text),
                metadata_json={
                    "summary_id": summary.id,
                    "summary_kind": summary.summary_kind,
                },
            )
            for summary in summaries.all()
            if _score_text(normalized_query, summary.summary_text) > 0
        ]

    def _semantic_chunk_hits(
        self,
        *,
        query: RetrievalQueryData,
        normalized_query: str,
    ) -> list[RetrievalHitData]:
        """Return semantic chunk hits."""
        chunks = self._db.query(SemanticChunk)
        if query.episode_ids:
            chunks = chunks.filter(SemanticChunk.episode_id.in_(query.episode_ids))
        return [
            RetrievalHitData(
                hit_kind=RetrievalHitKind.SEMANTIC_CHUNK,
                resource_id=chunk.id,
                score=_score_text(normalized_query, chunk.text)
                + _embedding_bonus(chunk.embedding_status),
                title=f"Episode {chunk.episode_id} chunk {chunk.chunk_index}",
                snippet=chunk.context_snippet or _snippet(chunk.text),
                metadata_json={
                    "episode_id": chunk.episode_id,
                    "transcript_id": chunk.transcript_id,
                    "start_seconds": chunk.start_seconds,
                    "end_seconds": chunk.end_seconds,
                },
            )
            for chunk in chunks.all()
            if _score_text(normalized_query, chunk.text) > 0
        ]

    def _graph_triple_hits(
        self,
        *,
        query: RetrievalQueryData,
        normalized_query: str,
    ) -> list[RetrievalHitData]:
        """Return graph triple hits."""
        triples = self._db.query(GraphTriple)
        if query.episode_ids:
            triples = triples.filter(
                GraphTriple.provenance_episode_id.in_(query.episode_ids),
            )
        if query.media_ids:
            triples = triples.filter(GraphTriple.subject_media_id.in_(query.media_ids))
        return [
            RetrievalHitData(
                hit_kind=RetrievalHitKind.GRAPH_TRIPLE,
                resource_id=triple.id,
                score=_score_text(normalized_query, _triple_text(triple)),
                title=f"{triple.subject_media_id}:{triple.predicate}",
                snippet=_snippet(_triple_text(triple)),
                metadata_json={
                    "subject_media_id": triple.subject_media_id,
                    "object_media_id": triple.object_media_id,
                    "object_kind": triple.object_kind,
                },
            )
            for triple in triples.all()
            if _score_text(normalized_query, _triple_text(triple)) > 0
        ]


def _score_text(
    normalized_query: str,
    text: str | None,
) -> float:
    """Score text by normalized term coverage."""
    if not text:
        return 0.0
    normalized_text = _normalize_query(text)
    query_terms = normalized_query.split()
    if not query_terms:
        return 0.0
    matched_terms = sum(1 for term in query_terms if term in normalized_text)
    if matched_terms == 0:
        return 0.0
    phrase_bonus = 1.0 if normalized_query in normalized_text else 0.0
    return matched_terms / len(query_terms) + phrase_bonus


def _embedding_bonus(
    embedding_status: str,
) -> float:
    """Return a small ranking bonus for embedded semantic chunks."""
    if embedding_status == SemanticChunkEmbeddingStatus.EMBEDDED.value:
        return 0.1
    return 0.0


def _triple_text(
    triple: GraphTriple,
) -> str:
    """Return searchable text for a graph triple."""
    return " ".join(
        item
        for item in (
            triple.predicate,
            triple.object_value,
            triple.evidence_text,
        )
        if item
    )


def _snippet(
    text: str,
) -> str:
    """Return a compact retrieval snippet."""
    return " ".join(text.split())[:240]


def _normalize_query(
    query: str,
) -> str:
    """Normalize query text for deterministic local ranking."""
    return " ".join(query.casefold().split())
