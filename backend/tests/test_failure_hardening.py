"""Regression coverage for failure identity, copying and log metadata."""

import copy
import logging
import multiprocessing
from collections.abc import Callable
from unittest.mock import Mock

import httpx
import pytest
from assertpy import assert_that
from sqlalchemy.exc import IntegrityError

from podex.services.discovery_spotify import SpotifyDiscovery
from podex.services.failure_codes import FailureReason, PipelineFailure
from podex.services.failure_descriptors import describe_failure, log_failure
from podex.services.feed_dns import FeedDnsError
from podex.services.feed_http import FeedFetchError

_PRIVATE_VALUE = "synthetic-private-value-812"


def _pickle_round_trip(error: BaseException) -> BaseException:
    """Exercise the pickle protocol used by local exception transfer queues."""
    queue = multiprocessing.get_context("fork").SimpleQueue()
    try:
        queue.put(error)
        restored: BaseException = queue.get()
        return restored
    finally:
        queue.close()


@pytest.mark.parametrize("kind", ["feed", "dns", "pipeline"])
@pytest.mark.parametrize(
    "round_trip",
    [_pickle_round_trip, copy.copy, copy.deepcopy],
    ids=["pickle", "copy", "deepcopy"],
)
def test_structured_failures_round_trip_with_metadata(
    kind: str,
    round_trip: Callable[[BaseException], BaseException],
) -> None:
    """Preserve exception type, arguments, reasons, status, notes and extra state."""
    errors: dict[str, BaseException] = {
        "feed": FeedFetchError(
            "fixture failure", reason=FailureReason.HTTP_STATUS, status_code=429
        ),
        "dns": FeedDnsError("fixture failure", reason=FailureReason.DNS_BUSY),
        "pipeline": PipelineFailure(
            "fixture failure", reason=FailureReason.CONFIGURATION
        ),
    }
    original = errors[kind]
    original.add_note("fixture note")
    original.__dict__["fixture_attribute"] = "fixture value"
    restored = round_trip(original)
    assert_that(type(restored)).is_equal_to(type(original))
    assert_that(restored.args).is_equal_to(original.args)
    assert_that(restored.__dict__).is_equal_to(original.__dict__)
    assert_that(restored).is_not_same_as(original)


@pytest.mark.parametrize("kind", ["chain", "group", "uncoded_wrapper"])
def test_descriptor_metadata_belongs_to_the_first_reasoned_exception(kind: str) -> None:
    """Class, status and reason describe one selected exception, in tree order."""
    feed = FeedFetchError("fixture", reason=FailureReason.HTTP_STATUS, status_code=503)
    if kind == "chain":
        error: BaseException = PipelineFailure(
            "fixture", reason=FailureReason.CONFIGURATION
        )
        error.__cause__ = feed
        expected = "rss: PipelineFailure; configuration"
    elif kind == "group":
        error = ExceptionGroup(
            "fixture", [FeedDnsError("fixture", reason=FailureReason.DNS_BUSY), feed]
        )
        expected = "rss: FeedDnsError; dns_busy"
    else:
        request = httpx.Request("GET", "https://example.test/feed")
        error = ValueError("fixture wrapper")
        error.__cause__ = httpx.HTTPStatusError(
            "fixture", request=request, response=httpx.Response(503)
        )
        expected = "rss: HTTPStatusError (HTTP 503); request_failed"
    assert_that(describe_failure("rss", error)).is_equal_to(expected)


def _broken_metadata(_value: object) -> object:
    """Simulate an exception adapter whose diagnostic property fails."""
    raise RuntimeError(_PRIVATE_VALUE)


@pytest.mark.parametrize("attribute", ["status_code", "failure_reason"])
def test_raising_metadata_properties_cannot_escape_the_failure_handler(
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
) -> None:
    """Unreadable properties lose only their own metadata, without raising."""
    error = FeedFetchError("fixture", reason=FailureReason.HTTP_STATUS, status_code=503)
    monkeypatch.setattr(
        FeedFetchError, attribute, property(_broken_metadata), raising=False
    )
    expected = (
        "rss: FeedFetchError; http_status"
        if attribute == "status_code"
        else "rss: FeedFetchError (HTTP 503); request_failed"
    )
    assert_that(describe_failure("rss", error)).is_equal_to(expected)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://example.test/?key=" + _PRIVATE_VALUE, "[invalid context]"),
        ("fixture\n" + _PRIVATE_VALUE, "[invalid context]"),
        ("identifier" + "X" * 3000, ("identifier" + "X" * 3000)[:64]),
        (10**100, str(10**100)[:64]),
    ],
)
def test_log_context_is_validated_and_bounded(
    caplog: pytest.LogCaptureFixture,
    value: str | int,
    expected: str,
) -> None:
    """Reject non-identifiers and truncate each accepted context value."""
    error = ValueError("fixture")
    descriptor = describe_failure("discovery", error)
    log_failure(logging.getLogger(__name__), descriptor, error, context=(value,))
    record = caplog.records[-1]
    assert_that(record.getMessage()).contains(expected).does_not_contain(_PRIVATE_VALUE)
    assert_that(len(record.getMessage())).is_less_than(150)
    if isinstance(value, int) or len(value) > 64:
        assert_that(record.getMessage()).does_not_contain(str(value))


def test_missing_log_context_has_no_empty_tuple_prefix(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A diagnostic without context starts with its source, without noise."""
    error = ValueError("fixture")
    descriptor = describe_failure("discovery", error)
    log_failure(logging.getLogger(__name__), descriptor, error)
    assert_that(caplog.records[-1].getMessage()).is_equal_to(descriptor)


@pytest.mark.parametrize("attribute", ["__module__", "__qualname__"])
@pytest.mark.parametrize("database", [False, True])
def test_exception_namespace_cannot_put_unvalidated_text_in_logs(
    caplog: pytest.LogCaptureFixture,
    attribute: str,
    database: bool,
) -> None:
    """Validate exception namespaces before copying them to logged stand-ins."""
    namespace = _PRIVATE_VALUE + "?invalid"
    if database:
        database_kind = type("FixtureError", (IntegrityError,), {attribute: namespace})
        error: BaseException = database_kind("fixture", {}, ValueError("fixture"))
    else:
        plain_kind = type("FixtureError", (Exception,), {attribute: namespace})
        error = plain_kind("fixture")
    log_failure(
        logging.getLogger(__name__), describe_failure("discovery", error), error
    )
    assert_that(caplog.text).does_not_contain(_PRIVATE_VALUE)
    captured = caplog.records[-1].exc_info
    if captured is None or captured[1] is None:
        raise AssertionError("Expected a logged exception object")
    assert_that(type(captured[1]).__module__).is_equal_to("builtins")


def test_spotify_returned_identifier_uses_the_context_guard(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Search-returned ids cannot bypass validation in the subsequent show fetch."""
    identifier = _PRIVATE_VALUE + "?key=fixture"
    sdk = Mock()
    sdk.search.return_value = {"shows": {"items": [{"id": identifier}]}}
    sdk.show.side_effect = RuntimeError("fixture")
    provider = SpotifyDiscovery()
    monkeypatch.setattr(provider, "_spotify", sdk)
    assert_that(provider.search_podcasts("fixture")).is_empty()
    sdk.show.assert_called_once_with(identifier, market="US")
    assert_that(caplog.text).contains(
        "show_fetch", "[invalid context]"
    ).does_not_contain(_PRIVATE_VALUE)
