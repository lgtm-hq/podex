"""Regression coverage for diagnostic metadata and historical error reads."""

import logging
from datetime import UTC, datetime
from unittest.mock import Mock

import httpx
import pytest
import spotipy
from assertpy import assert_that
from fastapi.testclient import TestClient
from requests import HTTPError, Response
from requests import Session as HttpSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.models import (
    IngestionRun,
    Podcast,
    ScheduledWorkItemModel,
    SemanticChunk,
    Transcript,
)
from podex.services.discovery import DiscoveryResult
from podex.services.discovery_podscripts import PodscriptsDiscovery
from podex.services.discovery_spotify import SpotifyDiscovery
from podex.services.failure_descriptors import (
    describe_failure,
    log_failure,
    persisted_failure,
)
from podex.services.pipeline_queries import get_ingestion_run_by_id
from podex.services.recurring_discovery import (
    plan_recurring_discovery_work,
    run_due_episode_discovery_work,
)
from podex.services.scheduled_work import list_scheduled_work_items
from podex.services.semantic_chunks import sync_semantic_chunks_for_transcript
from tests.conftest import enable_ops, seed_catalog_graph

_PRIVATE_VALUE = "synthetic-private-value-812"


@pytest.mark.parametrize("kind", ["plain", "database_driver"])
@pytest.mark.parametrize("name", [_PRIVATE_VALUE, "X" * 3000, "bad.name", "9Error"])
def test_invalid_exception_names_are_bounded_in_results_and_logs(
    name: str,
    caplog: pytest.LogCaptureFixture,
    kind: str,
) -> None:
    """Runtime class metadata cannot bypass the diagnostic character limit."""
    error: Exception = type(name, (Exception,), {})(_PRIVATE_VALUE)
    if kind == "database_driver":
        error = IntegrityError("fixture statement", {}, error)
    descriptor = describe_failure("discovery", error)
    log_failure(logging.getLogger(__name__), descriptor, error)
    expected_class = "Exception" if kind == "plain" else "IntegrityError"
    assert_that(descriptor).is_equal_to(f"discovery: {expected_class}; request_failed")
    assert_that(len(descriptor)).is_less_than_or_equal_to(384)
    assert_that(caplog.text).does_not_contain(name, _PRIVATE_VALUE)


def test_runner_rejects_oversized_exception_metadata(db_session: Session) -> None:
    """Runner exceptions obey the same bounded persistence contract as results."""
    db_session.add(Podcast(name="Fixture", slug="fixture", status="active"))
    db_session.commit()
    plan_recurring_discovery_work(db=db_session, now=datetime(2026, 5, 10, tzinfo=UTC))

    def runner(_db: Session, _podcast: Podcast) -> DiscoveryResult:
        """Raise with metadata which cannot form a valid diagnostic."""
        raise type("X" * 3000, (Exception,), {})(_PRIVATE_VALUE)

    run_due_episode_discovery_work(db=db_session, runner=runner)
    db_session.commit()
    assert_that(db_session.query(IngestionRun).one().error_summary).is_equal_to(
        "discovery: Exception; request_failed",
    )
    assert_that(
        db_session.query(ScheduledWorkItemModel).one().error_message
    ).is_equal_to(
        "discovery: Exception; request_failed",
    )


def test_descriptor_validation_rejects_oversized_class_names() -> None:
    """A runner cannot supply an unbounded descriptor-shaped string."""
    assert_that(persisted_failure(f"rss: {'X' * 3000}; request_failed")).is_equal_to(
        "discovery: unclassified_failure",
    )


@pytest.mark.parametrize(
    "stored",
    [
        _PRIVATE_VALUE,
        "rss: Error; request_failed; " + _PRIVATE_VALUE,
        f"rss: {'X' * 3000}; request_failed",
        "; ".join(["rss: TimeoutError; request_failed"] * 200),
    ],
)
def test_legacy_errors_are_withheld_on_reads_without_rewriting_rows(
    db_session: Session,
    client: TestClient,
    stored: str,
) -> None:
    """Both ops run queries and work summaries withhold unstructured history."""
    db_session.add(Podcast(name="Fixture", slug="fixture", status="active"))
    run = IngestionRun(status="FAILED", error_summary=stored)
    db_session.add(run)
    db_session.commit()
    plan_recurring_discovery_work(db=db_session, now=datetime(2026, 5, 10, tzinfo=UTC))
    work = db_session.query(ScheduledWorkItemModel).one()
    work.error_message = stored
    db_session.commit()
    enable_ops(client, api_key="fixture-ops-key")
    response = client.get(
        "/api/v2/ops/pipelines", headers={"X-Ops-Key": "fixture-ops-key"}
    )
    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["runs"][0]["error_summary"]).is_equal_to(
        "legacy failure text withheld",
    )
    loaded = get_ingestion_run_by_id(db=db_session, ingestion_run_id=run.id)
    if loaded is None:
        raise AssertionError("Expected a stored ingestion run")
    assert_that(loaded.error_summary).is_equal_to("legacy failure text withheld")
    assert_that(list_scheduled_work_items(db=db_session)[0].error_message).is_equal_to(
        "legacy failure text withheld",
    )
    db_session.refresh(run)
    db_session.refresh(work)
    assert_that(run.error_summary).is_equal_to(stored)
    assert_that(work.error_message).is_equal_to(stored)


def test_embedding_failure_persists_a_descriptor(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Embedding exceptions cannot place their message into a chunk column."""
    graph = seed_catalog_graph(db_session)
    transcript = Transcript(
        episode_id=graph.episode_id, provider="fixture", raw_text="word " * 30
    )
    db_session.add(transcript)
    db_session.flush()
    provider = Mock(model_name="fixture-embedding", dimensions=3)
    provider.embed_texts.side_effect = RuntimeError(_PRIVATE_VALUE)
    result = sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        embedding_provider=provider,
    )
    db_session.commit()
    assert_that(result.failed_count).is_equal_to(1)
    assert_that(caplog.text).contains(
        "embedding: RuntimeError; request_failed"
    ).does_not_contain(_PRIVATE_VALUE)
    assert_that(db_session.query(SemanticChunk).one().embedding_error).is_equal_to(
        "embedding: RuntimeError; request_failed",
    )


def _http_failure(request: httpx.Request) -> httpx.Response:
    """Fail at the socket seam with an untrusted provider diagnostic."""
    raise httpx.HTTPStatusError(
        _PRIVATE_VALUE,
        request=request,
        response=httpx.Response(404, request=request),
    )


@pytest.mark.parametrize(
    "operation", ["podcast_fetch", "episode_page", "catalog_fetch"]
)
def test_podscripts_handlers_keep_context_without_error_text(
    operation: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drive each real provider catch through a scripted HTTP transport."""
    with PodscriptsDiscovery(delay=0) as provider:
        provider.client.close()
        provider.client = httpx.Client(transport=httpx.MockTransport(_http_failure))
        if operation == "podcast_fetch":
            assert_that(provider.discover_podcast("fixture-show")).is_none()
        elif operation == "episode_page":
            assert_that(
                provider.discover_episodes(Podcast(podscripts_slug="fixture-show"))
            ).is_empty()
        else:
            assert_that(provider.list_available_podcasts()).is_empty()
    record = next(record for record in caplog.records if record.exc_info is not None)
    assert_that(record.getMessage()).contains(
        operation, "podscripts: HTTPStatusError (HTTP 404); request_failed"
    )
    assert_that(caplog.text).does_not_contain(_PRIVATE_VALUE)
    if operation != "catalog_fetch":
        assert_that(record.getMessage()).contains("fixture-show")
    if operation == "episode_page":
        assert_that(record.getMessage()).contains("('episode_page', 'fixture-show', 1)")
    assert_that(record.exc_info).is_not_none()


@pytest.mark.parametrize("operation", ["show_fetch", "episode_fetch", "search"])
def test_spotify_handlers_keep_context_without_error_text(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    operation: str,
) -> None:
    """Run the provider and SDK over a failing, socket-free request adapter."""
    transport = Mock(spec=HttpSession)
    response = Mock(
        spec=Response,
        status_code=503,
        headers={},
        url=f"https://example.test/?key={_PRIVATE_VALUE}",
    )
    response.json.return_value = {"error": {"message": "temporarily unavailable"}}
    response.raise_for_status.side_effect = HTTPError(_PRIVATE_VALUE, response=response)
    transport.request.return_value = response
    sdk = spotipy.Spotify(auth="fixture-access", requests_session=transport)
    provider = SpotifyDiscovery(client_id="fixture-id", client_secret=_PRIVATE_VALUE)
    monkeypatch.setattr(provider, "_spotify", sdk)
    if operation == "show_fetch":
        assert_that(provider.discover_podcast("FixtureShow123456789012")).is_none()
    elif operation == "episode_fetch":
        assert_that(
            provider.discover_episodes(Podcast(spotify_id="FixtureShow123456789012"))
        ).is_empty()
    else:
        assert_that(provider.search_podcasts("fixture-query")).is_empty()
    transport.close()
    assert_that(transport.request.call_count).is_equal_to(1)
    record = next(record for record in caplog.records if record.exc_info is not None)
    assert_that(record.getMessage()).contains(
        operation, "spotify: SpotifyException (HTTP 503); request_failed"
    )
    assert_that(caplog.text).does_not_contain(_PRIVATE_VALUE)
    if operation != "search":
        assert_that(record.getMessage()).contains("FixtureShow123456789012")
    assert_that(record.exc_info).is_not_none()


def test_spotify_configuration_failure_has_a_fixed_reason(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing credentials remain distinguishable from a failed HTTP request."""
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    provider = SpotifyDiscovery()
    assert_that(provider.discover_podcast("FixtureShow123456789012")).is_none()
    assert_that(caplog.text).contains("show_fetch", "; configuration")


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "interrupted before completion",
        "Missing podcast_id",
        "Podcast not found",
        "Podcast is not active",
        "Discovery schedule disabled",
        "rss: FeedFetchError (HTTP 404); http_status",
        "rss: TimeoutError; request_failed; "
        "spotify: SpotifyException (HTTP 429); request_failed",
    ],
)
def test_read_guard_preserves_safe_historical_outcomes(
    db_session: Session,
    stored: str | None,
) -> None:
    """Reading safe diagnostics preserves null, fixed reasons and joined results."""
    run = IngestionRun(status="FAILED", error_summary=stored)
    db_session.add(run)
    db_session.flush()
    loaded = get_ingestion_run_by_id(db=db_session, ingestion_run_id=run.id)
    if loaded is None:
        raise AssertionError("Expected a stored ingestion run")
    assert_that(loaded.error_summary).is_equal_to(stored)
