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

import errno
import logging
import socket
import tempfile
import threading
import traceback
from collections.abc import Callable, Iterator
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
from podex.services.redis_limiter import (
    RateLimitStoreError,
    RedisSlidingWindowRateLimiter,
)
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
        # The encoding is looked up when the first command is packed.
        ("redis://cache.invalid:6379/0?encoding={secret}", "LookupError"),
        ("unix:///var/run/redis.sock?encoding={secret}", "LookupError"),
    ],
    ids=[
        "ssl-cert-reqs",
        "unknown-option",
        "port-option",
        "encoding-tcp",
        "encoding-unix",
    ],
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


@pytest.fixture(name="short_dir")
def short_dir_fixture() -> Iterator[str]:
    """Return a short temporary directory: local socket paths are length-limited."""
    with tempfile.TemporaryDirectory(prefix="px-") as directory:
        yield directory


def _unresolvable(*_args: object, **_kwargs: object) -> NoReturn:
    """Fail name resolution as the resolver does for an unknown host."""
    raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")


@pytest.mark.parametrize("target", ["host", "socket-path"])
def test_redis_connection_failure_fails_open_and_logs_class_only(
    target: str,
    short_dir: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request admitted after a failed connection logs the class only."""
    secret = _secret()
    if target == "host":
        # The resolver is stubbed; the client's error quotes host and port.
        monkeypatch.setattr(socket, "getaddrinfo", _unresolvable)
        url = f"redis://{secret}.invalid:6379/0"
        cause = f"socket.gaierror, errno {socket.EAI_NONAME}"
    else:
        # A local socket path that does not exist; the error quotes the path.
        url = f"unix://{short_dir}/{secret}/redis.sock"
        cause = f"FileNotFoundError, errno {errno.ENOENT}"
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
    assert_that(failures[0]).ends_with(
        f": redis.exceptions.ConnectionError (cause {cause})",
    )
    assert_that(failures[0]).contains("failing open")
    assert_that(failures[0]).does_not_contain(short_dir)


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


class _FailingClient:
    """Stand-in whose script and scan both fail with the given error."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def eval(self, *_args: object, **_kwargs: object) -> NoReturn:
        """Fail the limiter's script."""
        raise self.error

    def scan_iter(self, *_args: object, **_kwargs: object) -> NoReturn:
        """Fail the cleanup scan."""
        raise self.error


def _lock_error(secret: str) -> Exception:
    """A Redis error that is also a ValueError."""
    error: Exception = redis.exceptions.LockError(f"lock {secret} not owned")
    return error


def _connection_error_without_errno(secret: str) -> Exception:
    """A connection error whose operating-system cause has no ``errno``."""
    error: Exception = redis.ConnectionError(f"Error connecting to {secret}")
    error.__cause__ = OSError(f"AF_UNIX path too long: {secret}")
    return error


@pytest.mark.parametrize(
    ("make_error", "described"),
    [
        (_lock_error, "redis.exceptions.LockError"),
        (
            _connection_error_without_errno,
            "redis.exceptions.ConnectionError (cause OSError)",
        ),
    ],
    ids=["lock-error", "cause-without-errno"],
)
def test_redis_error_fails_open_described_without_text(
    make_error: Callable[[str], Exception],
    described: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every Redis error fails open, even one that is also a ValueError."""
    secret = _secret()
    limiter = RedisSlidingWindowRateLimiter(
        _FailingClient(make_error(secret)),
        max_requests=2,
        window_seconds=60.0,
    )

    with caplog.at_level(logging.DEBUG):
        decision = limiter.check("client")
        limiter.reset()

    assert_that(decision.allowed).is_true()
    assert_that(decision.remaining).is_equal_to(2)
    assert_that(caplog.messages).is_equal_to(
        [
            "rate limiter redis check failed; failing open for key=client: "
            + described,
            f"rate limiter redis reset failed; ignoring: {described}",
        ],
    )
    _assert_absent(caplog.text, secret)


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
    clients: list[redis.Redis] = []
    real_from_url: Callable[..., redis.Redis] = redis.Redis.from_url

    def recording_from_url(given: str, **kwargs: object) -> redis.Redis:
        """Record the arguments, then build the real client."""
        calls.append((given, kwargs))
        clients.append(real_from_url(given, **kwargs))
        return clients[-1]

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(recording_from_url))

    limiter = build_rate_limiter(_redis_settings(url))

    assert_that(limiter).is_instance_of(RedisSlidingWindowRateLimiter)
    assert_that(calls).is_equal_to(
        [(url, {"socket_connect_timeout": 0.5, "socket_timeout": 0.25})],
    )
    # The option check's connection never enters the client's pool.
    pool_state = vars(clients[0].connection_pool)
    assert_that(pool_state["_created_connections"]).is_equal_to(0)
    assert_that(list(pool_state["_available_connections"])).is_empty()
    assert_that(list(pool_state["_in_use_connections"])).is_empty()


# Replies by command name: the handshake, and the limiter's script admitting
# the request as the first hit of the window.
_PEER_REPLIES = {
    b"HELLO": b"%1\r\n+proto\r\n:3\r\n",
    b"EVAL": b"*4\r\n:1\r\n:1\r\n$1\r\n0\r\n$1\r\n0\r\n",
}


def _answer(connection: socket.socket) -> None:
    """Answer every command in each chunk the client sends, until it closes."""
    connection.settimeout(2.0)
    with connection:
        while True:
            try:
                received = connection.recv(65536)
            except OSError:
                return
            if not received:
                return
            # Each command is "*<count>\r\n$<length>\r\n<NAME>\r\n...".
            replies = [
                _PEER_REPLIES.get(
                    (command.split(b"\r\n") + [b"", b"", b""])[2],
                    b"+OK\r\n",
                )
                for command in received.split(b"*")[1:]
            ]
            try:
                connection.sendall(b"".join(replies))
            except OSError:
                return


def _start_peer(listener: socket.socket) -> None:
    """Serve a local stand-in Redis server for the limiter's commands.

    It listens on the loopback interface or a local socket only. It answers
    the handshake, answers the limiter's script with an admitting window and
    every other command with ``+OK``, each connection on its own thread until
    the listener closes.
    """
    listener.listen()

    def accept() -> None:
        """Hand each connection to its own thread."""
        while True:
            try:
                connection, _address = listener.accept()
            except OSError:
                return
            threading.Thread(target=_answer, args=(connection,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()


@pytest.fixture(name="peer_urls")
def peer_urls_fixture(short_dir: str) -> Iterator[dict[str, str]]:
    """Start loopback and local-socket peers; return their base URLs."""
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.bind(("127.0.0.1", 0))
    port: int = tcp.getsockname()[1]
    local = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    local.bind(f"{short_dir}/redis.sock")
    _start_peer(tcp)
    _start_peer(local)
    try:
        yield {
            "tcp": f"redis://127.0.0.1:{port}/0",
            "tls": f"rediss://127.0.0.1:{port}/0",
            "unix": f"unix://{short_dir}/redis.sock",
        }
    finally:
        tcp.close()
        local.close()


# A TLS version number no TLS library defines; the client prints it in hex.
_TLS_VERSION = 0x5EC7E7


@pytest.mark.parametrize(
    ("peer", "options", "failure"),
    [
        ("tcp", "encoding={secret}", "LookupError"),
        ("unix", "encoding={secret}", "LookupError"),
        ("tls", f"ssl_min_version={_TLS_VERSION}", "ValueError"),
    ],
    ids=["encoding-tcp", "encoding-unix", "tls-version"],
)
def test_option_rejected_on_first_use_stops_start_up_by_class_only(
    peer: str,
    options: str,
    failure: str,
    peer_urls: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Options the client accepts but rejects on first use stop start-up."""
    secret = _secret()

    with caplog.at_level(logging.DEBUG), pytest.raises(UnusableSettingError) as caught:
        create_app(
            _redis_settings(f"{peer_urls[peer]}?{options.format(secret=secret)}"),
        )

    _assert_reported(
        caught.value,
        setting=_REDIS_SETTING,
        failure=failure,
        secret=secret,
    )
    _assert_absent(caplog.text, secret)
    _assert_absent(caplog.text + _printed(caught.value), f"{_TLS_VERSION:x}")


@pytest.mark.parametrize(
    ("peer", "options", "failure"),
    [
        ("tcp", "encoding={secret}", "LookupError"),
        ("unix", "encoding={secret}", "LookupError"),
        ("tls", f"ssl_min_version={_TLS_VERSION}", "ValueError"),
    ],
    ids=["encoding-tcp", "encoding-unix", "tls-version"],
)
def test_client_fault_on_a_request_fails_it_by_class_only(
    peer: str,
    options: str,
    failure: str,
    peer_urls: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A client built without the start-up check fails closed, unquoted.

    The limiter is given the client directly, as a deployment would have it if
    an option slipped past the start-up check: the request still fails, as it
    did, but its error names the class only.
    """
    secret = _secret()
    url = f"{peer_urls[peer]}?{options.format(secret=secret)}"
    limiter = RedisSlidingWindowRateLimiter(
        redis.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5),
        max_requests=1,
        window_seconds=60.0,
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(RateLimitStoreError) as caught:
        limiter.check("client")

    assert_that(str(caught.value)).is_equal_to(
        f"rate limiter redis check failed: {failure}",
    )
    assert_that(caught.value.__cause__).is_none()
    assert_that(caught.value.__context__).is_none()
    printed = _printed(caught.value) + caplog.text
    _assert_absent(printed, secret)
    _assert_absent(printed, f"{_TLS_VERSION:x}")


@pytest.mark.parametrize(
    ("peer", "options"),
    [
        ("tcp", "encoding=utf-8&db=2"),
        ("unix", "encoding=latin-1"),
    ],
)
def test_valid_options_reach_the_peer_and_admit(
    peer: str,
    options: str,
    peer_urls: dict[str, str],
) -> None:
    """With valid options the first request is evaluated by the peer as before."""
    limiter = build_rate_limiter(_redis_settings(f"{peer_urls[peer]}?{options}"))

    decision = limiter.check("client")

    assert_that(decision.allowed).is_true()
    assert_that(decision.remaining).is_equal_to(limiter.max_requests - 1)


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
        "backend": TranscriptStorageSettings(
            storage_backend=f"encrypted_{secret}",
            encryption_key=SecretStr(_key()),
        ),
        "bucket": TranscriptStorageSettings(
            storage_backend="encrypted_s3",
            encryption_key=SecretStr(_key()),
            s3_bucket=f"private/{secret}",
            s3_region_name="auto",
        ),
        # A trailing newline, as read from a secrets file.
        "access-key-id": TranscriptStorageSettings(
            storage_backend="encrypted_s3",
            encryption_key=SecretStr(_key()),
            s3_bucket="private-transcripts",
            s3_region_name="auto",
            s3_access_key_id=SecretStr(f"{secret}\n"),
            s3_secret_access_key=SecretStr(_ACCESS_KEY),
        ),
        "secret-access-key": TranscriptStorageSettings(
            storage_backend="encrypted_s3",
            encryption_key=SecretStr(_key()),
            s3_bucket="private-transcripts",
            s3_region_name="auto",
            s3_access_key_id=SecretStr(_ACCESS_ID),
            s3_secret_access_key=SecretStr(f"{secret}\r"),
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
    "backend": ("PODEX_TRANSCRIPTS__STORAGE_BACKEND", "ValueError"),
    "bucket": (
        "PODEX_TRANSCRIPTS__S3_BUCKET",
        "botocore.exceptions.ParamValidationError",
    ),
    "access-key-id": ("PODEX_TRANSCRIPTS__S3_ACCESS_KEY_ID", "ValueError"),
    "secret-access-key": ("PODEX_TRANSCRIPTS__S3_SECRET_ACCESS_KEY", "ValueError"),
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


def pending_takedown(db: Session, *, episode_id: int) -> TakedownRequest:
    """Persist a pending takedown of the episode's raw transcript."""
    request = TakedownRequest(
        subject_type="episode",
        subject_id=episode_id,
        requester_type="creator",
        requester_name="Creator",
        requester_email="creator@example.com",
        basis="Remove this source",
        requested_actions_json=["suppress_raw_transcript"],
        status="pending",
    )
    db.add(request)
    db.commit()
    return request


def approve_takedown(client: TestClient, request: TakedownRequest) -> Any:
    """Approve the takedown through the ops route."""
    return client.post(
        f"/api/v2/ops/takedown-requests/{request.id}/decide",
        json={"status": "approved", "note": "Verified"},
        headers=_HEADERS,
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
    request = pending_takedown(
        db_session,
        episode_id=seed_catalog_graph(db_session).episode_id,
    )
    _use_settings(client, _storage_cases(secret, tmp_path)[case])

    with caplog.at_level(logging.DEBUG):
        response = approve_takedown(client, request)

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
