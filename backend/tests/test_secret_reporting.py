"""Secrets in free text never reach error reports or logs.

Each test loads settings carrying a synthetic value for every secret, starts
the reporter with ``init_sentry`` and an in-memory transport, configures
logging as the application does, triggers one failure, then searches the
complete serialised envelopes (events with their breadcrumbs) and the
captured log output for every synthetic secret.
"""

import logging
from collections.abc import Callable, Iterator
from secrets import token_hex

import httpx
import pytest
import sentry_sdk
from assertpy import assert_that
from pydantic import SecretStr
from sqlalchemy import Column, MetaData, String, Table, create_engine, insert
from sqlalchemy.exc import IntegrityError

from podex.config import DatabaseSettings, Settings
from podex.database import build_engine_kwargs
from podex.logging_config import configure_logging
from podex.models import Media, MediaType
from podex.observability import init_sentry
from podex.services.enrichment.base import RateLimiter
from podex.services.media_enrichment import MediaEnricher
from tests.conftest import SentryEnvelopeRecorder
from tests.secret_support import SECRET_ENVIRONMENT, leaked, load_secret_settings

_logger = logging.getLogger("podex.tests.secret_reporting")

_USERINFO_PASSWORD = f"userinfo-pw-{token_hex(8)}"
_USERINFO_BASE_URL = f"https://reader:{_USERINFO_PASSWORD}@openlibrary.invalid"

_Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(name="settings")
def settings_fixture(
    monkeypatch: pytest.MonkeyPatch,
    sentry_transport: SentryEnvelopeRecorder,
) -> Settings:
    """Load secret-bearing settings and start the reporter as the app does.

    Returns:
        Settings: The loaded settings.
    """
    del sentry_transport  # requested so init_sentry uses the in-memory transport
    settings = load_secret_settings(monkeypatch)
    assert_that(init_sentry(settings)).is_true()
    configure_logging()
    return settings


@pytest.fixture(name="http_requests")
def http_requests_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[httpx.Request]]:
    """Route every new HTTPX client through a failing in-memory transport.

    Rate-limit waits are disabled so the test never sleeps.

    Yields:
        list[httpx.Request]: Every request the clients sent.
    """
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Record the request and fail it as an unavailable provider would."""
        earlier = [seen for seen in sent if seen.url.host == request.url.host]
        sent.append(request)
        # Each host first answers with an error status, then stops connecting.
        if not earlier:
            return httpx.Response(503, request=request)
        raise httpx.ConnectError(f"connection to {request.url} failed", request=request)

    monkeypatch.setattr(
        "httpx._client.HTTPTransport",
        lambda **_options: httpx.MockTransport(respond),
    )
    monkeypatch.setattr(RateLimiter, "wait_sync", lambda _self: None)
    yield sent


def _report_and_collect(
    transport: SentryEnvelopeRecorder,
    caplog: pytest.LogCaptureFixture,
) -> str:
    """Report one closing error so breadcrumbs are sent, and return all output.

    Returns:
        str: Every serialised envelope followed by the captured log text.
    """
    try:
        raise RuntimeError("closing report")
    except RuntimeError:
        _logger.exception("closing report")
    assert_that(transport.events()).is_not_empty()
    return transport.serialized() + caplog.text


def _enrich(settings: Settings, media_type: MediaType) -> None:
    """Run the enricher, built from settings, over one media item."""
    media = Media(type=media_type, title="Dune", author="Frank Herbert", year=1965)
    media.id = 1
    enricher = MediaEnricher(settings.enrichment)
    try:
        enricher.enrich(media, use_wikipedia_fallback=False)
    finally:
        enricher.close()


def test_enrichment_failures_with_keys_in_urls_leak_no_key(
    settings: Settings,
    http_requests: list[httpx.Request],
    sentry_transport: SentryEnvelopeRecorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failing keyed providers authenticate as before and leak no key."""
    with caplog.at_level(logging.DEBUG):
        for media_type in (MediaType.MOVIE, MediaType.BOOK, MediaType.STUDY):
            _enrich(settings, media_type)
        output = _report_and_collect(sentry_transport, caplog)

    assert_that(leaked(output)).is_empty()
    hosts = {request.url.host: request for request in http_requests}
    tmdb = hosts["api.themoviedb.org"]
    omdb = hosts["www.omdbapi.com"]
    books = hosts["www.googleapis.com"]
    pubmed = hosts["eutils.ncbi.nlm.nih.gov"]
    s2 = hosts["api.semanticscholar.org"]
    assert_that(tmdb.url.params["api_key"]).is_equal_to(
        SECRET_ENVIRONMENT["PODEX_ENRICHMENT__TMDB__API_KEY"],
    )
    assert_that(omdb.url.params["apikey"]).is_equal_to(
        SECRET_ENVIRONMENT["PODEX_ENRICHMENT__OMDB__API_KEY"],
    )
    assert_that(pubmed.url.params["api_key"]).is_equal_to(
        SECRET_ENVIRONMENT["PODEX_ENRICHMENT__PUBMED__API_KEY"],
    )
    assert_that(books.headers["x-goog-api-key"]).is_equal_to(
        SECRET_ENVIRONMENT["PODEX_ENRICHMENT__GOOGLE_BOOKS__API_KEY"],
    )
    assert_that(books.url.params).does_not_contain_key("key")
    assert_that(s2.headers["x-api-key"]).is_equal_to(
        SECRET_ENVIRONMENT["PODEX_ENRICHMENT__SEMANTIC_SCHOLAR__API_KEY"],
    )


def test_http_client_error_with_url_userinfo_leaks_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
    http_requests: list[httpx.Request],
    sentry_transport: SentryEnvelopeRecorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A provider URL carrying userinfo is kept out of logs and reports."""
    monkeypatch.setenv("PODEX_ENRICHMENT__OPEN_LIBRARY__BASE_URL", _USERINFO_BASE_URL)
    settings = load_secret_settings(monkeypatch)
    init_sentry(settings)

    with caplog.at_level(logging.DEBUG):
        _enrich(settings, MediaType.BOOK)
        output = _report_and_collect(sentry_transport, caplog)

    open_library = [r for r in http_requests if r.url.host == "openlibrary.invalid"]
    assert_that(open_library).is_not_empty()
    assert_that(open_library[0].url.password).is_equal_to(_USERINFO_PASSWORD)
    assert_that(output).does_not_contain(_USERINFO_PASSWORD)
    assert_that(leaked(output)).is_empty()


def test_reported_http_error_redacts_userinfo_and_configured_keys(
    settings: Settings,
    sentry_transport: SentryEnvelopeRecorder,
) -> None:
    """An HTTP error escaping to the reporter loses URL credentials and keys."""
    key = settings.enrichment.tmdb.api_key.get_secret_value()
    url = f"https://reader:{_USERINFO_PASSWORD}@api.themoviedb.org/3/x?api_key={key}"
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, request=request)
        ),
    ) as client:
        response = client.get(url)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        sentry_sdk.capture_exception(error)

    wire = sentry_transport.serialized()
    assert_that(sentry_transport.events()).is_length(1)
    assert_that(wire).contains("[redacted]")
    assert_that(wire).does_not_contain(_USERINFO_PASSWORD)
    assert_that(leaked(wire)).is_empty()


def test_database_error_with_secret_parameters_leaks_no_secret(
    settings: Settings,
    sentry_transport: SentryEnvelopeRecorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Statement parameters are not rendered into database errors."""
    secret = settings.auth.workos_api_key.get_secret_value()
    engine = create_engine(
        "sqlite://",
        **build_engine_kwargs(
            Settings(database=DatabaseSettings(url=SecretStr("sqlite://"))),
        ),
    )
    credentials = Table(
        "credentials",
        MetaData(),
        Column("value", String, unique=True),
    )
    credentials.metadata.create_all(engine)
    with caplog.at_level(logging.DEBUG):
        try:
            with engine.begin() as connection:
                connection.execute(insert(credentials), [{"value": secret}])
                connection.execute(insert(credentials), [{"value": secret}])
        except IntegrityError:
            _logger.exception("credential write failed")
        output = _report_and_collect(sentry_transport, caplog)
    engine.dispose()

    assert_that(output).contains("credential write failed")
    assert_that(output).contains("IntegrityError (SQLITE_CONSTRAINT_UNIQUE)")
    assert_that(output).contains("[driver message withheld]")
    assert_that(leaked(output)).is_empty()


def test_exception_rendering_settings_leaks_no_secret(
    settings: Settings,
    sentry_transport: SentryEnvelopeRecorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An exception or log message that renders settings shows no secret."""
    with caplog.at_level(logging.DEBUG):
        _logger.info("loaded %s", settings.auth)
        try:
            raise RuntimeError(f"invalid configuration: {settings!r} / {settings}")
        except RuntimeError:
            _logger.exception("startup failed")
        output = _report_and_collect(sentry_transport, caplog)

    assert_that(output).contains("startup failed")
    assert_that(output).contains("**********")
    assert_that(leaked(output)).is_empty()
