"""First use of the Redis, storage and encryption settings withholds their values.

The shared rate-limit store's client connects on the first rate-limited
request, and the transcript artifact store builds its object storage client and
cipher when an ops route first needs it. A malformed value was quoted there by
the library that first used it. Each test builds the client from a value
carrying a synthetic secret, drives one request or one store construction over
stubs (no network, Redis or object storage), and checks that the response, the
raised error as a traceback prints it and the captured logs hold none of the
value while the setting name and the error class are reported. Valid values
reach the clients with the same arguments as before.
"""

import logging
import socket
import traceback
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from secrets import token_hex
from typing import Any, NoReturn

import boto3
import pytest
import redis
from assertpy import assert_that
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from podex.api.deps import get_app_settings
from podex.config import (
    RateLimitSettings,
    RetentionSettings,
    Settings,
    TranscriptStorageSettings,
)
from podex.main import create_app
from podex.models import TakedownRequest
from podex.services.limiter_factory import build_rate_limiter
from podex.services.redis_limiter import RedisSlidingWindowRateLimiter
from podex.services.transcript_artifacts import (
    EncryptedFilesystemTranscriptArtifactStore,
    EncryptedS3TranscriptArtifactStore,
    build_transcript_artifact_store,
)
from podex.unusable_settings import UnusableSettingError
from tests.conftest import seed_catalog_graph
from tests.test_transcript_artifacts import RecordingS3Client

_REDIS_SETTING = "PODEX_RATE_LIMIT__REDIS_URL"
_OPS_KEY = "first-use-ops-key"
_HEADERS = {"X-Ops-Key": _OPS_KEY}
# A character outside ASCII; the encoding error would print its escape.
_NON_ASCII = "\u5bc6"
# Synthetic static storage credentials for the valid-configuration case.
_ACCESS_ID = f"access-id-{token_hex(4)}"
_ACCESS_KEY = token_hex(16)


def _secret() -> str:
    """Return a synthetic secret that is a valid lowercase host label."""
    return f"firstuse{token_hex(8)}"


def _printed(error: BaseException) -> str:
    """Return the error as a traceback prints it, with its chain, and its repr."""
    return "".join(traceback.format_exception(error)) + repr(error)


def _assert_absent(text: str, secret: str) -> None:
    """``text`` holds no part of the synthetic value, in any letter case."""
    assert_that(text.lower()).does_not_contain(secret.lower())
    assert_that(text).does_not_contain(_NON_ASCII)
    assert_that(text.lower()).does_not_contain(f"\\u{ord(_NON_ASCII):04x}")


def _assert_reported(
    error: UnusableSettingError,
    *,
    setting: str,
    failure: str,
    secret: str,
) -> None:
    """The error names the setting and class only and keeps no original error."""
    assert_that(str(error)).starts_with(f"{setting} is not usable: {failure}. ")
    assert_that(error.__cause__).is_none()
    assert_that(error.__context__).is_none()
    _assert_absent(_printed(error), secret)


def _redis_settings(url: str) -> Settings:
    """Return settings selecting the shared store at ``url``, limiting /health."""
    return Settings(
        rate_limit=RateLimitSettings(redis_url=SecretStr(url), exempt_paths=[]),
    )


def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if anything resolves a host name."""

    def resolve(*_args: object, **_kwargs: object) -> NoReturn:
        """Refuse name resolution."""
        raise AssertionError("unexpected name resolution")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


@pytest.mark.parametrize(
    ("template", "failure"),
    [
        # The client's TLS options reject the flag, quoting it.
        (
            "rediss://cache.invalid:6380/0?ssl_cert_reqs={secret}",
            "redis.exceptions.RedisError",
        ),
        # An unknown option reaches the connection class as a keyword, whose
        # name the interpreter quotes.
        ("redis://cache.invalid:6379/0?{secret}=1", "TypeError"),
        # A port given as an option is converted on first use, quoting it.
        ("redis://cache.invalid/0?port={secret}", "ValueError"),
    ],
    ids=["ssl-cert-reqs", "unknown-option", "port-option"],
)
def test_redis_option_failure_names_setting_and_class_only(
    template: str,
    failure: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Options the connection class rejects stop start-up by class only."""
    _forbid_network(monkeypatch)
    secret = _secret()

    with caplog.at_level(logging.DEBUG), pytest.raises(UnusableSettingError) as caught:
        create_app(_redis_settings(template.format(secret=secret)))

    _assert_reported(
        caught.value,
        setting=_REDIS_SETTING,
        failure=failure,
        secret=secret,
    )
    _assert_absent(caplog.text, secret)


def _unresolvable(*_args: object, **_kwargs: object) -> NoReturn:
    """Fail name resolution as the resolver does for an unknown host."""
    raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")


@pytest.mark.parametrize("target", ["host", "socket-path"])
def test_redis_connection_failure_fails_open_and_logs_class_only(
    target: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request admitted after a failed connection logs the class only."""
    secret = _secret()
    if target == "host":
        # The resolver is stubbed; the client's error quotes host and port.
        monkeypatch.setattr(socket, "getaddrinfo", _unresolvable)
        url = f"redis://{secret}.invalid:6379/0"
    else:
        # A local socket path that does not exist; the error quotes the path.
        url = f"unix://{tmp_path}/{secret}/redis.sock"
    app = create_app(_redis_settings(url))

    with caplog.at_level(logging.DEBUG), TestClient(app) as client:
        response = client.get("/health")

    assert_that(response.status_code).is_equal_to(200)
    _assert_absent(response.text, secret)
    _assert_absent(str(response.headers), secret)
    _assert_absent(caplog.text, secret)
    failures = [
        record.getMessage()
        for record in caplog.records
        if record.name == "podex.services.redis_limiter"
    ]
    assert_that(failures).is_length(1)
    assert_that(failures[0]).ends_with(": redis.exceptions.ConnectionError")
    assert_that(failures[0]).contains("failing open")


def test_redis_reset_failure_logs_class_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed cleanup scan is logged by class, not by the client's message."""
    secret = _secret()

    class _UnreachableClient:
        """Stand-in whose scan fails as an unreachable server does."""

        def scan_iter(self, *_args: object, **_kwargs: object) -> NoReturn:
            """Fail with the client's own connection message."""
            raise redis.ConnectionError(f"Error 8 connecting to {secret}:6379.")

        def delete(self, *_args: object, **_kwargs: object) -> int:
            """Never reached because the scan fails first."""
            return 0

    limiter = RedisSlidingWindowRateLimiter(
        _UnreachableClient(),
        max_requests=1,
        window_seconds=60.0,
    )

    with caplog.at_level(logging.DEBUG):
        limiter.reset()

    _assert_absent(caplog.text, secret)
    assert_that(caplog.messages).is_equal_to(
        ["rate limiter redis reset failed; ignoring: redis.exceptions.ConnectionError"],
    )


@pytest.mark.parametrize(
    "url",
    [
        "redis://localhost:6379/0",
        "redis://:pass%40word@cache.example:6380/2?health_check_interval=30",
        "rediss://user:pw@cache.example:6380/1?ssl_cert_reqs=required",
        "rediss://cache.example:6380/0?ssl_cert_reqs=none&ssl_check_hostname=false",
        "unix:///var/run/redis.sock?db=3&health_check_interval=10",
    ],
)
def test_valid_redis_url_builds_client_with_same_arguments(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client gets the URL and timeouts as before, and nothing connects."""
    _forbid_network(monkeypatch)
    calls: list[tuple[str, dict[str, object]]] = []
    real_from_url: Callable[..., redis.Redis] = redis.Redis.from_url

    def recording_from_url(given: str, **kwargs: object) -> redis.Redis:
        """Record the arguments, then build the real client."""
        calls.append((given, kwargs))
        return real_from_url(given, **kwargs)

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(recording_from_url))

    limiter = build_rate_limiter(_redis_settings(url))

    assert_that(limiter).is_instance_of(RedisSlidingWindowRateLimiter)
    assert_that(calls).is_equal_to(
        [(url, {"socket_connect_timeout": 0.5, "socket_timeout": 0.25})],
    )


def _key() -> str:
    """Return a valid cipher key."""
    key: bytes = Fernet.generate_key()
    return key.decode("ascii")


def _storage_cases(secret: str, tmp_path: Path) -> dict[str, TranscriptStorageSettings]:
    """Return one malformed storage configuration per setting."""
    return {
        "endpoint": TranscriptStorageSettings(
            storage_backend="encrypted_s3",
            encryption_key=SecretStr(_key()),
            s3_bucket="private-transcripts",
            s3_endpoint_url=f"https://{secret} .example",
            s3_region_name="auto",
        ),
        "region": TranscriptStorageSettings(
            storage_backend="encrypted_s3",
            encryption_key=SecretStr(_key()),
            s3_bucket="private-transcripts",
            s3_region_name=f"{secret}/west",
        ),
        "key-filesystem": TranscriptStorageSettings(
            storage_path=tmp_path,
            encryption_key=SecretStr(f"{secret}{_NON_ASCII}{secret}"),
        ),
        "key-s3": TranscriptStorageSettings(
            storage_backend="encrypted_s3",
            encryption_key=SecretStr(f"{secret}{_NON_ASCII}{secret}"),
            s3_bucket="private-transcripts",
            s3_region_name="auto",
        ),
    }


_STORAGE_FAILURES = {
    "endpoint": ("PODEX_TRANSCRIPTS__S3_ENDPOINT_URL", "ValueError"),
    "region": (
        "PODEX_TRANSCRIPTS__S3_REGION_NAME",
        "botocore.exceptions.InvalidRegionError",
    ),
    "key-filesystem": ("PODEX_TRANSCRIPTS__ENCRYPTION_KEY", "UnicodeEncodeError"),
    "key-s3": ("PODEX_TRANSCRIPTS__ENCRYPTION_KEY", "UnicodeEncodeError"),
}


@pytest.fixture(name="isolated_aws")
def isolated_aws_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the object storage client away from local AWS files and the network."""
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-creds"))
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    _forbid_network(monkeypatch)


@pytest.mark.usefixtures("isolated_aws")
@pytest.mark.parametrize("case", list(_STORAGE_FAILURES))
def test_store_setting_failure_names_setting_and_class_only(
    case: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Building the store reports a malformed setting by name and class."""
    secret = _secret()
    setting, failure = _STORAGE_FAILURES[case]
    settings = Settings(transcripts=_storage_cases(secret, tmp_path)[case])

    with caplog.at_level(logging.DEBUG), pytest.raises(UnusableSettingError) as caught:
        build_transcript_artifact_store(settings=settings)

    _assert_reported(caught.value, setting=setting, failure=failure, secret=secret)
    _assert_absent(caplog.text, secret)


@pytest.mark.usefixtures("isolated_aws")
@pytest.mark.parametrize(
    "template",
    [
        # Rejected when the client is built.
        "https://{secret} .example",
        "https://[{secret}]",
        # Rejected by the endpoint rules on every bucket request.
        "https://{secret}.example:{secret}",
        "https://{secret}.example:99999",
        "ftp://{secret}.example",
        "https://{secret}.example/?token={secret}",
    ],
    ids=["host", "ipv6", "port-text", "port-range", "scheme", "query"],
)
def test_malformed_endpoint_shapes_name_setting_and_class_only(
    template: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every endpoint shape the storage client rejects is reported unquoted."""
    secret = _secret()
    settings = Settings(
        transcripts=TranscriptStorageSettings(
            storage_backend="encrypted_s3",
            encryption_key=SecretStr(_key()),
            s3_bucket="private-transcripts",
            s3_endpoint_url=template.format(secret=secret),
        ),
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(UnusableSettingError) as caught:
        build_transcript_artifact_store(settings=settings)

    _assert_reported(
        caught.value,
        setting="PODEX_TRANSCRIPTS__S3_ENDPOINT_URL",
        failure="ValueError",
        secret=secret,
    )
    _assert_absent(caplog.text, secret)


def _use_settings(client: TestClient, transcripts: TranscriptStorageSettings) -> None:
    """Configure ops access, purging and the given storage for the test app."""
    app = client.app
    if not isinstance(app, FastAPI):
        raise AssertionError("Expected a FastAPI test app")
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        ops_api_key=_OPS_KEY,
        retention=RetentionSettings(purge_enabled=True),
        transcripts=transcripts,
    )


def _assert_conflict_reports(
    response: Any,
    *,
    case: str,
    secret: str,
    logs: str,
) -> None:
    """The route refuses with the setting and class and quotes nothing."""
    setting, failure = _STORAGE_FAILURES[case]
    assert_that(response.status_code).is_equal_to(409)
    assert_that(response.json()["detail"]).starts_with(
        f"{setting} is not usable: {failure}. ",
    )
    _assert_absent(response.text, secret)
    _assert_absent(logs, secret)


@pytest.mark.usefixtures("isolated_aws")
@pytest.mark.parametrize("case", list(_STORAGE_FAILURES))
def test_purge_route_reports_store_setting_without_value(
    case: str,
    client: TestClient,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The purge route builds the store first and refuses as it did, unquoted."""
    secret = _secret()
    _use_settings(client, _storage_cases(secret, tmp_path)[case])

    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/v2/ops/retention/1/purge", headers=_HEADERS)

    _assert_conflict_reports(response, case=case, secret=secret, logs=caplog.text)


@pytest.mark.usefixtures("isolated_aws")
@pytest.mark.parametrize("case", list(_STORAGE_FAILURES))
def test_takedown_route_reports_store_setting_without_value(
    case: str,
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Approving a takedown builds the store and refuses as it did, unquoted."""
    secret = _secret()
    graph = seed_catalog_graph(db_session)
    request = TakedownRequest(
        subject_type="episode",
        subject_id=graph.episode_id,
        requester_type="creator",
        requester_name="Creator",
        requester_email="creator@example.com",
        basis="Remove this source",
        requested_actions_json=["suppress_raw_transcript"],
        status="pending",
    )
    db_session.add(request)
    db_session.commit()
    _use_settings(client, _storage_cases(secret, tmp_path)[case])

    with caplog.at_level(logging.DEBUG):
        response = client.post(
            f"/api/v2/ops/takedown-requests/{request.id}/decide",
            json={"status": "approved", "note": "Verified"},
            headers=_HEADERS,
        )

    _assert_conflict_reports(response, case=case, secret=secret, logs=caplog.text)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ({}, {}),
        (
            {
                "s3_endpoint_url": "https://account.r2.cloudflarestorage.com",
                "s3_region_name": "auto",
                "s3_access_key_id": SecretStr(_ACCESS_ID),
                "s3_secret_access_key": SecretStr(_ACCESS_KEY),
            },
            {
                "endpoint_url": "https://account.r2.cloudflarestorage.com",
                "region_name": "auto",
                "aws_access_key_id": _ACCESS_ID,
                "aws_secret_access_key": _ACCESS_KEY,
            },
        ),
        (
            {"s3_endpoint_url": "http://127.0.0.1:9000", "s3_region_name": "us-west-2"},
            {"endpoint_url": "http://127.0.0.1:9000", "region_name": "us-west-2"},
        ),
        (
            {"s3_endpoint_url": "http://[::1]:9000"},
            {"endpoint_url": "http://[::1]:9000"},
        ),
        (
            {"s3_endpoint_url": "https://storage.example:9443/base/"},
            {"endpoint_url": "https://storage.example:9443/base/"},
        ),
    ],
    ids=["defaults", "r2", "ipv4", "ipv6", "path"],
)
def test_valid_storage_settings_build_client_with_same_arguments(
    configured: dict[str, Any],
    expected: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The object storage client gets exactly the configured arguments."""
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    stub = RecordingS3Client()

    def recording_client(*args: object, **kwargs: object) -> RecordingS3Client:
        """Record the arguments and return the stub client."""
        calls.append((args, kwargs))
        return stub

    monkeypatch.setattr(boto3, "client", recording_client)

    store = build_transcript_artifact_store(
        settings=Settings(
            transcripts=TranscriptStorageSettings(
                storage_backend="encrypted_s3",
                encryption_key=SecretStr(_key()),
                s3_bucket="private-transcripts",
                **configured,
            ),
        ),
    )

    assert_that(calls).is_equal_to([(("s3",), expected)])
    assert_that(store).is_instance_of(EncryptedS3TranscriptArtifactStore)


def test_valid_key_derives_the_same_cipher_and_key_id(tmp_path: Path) -> None:
    """An ASCII key is used as before: same key id, and objects round-trip."""
    key = _key()
    store = EncryptedFilesystemTranscriptArtifactStore(
        root_path=tmp_path,
        encryption_key=key,
    )

    stored = store.put_json(storage_key="a/raw.json.enc", payload={"raw_text": "t"})

    expected_id = sha256(key.encode("ascii")).hexdigest()[:16]
    assert_that(stored.encryption_key_id).is_equal_to(expected_id)
    ciphertext = (tmp_path / "a" / "raw.json.enc").read_bytes()
    decrypted: bytes = Fernet(key.encode("ascii")).decrypt(ciphertext)
    assert_that(decrypted).is_equal_to(b'{"raw_text":"t"}')
