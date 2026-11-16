"""What DEBUG records print for one storage request, with the log boundary on.

With DEBUG enabled the storage client logs its request line, the prepared
request and its signing steps. The boundary (ADR 0010) rebuilds those records
from allowlisted parts. This pins, for each field of a storage request, whether
it is printed: the endpoint host and path, the bucket, the object key and the
signing scope's region are; URL userinfo, the access key id, the secret, the
session token, the signature, the encryption key and its key id never are.
The store's requests carry no query string. A distinct sentinel stands for
each field; nothing reaches the network.
"""

import logging
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from secrets import token_hex
from typing import Any, NoReturn

import boto3
import pytest
from assertpy import assert_that
from pydantic import SecretStr

from podex.config import Settings, TranscriptStorageSettings
from podex.logging_config import install_log_boundaries, redact_secret_values
from podex.services.transcript_artifacts import (
    EncryptedS3TranscriptArtifactStore,
    TranscriptStorageError,
    build_transcript_artifact_store,
)
from tests.test_first_use_settings import _key


@pytest.fixture(name="debug_storage_logs")
def debug_storage_logs_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> Iterator[pytest.LogCaptureFixture]:
    """Install the boundary, capture DEBUG records, keep the client offline.

    No configured secret values are set, so what is withheld is withheld by the
    boundary's rules, not by matching a known value.
    """
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-creds"))
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setattr(boto3, "DEFAULT_SESSION", None)

    def unresolvable(*_args: object, **_kwargs: object) -> NoReturn:
        """Fail name resolution, after the request is prepared and signed."""
        raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", unresolvable)
    install_log_boundaries()
    redact_secret_values([])
    try:
        with caplog.at_level(logging.DEBUG):
            yield caplog
    finally:
        redact_secret_values([])


def _sentinels() -> dict[str, str]:
    """Return one distinct value per request field."""
    suffix = token_hex(4)
    return {
        "host": f"hostsentinel{suffix}",
        "path": f"pathsentinel{suffix}",
        "user": f"usersentinel{suffix}",
        "password": f"passwordsentinel{suffix}",
        "bucket": f"bucketsentinel{suffix}",
        "key": f"objectsentinel{suffix}",
        "region": f"regionsentinel{suffix}",
        "access-key-id": f"AKIDSENTINEL{suffix.upper()}",
        "secret": f"secretsentinel{suffix}",
        "session-token": f"tokensentinel{suffix}",
        "encryption-key": _key(),
    }


def _store(
    values: dict[str, str],
    credentials: str,
    monkeypatch: pytest.MonkeyPatch,
) -> EncryptedS3TranscriptArtifactStore:
    """Build the store from settings; credentials as settings or environment."""
    fields: dict[str, Any] = {
        "storage_backend": "encrypted_s3",
        "encryption_key": SecretStr(values["encryption-key"]),
        "s3_bucket": values["bucket"],
        "s3_endpoint_url": (
            f"https://{values['user']}:{values['password']}"
            f"@{values['host']}.example/{values['path']}"
        ),
        "s3_region_name": values["region"],
    }
    if credentials == "settings":
        fields["s3_access_key_id"] = SecretStr(values["access-key-id"])
        fields["s3_secret_access_key"] = SecretStr(values["secret"])
    else:
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", values["access-key-id"])
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", values["secret"])
        monkeypatch.setenv("AWS_SESSION_TOKEN", values["session-token"])
    store = build_transcript_artifact_store(
        settings=Settings(transcripts=TranscriptStorageSettings(**fields)),
    )
    if not isinstance(store, EncryptedS3TranscriptArtifactStore):
        raise AssertionError("Expected the S3 store")
    return store


def _request_each_way(store: EncryptedS3TranscriptArtifactStore, key: str) -> list[str]:
    """Put, get and delete ``key``; return the signature of each request."""
    signatures: list[str] = []

    def capture_signature(request: Any, **_kwargs: Any) -> None:
        """Keep the signature the client computed for this request."""
        authorization = str(request.headers["Authorization"])
        signatures.append(authorization.rsplit("Signature=", 1)[1])

    # The real client, which has an event system the store's port does not.
    client: Any = store.client
    client.meta.events.register("before-send.s3", capture_signature)
    operations: list[Callable[[], object]] = [
        lambda: store.put_json(storage_key=key, payload={}),
        lambda: store.get_json(storage_key=key),
        lambda: store.delete(storage_key=key),
    ]
    for operation in operations:
        with pytest.raises(TranscriptStorageError):
            operation()
    return signatures


# Printed by the storage client's DEBUG records, after the boundary.
_PRINTED_FIELDS = ("host", "path", "bucket", "key", "region")
# Never printed.
_WITHHELD_FIELDS = (
    "user",
    "password",
    "access-key-id",
    "secret",
    "session-token",
    "encryption-key",
)


@pytest.mark.parametrize("credentials", ["settings", "environment"])
def test_debug_records_print_only_the_allowlisted_request_fields(
    credentials: str,
    debug_storage_logs: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each store request prints its location, never its credentials."""
    values = _sentinels()
    store = _store(values, credentials, monkeypatch)

    signatures = _request_each_way(store, values["key"])

    logged = debug_storage_logs.text
    assert_that(signatures).is_length(3)
    for field in _PRINTED_FIELDS:
        assert_that(logged).contains(values[field])
    for value in [
        *(values[field] for field in _WITHHELD_FIELDS),
        store.key_id,
        *signatures,
    ]:
        assert_that(logged).does_not_contain(value)
    request_lines = [line for line in logged.splitlines() if values["key"] in line]
    assert_that(request_lines).is_not_empty()
    for line in request_lines:
        assert_that(line.split(values["key"], 1)[1].startswith("?")).is_false()
