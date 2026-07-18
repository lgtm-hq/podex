"""Tests for encrypted raw transcript artifact lifecycle commands."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from podex.config import Settings
from podex.models import (
    Episode,
    Podcast,
    Transcript,
    TranscriptDigest,
    TranscriptSourceRetentionPolicy,
)
from podex.services.transcript_acquisition import (
    TranscriptAcquisitionResult,
    TranscriptResult,
)
from podex.services.transcript_artifacts import (
    EncryptedFilesystemTranscriptArtifactStore,
    EncryptedS3TranscriptArtifactStore,
    StoredTranscriptArtifactData,
    TranscriptArtifactStorageBackend,
    build_transcript_artifact_store,
    load_transcript_processing_payload,
    persist_transcript_acquisition,
    reacquire_purged_transcript,
)


class RecordingArtifactStore:
    """Record payload writes and deletes while exercising lifecycle services."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []

    def put_json(
        self,
        *,
        storage_key: str,
        payload: dict[str, Any],
    ) -> StoredTranscriptArtifactData:
        """Capture an artifact payload and return representative metadata."""
        self.objects[storage_key] = payload
        return StoredTranscriptArtifactData(
            storage_key=storage_key,
            storage_backend="recording",
            encryption_key_id="key-v1",
            byte_size=120,
        )

    def delete(self, *, storage_key: str) -> None:
        """Capture deletion of a stored payload."""
        self.deleted.append(storage_key)
        self.objects.pop(storage_key, None)

    def get_json(self, *, storage_key: str) -> dict[str, Any]:
        """Return a previously captured raw payload."""
        return self.objects[storage_key]


class RecordingS3Client:
    """Capture encrypted S3-compatible operations for adapter tests."""

    def __init__(self) -> None:
        self.put_requests: list[dict[str, Any]] = []
        self.delete_requests: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        """Capture one object upload."""
        self.put_requests.append(kwargs)
        return {}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        """Capture one object deletion."""
        self.delete_requests.append(kwargs)
        return {}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        """Return bytes from the matching captured upload."""
        key = kwargs["Key"]
        body = next(
            request["Body"] for request in self.put_requests if request["Key"] == key
        )
        return {"Body": body}


class SuccessfulAcquirer:
    """Return a deterministic fresh transcript for re-acquisition."""

    def acquire(self, episode: Episode) -> TranscriptAcquisitionResult:
        """Return a provider result for the requested episode."""
        return TranscriptAcquisitionResult(
            success=True,
            result=TranscriptResult(
                provider="podscripts.co",
                raw_text="freshly reacquired transcript",
                segments=[{"start": 0, "text": "freshly reacquired transcript"}],
                fetched_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
            ),
            source="podscripts.co",
        )


def _create_episode(db_session: Session) -> Episode:
    """Create an episode parent for artifact command tests."""
    podcast = Podcast(name="Artifact Podcast", slug="artifact-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Artifact Episode")
    db_session.add(episode)
    db_session.flush()
    return episode


def _acquisition() -> TranscriptAcquisitionResult:
    """Build a successful source payload for persistence tests."""
    return TranscriptAcquisitionResult(
        success=True,
        result=TranscriptResult(
            provider="podscripts.co",
            raw_text="raw private transcript",
            segments=[{"start": 0, "text": "raw private transcript"}],
            fetched_at=datetime(2026, 5, 24, 9, 0, tzinfo=UTC),
        ),
        source="podscripts.co",
    )


def test_encrypted_filesystem_store_does_not_write_plaintext(tmp_path: Path) -> None:
    """Verify stored raw payload bytes are encrypted at rest."""
    store = EncryptedFilesystemTranscriptArtifactStore(
        root_path=tmp_path,
        encryption_key=Fernet.generate_key().decode("ascii"),
    )

    stored = store.put_json(
        storage_key="transcripts/1/1/raw.json.enc",
        payload={"raw_text": "confidential raw transcript"},
    )

    ciphertext = (tmp_path / stored.storage_key).read_bytes()
    assert_that(ciphertext).does_not_contain(b"confidential raw transcript")
    assert_that(stored.storage_backend).is_equal_to("encrypted_filesystem")
    assert_that(store.get_json(storage_key=stored.storage_key)).contains_entry(
        {"raw_text": "confidential raw transcript"},
    )


def test_encrypted_s3_store_uploads_ciphertext_and_deletes_by_key() -> None:
    """Verify S3-compatible storage receives only encrypted transcript bytes."""
    client = RecordingS3Client()
    store = EncryptedS3TranscriptArtifactStore(
        bucket="private-transcripts",
        encryption_key=Fernet.generate_key().decode("ascii"),
        client=client,
    )

    stored = store.put_json(
        storage_key="transcripts/1/1/raw.json.enc",
        payload={"raw_text": "confidential raw transcript"},
    )
    loaded = store.get_json(storage_key=stored.storage_key)
    store.delete(storage_key=stored.storage_key)

    uploaded_body = client.put_requests[0]["Body"]
    assert_that(uploaded_body).does_not_contain(b"confidential raw transcript")
    assert_that(client.put_requests[0]["Bucket"]).is_equal_to("private-transcripts")
    assert_that(stored.storage_backend).is_equal_to("encrypted_s3")
    assert_that(loaded).contains_entry({"raw_text": "confidential raw transcript"})
    assert_that(client.delete_requests[0]).is_equal_to(
        {"Bucket": "private-transcripts", "Key": stored.storage_key},
    )


def test_build_artifact_store_selects_encrypted_filesystem_by_default(
    tmp_path: Path,
) -> None:
    """Verify local deployments retain the encrypted filesystem adapter."""
    store = build_transcript_artifact_store(
        settings=Settings(
            transcript_artifact_storage_path=tmp_path,
            transcript_artifact_encryption_key=Fernet.generate_key().decode("ascii"),
        ),
    )

    assert_that(store).is_instance_of(EncryptedFilesystemTranscriptArtifactStore)


def test_build_artifact_store_selects_s3_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify hosted configuration selects the S3-compatible adapter."""
    client = RecordingS3Client()
    monkeypatch.setattr(
        "podex.services.transcript_artifacts._build_s3_client",
        lambda **_kwargs: client,
    )

    store = build_transcript_artifact_store(
        settings=Settings(
            transcript_artifact_storage_backend=(
                TranscriptArtifactStorageBackend.ENCRYPTED_S3.value
            ),
            transcript_artifact_encryption_key=Fernet.generate_key().decode("ascii"),
            transcript_artifact_s3_bucket="private-transcripts",
        ),
    )

    assert_that(store).is_instance_of(EncryptedS3TranscriptArtifactStore)


def test_persist_transcript_acquisition_writes_artifact_provenance(
    db_session: Session,
) -> None:
    """Verify raw acquisition writes an external payload metadata record."""
    episode = _create_episode(db_session)
    store = RecordingArtifactStore()

    transcript, artifact = persist_transcript_acquisition(
        db=db_session,
        episode=episode,
        acquisition=_acquisition(),
        artifact_store=store,
    )

    assert_that(transcript.retention_tier).is_equal_to("hot")
    assert_that(transcript.raw_text).is_none()
    assert_that(transcript.segments_json).is_none()
    assert_that(artifact).is_not_none()
    if artifact is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(artifact.storage_backend).is_equal_to("recording")
    assert_that(artifact.provider).is_equal_to("podscripts.co")
    assert_that(store.objects[artifact.storage_key]["raw_text"]).is_equal_to(
        "raw private transcript"
    )
    payload = load_transcript_processing_payload(
        db=db_session,
        transcript=transcript,
        artifact_store=store,
    )
    assert_that(payload).is_not_none()
    if payload is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(payload.raw_text).is_equal_to("raw private transcript")
    assert_that(payload.segments).is_length(1)


def test_persist_transcript_acquisition_honors_saved_source_opt_out(
    db_session: Session,
) -> None:
    """Verify a saved source opt-out prevents future raw artifact retention."""
    episode = _create_episode(db_session)
    db_session.add(
        TranscriptSourceRetentionPolicy(
            podcast_id=episode.podcast_id,
            source_key="podscripts.co",
            policy_version="source-opt-out-v1",
            hot_days=30,
            warm_days=180,
            min_purge_confidence=0.85,
            source_retention_opt_out=True,
        )
    )
    db_session.flush()
    store = RecordingArtifactStore()

    transcript, artifact = persist_transcript_acquisition(
        db=db_session,
        episode=episode,
        acquisition=_acquisition(),
        artifact_store=store,
    )

    assert_that(transcript.source_retention_opt_out).is_true()
    assert_that(transcript.raw_text).is_none()
    assert_that(transcript.segments_json).is_none()
    assert_that(artifact).is_none()
    assert_that(store.objects).is_empty()


def test_reacquire_purged_transcript_creates_hot_asset_from_digest(
    db_session: Session,
) -> None:
    """Verify re-acquisition retains old proof and starts a new hot asset."""
    episode = _create_episode(db_session)
    purged_at = datetime(2026, 5, 23, 9, 0, tzinfo=UTC)
    prior = Transcript(
        episode_id=episode.id,
        provider="podscripts.co",
        raw_text=None,
        segments_json=None,
        fetched_at=purged_at,
        retention_tier="purged",
        purged_at=purged_at,
    )
    db_session.add(prior)
    db_session.flush()
    digest = TranscriptDigest(
        transcript_id=prior.id,
        episode_id=episode.id,
        digest_key=f"transcript:{prior.id}:purge:test",
        source_text_hash="abc123",
        provider=prior.provider,
        summary_text="retained proof",
        generated_at=purged_at,
        purged_at=purged_at,
    )
    db_session.add(digest)
    db_session.flush()
    store = RecordingArtifactStore()

    result = reacquire_purged_transcript(
        db=db_session,
        transcript=prior,
        acquirer=SuccessfulAcquirer(),
        artifact_store=store,
    )

    assert_that(result.transcript.id).is_not_equal_to(prior.id)
    assert_that(result.transcript.retention_tier).is_equal_to("hot")
    assert_that(result.transcript.purged_at).is_none()
    assert_that(result.artifact.reacquired_from_digest_id).is_equal_to(digest.id)
    assert_that(result.prior_digest.id).is_equal_to(digest.id)
