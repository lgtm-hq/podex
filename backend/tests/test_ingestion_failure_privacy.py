"""Failure diagnostics never persist or expose untrusted provider text."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import anthropic
import httpx
import httpx2
import psycopg.errors
import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.models import IngestionRun, Podcast, ScheduledWorkItemModel
from podex.services.discovery import DiscoveryResult
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.llm_extraction import LLMExtractor
from podex.services.llm_response import MalformedResponseError
from podex.services.llm_transcript_cleanup import TranscriptCleaner
from podex.services.recurring_discovery import (
    plan_recurring_discovery_work,
    run_due_episode_discovery_work,
)
from tests.conftest import enable_ops

_PRIVATE_VALUE = "synthetic-private-value-812"


def _failure(kind: str) -> Exception:
    """Construct real HTTP and database errors carrying untrusted diagnostics."""
    if kind == "database":
        database_failure: Exception = IntegrityError(
            f"INSERT {_PRIVATE_VALUE}",
            {"value": _PRIVATE_VALUE},
            psycopg.errors.UniqueViolation(f"row value {_PRIVATE_VALUE}"),
        )
        return database_failure
    transport = httpx if kind == "httpx" else httpx2
    request = transport.Request(
        "GET", f"https://example.test/?api_key={_PRIVATE_VALUE}"
    )
    failure: Exception = transport.HTTPStatusError(
        f"provider body {_PRIVATE_VALUE}",
        request=request,
        response=transport.Response(503, request=request),
    )
    return failure


@pytest.mark.parametrize(
    "failure_case",
    [
        (source, kind)
        for source in ("podscripts", "rss", "spotify", "runner")
        for kind in ("httpx", "httpx2", "database")
    ],
    ids=lambda case: f"{case[0]}-{case[1]}",
)
def test_discovery_failure_is_private_across_storage_api_and_logs(
    db_session: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_case: tuple[str, str],
) -> None:
    """Both caught provider errors and raised runner errors keep class and code."""
    source, kind = failure_case
    podcast = Podcast(
        name="Failure fixture",
        slug="failure-fixture",
        status="active",
        podscripts_slug="fixture" if source == "podscripts" else None,
        rss_url="https://example.test/feed" if source == "rss" else None,
        spotify_id="fixture" if source == "spotify" else None,
    )
    db_session.add(podcast)
    db_session.commit()
    plan_recurring_discovery_work(
        db=db_session,
        now=datetime(2026, 5, 10, tzinfo=UTC),
    )
    db_session.commit()

    def runner(db: Session, current: Podcast) -> DiscoveryResult:
        """Inject at the provider or whole-run boundary without changing the DB."""
        failure = _failure(kind)
        if source == "runner":
            raise failure
        with DiscoveryOrchestrator(db) as orchestrator:
            provider = SimpleNamespace(
                is_configured=True,
                discover_episodes=Mock(side_effect=failure),
            )
            monkeypatch.setattr(orchestrator, "_sources", {source: provider})
            return orchestrator.discover_for_podcast(current)

    results = run_due_episode_discovery_work(db=db_session, runner=runner)
    db_session.commit()
    db_session.expire_all()
    run = db_session.query(IngestionRun).one()
    item = db_session.query(ScheduledWorkItemModel).one()
    enable_ops(client, api_key="fixture-ops-key")
    response = client.get(
        "/api/v2/ops/pipelines", headers={"X-Ops-Key": "fixture-ops-key"}
    )

    expected_class = "IntegrityError" if kind == "database" else "HTTPStatusError"
    expected_code = "SQLSTATE 23505" if kind == "database" else "HTTP 503"
    assert_that(response.status_code).is_equal_to(200)
    assert_that(run.error_summary).contains(expected_class, expected_code)
    assert_that(item.error_message).is_equal_to(run.error_summary)
    assert_that(response.json()["runs"][0]["error_summary"]).is_equal_to(
        run.error_summary
    )
    assert_that(caplog.text).contains(expected_class, expected_code)
    assert_that(
        str(results)
        + str(run.error_summary)
        + str(item.error_message)
        + response.text
        + caplog.text
    ).does_not_contain(_PRIVATE_VALUE)
    if results:
        assert_that(results[0].errors).is_equal_to([run.error_summary])


def test_custom_runner_cannot_persist_unstructured_error_text(
    db_session: Session,
) -> None:
    """A custom result cannot bypass the scheduler's descriptor boundary."""
    db_session.add(Podcast(name="Fixture", slug="fixture", status="active"))
    db_session.commit()
    plan_recurring_discovery_work(db=db_session, now=datetime(2026, 5, 10, tzinfo=UTC))

    def runner(_db: Session, _podcast: Podcast) -> DiscoveryResult:
        """Return arbitrary failure text rather than a structured diagnostic."""
        return DiscoveryResult(errors=[_PRIVATE_VALUE])

    results = run_due_episode_discovery_work(db=db_session, runner=runner)
    db_session.commit()
    run = db_session.query(IngestionRun).one()
    assert_that(run.error_summary).is_equal_to("discovery: unclassified_failure")
    assert_that(results[0].errors).is_equal_to([run.error_summary])
    assert_that(
        db_session.query(ScheduledWorkItemModel).one().error_message
    ).is_equal_to(
        run.error_summary,
    )


@pytest.mark.parametrize("service", ["extraction", "cleanup"])
@pytest.mark.parametrize("kind", ["status", "connection", "malformed"])
def test_llm_failures_return_private_descriptors_and_log_objects(
    caplog: pytest.LogCaptureFixture,
    service: str,
    kind: str,
) -> None:
    """SDK failures retain their class and status without messages or bodies."""
    request = httpx2.Request("POST", f"https://example.test/?key={_PRIVATE_VALUE}")
    failures = {
        "status": anthropic.APIStatusError(
            _PRIVATE_VALUE,
            response=httpx2.Response(429, request=request),
            body=_PRIVATE_VALUE,
        ),
        "connection": anthropic.APIConnectionError(
            message=_PRIVATE_VALUE, request=request
        ),
        "malformed": MalformedResponseError(_PRIVATE_VALUE),
    }
    failure = failures[kind]
    client = Mock()
    client.messages.create.side_effect = failure
    if service == "extraction":
        result = LLMExtractor(client=client).extract_from_transcript(
            [{"text": "word " * 60}]
        )
    else:
        result = TranscriptCleaner(client=client).cleanup_transcript("word " * 60)
    assert_that(result.errors).is_length(1)
    assert_that(result.errors[0]).contains(service, type(failure).__name__)
    if kind == "status":
        assert_that(result.errors[0]).contains("HTTP 429")
    assert_that(str(result.errors) + caplog.text).does_not_contain(_PRIVATE_VALUE)
    assert_that(caplog.records[-1].exc_info).is_not_none()


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (_PRIVATE_VALUE, "missing_json_array"),
        (f"[{_PRIVATE_VALUE}", "incomplete_json_array"),
        (f"[{_PRIVATE_VALUE}]", "invalid_json"),
        (f'{{"value":"{_PRIVATE_VALUE}"}}', "expected_json_array"),
    ],
)
def test_extraction_parse_failures_do_not_return_response_text(
    caplog: pytest.LogCaptureFixture,
    text: str,
    reason: str,
) -> None:
    """Malformed generated content produces fixed reasons rather than snippets."""
    client = Mock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
    )
    result = LLMExtractor(client=client).extract_from_transcript(
        [{"text": "word " * 60}]
    )
    assert_that(result.errors).is_length(1)
    assert_that(result.errors[0]).contains(reason)
    assert_that(str(result.errors) + caplog.text).does_not_contain(_PRIVATE_VALUE)
