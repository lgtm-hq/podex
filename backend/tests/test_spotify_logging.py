"""The Spotify SDK's debug logging never shows client credentials or tokens.

The SDK renders its request headers into DEBUG lines: ``Authorization:
Basic base64(client_id:client_secret)`` for the token request and
``Bearer <access token>`` for API calls. Every request here is answered by
an in-memory session; nothing reaches the network or the token cache file.
"""

import base64
import json
import logging
from secrets import token_hex
from typing import Any

import pytest
import requests
import urllib3
from assertpy import assert_that
from spotipy import Spotify
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.exceptions import SpotifyException
from spotipy.util import Retry

from podex.logging_config import configure_logging
from podex.services.discovery_spotify import SpotifyDiscovery

_CLIENT_ID = f"spotify-client-{token_hex(6)}"
_CLIENT_SECRET = f"spotify-secret-{token_hex(8)}"
_ACCESS_TOKEN = f"spotify-token-{token_hex(8)}"
_SHOW_ID = "4rOoJ6Egrf8K2IrywzwOMk"
_PROVIDER_TEXT = f"provider-text-{token_hex(8)}"


class _JsonResponse:
    """JSON response, as much of ``requests.Response`` as the SDK uses."""

    def __init__(self, url: str, payload: dict[str, Any], status: int = 200) -> None:
        self.url = url
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.text = json.dumps(payload)
        self._payload = payload

    def raise_for_status(self) -> None:
        """Raise for an error status, as ``requests`` does."""
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self) -> dict[str, Any]:
        """Return the decoded payload."""
        return self._payload


def _answer(
    _session: requests.Session,
    method: str,
    url: str,
    **_kwargs: Any,
) -> _JsonResponse:
    """Answer the token endpoint and the show endpoint from memory."""
    del method
    if url.startswith("https://accounts.spotify.com/"):
        return _JsonResponse(
            url,
            {"access_token": _ACCESS_TOKEN, "expires_in": 3600},
        )
    return _JsonResponse(
        url,
        {"id": _SHOW_ID, "name": "Example Show", "description": _PROVIDER_TEXT},
    )


def _everything(caplog: pytest.LogCaptureFixture) -> str:
    """Return the formatted output and every record's raw parts."""
    parts = [caplog.text]
    for record in caplog.records:
        parts.extend((repr(record.msg), repr(record.args), str(record.exc_text)))
    return "\n".join(parts)


def test_spotify_debug_lines_mask_credentials(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Token and API requests are logged without any credential form."""
    monkeypatch.setattr(requests.Session, "request", _answer)
    monkeypatch.setattr("spotipy.oauth2.CacheFileHandler", MemoryCacheHandler)
    configure_logging()
    caplog.set_level(logging.DEBUG)

    discovered = SpotifyDiscovery(
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
    ).discover_podcast(_SHOW_ID)

    basic = base64.b64encode(f"{_CLIENT_ID}:{_CLIENT_SECRET}".encode()).decode()
    assert_that(discovered).is_not_none()
    assert_that(caplog.text).contains(
        "Sending POST request to https://accounts.spotify.com/api/token with "
        "Headers: {} (+1 other headers) and Body: [withheld]",
    )
    assert_that(caplog.text).contains(
        f"Sending GET to https://api.spotify.com/v1/shows/{_SHOW_ID} with Params: "
        "{'market': '[withheld]'}",
        "RESULTS: [withheld response body",
    )
    for credential in (_CLIENT_SECRET, basic, _ACCESS_TOKEN, _PROVIDER_TEXT):
        assert_that(_everything(caplog)).does_not_contain(credential)


def _refuse_show(
    _session: requests.Session,
    method: str,
    url: str,
    **_kwargs: Any,
) -> _JsonResponse:
    """Grant a token, then reject the show request."""
    if url.startswith("https://accounts.spotify.com/"):
        return _answer(_session, method, url)
    return _JsonResponse(
        url,
        {"error": {"status": 401, "message": f"Token expired: {_PROVIDER_TEXT}"}},
        status=401,
    )


def test_spotify_failure_lines_mask_credentials(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A rejected API call is logged with method, URL and status only."""
    monkeypatch.setattr(requests.Session, "request", _refuse_show)
    monkeypatch.setattr("spotipy.oauth2.CacheFileHandler", MemoryCacheHandler)
    configure_logging()
    caplog.set_level(logging.DEBUG)

    discovered = SpotifyDiscovery(
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
    ).discover_podcast(_SHOW_ID)

    basic = base64.b64encode(f"{_CLIENT_ID}:{_CLIENT_SECRET}".encode()).decode()
    assert_that(discovered).is_none()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert_that(errors).is_length(1)
    assert_that(errors[0].getMessage()).is_equal_to(
        f"HTTP Error for GET to https://api.spotify.com/v1/shows/{_SHOW_ID} with "
        "Params: {'market': '[withheld]'} returned 401 due to "
        "[provider message withheld]",
    )
    assert_that(caplog.text).contains(
        "Headers: {'content-type': 'application/json'} (+1 other headers)",
    )
    for credential in (_CLIENT_SECRET, basic, _ACCESS_TOKEN, _PROVIDER_TEXT):
        assert_that(_everything(caplog)).does_not_contain(credential)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Headers: {'Authorization': 'Basic YWJjOmRlZg=='} and Body: {}",
            "[record withheld: unrecognised form, 61 characters]",
        ),
        (
            "Headers: {'Authorization': 'Bearer tok', 'Content-Type': 'x'}",
            "[record withheld: unrecognised form, 61 characters]",
        ),
        (
            "Body: {'refresh_token': 'r1', 'code': 'c1', 'status_code': 200}",
            "[record withheld: unrecognised form, 63 characters]",
        ),
        (
            "RESULTS: {'name': 'Example Show'}",
            "RESULTS: [withheld response body (24 characters)]",
        ),
    ],
)
@pytest.mark.parametrize("level", [logging.DEBUG, logging.WARNING])
def test_authorization_material_is_never_printed(
    message: str,
    expected: str,
    level: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Lines the former Spotify filter masked are withheld or skeletons."""
    configure_logging()
    caplog.set_level(logging.DEBUG)

    logging.getLogger("spotipy.oauth2").log(level, message)

    assert_that(caplog.records[-1].getMessage()).is_equal_to(expected)
    for value in ("YWJjOmRlZg==", "tok'", "r1", "c1", "Example Show"):
        assert_that(_everything(caplog)).does_not_contain(value)


@pytest.mark.parametrize(
    ("retry_after", "shown"),
    [("7", "7"), (f"soon {_PROVIDER_TEXT}", "[withheld]")],
)
def test_rate_limit_warning_prints_only_a_numeric_delay(
    retry_after: str,
    shown: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The SDK's rate-limit warning keeps a numeric Retry-After, nothing else."""
    configure_logging()
    caplog.set_level(logging.DEBUG)
    retry = Retry(total=3, status_forcelist=[429], allowed_methods=["GET"])
    response = urllib3.HTTPResponse(
        status=429,
        headers={"Retry-After": retry_after},
        preload_content=False,
    )

    retry.increment(method="GET", url="/v1/shows/x", response=response)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert_that(warnings).is_length(1)
    assert_that(warnings[0].getMessage()).is_equal_to(
        "Your application has reached a rate/request limit. Retry will occur "
        f"after: {shown} s",
    )
    assert_that(_everything(caplog)).does_not_contain(_PROVIDER_TEXT)


def _reject_search(
    _session: requests.Session,
    _method: str,
    url: str,
    **_kwargs: Any,
) -> _JsonResponse:
    """Reject a search with a provider message."""
    return _JsonResponse(
        url,
        {"error": {"status": 400, "message": f"Bad query {_PROVIDER_TEXT}"}},
        status=400,
    )


def test_error_line_for_a_long_query_is_a_skeleton(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A very long query still yields the skeleton, never the provider text."""
    monkeypatch.setattr(requests.Session, "request", _reject_search)
    configure_logging()
    caplog.set_level(logging.DEBUG)

    with pytest.raises(SpotifyException):
        Spotify(auth=_ACCESS_TOKEN).search(q="q" * 4_100)

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert_that(errors).is_length(1)
    assert_that(errors[0].getMessage()).starts_with(
        "HTTP Error for GET to https://api.spotify.com/v1/search with Params: {",
    )
    assert_that(errors[0].getMessage()).ends_with(
        "returned 400 due to [provider message withheld]",
    )
    for value in (_PROVIDER_TEXT, _ACCESS_TOKEN, "qqqq"):
        assert_that(_everything(caplog)).does_not_contain(value)
