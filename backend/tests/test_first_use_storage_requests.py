"""Storage request failures and refused storage settings withhold configuration.

The object storage client's own errors quote its configuration when a bucket
request fails on the client side: the bucket name, the whole signed
``Authorization`` header, or the endpoint URL with any userinfo. Each request
of the encrypted S3 store reports such an error by its class only, and the ops
routes answer as they did (500, with the opaque body). A server's error
response propagates unchanged.

Settings now checked when the store is built (endpoint shape, bucket name,
control characters in the static credentials) refuse ops actions even when the
action would not have reached the bucket; those cases are pinned here too.
Nothing reaches the network: name resolution is stubbed and AWS files and
region are isolated.
"""

import logging
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import boto3
import pytest
from assertpy import assert_that
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    HTTPClientError,
    ParamValidationError,
)
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from podex.config import Settings, TranscriptStorageSettings
from podex.models import TakedownRequest, Transcript, TranscriptArtifact
from podex.services.transcript_artifacts import (
    AMBIENT_REGION_SOURCE,
    EncryptedS3TranscriptArtifactStore,
    TranscriptStorageError,
    build_transcript_artifact_store,
)
from podex.unusable_settings import UnusableSettingError
from tests.conftest import seed_catalog_graph
from tests.test_first_use_settings import (
    _ACCESS_ID,
    _ACCESS_KEY,
    _HEADERS,
    _assert_absent,
    _assert_reported,
    _key,
    _printed,
    _secret,
    _use_settings,
    approve_takedown,
    pending_takedown,
)

_BUCKET = "private-transcripts"
_STORAGE_KEY = "transcripts/1/1/raw.json.enc"


@pytest.fixture(name="offline_aws")
def offline_aws_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate AWS files and region; every host name is unresolvable."""
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-creds"))
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")

    def unresolvable(*_args: object, **_kwargs: object) -> NoReturn:
        """Fail name resolution as the resolver does for an unknown host."""
        raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", unresolvable)


def _real_store(
    *,
    endpoint_url: str = "https://storage.example",
    access_key_id: str = _ACCESS_ID,
) -> EncryptedS3TranscriptArtifactStore:
    """Return a store over a real storage client built without the store's checks."""
    client: Any = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name="auto",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=_ACCESS_KEY,
    )
    return EncryptedS3TranscriptArtifactStore(
        bucket=_BUCKET,
        encryption_key=_key(),
        client=client,
    )


def _operations(
    store: EncryptedS3TranscriptArtifactStore,
) -> dict[str, Callable[[], object]]:
    """Return one call per store request."""
    return {
        "get": lambda: store.get_json(storage_key=_STORAGE_KEY),
        "put": lambda: store.put_json(storage_key=_STORAGE_KEY, payload={}),
        "delete": lambda: store.delete(storage_key=_STORAGE_KEY),
    }


@pytest.mark.usefixtures("offline_aws")
@pytest.mark.parametrize("operation", ["get", "put", "delete"])
@pytest.mark.parametrize(
    ("case", "failure"),
    [
        # A bucket name the client rejects on every request, quoting it.
        ("bucket", "botocore.exceptions.ParamValidationError"),
        # A trailing newline in the key id: the HTTP layer quotes the header.
        ("access-key-id", "botocore.exceptions.HTTPClientError"),
        # An unreachable endpoint, quoted in full with its userinfo.
        ("endpoint", "botocore.exceptions.EndpointConnectionError"),
    ],
)
def test_client_side_request_failure_names_class_only(
    case: str,
    failure: str,
    operation: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real client's own request errors are reported by class only."""
    secret = _secret()
    if case == "bucket":
        store = _real_store()
        # Past the store's own check, as a bucket the client rejects would be.
        store.bucket = f"private/{secret}"
    elif case == "access-key-id":
        store = _real_store(
            endpoint_url="http://127.0.0.1:9",
            access_key_id=f"{secret}\n",
        )
    else:
        store = _real_store(endpoint_url=f"https://user:{secret}@{secret}.example")

    with (
        caplog.at_level(logging.INFO),
        pytest.raises(TranscriptStorageError) as caught,
    ):
        _operations(store)[operation]()

    assert_that(str(caught.value)).is_equal_to(
        f"Transcript artifact storage request failed: {failure}",
    )
    assert_that(caught.value.__cause__).is_none()
    assert_that(caught.value.__context__).is_none()
    _assert_absent(_printed(caught.value), secret)
    _assert_absent(caplog.text, secret)


class _RejectingClient:
    """Storage client stand-in answering every request with a server error."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def put_object(self, **_kwargs: object) -> NoReturn:
        """Fail as the server answered."""
        raise self.error

    def delete_object(self, **_kwargs: object) -> NoReturn:
        """Fail as the server answered."""
        raise self.error

    def get_object(self, **_kwargs: object) -> NoReturn:
        """Fail as the server answered."""
        raise self.error


@pytest.mark.parametrize("operation", ["get", "put", "delete"])
def test_server_error_response_propagates_unchanged(operation: str) -> None:
    """A server's error response is not a client-side error and is not rewrapped."""
    error = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "The key does not exist."}},
        "GetObject",
    )
    store = EncryptedS3TranscriptArtifactStore(
        bucket=_BUCKET,
        encryption_key=_key(),
        client=_RejectingClient(error),
    )

    with pytest.raises(ClientError) as caught:
        _operations(store)[operation]()

    assert_that(caught.value).is_same_as(error)


@pytest.mark.parametrize(
    "error_type",
    [ParamValidationError, HTTPClientError, EndpointConnectionError],
)
def test_store_error_is_not_caught_as_a_value_or_runtime_error(
    error_type: type[Exception],
) -> None:
    """Callers that catch ValueError, RuntimeError or OSError behave as before.

    The client's errors derive from none of these, so a purge's verification
    step and the ops routes let them through (a 500). The replacement must
    not change that.
    """
    assert_that(issubclass(error_type, (ValueError, RuntimeError, OSError))).is_false()
    assert_that(
        issubclass(TranscriptStorageError, (ValueError, RuntimeError, OSError)),
    ).is_false()


def _pending_takedown(db: Session, *, with_artifact: bool) -> TakedownRequest:
    """Persist a pending raw-transcript takedown, optionally with a stored object."""
    graph = seed_catalog_graph(db)
    if with_artifact:
        transcript = Transcript(
            episode_id=graph.episode_id,
            provider="podscripts",
            raw_text="private source text",
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        db.add(transcript)
        db.flush()
        db.add(
            TranscriptArtifact(
                transcript_id=transcript.id,
                episode_id=graph.episode_id,
                storage_key=_STORAGE_KEY,
                storage_backend="encrypted_s3",
                encryption_key_id="0" * 16,
                byte_size=1,
                provider="podscripts",
                source_text_hash="0" * 64,
                content_type="application/json",
                stored_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
    return pending_takedown(db, episode_id=graph.episode_id)


def _unreachable_storage(secret: str) -> TranscriptStorageSettings:
    """Return storage settings whose endpoint, with userinfo, cannot be reached."""
    return TranscriptStorageSettings(
        storage_backend="encrypted_s3",
        encryption_key=SecretStr(_key()),
        s3_bucket=_BUCKET,
        s3_endpoint_url=f"https://user:{secret}@{secret}.example",
        s3_region_name="auto",
        s3_access_key_id=SecretStr(_ACCESS_ID),
        s3_secret_access_key=SecretStr(_ACCESS_KEY),
    )


@pytest.mark.usefixtures("offline_aws")
def test_unreachable_endpoint_answers_takedown_with_opaque_500(
    client: TestClient,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The route answers 500 as before, with the opaque body."""
    secret = _secret()
    request = _pending_takedown(db_session, with_artifact=True)
    _use_settings(client, _unreachable_storage(secret))
    opaque = TestClient(client.app, raise_server_exceptions=False)

    with caplog.at_level(logging.INFO):
        response = approve_takedown(opaque, request)

    assert_that(response.status_code).is_equal_to(500)
    assert_that(response.json()["detail"]).is_equal_to("Internal server error.")
    _assert_absent(response.text, secret)
    _assert_absent(caplog.text, secret)


@pytest.mark.usefixtures("offline_aws")
def test_unreachable_endpoint_server_error_record_holds_no_text(
    client: TestClient,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The error the server logs for that request names the class only."""
    secret = _secret()
    request = _pending_takedown(db_session, with_artifact=True)
    _use_settings(client, _unreachable_storage(secret))

    with (
        caplog.at_level(logging.INFO),
        pytest.raises(TranscriptStorageError) as caught,
    ):
        approve_takedown(client, request)

    assert_that(str(caught.value)).is_equal_to(
        "Transcript artifact storage request failed: "
        "botocore.exceptions.EndpointConnectionError",
    )
    # The framework re-raises it from its own task group, which becomes its
    # cause; the client's error is nowhere in the chain.
    assert_that(
        [error for error in _chain(caught.value) if isinstance(error, BotoCoreError)],
    ).is_empty()
    _assert_absent(_printed(caught.value), secret)
    _assert_absent(caplog.text, secret)


def _chain(error: BaseException) -> list[BaseException]:
    """Return every exception linked from ``error``: causes, contexts, members."""
    seen: list[BaseException] = []
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if any(current is known for known in seen):
            continue
        seen.append(current)
        pending.extend(
            linked
            for linked in (current.__cause__, current.__context__)
            if linked is not None
        )
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
    return seen


@pytest.mark.usefixtures("offline_aws")
def test_ambient_region_names_its_source_and_class_only(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A region from the AWS environment is reported without its text."""
    secret = _secret()
    monkeypatch.setenv("AWS_DEFAULT_REGION", f"{secret}/west")
    transcripts = TranscriptStorageSettings(
        storage_backend="encrypted_s3",
        encryption_key=SecretStr(_key()),
        s3_bucket=_BUCKET,
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(UnusableSettingError) as caught:
        build_transcript_artifact_store(settings=Settings(transcripts=transcripts))
    _use_settings(client, transcripts)
    response = client.post("/api/v2/ops/retention/1/purge", headers=_HEADERS)

    _assert_reported(
        caught.value,
        setting=AMBIENT_REGION_SOURCE,
        failure="botocore.exceptions.InvalidRegionError",
        secret=secret,
    )
    assert_that(response.status_code).is_equal_to(409)
    assert_that(response.json()["detail"]).starts_with(AMBIENT_REGION_SOURCE)
    _assert_absent(response.text, secret)
    _assert_absent(caplog.text, secret)


# Settings refused when the store is built, although each could only fail a
# request that reaches the bucket.
_REFUSED_BEFORE_ANY_REQUEST: dict[str, dict[str, Any]] = {
    "endpoint-query": {"s3_endpoint_url": "https://storage.example/?token=x"},
    "endpoint-port": {"s3_endpoint_url": "https://storage.example:port"},
    "endpoint-scheme": {"s3_endpoint_url": "ftp://storage.example"},
    "bucket": {"s3_bucket": "private/transcripts"},
    "access-key-id": {
        "s3_access_key_id": SecretStr(f"{_ACCESS_ID}\n"),
        "s3_secret_access_key": SecretStr(_ACCESS_KEY),
    },
}


def _refused_storage(case: str) -> TranscriptStorageSettings:
    """Return storage settings with one value refused when the store is built."""
    fields: dict[str, Any] = {
        "storage_backend": "encrypted_s3",
        "encryption_key": SecretStr(_key()),
        "s3_bucket": _BUCKET,
        "s3_region_name": "auto",
    }
    fields.update(_REFUSED_BEFORE_ANY_REQUEST[case])
    return TranscriptStorageSettings(**fields)


@pytest.mark.usefixtures("offline_aws")
@pytest.mark.parametrize("case", list(_REFUSED_BEFORE_ANY_REQUEST))
def test_refused_setting_blocks_takedown_without_stored_objects(
    case: str,
    client: TestClient,
    db_session: Session,
) -> None:
    """Behaviour change: an approval that needs no bucket request is refused.

    Before these checks such a takedown (no retained object) was approved with
    a 200; now the store cannot be built, the route answers 409 and the request
    stays pending.
    """
    request = _pending_takedown(db_session, with_artifact=False)
    _use_settings(client, _refused_storage(case))

    response = approve_takedown(client, request)

    assert_that(response.status_code).is_equal_to(409)
    assert_that(response.json()["detail"]).contains(" is not usable: ")
    db_session.expire_all()
    assert_that(db_session.get(TakedownRequest, request.id)).has_status("pending")


@pytest.mark.usefixtures("offline_aws")
@pytest.mark.parametrize("case", list(_REFUSED_BEFORE_ANY_REQUEST))
def test_refused_setting_answers_purge_of_missing_transcript_with_409(
    case: str,
    client: TestClient,
) -> None:
    """Behaviour change: the store is built first, so 409 replaces the 404."""
    _use_settings(client, _refused_storage(case))

    response = client.post("/api/v2/ops/retention/999999/purge", headers=_HEADERS)

    assert_that(response.status_code).is_equal_to(409)
    assert_that(response.json()["detail"]).contains(" is not usable: ")
