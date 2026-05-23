# LightRAG Spike

Date: 2026-05-22

## Goal

Evaluate whether Podex should adopt LightRAG directly for hybrid graph-plus-vector
retrieval, or keep the retrieval layer hand-rolled over Podex derivatives.

## Findings

LightRAG is an active MIT-licensed Python project from HKUDS for graph-enhanced
RAG. Its docs highlight knowledge-graph extraction, embedding/reranker
configuration, PostgreSQL storage support, and `mix` query mode, which map
conceptually to Podex's semantic chunk plus graph triple retrieval needs.

The paper positions LightRAG around graph structures integrated with vector
representations for efficient retrieval. That direction matches Podex's Phase 2.5
data model, but the current Podex boundary is narrower: public discovery needs
ranked snippets, entity summaries, graph relationship context, provenance, and a
retention gate before raw transcript purge.

## Decision

Do not take a direct LightRAG runtime dependency in Phase 2.5. Adopt Podex's
Postgres-backed derivative retrieval path behind `HybridRetrievalPort`, and keep
LightRAG as a candidate adapter behind the same port after we can benchmark it
against retained JRE episodes.

This keeps the production surface aligned with the database derivatives already
needed for retention policy, while preserving a clean replacement point for a
future LightRAG adapter.

## References

- [HKUDS LightRAG repository](https://github.com/HKUDS/LightRAG)
- [LightRAG paper](https://arxiv.org/abs/2410.05779)
