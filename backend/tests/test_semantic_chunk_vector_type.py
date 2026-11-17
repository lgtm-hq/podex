"""Embedding column binding and storage per dialect.

SQLite stores embeddings as compact JSON text; PostgreSQL binds them as
pgvector values. The stored SQLite text below is pinned to what the column
wrote before PostgreSQL binding was fixed, so that fix cannot change it.
"""

from typing import cast

import pytest
from assertpy import assert_that
from sqlalchemy import Text, create_engine, insert, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql import ClauseElement

from podex.models import Episode, Podcast, SemanticChunk, Transcript
from podex.models.semantic_chunk import PgVectorType, PostgresVectorBind

SQLITE_STORED_TEXT: list[tuple[list[float] | None, str | None]] = [
    (None, None),
    ([0.1, 0.2, 0.3], "[0.1,0.2,0.3]"),
    ([-1.5, -0.0001, -7.0], "[-1.5,-0.0001,-7.0]"),
    ([1e-07, 2.5e20, -3e-12], "[1e-07,2.5e+20,-3e-12]"),
    (
        [0.123456789012345, 3.141592653589793, -2.718281828459045],
        "[0.123456789012345,3.141592653589793,-2.718281828459045]",
    ),
    ([1, 2, 0], "[1.0,2.0,0.0]"),
]


def _postgres_sql(statement: ClauseElement) -> str:
    """Compile ``statement`` for the application's PostgreSQL driver."""
    dialect = create_engine("postgresql+psycopg://").dialect
    return str(statement.compile(dialect=dialect))


def _add_transcript(db: Session) -> Transcript:
    """Add and flush a synthetic podcast, episode and transcript."""
    podcast = Podcast(name="Vector Show", slug="vector-show")
    db.add(podcast)
    db.flush()
    episode = Episode(podcast_id=podcast.id, title="Vector Episode")
    db.add(episode)
    db.flush()
    transcript = Transcript(episode_id=episode.id, provider="podscripts")
    db.add(transcript)
    db.flush()
    return transcript


@pytest.mark.parametrize(("vector", "stored_text"), SQLITE_STORED_TEXT)
def test_sqlite_stores_embedding_text_unchanged(
    db_session: Session,
    vector: list[float] | None,
    stored_text: str | None,
) -> None:
    """SQLite keeps the compact JSON text (or NULL) and reads it back exactly."""
    transcript = _add_transcript(db_session)
    chunk = SemanticChunk(
        episode_id=transcript.episode_id,
        transcript_id=transcript.id,
        chunk_key="vector:0",
        chunk_index=0,
        pipeline_version="v1",
        source_text_hash="d" * 64,
        text="alpha",
        context_snippet="alpha",
        token_count=1,
        embedding_vector=vector,
    )
    db_session.add(chunk)
    db_session.commit()
    db_session.expire_all()

    raw = db_session.execute(
        text("SELECT embedding_vector FROM semantic_chunks"),
    ).scalar_one()
    stored = db_session.execute(
        select(SemanticChunk.embedding_vector).where(SemanticChunk.id == chunk.id),
    ).scalar_one()

    assert_that(raw).is_equal_to(stored_text)
    expected = None if vector is None else [float(item) for item in vector]
    assert_that(stored).is_equal_to(expected)
    if stored is not None:
        assert_that({type(item) for item in stored}).is_equal_to({float})


def test_sqlite_loads_the_text_implementation() -> None:
    """Every dialect but PostgreSQL keeps the ``Text`` implementation."""
    impl = PgVectorType(3).load_dialect_impl(create_engine("sqlite://").dialect)

    assert_that(impl).is_instance_of(Text)


def test_postgres_loads_the_vector_bind_implementation() -> None:
    """PostgreSQL gets the vector bind type with the configured dimension."""
    dialect = create_engine("postgresql+psycopg://").dialect
    impl = PgVectorType(3).load_dialect_impl(dialect)

    assert_that(impl).is_instance_of(PostgresVectorBind)
    assert_that(getattr(impl, "dimensions", None)).is_equal_to(3)


def test_postgres_ddl_declares_the_vector_column() -> None:
    """The model's PostgreSQL DDL declares ``vector(1536)``; SQLite gets TEXT."""
    postgres_ddl = _postgres_sql(CreateTable(SemanticChunk.__table__))
    sqlite_ddl = str(
        CreateTable(SemanticChunk.__table__).compile(
            dialect=create_engine("sqlite://").dialect,
        ),
    )

    assert_that(postgres_ddl).contains("embedding_vector vector(1536),")
    assert_that(sqlite_ddl).contains("embedding_vector TEXT,")


@pytest.mark.parametrize("dimensions", [0, -1])
def test_dimensions_must_be_positive(dimensions: int) -> None:
    """A zero or negative dimension is refused when the type is built."""
    with pytest.raises(ValueError, match="must be positive"):
        PgVectorType(dimensions)
    with pytest.raises(ValueError, match="must be positive"):
        PostgresVectorBind(dimensions)


@pytest.mark.parametrize("dimensions", [1.5, "3", True])
def test_dimensions_must_be_an_integer(dimensions: object) -> None:
    """A non-integer dimension, ``bool`` included, is refused."""
    with pytest.raises(TypeError, match="must be an integer"):
        PgVectorType(cast(int, dimensions))


def test_sqlite_binds_embedding_without_a_cast() -> None:
    """The SQLite statement binds the embedding as a plain parameter."""
    dialect = create_engine("sqlite://").dialect
    sql = str(
        update(SemanticChunk).values(embedding_vector=[1.0]).compile(dialect=dialect),
    )

    assert_that(sql).is_equal_to(
        "UPDATE semantic_chunks SET embedding_vector=?, updated_at=?",
    )


def test_postgres_insert_binds_embedding_as_vector() -> None:
    """On PostgreSQL the inserted embedding is cast to the column's vector type."""
    sql = _postgres_sql(
        insert(SemanticChunk).values(chunk_key="k", embedding_vector=None),
    )

    assert_that(sql).contains("%(embedding_vector)s::vector(1536)")
    assert_that(sql).does_not_contain("%(embedding_vector)s::VARCHAR")


def test_postgres_update_binds_embedding_as_vector() -> None:
    """On PostgreSQL an embedding UPDATE is cast to the column's vector type."""
    sql = _postgres_sql(update(SemanticChunk).values(embedding_vector=[1.0]))

    assert_that(sql).contains("embedding_vector=%(embedding_vector)s::vector(1536)")


def test_postgres_bind_cast_follows_column_dimensions() -> None:
    """The bind cast carries the dimension configured on the type."""
    dialect = create_engine("postgresql+psycopg://").dialect
    impl = PgVectorType(8).load_dialect_impl(dialect)

    assert_that(impl.render_bind_cast).is_true()
    assert_that(dialect.type_compiler_instance.process(impl)).is_equal_to(
        "vector(8)",
    )


def test_literal_rendering_per_dialect() -> None:
    """Inlined literals cast to vector on PostgreSQL and stay text on SQLite."""
    statement = update(SemanticChunk).values(
        embedding_vector=[1.0, -2.5],
        updated_at=None,
    )
    literal = {"literal_binds": True}

    postgres_sql = str(
        statement.compile(
            dialect=create_engine("postgresql+psycopg://").dialect,
            compile_kwargs=literal,
        ),
    )
    sqlite_sql = str(
        statement.compile(
            dialect=create_engine("sqlite://").dialect,
            compile_kwargs=literal,
        ),
    )

    assert_that(postgres_sql).contains(
        "embedding_vector='[1.0,-2.5]'::vector(1536)",
    )
    assert_that(sqlite_sql).contains("embedding_vector='[1.0,-2.5]',")


def test_postgres_literal_doubles_single_quotes() -> None:
    """The vector literal escapes quotes the way PostgreSQL string literals do."""
    dialect = create_engine("postgresql+psycopg://").dialect
    render = PostgresVectorBind(2).literal_processor(dialect)

    assert_that(render("[1,'2]")).is_equal_to("'[1,''2]'::vector(2)")


def test_postgres_bind_value_is_pgvector_text() -> None:
    """The bound value is pgvector's bracketed text form; NULL stays NULL."""
    dialect = create_engine("postgresql+psycopg://").dialect
    vector_type = PgVectorType(3)

    assert_that(
        vector_type.process_bind_param([0.5, -1, 2.5e-05], dialect),
    ).is_equal_to("[0.5,-1.0,2.5e-05]")
    assert_that(vector_type.process_bind_param(None, dialect)).is_none()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # pgvector's text output, as psycopg returns it without a vector loader.
        ("[1,-2.5,3e-05,0.1]", [1.0, -2.5, 3e-05, 0.1]),
        ([1, -2.5, 0.25], [1.0, -2.5, 0.25]),
        (None, None),
    ],
)
def test_result_conversion_accepts_driver_forms(
    raw: object,
    expected: list[float] | None,
) -> None:
    """Text, list and NULL results convert to floats or ``None``."""
    dialect = create_engine("postgresql+psycopg://").dialect

    assert_that(
        PgVectorType(4).process_result_value(raw, dialect),
    ).is_equal_to(expected)


@pytest.mark.parametrize("raw", [b"[1]", 1.5, {"vector": [1]}])
def test_result_conversion_rejects_unexpected_types(raw: object) -> None:
    """A result that is neither text, a list nor NULL raises ``TypeError``."""
    dialect = create_engine("postgresql+psycopg://").dialect

    with pytest.raises(TypeError, match="unexpected embedding value type"):
        PgVectorType(4).process_result_value(raw, dialect)
