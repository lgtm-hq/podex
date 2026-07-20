"""Env-gated R2 integration smoke for the encrypted S3 artifact adapter.

Runs only when explicitly armed against a real (staging) Cloudflare R2
bucket; it is skipped entirely by default and therefore never runs in CI
and never affects coverage. Arm it with:

    PODEX_R2_SMOKE=1 \
    PODEX_TRANSCRIPTS__S3_BUCKET=<staging-bucket> \
    PODEX_TRANSCRIPTS__S3_ENDPOINT_URL=<R2 S3 API endpoint> \
    PODEX_TRANSCRIPTS__S3_ACCESS_KEY_ID=<token-key-id> \
    PODEX_TRANSCRIPTS__S3_SECRET_ACCESS_KEY=<token-secret> \
    uv run pytest tests/test_transcript_artifacts_r2_smoke.py

See the r2 runbook in lgtm-hq/podex-ops (runbooks/r2.md) for the full
staging smoke procedure (lgtm-hq/podex#300).
"""

import contextlib
import os
import uuid
from typing import Any

import botocore.exceptions
import pytest
from assertpy import assert_that
from cryptography.fernet import Fernet

from podex.services.transcript_artifacts import (
    EncryptedS3TranscriptArtifactStore,
    TranscriptArtifactStorageBackend,
)

_REQUIRED_ENV_VARS: tuple[str, ...] = (
    "PODEX_TRANSCRIPTS__S3_BUCKET",
    "PODEX_TRANSCRIPTS__S3_ENDPOINT_URL",
    "PODEX_TRANSCRIPTS__S3_ACCESS_KEY_ID",
    "PODEX_TRANSCRIPTS__S3_SECRET_ACCESS_KEY",
)


def _smoke_disabled_reason() -> str:
    """Return why the smoke is disabled, or an empty string when armed.

    Returns:
        Human-readable skip reason, or ``""`` when every gate is satisfied.
    """
    if os.environ.get("PODEX_R2_SMOKE") != "1":
        return "PODEX_R2_SMOKE=1 not set; R2 integration smoke is opt-in"
    missing = [name for name in _REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        return f"missing required R2 smoke environment variables: {missing}"
    return ""


pytestmark = pytest.mark.skipif(
    bool(_smoke_disabled_reason()),
    reason=_smoke_disabled_reason() or "R2 smoke armed",
)


@pytest.fixture(name="r2_store")
def r2_store_fixture() -> EncryptedS3TranscriptArtifactStore:
    """Build the adapter against the real R2 endpoint from the environment.

    Returns:
        Encrypted S3-compatible store configured for the staging bucket.
    """
    return EncryptedS3TranscriptArtifactStore(
        bucket=os.environ["PODEX_TRANSCRIPTS__S3_BUCKET"],
        encryption_key=os.environ.get(
            "PODEX_TRANSCRIPTS__ENCRYPTION_KEY",
            Fernet.generate_key().decode("ascii"),
        ),
        endpoint_url=os.environ["PODEX_TRANSCRIPTS__S3_ENDPOINT_URL"],
        region_name=os.environ.get("PODEX_TRANSCRIPTS__S3_REGION_NAME", "auto"),
        access_key_id=os.environ["PODEX_TRANSCRIPTS__S3_ACCESS_KEY_ID"],
        secret_access_key=os.environ["PODEX_TRANSCRIPTS__S3_SECRET_ACCESS_KEY"],
    )


def test_r2_put_get_delete_round_trip(
    r2_store: EncryptedS3TranscriptArtifactStore,
) -> None:
    """Round-trip one encrypted payload through the real R2 bucket."""
    storage_key = f"smoke/{uuid.uuid4()}/raw.json.enc"
    payload: dict[str, Any] = {
        "provider": "r2-smoke",
        "raw_text": "hello from the podex R2 smoke test",
        "segments": [{"start": 0.0, "end": 1.5, "text": "hello"}],
    }

    try:
        stored = r2_store.put_json(storage_key=storage_key, payload=payload)
        assert_that(stored.storage_key).is_equal_to(storage_key)
        assert_that(stored.storage_backend).is_equal_to(
            TranscriptArtifactStorageBackend.ENCRYPTED_S3.value,
        )
        assert_that(stored.byte_size).is_greater_than(0)

        assert_that(r2_store.get_json(storage_key=storage_key)).is_equal_to(payload)
    except BaseException:
        # Best-effort cleanup that never masks the original test failure.
        with contextlib.suppress(Exception):
            r2_store.delete(storage_key=storage_key)
        raise
    else:
        r2_store.delete(storage_key=storage_key)

    with pytest.raises(botocore.exceptions.ClientError) as excinfo:
        r2_store.get_json(storage_key=storage_key)
    assert_that(excinfo.value.response["Error"]["Code"]).is_equal_to("NoSuchKey")
