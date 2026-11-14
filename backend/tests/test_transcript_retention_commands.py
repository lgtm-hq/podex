"""Tests for persisted transcript retention commands."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from podex.config import RetentionSettings
from podex.models import (
    Episode,
    Podcast,
    Transcript,
    TranscriptArtifact,
    TranscriptDigest,
)
from podex.services.transcript_acquisition import (
    TranscriptAcquisitionResult,
    TranscriptResult,
)
from podex.services.transcript_artifacts import (
    ARTIFACT_VERIFICATION_FAILED,
    EncryptedFilesystemTranscriptArtifactStore,
    StoredTranscriptArtifactData,
    TranscriptArtifactStore,
    persist_transcript_acquisition,
)
from podex.services.transcript_retention import (
    TranscriptLifecycleTier,
    TranscriptRetentionPolicy,
)
from podex.services.transcript_retention_commands import (
    NO_PURGE_SOURCE_REASON,
    create_transcript_digest,
    evaluate_transcript_retention,
    purge_transcript_if_eligible,
    resolve_column_proof_source,
)
from tests.test_transcript_artifacts import RecordingArtifactStore


def _create_transcript(db_session: Session, fetched_at: datetime) -> Transcript:
    """Create a transcript fixture."""
    podcast = Podcast(name="Retention Podcast", slug="retention-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Retention Episode")
    db_session.add(episode)
    db_session.flush()
    transcript = Transcript(
        episode_id=episode.id,
        provider="podscripts",
        raw_text="raw transcript",
        cleaned_text="clean transcript",
        segments_json=[{"start": 1, "text": "hello"}],
        fetched_at=fetched_at,
        digest_text="digest",
        digest_created_at=fetched_at,
    )
    db_session.add(transcript)
    db_session.flush()
    return transcript


def test_evaluate_transcript_retention_persists_policy_decision(
    db_session: Session,
) -> None:
    """Verify retention evaluation updates transcript lifecycle fields."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    transcript = _create_transcript(
        db_session=db_session,
        fetched_at=now - timedelta(days=45),
    )

    result = evaluate_transcript_retention(
        db=db_session,
        transcript=transcript,
        extraction_confidence=0.95,
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=7),
            warm_retention_period=timedelta(days=30),
            retention_sample_rate=0,
        ),
        now=now,
    )
    db_session.commit()

    assert_that(result.decision.tier).is_equal_to(TranscriptLifecycleTier.COLD)
    assert_that(transcript.retention_tier).is_equal_to("cold")
    assert_that(transcript.retention_policy_version).is_equal_to("retention-sample-v1")
    assert_that(transcript.purge_eligible_at).is_equal_to(now.replace(tzinfo=None))
    assert_that(transcript.retention_blockers_json).is_empty()


def test_purge_transcript_if_eligible_removes_raw_fields(
    db_session: Session,
) -> None:
    """Verify eligible transcripts can purge raw transcript storage."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    transcript = _create_transcript(
        db_session=db_session,
        fetched_at=now - timedelta(days=45),
    )
    evaluate_transcript_retention(
        db=db_session,
        transcript=transcript,
        extraction_confidence=0.95,
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=7),
            warm_retention_period=timedelta(days=30),
            retention_sample_rate=0,
        ),
        now=now,
    )

    purged = purge_transcript_if_eligible(
        db=db_session,
        transcript=transcript,
        now=now,
        retention=RetentionSettings(purge_enabled=True),
    )
    db_session.commit()

    assert_that(purged).is_true()
    assert_that(transcript.raw_text).is_none()
    assert_that(transcript.cleaned_text).is_none()
    assert_that(transcript.segments_json).is_none()
    assert_that(transcript.retention_tier).is_equal_to("purged")
    digest = db_session.query(TranscriptDigest).one()
    assert_that(digest.transcript_id).is_equal_to(transcript.id)
    assert_that(digest.summary_text).is_equal_to("digest")
    assert_that(digest.source_text_hash).is_not_empty()


def test_purge_transcript_if_eligible_deletes_external_raw_artifact(
    db_session: Session,
) -> None:
    """Verify purge verifies, then removes, an externally stored raw object."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    transcript = _create_transcript(
        db_session=db_session,
        fetched_at=now - timedelta(days=45),
    )
    artifact = TranscriptArtifact(
        transcript_id=transcript.id,
        episode_id=transcript.episode_id,
        storage_key=f"transcripts/{transcript.episode_id}/{transcript.id}/raw.json.enc",
        storage_backend="fake",
        encryption_key_id="key",
        provider=transcript.provider,
        source_text_hash=sha256(b"stored raw transcript").hexdigest(),
        content_type="application/json",
        byte_size=12,
        stored_at=now,
    )
    db_session.add(artifact)
    store = RecordingArtifactStore()
    store.put_json(
        storage_key=artifact.storage_key,
        payload={"raw_text": "stored raw transcript", "segments": []},
    )
    evaluate_transcript_retention(
        db=db_session,
        transcript=transcript,
        extraction_confidence=0.95,
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=7),
            warm_retention_period=timedelta(days=30),
            retention_sample_rate=0,
        ),
        now=now,
    )

    purged = purge_transcript_if_eligible(
        db=db_session,
        transcript=transcript,
        artifact_store=store,
        now=now,
        retention=RetentionSettings(purge_enabled=True),
    )
    db_session.commit()

    assert_that(purged).is_true()
    assert_that(store.deleted).is_equal_to([artifact.storage_key])
    assert_that(artifact.purged_at).is_equal_to(now.replace(tzinfo=None))
    digest = db_session.query(TranscriptDigest).one()
    assert_that(digest.source_text_hash).is_equal_to(artifact.source_text_hash)
    assert_that(digest.metadata_json).contains_entry(
        {"source_representation": "encrypted_artifact_raw_text"},
        {"source_artifact_id": artifact.id},
    )


def test_source_opt_out_allows_immediate_retention_suppression(
    db_session: Session,
) -> None:
    """Verify source opt-out bypasses derivative prerequisites."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    transcript = _create_transcript(db_session=db_session, fetched_at=now)
    transcript.digest_text = None
    transcript.digest_created_at = None

    result = evaluate_transcript_retention(
        db=db_session,
        transcript=transcript,
        extraction_confidence=None,
        source_retention_opt_out=True,
        now=now,
    )
    db_session.commit()

    assert_that(result.decision.purge_eligible).is_true()
    assert_that(transcript.source_retention_opt_out).is_true()
    assert_that(transcript.purge_eligible_at).is_equal_to(now.replace(tzinfo=None))


def test_existing_calibration_sample_remains_exempt_during_evaluation(
    db_session: Session,
) -> None:
    """Verify retention evaluation cannot discard an assigned corpus member."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    transcript = _create_transcript(
        db_session=db_session,
        fetched_at=now - timedelta(days=45),
    )
    transcript.retention_exempt_sample = True

    result = evaluate_transcript_retention(
        db=db_session,
        transcript=transcript,
        extraction_confidence=0.95,
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=7),
            warm_retention_period=timedelta(days=30),
            retention_sample_rate=0,
        ),
        now=now,
    )

    assert_that(transcript.retention_exempt_sample).is_true()
    assert_that(result.decision.purge_eligible).is_false()


_EMPTY_TEXT_HASH = sha256(b"").hexdigest()
_ACQUIRED_TEXT = "acquired private transcript"


class _OrderingStore:
    """Wrap an encrypted store and record read/delete order against the DB."""

    def __init__(self, *, inner: TranscriptArtifactStore, db: Session) -> None:
        self.inner = inner
        self.db = db
        self.events: list[str] = []

    def put_json(
        self,
        *,
        storage_key: str,
        payload: dict[str, Any],
    ) -> StoredTranscriptArtifactData:
        """Delegate an encrypted write."""
        return self.inner.put_json(storage_key=storage_key, payload=payload)

    def get_json(self, *, storage_key: str) -> dict[str, Any]:
        """Record and delegate an encrypted read."""
        self.events.append("read")
        payload: dict[str, Any] = self.inner.get_json(storage_key=storage_key)
        return payload

    def delete(self, *, storage_key: str) -> None:
        """Record whether the proof row was already persisted, then delete."""
        persisted = self.db.query(TranscriptDigest).count()
        self.events.append(f"delete:digests={persisted}")
        self.inner.delete(storage_key=storage_key)


def _acquire_encrypted_only(
    db_session: Session,
    store: TranscriptArtifactStore,
    now: datetime,
) -> tuple[Transcript, TranscriptArtifact]:
    """Persist a nonempty acquisition whose text lives only in the artifact."""
    podcast = Podcast(name="Encrypted Podcast", slug="encrypted-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Encrypted Episode")
    db_session.add(episode)
    db_session.flush()
    transcript, artifact = persist_transcript_acquisition(
        db=db_session,
        episode=episode,
        acquisition=TranscriptAcquisitionResult(
            success=True,
            result=TranscriptResult(
                provider="podscripts.co",
                raw_text=_ACQUIRED_TEXT,
                segments=[{"start": 0, "text": _ACQUIRED_TEXT}],
                fetched_at=now - timedelta(days=45),
            ),
            source="podscripts.co",
        ),
        artifact_store=store,
    )
    if artifact is None:
        raise AssertionError("Expected an encrypted artifact")
    transcript.digest_text = "retained processing summary"
    transcript.digest_created_at = now - timedelta(days=40)
    transcript.purge_eligible_at = now
    db_session.flush()
    return transcript, artifact


def test_encrypted_only_purge_digest_hashes_verified_source(
    db_session: Session,
    tmp_path: Path,
) -> None:
    """Purge proof is the verified artifact hash, persisted before deletion."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    inner = EncryptedFilesystemTranscriptArtifactStore(
        root_path=tmp_path,
        encryption_key=Fernet.generate_key().decode("ascii"),
    )
    store = _OrderingStore(inner=inner, db=db_session)
    transcript, artifact = _acquire_encrypted_only(db_session, store, now)
    assert_that(transcript.raw_text).is_none()
    assert_that(transcript.cleaned_text).is_none()

    purged = purge_transcript_if_eligible(
        db=db_session,
        transcript=transcript,
        artifact_store=store,
        now=now,
        retention=RetentionSettings(purge_enabled=True),
    )
    db_session.commit()

    expected = sha256(_ACQUIRED_TEXT.encode("utf-8")).hexdigest()
    digest = db_session.query(TranscriptDigest).one()
    assert_that(purged).is_true()
    assert_that(digest.source_text_hash).is_equal_to(expected)
    assert_that(digest.source_text_hash).is_not_equal_to(_EMPTY_TEXT_HASH)
    assert_that(digest.source_text_hash).is_equal_to(artifact.source_text_hash)
    assert_that(store.events).is_equal_to(["read", "delete:digests=1"])
    assert_that((tmp_path / artifact.storage_key).exists()).is_false()


def proof_state(db: Session) -> dict[str, list[dict[str, Any]]]:
    """Capture transcript, artifact and digest rows to detect any write."""
    db.flush()
    return {
        table.name: [dict(row) for row in db.execute(table.select()).mappings()]
        for table in (
            Transcript.__table__,
            TranscriptArtifact.__table__,
            TranscriptDigest.__table__,
        )
    }


def _encrypted_store(tmp_path: Path) -> EncryptedFilesystemTranscriptArtifactStore:
    """Build a real encrypted filesystem store under a temporary root."""
    return EncryptedFilesystemTranscriptArtifactStore(
        root_path=tmp_path,
        encryption_key=Fernet.generate_key().decode("ascii"),
    )


def _object_bytes(tmp_path: Path, artifact: TranscriptArtifact) -> bytes | None:
    """Return the stored ciphertext, or ``None`` when no object exists."""
    path = tmp_path / artifact.storage_key
    return path.read_bytes() if path.exists() else None


def _purge(
    db: Session,
    transcript: Transcript,
    store: TranscriptArtifactStore | None,
    now: datetime,
) -> bool:
    """Run an authorized purge."""
    purged: bool = purge_transcript_if_eligible(
        db=db,
        transcript=transcript,
        artifact_store=store,
        now=now,
        retention=RetentionSettings(purge_enabled=True),
    )
    return purged


def _tamper_missing(
    store: EncryptedFilesystemTranscriptArtifactStore,
    artifact: TranscriptArtifact,
) -> None:
    """Remove the stored object."""
    store.delete(storage_key=artifact.storage_key)


def _tamper_undecryptable(
    store: EncryptedFilesystemTranscriptArtifactStore,
    artifact: TranscriptArtifact,
) -> None:
    """Replace the object with bytes that fail integrity checks."""
    (store.root_path / artifact.storage_key).write_bytes(b"not a fernet token")


def _tamper_different_text(
    store: EncryptedFilesystemTranscriptArtifactStore,
    artifact: TranscriptArtifact,
) -> None:
    """Replace the object with valid ciphertext of different text."""
    store.put_json(
        storage_key=artifact.storage_key,
        payload={"raw_text": "substituted transcript", "segments": []},
    )


def _tamper_malformed(
    store: EncryptedFilesystemTranscriptArtifactStore,
    artifact: TranscriptArtifact,
) -> None:
    """Replace the object with valid ciphertext of a malformed payload."""
    store.put_json(
        storage_key=artifact.storage_key,
        payload={"raw_text": 42, "segments": []},
    )


def _tamper_empty_text(
    store: EncryptedFilesystemTranscriptArtifactStore,
    artifact: TranscriptArtifact,
) -> None:
    """Replace the object with valid ciphertext of empty text."""
    store.put_json(
        storage_key=artifact.storage_key,
        payload={"raw_text": "", "segments": []},
    )


@pytest.mark.parametrize(
    ("tamper", "code"),
    [
        (_tamper_missing, "unreadable"),
        (_tamper_undecryptable, "unreadable"),
        (_tamper_different_text, "mismatch"),
        (_tamper_malformed, "malformed"),
        (_tamper_empty_text, "empty"),
    ],
    ids=["missing", "undecryptable", "different-text", "malformed", "empty-text"],
)
def test_unverifiable_artifact_refuses_purge_without_changes(
    db_session: Session,
    tmp_path: Path,
    tamper: Callable[
        [EncryptedFilesystemTranscriptArtifactStore, TranscriptArtifact], None
    ],
    code: str,
) -> None:
    """Failed verification leaves rows, ciphertext and proofs exactly as before."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    inner = _encrypted_store(tmp_path)
    store = _OrderingStore(inner=inner, db=db_session)
    transcript, artifact = _acquire_encrypted_only(db_session, store, now)
    tamper(inner, artifact)
    before = proof_state(db_session)
    ciphertext = _object_bytes(tmp_path, artifact)

    with pytest.raises(ValueError) as error:
        _purge(db_session, transcript, store, now)

    assert_that(str(error.value)).is_equal_to(f"{ARTIFACT_VERIFICATION_FAILED}: {code}")
    assert_that(error.value.__cause__).is_none()
    assert_that(error.value.__context__).is_none()
    assert_that(proof_state(db_session)).is_equal_to(before)
    assert_that(_object_bytes(tmp_path, artifact)).is_equal_to(ciphertext)
    assert_that(store.events).is_equal_to(["read"])
    assert_that(db_session.query(TranscriptDigest).count()).is_zero()


_LEAK_MARKER = "LEAK-MARKER-private-transcript-body"


def _rendered_error_state(error: BaseException) -> tuple[list[str], set[str]]:
    """Render every chained exception's attributes and every frame's locals.

    Frames from this test module are skipped because the test itself holds
    the marker; the returned function names prove library frames were seen.
    """
    rendered: list[str] = []
    functions: set[str] = set()
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered.extend(
            repr(getattr(current, name, None))
            for name in dir(current)
            if not name.startswith("__")
        )
        frame_link = current.__traceback__
        while frame_link is not None:
            frame = frame_link.tb_frame
            if frame.f_code.co_filename != __file__:
                functions.add(frame.f_code.co_name)
                rendered.extend(repr(value) for value in frame.f_locals.values())
            frame_link = frame_link.tb_next
        pending.extend(
            linked
            for linked in (current.__cause__, current.__context__)
            if linked is not None
        )
    return rendered, functions


def _plant_malformed_json(
    store: EncryptedFilesystemTranscriptArtifactStore, storage_key: str
) -> None:
    """Encrypt truncated JSON that embeds the marker."""
    (store.root_path / storage_key).write_bytes(
        store.fernet.encrypt(f'{{"raw_text": "{_LEAK_MARKER}'.encode()),
    )


def _plant_wrong_shape(
    store: EncryptedFilesystemTranscriptArtifactStore, storage_key: str
) -> None:
    """Encrypt valid JSON whose segments have the wrong type."""
    store.put_json(
        storage_key=storage_key,
        payload={"raw_text": _LEAK_MARKER, "segments": _LEAK_MARKER},
    )


def _plant_mismatch(
    store: EncryptedFilesystemTranscriptArtifactStore, storage_key: str
) -> None:
    """Encrypt well-formed text that differs from the acquired text."""
    store.put_json(
        storage_key=storage_key,
        payload={"raw_text": _LEAK_MARKER, "segments": []},
    )


def _plant_unpaired_surrogate(
    store: EncryptedFilesystemTranscriptArtifactStore, storage_key: str
) -> None:
    """Encrypt text that decodes but cannot be encoded as UTF-8."""
    store.put_json(
        storage_key=storage_key,
        payload={"raw_text": f"{_LEAK_MARKER}\ud800", "segments": []},
    )


@pytest.mark.parametrize(
    ("plant", "code"),
    [
        (_plant_malformed_json, "malformed"),
        (_plant_wrong_shape, "malformed"),
        (_plant_mismatch, "mismatch"),
        (_plant_unpaired_surrogate, "malformed"),
    ],
    ids=["malformed-json", "wrong-shape", "mismatch", "unpaired-surrogate"],
)
def test_verification_errors_retain_no_transcript_text(
    db_session: Session,
    tmp_path: Path,
    plant: Callable[[EncryptedFilesystemTranscriptArtifactStore, str], None],
    code: str,
) -> None:
    """No chained error, attribute or frame local keeps decrypted text."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    store = _encrypted_store(tmp_path)
    transcript, artifact = _acquire_encrypted_only(db_session, store, now)
    plant(store, artifact.storage_key)

    with pytest.raises(ValueError) as error:
        _purge(db_session, transcript, store, now)

    rendered, functions = _rendered_error_state(error.value)
    assert_that(functions).contains("purge_transcript_with_proof")
    for text in rendered:
        assert_that(text).does_not_contain(_LEAK_MARKER)
        assert_that(text).does_not_contain(_ACQUIRED_TEXT)
    assert_that(error.value.__cause__).is_none()
    assert_that(error.value.__context__).is_none()
    assert_that(functions).contains("verify_artifact_source_hash")
    assert_that(str(error.value)).is_equal_to(f"{ARTIFACT_VERIFICATION_FAILED}: {code}")


@pytest.mark.parametrize("empty_text", ["", " \n\t"], ids=["empty", "whitespace"])
def test_artifact_with_empty_text_is_refused_even_when_hash_matches(
    db_session: Session,
    tmp_path: Path,
    empty_text: str,
) -> None:
    """Empty acquired text cannot be proven, so it is never purged."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    inner = _encrypted_store(tmp_path)
    store = _OrderingStore(inner=inner, db=db_session)
    transcript, artifact = _acquire_encrypted_only(db_session, store, now)
    inner.put_json(
        storage_key=artifact.storage_key,
        payload={"raw_text": empty_text, "segments": []},
    )
    artifact.source_text_hash = sha256(empty_text.encode("utf-8")).hexdigest()
    before = proof_state(db_session)
    ciphertext = _object_bytes(tmp_path, artifact)

    with pytest.raises(ValueError) as error:
        _purge(db_session, transcript, store, now)

    assert_that(str(error.value)).is_equal_to(f"{ARTIFACT_VERIFICATION_FAILED}: empty")
    assert_that(proof_state(db_session)).is_equal_to(before)
    assert_that(_object_bytes(tmp_path, artifact)).is_equal_to(ciphertext)
    assert_that(store.events).is_equal_to(["read"])
    assert_that(db_session.query(TranscriptDigest).count()).is_zero()
    assert_that(artifact.purged_at).is_none()


def test_missing_store_refuses_purge_without_changes(
    db_session: Session,
    tmp_path: Path,
) -> None:
    """An artifact-backed transcript cannot be purged without its store."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    transcript, artifact = _acquire_encrypted_only(
        db_session, _encrypted_store(tmp_path), now
    )
    before = proof_state(db_session)
    ciphertext = _object_bytes(tmp_path, artifact)

    with pytest.raises(ValueError, match="storage is not configured"):
        _purge(db_session, transcript, None, now)

    assert_that(proof_state(db_session)).is_equal_to(before)
    assert_that(_object_bytes(tmp_path, artifact)).is_equal_to(ciphertext)


def test_every_retained_artifact_must_verify_before_any_delete(
    db_session: Session,
    tmp_path: Path,
) -> None:
    """One bad artifact blocks deletion of an otherwise valid sibling."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    inner = _encrypted_store(tmp_path)
    store = _OrderingStore(inner=inner, db=db_session)
    transcript, artifact = _acquire_encrypted_only(db_session, store, now)
    sibling = TranscriptArtifact(
        transcript_id=transcript.id,
        episode_id=transcript.episode_id,
        storage_key=f"{artifact.storage_key}.sibling",
        storage_backend=artifact.storage_backend,
        encryption_key_id=artifact.encryption_key_id,
        provider=artifact.provider,
        source_text_hash=sha256(b"sibling text").hexdigest(),
        content_type=artifact.content_type,
        byte_size=1,
        stored_at=now,
    )
    db_session.add(sibling)
    inner.put_json(
        storage_key=sibling.storage_key,
        payload={"raw_text": "tampered sibling", "segments": []},
    )
    before = proof_state(db_session)

    with pytest.raises(ValueError, match=ARTIFACT_VERIFICATION_FAILED):
        _purge(db_session, transcript, store, now)

    assert_that(proof_state(db_session)).is_equal_to(before)
    assert_that(store.events).is_equal_to(["read", "read"])
    assert_that(_object_bytes(tmp_path, artifact)).is_not_none()


def test_purge_retry_after_failed_verification_succeeds_once_restored(
    db_session: Session,
    tmp_path: Path,
) -> None:
    """A refused purge leaves nothing behind, so a later retry proves cleanly."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    store = _encrypted_store(tmp_path)
    transcript, artifact = _acquire_encrypted_only(db_session, store, now)
    original = _object_bytes(tmp_path, artifact)
    if original is None:
        raise AssertionError("Expected stored ciphertext")
    _tamper_different_text(store, artifact)
    with pytest.raises(ValueError, match=ARTIFACT_VERIFICATION_FAILED):
        _purge(db_session, transcript, store, now)

    (tmp_path / artifact.storage_key).write_bytes(original)
    purged = _purge(db_session, transcript, store, now)

    assert_that(purged).is_true()
    digest = db_session.query(TranscriptDigest).one()
    assert_that(digest.source_text_hash).is_equal_to(artifact.source_text_hash)
    assert_that(_object_bytes(tmp_path, artifact)).is_none()
    assert_that(_purge(db_session, transcript, store, now)).is_false()
    assert_that(db_session.query(TranscriptDigest).count()).is_equal_to(1)


def test_artifact_proof_never_persists_decrypted_text(
    db_session: Session,
    tmp_path: Path,
) -> None:
    """Without a stored summary the digest uses the fixed placeholder."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    store = _encrypted_store(tmp_path)
    transcript, _artifact = _acquire_encrypted_only(db_session, store, now)
    transcript.digest_text = None

    _purge(db_session, transcript, store, now)

    digest = db_session.query(TranscriptDigest).one()
    assert_that(digest.summary_text).is_equal_to(
        "Raw transcript was purged after retention evaluation."
    )
    assert_that(str(digest.metadata_json)).does_not_contain(_ACQUIRED_TEXT)


@pytest.mark.parametrize(
    ("cleaned", "raw", "representation", "hashed"),
    [
        ("clean text", "raw text", "legacy_cleaned_text", "clean text"),
        (None, "raw text", "legacy_raw_text", "raw text"),
        ("", "raw text", "legacy_raw_text", "raw text"),
    ],
    ids=["cleaned-preferred", "raw-only", "empty-cleaned"],
)
def test_legacy_column_proof_records_representation_and_identity(
    db_session: Session,
    cleaned: str | None,
    raw: str,
    representation: str,
    hashed: str,
) -> None:
    """Legacy columns keep the prior digest key and name what was hashed."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    transcript = _create_transcript(db_session, fetched_at=now)
    transcript.cleaned_text = cleaned
    transcript.raw_text = raw
    transcript.purge_eligible_at = now

    assert_that(_purge(db_session, transcript, None, now)).is_true()

    expected = sha256(hashed.encode("utf-8")).hexdigest()
    digest = db_session.query(TranscriptDigest).one()
    assert_that(digest.digest_key).is_equal_to(
        f"transcript:{transcript.id}:purge:{expected}"
    )
    assert_that(digest.source_text_hash).is_equal_to(expected)
    assert_that(digest.metadata_json).contains_entry(
        {"source_representation": representation}
    )
    assert_that(digest.metadata_json).does_not_contain_key("source_artifact_id")


def test_legacy_purge_replays_existing_proof(db_session: Session) -> None:
    """A proof already recorded for the same source is reused, not duplicated."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    transcript = _create_transcript(db_session, fetched_at=now)
    transcript.purge_eligible_at = now
    existing = create_transcript_digest(
        db=db_session,
        transcript=transcript,
        source=resolve_column_proof_source(transcript),
        purged_at=now - timedelta(days=1),
    )

    assert_that(_purge(db_session, transcript, None, now)).is_true()

    assert_that(db_session.query(TranscriptDigest).one().id).is_equal_to(existing.id)


@pytest.mark.parametrize("raw", [None, ""], ids=["none", "empty"])
def test_purge_without_any_source_text_is_refused(
    db_session: Session,
    raw: str | None,
) -> None:
    """No retained text means no proof can be made, so nothing is purged."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    transcript = _create_transcript(db_session, fetched_at=now)
    transcript.cleaned_text = None
    transcript.raw_text = raw
    transcript.purge_eligible_at = now
    before = proof_state(db_session)

    with pytest.raises(ValueError, match=NO_PURGE_SOURCE_REASON):
        _purge(db_session, transcript, None, now)

    assert_that(proof_state(db_session)).is_equal_to(before)
