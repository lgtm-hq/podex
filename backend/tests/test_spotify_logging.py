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
from assertpy import assert_that
from spotipy.cache_handler import MemoryCacheHandler

from podex.logging_config import configure_logging, mask_authorization
from podex.services.discovery_spotify import SpotifyDiscovery

_CLIENT_ID = f"spotify-client-{token_hex(6)}"
_CLIENT_SECRET = f"spotify-secret-{token_hex(8)}"
_ACCESS_TOKEN = f"spotify-token-{token_hex(8)}"
_SHOW_ID = "4rOoJ6Egrf8K2IrywzwOMk"


class _JsonResponse:
    """Successful JSON response, as much of ``requests.Response`` as the SDK uses."""

    def __init__(self, url: str, payload: dict[str, Any]) -> None:
        self.url = url
        self.status_code = 200
        self.headers = {"Content-Type": "application/json"}
        self.text = json.dumps(payload)
        self._payload = payload

    def raise_for_status(self) -> None:
        """Succeed, as for any 2xx response."""

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
    return _JsonResponse(url, {"id": _SHOW_ID, "name": "Example Show"})


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
    assert_that(caplog.text).contains("Sending POST request")
    assert_that(caplog.text).contains("'Authorization': '[redacted]'")
    for credential in (_CLIENT_SECRET, basic, _ACCESS_TOKEN):
        assert_that(caplog.text).does_not_contain(credential)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Headers: {'Authorization': 'Basic YWJjOmRlZg=='} and Body: {}",
            "Headers: {'Authorization': '[redacted]'} and Body: {}",
        ),
        (
            "Headers: {'Authorization': 'Bearer tok', 'Content-Type': 'x'}",
            "Headers: {'Authorization': '[redacted]', 'Content-Type': 'x'}",
        ),
        (
            "Body: {'refresh_token': 'r1', 'code': 'c1', 'status_code': 200}",
            "Body: {'refresh_token': '[redacted]', 'code': '[redacted]', "
            "'status_code': 200}",
        ),
        ("RESULTS: {'name': 'Example Show'}", "RESULTS: {'name': 'Example Show'}"),
    ],
)
def test_authorization_material_is_masked(message: str, expected: str) -> None:
    """Header values and token fields are masked; other fields are kept."""
    assert_that(mask_authorization(message)).is_equal_to(expected)
