"""Private storage commands for encrypted raw transcript artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, auto
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

import boto3
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from podex.config import Settings
from podex.models import Episode, Transcript, TranscriptArtifact, TranscriptDigest
from podex.services.transcript_retention_policies import (
    apply_stored_acquisition_opt_out,
)
from podex.services.transcript_source import (
    TranscriptAcquisitionResult,
)

RAW_TRANSCRIPT_CONTENT_TYPE = "application/vnd.podex.transcript+json"


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
            raise ValueError("TRANSCRIPT_ARTIFACT_ENCRYPTION_KEY must be configured")
        self.root_path = root_path
        self.fernet = Fernet(encryption_key.encode("ascii"))
        self.key_id = sha256(encryption_key.encode("ascii")).hexdigest()[:16]

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
            raise ValueError("TRANSCRIPT_ARTIFACT_S3_BUCKET must be configured")
        if not encryption_key:
            raise ValueError("TRANSCRIPT_ARTIFACT_ENCRYPTION_KEY must be configured")
        self.bucket = bucket
        self.fernet = Fernet(encryption_key.encode("ascii"))
        self.key_id = sha256(encryption_key.encode("ascii")).hexdigest()[:16]
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
        self.client.put_object(
            Bucket=self.bucket,
            Key=storage_key,
            Body=ciphertext,
            ContentType="application/octet-stream",
            Metadata={"podex-key-id": self.key_id},
        )
        return StoredTranscriptArtifactData(
            storage_key=storage_key,
            storage_backend=TranscriptArtifactStorageBackend.ENCRYPTED_S3.value,
            encryption_key_id=self.key_id,
            byte_size=len(ciphertext),
        )

    def delete(self, *, storage_key: str) -> None:
        """Delete an encrypted artifact object from its private bucket."""
        self.client.delete_object(Bucket=self.bucket, Key=storage_key)

    def get_json(self, *, storage_key: str) -> dict[str, Any]:
        """Download and decrypt one S3-compatible transcript JSON object."""
        response = self.client.get_object(Bucket=self.bucket, Key=storage_key)
        body = response["Body"]
        ciphertext = body.read() if hasattr(body, "read") else body
        if not isinstance(ciphertext, bytes):
            raise ValueError("Encrypted transcript artifact body must be bytes")
        return _decode_encrypted_payload(fernet=self.fernet, ciphertext=ciphertext)


def build_transcript_artifact_store(
    *,
    settings: Settings,
) -> TranscriptArtifactStore | None:
    """Build the configured private artifact adapter when a key is supplied."""
    if not settings.transcript_artifact_encryption_key:
        return None
    backend = TranscriptArtifactStorageBackend(
        settings.transcript_artifact_storage_backend,
    )
    if backend is TranscriptArtifactStorageBackend.ENCRYPTED_S3:
        return EncryptedS3TranscriptArtifactStore(
            bucket=settings.transcript_artifact_s3_bucket,
            encryption_key=settings.transcript_artifact_encryption_key,
            endpoint_url=settings.transcript_artifact_s3_endpoint_url,
            region_name=settings.transcript_artifact_s3_region_name,
            access_key_id=settings.transcript_artifact_s3_access_key_id,
            secret_access_key=settings.transcript_artifact_s3_secret_access_key,
        )
    return EncryptedFilesystemTranscriptArtifactStore(
        root_path=settings.transcript_artifact_storage_path,
        encryption_key=settings.transcript_artifact_encryption_key,
    )


def _build_s3_client(
    *,
    endpoint_url: str,
    region_name: str,
    access_key_id: str,
    secret_access_key: str,
) -> S3ObjectClient:
    """Build an S3 client using default credentials unless explicitly supplied."""
    kwargs: dict[str, str] = {}
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    if region_name:
        kwargs["region_name"] = region_name
    if access_key_id or secret_access_key:
        if not access_key_id or not secret_access_key:
            raise ValueError("S3 artifact credentials must be configured together")
        kwargs["aws_access_key_id"] = access_key_id
        kwargs["aws_secret_access_key"] = secret_access_key
    return boto3.client("s3", **kwargs)


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

    assert artifact_store is not None
    artifact = _store_raw_transcript_artifact(
        db=db,
        transcript=transcript,
        artifact_store=artifact_store,
        raw_text=result.raw_text,
        segments=result.segments,
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
        payload = artifact_store.get_json(storage_key=artifact.storage_key)
        raw_text = payload.get("raw_text")
        segments = payload.get("segments")
        if not isinstance(raw_text, str) or not isinstance(segments, list):
            raise ValueError("Encrypted transcript artifact payload is malformed")
        if not all(isinstance(segment, dict) for segment in segments):
            raise ValueError("Encrypted transcript artifact segments are malformed")
        return TranscriptProcessingPayloadData(
            raw_text=raw_text,
            segments=segments,
        )

    if transcript.raw_text is None and transcript.segments_json is None:
        return None
    return TranscriptProcessingPayloadData(
        raw_text=transcript.raw_text or "",
        segments=transcript.segments_json or [],
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
    raw_text: str,
    segments: list[dict[str, Any]],
    reacquired_from_digest: TranscriptDigest | None,
) -> TranscriptArtifact:
    """Write one raw transcript payload and persist its provenance metadata."""
    source_hash = sha256(raw_text.encode("utf-8")).hexdigest()
    storage_key = f"transcripts/{transcript.episode_id}/{transcript.id}/raw.json.enc"
    stored = artifact_store.put_json(
        storage_key=storage_key,
        payload={
            "provider": transcript.provider,
            "raw_text": raw_text,
            "segments": segments,
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
