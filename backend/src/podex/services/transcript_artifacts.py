"""Private storage commands for encrypted raw transcript artifacts."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, auto
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, TypeVar
from urllib.parse import urlsplit

import boto3
from botocore.exceptions import BotoCoreError, ParamValidationError
from botocore.handlers import validate_bucket_name
from botocore.utils import (
    is_valid_endpoint_url,
    is_valid_ipv6_endpoint_url,
    validate_region_name,
)
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from podex.config import Settings
from podex.models import Episode, Transcript, TranscriptArtifact, TranscriptDigest
from podex.sdk_log_rules import class_name
from podex.services.transcript_acquisition import (
    TranscriptAcquisitionResult,
    TranscriptResult,
)
from podex.services.transcript_retention_policies import (
    apply_stored_acquisition_opt_out,
)
from podex.unusable_settings import without_setting_text

RAW_TRANSCRIPT_CONTENT_TYPE = "application/vnd.podex.transcript+json"
ARTIFACT_VERIFICATION_FAILED = "Retained transcript artifact failed source verification"
# Where everything the storage client reads beyond the store's own settings
# comes from: AWS environment variables and shared configuration (endpoint,
# region, profile, configuration files, retry and checksum modes).
AMBIENT_CONFIGURATION_SOURCE = "The storage client's environment configuration"
# What building the client raises for a value it read from there: its own
# configuration errors, and builtin errors from parsing a value (an endpoint,
# a number). Each can quote the value.
_AMBIENT_CONFIGURATION_FAILURES: tuple[type[Exception], ...] = (
    BotoCoreError,
    LookupError,
    OSError,
    TypeError,
    ValueError,
)
# What a storage request raises on the client side, besides a server's error
# response: the client's own errors, and builtin errors from preparing or
# signing the request, which carry request or credential text (an encoding
# error holds the whole signing key in its arguments).
_REQUEST_FAILURES: tuple[type[Exception], ...] = (
    BotoCoreError,
    AttributeError,
    LookupError,
    OSError,
    TypeError,
    ValueError,
)

ResultT = TypeVar("ResultT")


class TranscriptStorageError(Exception):
    """A storage request failed in the client; reported by the error class only.

    The storage client's own errors quote its configuration: the bucket name,
    the endpoint URL with any userinfo, or the whole ``Authorization`` header.
    This class derives from ``Exception`` only, as the client's own errors do;
    a failure that was also a builtin ``ValueError``, ``TypeError``,
    ``LookupError`` or ``OSError`` is replaced by the subclass of the same
    kind, so every handler treats the replacement as it treated the original.
    """


class TranscriptStorageValueError(TranscriptStorageError, ValueError):
    """A storage request failure that was a ``ValueError``."""


class TranscriptStorageTypeError(TranscriptStorageError, TypeError):
    """A storage request failure that was a ``TypeError``."""


class TranscriptStorageLookupError(TranscriptStorageError, LookupError):
    """A storage request failure that was a ``LookupError``."""


class TranscriptStorageOSError(TranscriptStorageError, OSError):
    """A storage request failure that was an ``OSError``."""


# Checked in order: the first builtin kind the failure is, picks the
# replacement; a failure of none of them gets the base class.
_REPLACEMENT_KINDS: tuple[tuple[type[Exception], type[TranscriptStorageError]], ...] = (
    (OSError, TranscriptStorageOSError),
    (ValueError, TranscriptStorageValueError),
    (TypeError, TranscriptStorageTypeError),
    (LookupError, TranscriptStorageLookupError),
)


class TranscriptArtifactStorageBackend(StrEnum):
    """Supported private raw transcript storage adapters."""

    ENCRYPTED_FILESYSTEM = auto()
    ENCRYPTED_S3 = auto()


@dataclass(frozen=True, slots=True)
class StoredTranscriptArtifactData:
    """Metadata returned when an encrypted object is written."""

    storage_key: str
    storage_backend: str
    encryption_key_id: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class TranscriptReacquisitionData:
    """New hot transcript and stored payload created from a purged asset."""

    transcript: Transcript
    artifact: TranscriptArtifact
    prior_digest: TranscriptDigest


@dataclass(frozen=True, slots=True)
class TranscriptProcessingPayloadData:
    """Decrypted raw input required by cleanup and extraction stages."""

    raw_text: str
    segments: list[dict[str, Any]]


class TranscriptArtifactStore(Protocol):
    """Port for private encrypted raw transcript object storage."""

    def put_json(
        self,
        *,
        storage_key: str,
        payload: dict[str, Any],
    ) -> StoredTranscriptArtifactData:
        """Write an encrypted JSON payload and return object metadata."""
        ...

    def get_json(self, *, storage_key: str) -> dict[str, Any]:
        """Read and decrypt one JSON payload."""
        ...

    def delete(self, *, storage_key: str) -> None:
        """Delete a stored encrypted payload if it exists."""
        ...


class S3ObjectClient(Protocol):
    """Minimum S3 client contract required by encrypted artifact storage."""

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        """Write one encrypted object to a private bucket."""
        ...

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        """Delete one encrypted object from a private bucket."""
        ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        """Read one encrypted object from a private bucket."""
        ...


class TranscriptAcquisitionClient(Protocol):
    """Port for obtaining a fresh provider transcript during re-acquisition."""

    def acquire(self, episode: Episode) -> TranscriptAcquisitionResult:
        """Acquire a transcript result for one episode."""
        ...


class EncryptedFilesystemTranscriptArtifactStore:
    """Encrypted local object adapter for development and single-host deploys.

    Args:
        root_path: Directory under which encrypted transcript objects live.
        encryption_key: URL-safe Fernet key supplied from secure configuration.
    """

    def __init__(
        self,
        *,
        root_path: Path,
        encryption_key: str,
    ) -> None:
        if not encryption_key:
            raise ValueError("PODEX_TRANSCRIPTS__ENCRYPTION_KEY must be configured")
        self.root_path = root_path
        key = _encryption_key_bytes(encryption_key)
        self.fernet = Fernet(key)
        self.key_id = sha256(key).hexdigest()[:16]

    def put_json(
        self,
        *,
        storage_key: str,
        payload: dict[str, Any],
    ) -> StoredTranscriptArtifactData:
        """Encrypt and persist one transcript JSON object."""
        object_path = self._object_path(storage_key=storage_key)
        object_path.parent.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ciphertext = self.fernet.encrypt(plaintext)
        object_path.write_bytes(ciphertext)
        return StoredTranscriptArtifactData(
            storage_key=storage_key,
            storage_backend="encrypted_filesystem",
            encryption_key_id=self.key_id,
            byte_size=len(ciphertext),
        )

    def delete(self, *, storage_key: str) -> None:
        """Delete an encrypted object if it remains on disk."""
        self._object_path(storage_key=storage_key).unlink(missing_ok=True)

    def get_json(self, *, storage_key: str) -> dict[str, Any]:
        """Read and decrypt one local transcript JSON object."""
        ciphertext = self._object_path(storage_key=storage_key).read_bytes()
        return _decode_encrypted_payload(fernet=self.fernet, ciphertext=ciphertext)

    def _object_path(self, *, storage_key: str) -> Path:
        """Resolve a constrained relative object key under the storage root."""
        root = self.root_path.resolve()
        candidate = (root / storage_key).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("Transcript artifact storage key escapes storage root")
        return candidate


class EncryptedS3TranscriptArtifactStore:
    """Encrypted S3-compatible object adapter for hosted deployments.

    Args:
        bucket: Private bucket holding encrypted artifact objects.
        encryption_key: URL-safe Fernet key supplied from secure configuration.
        endpoint_url: Optional endpoint for S3-compatible providers.
        region_name: Optional S3 region.
        access_key_id: Optional static credential for S3-compatible providers.
        secret_access_key: Optional corresponding secret credential.
        client: Optional injected S3 client for tests.
    """

    # Keep the existing named command inputs compatible with callers.
    # pylint: disable-next=too-many-arguments
    def __init__(
        self,
        *,
        bucket: str,
        encryption_key: str,
        endpoint_url: str = "",
        region_name: str = "",
        access_key_id: str = "",
        secret_access_key: str = "",
        client: S3ObjectClient | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("PODEX_TRANSCRIPTS__S3_BUCKET must be configured")
        if not encryption_key:
            raise ValueError("PODEX_TRANSCRIPTS__ENCRYPTION_KEY must be configured")
        # The client's own bucket name check, which it applies to every
        # request and whose error quotes the name.
        without_setting_text(
            lambda: validate_bucket_name({"Bucket": bucket}),
            source="PODEX_TRANSCRIPTS__S3_BUCKET",
            failures=(ParamValidationError,),
        )
        self.bucket = bucket
        key = _encryption_key_bytes(encryption_key)
        self.fernet = Fernet(key)
        self.key_id = sha256(key).hexdigest()[:16]
        self.client = client or _build_s3_client(
            endpoint_url=endpoint_url,
            region_name=region_name,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )

    def put_json(
        self,
        *,
        storage_key: str,
        payload: dict[str, Any],
    ) -> StoredTranscriptArtifactData:
        """Encrypt and upload one transcript JSON object."""
        plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ciphertext = self.fernet.encrypt(plaintext)
        _storage_request(
            lambda: self.client.put_object(
                Bucket=self.bucket,
                Key=storage_key,
                Body=ciphertext,
                ContentType="application/octet-stream",
                Metadata={"podex-key-id": self.key_id},
            ),
        )
        return StoredTranscriptArtifactData(
            storage_key=storage_key,
            storage_backend=TranscriptArtifactStorageBackend.ENCRYPTED_S3.value,
            encryption_key_id=self.key_id,
            byte_size=len(ciphertext),
        )

    def delete(self, *, storage_key: str) -> None:
        """Delete an encrypted artifact object from its private bucket."""
        _storage_request(
            lambda: self.client.delete_object(Bucket=self.bucket, Key=storage_key),
        )

    def get_json(self, *, storage_key: str) -> dict[str, Any]:
        """Download and decrypt one S3-compatible transcript JSON object."""
        ciphertext = _storage_request(
            lambda: self._download(storage_key=storage_key),
        )
        if not isinstance(ciphertext, bytes):
            raise ValueError("Encrypted transcript artifact body must be bytes")
        return _decode_encrypted_payload(fernet=self.fernet, ciphertext=ciphertext)

    def _download(self, *, storage_key: str) -> object:
        """Request one object and read its body, which streams from the client."""
        response = self.client.get_object(Bucket=self.bucket, Key=storage_key)
        body = response["Body"]
        content: object = body.read() if hasattr(body, "read") else body
        return content


def build_transcript_artifact_store(
    *,
    settings: Settings,
) -> TranscriptArtifactStore | None:
    """Build the configured private artifact adapter when a key is supplied."""
    encryption_key = settings.transcripts.encryption_key.get_secret_value()
    if not encryption_key:
        return None
    configured_backend = settings.transcripts.storage_backend
    # The enum's error quotes the value.
    backend = without_setting_text(
        lambda: TranscriptArtifactStorageBackend(configured_backend),
        source="PODEX_TRANSCRIPTS__STORAGE_BACKEND",
        failures=(ValueError,),
    )
    if backend is TranscriptArtifactStorageBackend.ENCRYPTED_S3:
        return EncryptedS3TranscriptArtifactStore(
            bucket=settings.transcripts.s3_bucket,
            encryption_key=encryption_key,
            endpoint_url=settings.transcripts.s3_endpoint_url,
            region_name=settings.transcripts.s3_region_name,
            access_key_id=settings.transcripts.s3_access_key_id.get_secret_value(),
            secret_access_key=(
                settings.transcripts.s3_secret_access_key.get_secret_value()
            ),
        )
    return EncryptedFilesystemTranscriptArtifactStore(
        root_path=settings.transcripts.storage_path,
        encryption_key=encryption_key,
    )


def _build_s3_client(
    *,
    endpoint_url: str,
    region_name: str,
    access_key_id: str,
    secret_access_key: str,
) -> S3ObjectClient:
    """Build an S3 client using default credentials unless explicitly supplied.

    Raises:
        UnusableSettingError: When the endpoint or the region is malformed;
            the message names the setting and the error class only.
        ValueError: When only one of the two static credentials is supplied.
    """
    kwargs: dict[str, str] = {}
    if endpoint_url:
        without_setting_text(
            lambda: _check_storage_endpoint(endpoint_url),
            source="PODEX_TRANSCRIPTS__S3_ENDPOINT_URL",
            failures=(ValueError,),
        )
        kwargs["endpoint_url"] = endpoint_url
    if region_name:
        # The client's own region check; its error quotes the region.
        without_setting_text(
            lambda: validate_region_name(region_name),
            source="PODEX_TRANSCRIPTS__S3_REGION_NAME",
            failures=(ValueError,),
        )
        kwargs["region_name"] = region_name
    if access_key_id or secret_access_key:
        if not access_key_id or not secret_access_key:
            raise ValueError("S3 artifact credentials must be configured together")
        # The key id goes into a header: a control character (a trailing
        # newline from a secrets file) makes the HTTP layer quote the whole
        # signed header on every request. The secret is only signing input,
        # where any character works, but both are encoded as UTF-8 to sign,
        # and that error holds the whole signing key.
        without_setting_text(
            lambda: _check_header_credential(access_key_id),
            source="PODEX_TRANSCRIPTS__S3_ACCESS_KEY_ID",
            failures=(ValueError,),
        )
        without_setting_text(
            lambda: secret_access_key.encode("utf-8"),
            source="PODEX_TRANSCRIPTS__S3_SECRET_ACCESS_KEY",
            failures=(UnicodeEncodeError,),
        )
        kwargs["aws_access_key_id"] = access_key_id
        kwargs["aws_secret_access_key"] = secret_access_key
    # The store's own settings are checked above, so what building the client
    # can still fail on is what it reads from the AWS environment and shared
    # configuration (an endpoint, region, profile, configuration file, retry
    # or checksum mode), whose errors quote the value.
    client: S3ObjectClient = without_setting_text(
        lambda: boto3.client("s3", **kwargs),
        source=AMBIENT_CONFIGURATION_SOURCE,
        failures=_AMBIENT_CONFIGURATION_FAILURES,
    )
    return client


def _check_header_credential(value: str) -> None:
    """Require a credential sent in a header to be UTF-8 without control characters.

    Raises:
        ValueError: Naming neither the value nor the offending character.
    """
    encodable = True
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        encodable = False
    if not encodable or any(
        unicodedata.category(character) == "Cc" for character in value
    ):
        raise ValueError("Credential cannot be sent in a header")


def _storage_request(request: Callable[[], ResultT]) -> ResultT:
    """Run one storage client request, reporting client errors by class only.

    Args:
        request: The client call.

    Returns:
        What ``request`` returns.

    Raises:
        TranscriptStorageError: When the client raises one of its own errors
            (``BotoCoreError``: parameter validation, connection, endpoint
            resolution, HTTP client and credential errors) or a builtin error
            while preparing or signing the request, whose text or arguments
            quote configuration or credentials. It names the class only, keeps
            neither cause nor context, and is of the same builtin kind as the
            original (see ``_REPLACEMENT_KINDS``). A server's error response
            (``ClientError``) and anything that is not an ``Exception``
            (cancellation, interrupts, exit) propagate unchanged.
    """
    failure = ""
    replacement: type[TranscriptStorageError] = TranscriptStorageError
    try:
        return request()
    except _REQUEST_FAILURES as error:
        failure = class_name(error)
        replacement = next(
            (
                kind_replacement
                for kind, kind_replacement in _REPLACEMENT_KINDS
                if isinstance(error, kind)
            ),
            TranscriptStorageError,
        )
    # Outside the handler: the original error is not kept as context.
    raise replacement(f"Transcript artifact storage request failed: {failure}")


def _check_storage_endpoint(endpoint_url: str) -> None:
    """Apply the checks the S3 client makes before using an endpoint.

    Building the client requires a valid host name or IPv6 address; each
    bucket request then requires an ``http`` or ``https`` URL without a query
    whose port, if any, is a number (the S3 endpoint rules' URL check). The
    client's errors for these quote the endpoint; this raises ``ValueError``
    without it. ``urlsplit`` raises ``ValueError`` itself for a malformed IPv6
    address or port.
    """
    client_check: bool = bool(
        is_valid_endpoint_url(endpoint_url) or is_valid_ipv6_endpoint_url(endpoint_url),
    )
    parts = urlsplit(endpoint_url)
    well_formed = (
        client_check
        and parts.scheme in {"http", "https"}
        and not parts.query
        # Reading the port raises ValueError unless it is a number in range.
        and (parts.port is None or parts.port >= 0)
    )
    if not well_formed:
        raise ValueError("Invalid endpoint")


def _encryption_key_bytes(encryption_key: str) -> bytes:
    """Encode the configured encryption key as the cipher expects.

    Raises:
        UnusableSettingError: When the key is not ASCII; the encoding error
            would quote part of it, so only the setting and class are named.
    """
    encoded: bytes = without_setting_text(
        lambda: encryption_key.encode("ascii"),
        source="PODEX_TRANSCRIPTS__ENCRYPTION_KEY",
        failures=(UnicodeEncodeError,),
    )
    return encoded


def persist_transcript_acquisition(
    *,
    db: Session,
    episode: Episode,
    acquisition: TranscriptAcquisitionResult,
    artifact_store: TranscriptArtifactStore | None,
    reacquired_from_digest: TranscriptDigest | None = None,
) -> tuple[Transcript, TranscriptArtifact | None]:
    """Persist a successful acquisition and its private raw object.

    Args:
        db: Database session.
        episode: Episode whose source payload was acquired.
        acquisition: Successful provider result and storage policy.
        artifact_store: Configured private encrypted object adapter.
        reacquired_from_digest: Optional proof record that triggered reacquisition.

    Returns:
        New transcript plus stored artifact metadata when raw storage is allowed.

    Raises:
        ValueError: If acquisition failed or raw storage lacks an adapter.
    """
    if not acquisition.success or acquisition.result is None:
        raise ValueError("Cannot persist an unsuccessful transcript acquisition")
    acquisition = apply_stored_acquisition_opt_out(
        db=db,
        podcast_id=episode.podcast_id,
        source_key=acquisition.result.provider,
        acquisition=acquisition,
    )
    if acquisition.result is None:
        raise ValueError("Stored transcript acquisition no longer has a result")
    if acquisition.should_store_raw and artifact_store is None:
        raise ValueError("Encrypted transcript artifact storage is not configured")

    result = acquisition.result
    transcript = Transcript(
        episode_id=episode.id,
        provider=result.provider,
        raw_text=None,
        segments_json=None,
        fetched_at=result.fetched_at,
        retention_tier="hot",
        source_retention_opt_out=acquisition.source_retention_opt_out,
    )
    db.add(transcript)
    db.flush()

    if not acquisition.should_store_raw:
        return transcript, None

    if artifact_store is None:  # pragma: no cover - guarded above
        raise AssertionError
    artifact = _store_raw_transcript_artifact(
        db=db,
        transcript=transcript,
        artifact_store=artifact_store,
        result=result,
        reacquired_from_digest=reacquired_from_digest,
    )
    return transcript, artifact


def load_transcript_processing_payload(
    *,
    db: Session,
    transcript: Transcript,
    artifact_store: TranscriptArtifactStore | None,
) -> TranscriptProcessingPayloadData | None:
    """Load private processing input from encrypted storage or legacy columns.

    New acquisitions store raw inputs only in an encrypted artifact. The column
    fallback allows already-ingested transcripts to finish processing without a
    data migration.

    Args:
        db: Database session.
        transcript: Transcript requiring cleanup or extraction input.
        artifact_store: Configured encrypted artifact adapter.

    Returns:
        Raw processing input, or ``None`` if no retained raw payload remains.

    Raises:
        ValueError: If artifact metadata exists without a configured store or
            if a stored payload is malformed.
    """
    artifact = (
        db.query(TranscriptArtifact)
        .filter(
            TranscriptArtifact.transcript_id == transcript.id,
            TranscriptArtifact.purged_at.is_(None),
        )
        .order_by(TranscriptArtifact.id.desc())
        .first()
    )
    if artifact is not None:
        if artifact_store is None:
            raise ValueError("Encrypted transcript artifact storage is not configured")
        return _processing_payload_from_json(
            artifact_store.get_json(storage_key=artifact.storage_key),
        )

    if transcript.raw_text is None and transcript.segments_json is None:
        return None
    return TranscriptProcessingPayloadData(
        raw_text=transcript.raw_text or "",
        segments=transcript.segments_json or [],
    )


class ArtifactVerificationFailure(StrEnum):
    """Fixed, content-free reasons a retained artifact fails verification."""

    UNREADABLE = auto()
    MALFORMED = auto()
    EMPTY = auto()
    MISMATCH = auto()


def verify_artifact_source_hash(
    *,
    artifact: TranscriptArtifact,
    artifact_store: TranscriptArtifactStore,
) -> str:
    """Read one retained artifact and prove it still holds the acquired text.

    Decryption, validation and hashing happen in a helper that returns only a
    hash or a failure code, so the error raised here carries no cause, no
    context and no frame holding decrypted text.

    Args:
        artifact: Unpurged artifact metadata recorded at acquisition.
        artifact_store: Configured encrypted artifact adapter.

    Returns:
        SHA-256 of the artifact's exact raw UTF-8 text, equal to its metadata.

    Raises:
        ValueError: If the object is missing, unreadable, cannot be decrypted,
            is malformed, holds empty text, or hashes differently from its
            recorded metadata.
    """
    outcome = _hash_artifact_text(
        artifact_store=artifact_store,
        storage_key=artifact.storage_key,
    )
    if outcome != artifact.source_text_hash and not isinstance(
        outcome, ArtifactVerificationFailure
    ):
        outcome = ArtifactVerificationFailure.MISMATCH
    if isinstance(outcome, ArtifactVerificationFailure):
        raise ValueError(f"{ARTIFACT_VERIFICATION_FAILED}: {outcome.value}")
    return outcome


def _hash_artifact_text(
    *,
    artifact_store: TranscriptArtifactStore,
    storage_key: str,
) -> str | ArtifactVerificationFailure:
    """Decrypt, validate and hash one artifact without letting plaintext escape.

    Every failure is recorded inside its handler and returned after the
    handler exits, and the payload reference is cleared before returning, so
    no exception, chained context or frame retains the decrypted text.
    """
    payload: TranscriptProcessingPayloadData | None = None
    result: str | ArtifactVerificationFailure
    try:
        payload = _processing_payload_from_json(
            artifact_store.get_json(storage_key=storage_key),
        )
        if _has_visible_text(payload.raw_text):
            result = sha256(payload.raw_text.encode("utf-8")).hexdigest()
        else:
            result = ArtifactVerificationFailure.EMPTY
    except (InvalidToken, LookupError, OSError):
        result = ArtifactVerificationFailure.UNREADABLE
    except (RuntimeError, TypeError, ValueError):
        result = ArtifactVerificationFailure.MALFORMED
    finally:
        payload = None
    return result


def _has_visible_text(text: str) -> bool:
    """Whether the text has any character a reader would see.

    Whitespace and Unicode format, control, surrogate, private-use and
    unassigned characters (zero-width space, byte-order mark, word joiner)
    do not count, so text made only of them is treated as empty.
    """
    return any(
        not character.isspace() and unicodedata.category(character)[0] != "C"
        for character in text
    )


def reacquire_purged_transcript(
    *,
    db: Session,
    transcript: Transcript,
    acquirer: TranscriptAcquisitionClient,
    artifact_store: TranscriptArtifactStore | None,
) -> TranscriptReacquisitionData:
    """Acquire a fresh hot raw asset for an already purged transcript.

    Args:
        db: Database session.
        transcript: Purged source transcript requiring a fresh processing run.
        acquirer: Provider orchestration used to obtain the new transcript.
        artifact_store: Configured encrypted artifact storage adapter.

    Returns:
        Newly stored transcript, artifact, and preserved prior digest.

    Raises:
        ValueError: If the transcript is not purged or acquisition cannot persist.
    """
    if transcript.purged_at is None:
        raise ValueError("Only purged transcripts can be re-acquired")
    prior_digest = (
        db.query(TranscriptDigest)
        .filter(TranscriptDigest.transcript_id == transcript.id)
        .order_by(TranscriptDigest.id.desc())
        .first()
    )
    if prior_digest is None:
        raise ValueError("Purged transcript is missing its digest proof")

    acquisition = acquirer.acquire(transcript.episode)
    if not acquisition.success:
        raise ValueError(acquisition.error or "Transcript re-acquisition failed")
    if not acquisition.should_store_raw:
        raise ValueError(
            "Re-acquired transcript cannot be retained under source policy"
        )

    fresh_transcript, artifact = persist_transcript_acquisition(
        db=db,
        episode=transcript.episode,
        acquisition=acquisition,
        artifact_store=artifact_store,
        reacquired_from_digest=prior_digest,
    )
    if artifact is None:
        raise ValueError("Re-acquisition did not create a raw transcript artifact")
    return TranscriptReacquisitionData(
        transcript=fresh_transcript,
        artifact=artifact,
        prior_digest=prior_digest,
    )


def _store_raw_transcript_artifact(
    *,
    db: Session,
    transcript: Transcript,
    artifact_store: TranscriptArtifactStore,
    result: TranscriptResult,
    reacquired_from_digest: TranscriptDigest | None,
) -> TranscriptArtifact:
    """Write one raw transcript payload and persist its provenance metadata."""
    source_hash = sha256(result.raw_text.encode("utf-8")).hexdigest()
    storage_key = f"transcripts/{transcript.episode_id}/{transcript.id}/raw.json.enc"
    stored = artifact_store.put_json(
        storage_key=storage_key,
        payload={
            "provider": transcript.provider,
            "raw_text": result.raw_text,
            "segments": result.segments,
            "fetched_at": (
                transcript.fetched_at.isoformat()
                if transcript.fetched_at is not None
                else None
            ),
        },
    )
    artifact = TranscriptArtifact(
        transcript_id=transcript.id,
        episode_id=transcript.episode_id,
        reacquired_from_digest_id=(
            reacquired_from_digest.id if reacquired_from_digest is not None else None
        ),
        storage_key=stored.storage_key,
        storage_backend=stored.storage_backend,
        encryption_key_id=stored.encryption_key_id,
        provider=transcript.provider,
        source_text_hash=source_hash,
        content_type=RAW_TRANSCRIPT_CONTENT_TYPE,
        byte_size=stored.byte_size,
        provenance_json={
            "acquisition_provider": transcript.provider,
            "fetched_at": (
                transcript.fetched_at.isoformat()
                if transcript.fetched_at is not None
                else None
            ),
        },
        stored_at=datetime.now(UTC),
    )
    db.add(artifact)
    db.flush()
    return artifact


def _processing_payload_from_json(
    payload: dict[str, Any],
) -> TranscriptProcessingPayloadData:
    """Validate a decrypted artifact payload's raw text and segment shapes."""
    raw_text = payload.get("raw_text")
    segments = payload.get("segments")
    if not isinstance(raw_text, str) or not isinstance(segments, list):
        raise ValueError("Encrypted transcript artifact payload is malformed")
    if not all(isinstance(segment, dict) for segment in segments):
        raise ValueError("Encrypted transcript artifact segments are malformed")
    return TranscriptProcessingPayloadData(raw_text=raw_text, segments=segments)


def _decode_encrypted_payload(
    *,
    fernet: Fernet,
    ciphertext: bytes,
) -> dict[str, Any]:
    """Decrypt an encrypted raw JSON object and validate its root shape."""
    payload = json.loads(fernet.decrypt(ciphertext).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Encrypted transcript artifact JSON must be an object")
    return payload


def list_retained_artifacts(
    *, db: Session, transcript_id: int
) -> list[TranscriptArtifact]:
    """Return unpurged encrypted artifacts attached to one transcript."""
    return list(
        db.query(TranscriptArtifact)
        .filter(
            TranscriptArtifact.transcript_id == transcript_id,
            TranscriptArtifact.purged_at.is_(None),
        )
        .all()
    )
