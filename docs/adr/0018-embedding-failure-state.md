<!-- markdownlint-disable MD041 -->

# 0018. Failed embeddings keep no vector; provider errors are failures

- Status: proposed
- Date: 2026-11-17
- Tags: derivatives, embeddings, data

## Context

`sync_semantic_chunks_for_transcript` stores one embedding per semantic chunk in
`embedding_vector` (`vector(1536)` on PostgreSQL). It labels the vector with
`embedding_model`, `embedding_dimensions` and `embedded_at`, and sets
`embedding_status` to `embedded`, `failed` or `pending`. Retrieval (#712) must
leave out stale model generations and unusable vectors. The embedding backfill
(#715) selects `pending`, `failed` and stale-model chunks.

Three gaps remained in how a failure was recorded:

- A chunk that was embedded and then failed under another model kept the old
  vector and `embedded_at`, but took the new model's label. The row named a
  model that did not produce its vector.
- Only `RuntimeError`, `ValueError` and `TypeError` from the provider were
  caught. An `OSError` or an HTTP client error escaped the sync, so no chunk was
  marked `failed`.
- A right-length vector that was not a `list` or `tuple` was reported as
  `embedding_dimensions_mismatch`.

ADR 0009 already defines the bounded failure descriptor for embedding failures
and its fixed validation-shape reasons.

## Decision

**State of the embedding columns.**

- A stored vector is always labelled with the model, dimension and
  `embedded_at` of the embed that produced it. A chunk's text never changes,
  because its key includes the text hash.
- `embedded`: carries the vector of the latest embed.
- `failed`: a failed embed clears `embedding_vector` and `embedded_at`.
  - `embedding_model` and `embedding_dimensions` describe the failed attempt.
  - Each is null when the provider's value could not be read.
  - The dimension is null when it is not an `int` within the int4 column.
- `pending`: a sync without a provider leaves the embedding columns as they
  were. A pending chunk may carry the vector, model, dimension and time of its
  last successful embed. If its last attempt failed, it carries no vector and
  that attempt's model and dimension.

**Provider errors.** This applies to any `Exception` raised by the provider's
own code:

- reading its `model_name` and `dimensions`;
- the `embed_texts` call;
- reading the result. The result is read at most one vector past the number of
  texts, so an endless result fails the count check instead of hanging.

Such an exception fails the whole batch. The stored descriptor is the ADR 0009
one: the exception's class name, an allowlisted HTTP status when the class is a
known HTTP adapter, and `request_failed`, or the exception's own explicit
reason. The message is never read. Errors from this module's own code (building
the request, the dimension and count checks) are not provider failures and
propagate. So do:

- `ContentSuppressedError`, a takedown refusal (ADR 0015);
- `BaseException` subclasses outside `Exception`: `KeyboardInterrupt`,
  `SystemExit`, `GeneratorExit`, `asyncio.CancelledError`, and a
  `BaseExceptionGroup` that holds them.

**Vector shape.** The provider port requires each vector to be a `list` or
`tuple` of Python `int` or `float` values finite in float32. Adapters convert
client-library arrays themselves. Any other vector type fails its chunk with a
new reason, `embedding_invalid_type`, whatever its length.
`embedding_dimensions_mismatch` now means a list or tuple of the wrong length.
The vector column type refuses, on every dialect and before the statement runs,
what pgvector refuses: non-sequence values, booleans, strings, `None`, NaN,
infinity and float32 overflow.

This extends ADR 0009. ADR 0009 is unchanged; this ADR adds one reason to its
closed set and widens which provider exceptions reach its descriptor.

Not chosen: on failure, keeping the previous vector with its previous model
label. That would keep a usable vector across a transient failure. But a
`failed` row would then carry a vector, and every reader would need both status
and label to judge it.

## Consequences

- A chunk with a vector always has a label that names its producer, so
  retrieval can filter on status and model. A `failed` chunk never contributes
  a vector.
- **Cost.** The sync re-embeds every chunk on every run with a provider. A
  transient provider failure on a re-sync wipes vectors that were correct,
  even when the model and text are unchanged. They are recovered only by
  embedding again, which repeats paid provider calls. Skipping chunks already
  embedded by the current model is left to the backfill (#715).
- A provider defect that raises, such as a broken property, is recorded as an
  embedding failure, not raised to the caller. Its traceback is still logged
  under the ADR 0009 boundary.
- A writer of `embedding_vector` other than the sync now gets a bind-time
  `TypeError` or `ValueError` (wrapped by SQLAlchemy) instead of a database
  error at flush.
- Rollback is a code revert. There is no migration, and stored rows are not
  rewritten.
