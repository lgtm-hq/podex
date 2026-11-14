"""Tests for identifying and repairing empty-source purge proofs."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    Episode,
    Podcast,
    Transcript,
    TranscriptArtifact,
    TranscriptDigest,
)
from podex.services.transcript_artifacts import ARTIFACT_VERIFICATION_FAILED
from podex.services.transcript_digest_repairs import (
    EmptySourceDigestStatus,
    inventory_empty_source_digests,
    repair_empty_source_digest,
)
from podex.services.transcript_retention_commands import (
    EMPTY_SOURCE_TEXT_HASH,
    create_transcript_digest,
    resolve_column_proof_source,
)
from tests.test_transcript_artifacts import RecordingArtifactStore
from tests.test_transcript_retention_commands import proof_state

_NOW = datetime(2026, 11, 1, tzinfo=UTC)
_SOURCE = "acquired encrypted text"


def _empty_proof(
    db: Session,
    store: RecordingArtifactStore,
    *,
    slug: str,
    with_artifact: bool = True,
) -> tuple[TranscriptDigest, TranscriptArtifact | None]:
    """Persist an encrypted-only transcript with an empty-text proof."""
    podcast = Podcast(name=slug, slug=slug)
    db.add(podcast)
    db.flush()
    episode = Episode(podcast_id=podcast.id, title=slug)
    db.add(episode)
    db.flush()
    transcript = Transcript(
        episode_id=episode.id,
        provider="podscripts",
        fetched_at=_NOW - timedelta(days=60),
        digest_text="processing summary",
    )
    db.add(transcript)
    db.flush()
    artifact = None
    if with_artifact:
        stored = store.put_json(
            storage_key=f"transcripts/{slug}/raw.json.enc",
            payload={"raw_text": _SOURCE, "segments": []},
        )
        artifact = TranscriptArtifact(
            transcript_id=transcript.id,
            episode_id=episode.id,
            storage_key=stored.storage_key,
            storage_backend=stored.storage_backend,
            encryption_key_id=stored.encryption_key_id,
            provider=transcript.provider,
            source_text_hash=sha256(_SOURCE.encode("utf-8")).hexdigest(),
            content_type="application/json",
            byte_size=stored.byte_size,
            stored_at=_NOW - timedelta(days=60),
        )
        db.add(artifact)
        db.flush()
    digest = create_transcript_digest(
        db=db,
        transcript=transcript,
        source=resolve_column_proof_source(transcript),
        purged_at=_NOW - timedelta(days=1),
    )
    return digest, artifact


def test_inventory_classifies_without_inventing_source(db_session: Session) -> None:
    """Each empty proof is classified by what source evidence still exists."""
    store = RecordingArtifactStore()
    retained, _ = _empty_proof(db_session, store, slug="retained")
    deleted, deleted_artifact = _empty_proof(db_session, store, slug="deleted")
    if deleted_artifact is None:
        raise AssertionError
    deleted_artifact.purged_at = _NOW - timedelta(days=1)
    store.delete(storage_key=deleted_artifact.storage_key)
    absent, _ = _empty_proof(db_session, store, slug="absent", with_artifact=False)
    acquired_hash = sha256(_SOURCE.encode("utf-8")).hexdigest()

    inventory = inventory_empty_source_digests(db=db_session)

    assert_that([item.digest_id for item in inventory]).is_equal_to(
        [retained.id, deleted.id, absent.id]
    )
    assert_that([item.status for item in inventory]).is_equal_to(
        [
            EmptySourceDigestStatus.RETAINED_ARTIFACT,
            EmptySourceDigestStatus.SOURCE_DELETED,
            EmptySourceDigestStatus.NO_RECORDED_SOURCE,
        ]
    )
    assert_that([item.recorded_artifact_hash for item in inventory]).is_equal_to(
        [acquired_hash, acquired_hash, None]
    )
    assert_that(retained.metadata_json).contains_entry(
        {"source_representation": "absent"}
    )


def test_repair_adds_verified_record_and_keeps_original(db_session: Session) -> None:
    """Retained input is verified and recorded beside the untouched original."""
    store = RecordingArtifactStore()
    original, artifact = _empty_proof(db_session, store, slug="repairable")
    if artifact is None:
        raise AssertionError
    original_row = proof_state(db_session)["transcript_digests"][0]

    repair = repair_empty_source_digest(
        db=db_session, digest_id=original.id, artifact_store=store, now=_NOW
    )

    assert_that(repair.id).is_not_equal_to(original.id)
    assert_that(repair.source_text_hash).is_equal_to(artifact.source_text_hash)
    assert_that(repair.source_text_hash).is_not_equal_to(EMPTY_SOURCE_TEXT_HASH)
    assert_that(repair.summary_text).is_equal_to(original.summary_text)
    assert_that(repair.metadata_json).contains_entry(
        {"repairs_digest_id": original.id},
        {"source_representation": "encrypted_artifact_raw_text"},
        {"source_artifact_id": artifact.id},
        {"verified_at": _NOW.isoformat()},
        {"repairs_purged_at": (_NOW - timedelta(days=1)).isoformat()},
    )
    assert_that(repair.metadata_json).does_not_contain_key("repaired_at")
    assert_that(repair.generated_at.replace(tzinfo=UTC)).is_equal_to(_NOW)
    assert_that(proof_state(db_session)["transcript_digests"][0]).is_equal_to(
        original_row
    )
    assert_that(store.deleted).is_empty()
    assert_that(artifact.purged_at).is_none()
    inventory = inventory_empty_source_digests(db=db_session)
    assert_that(inventory[0].status).is_equal_to(EmptySourceDigestStatus.REPAIRED)
    assert_that(inventory[0].repair_digest_id).is_equal_to(repair.id)
    with pytest.raises(ValueError, match="repaired"):
        repair_empty_source_digest(
            db=db_session, digest_id=original.id, artifact_store=store, now=_NOW
        )


@pytest.mark.parametrize("case", ["deleted", "absent", "mismatch", "not-empty"])
def test_unrepairable_digest_is_refused_without_writes(
    db_session: Session,
    case: str,
) -> None:
    """Deleted, absent, tampered or non-empty proofs are never rewritten."""
    store = RecordingArtifactStore()
    digest, artifact = _empty_proof(
        db_session, store, slug=case, with_artifact=case != "absent"
    )
    if case == "deleted" and artifact is not None:
        artifact.purged_at = _NOW - timedelta(days=1)
    if case == "mismatch" and artifact is not None:
        store.objects[artifact.storage_key] = {"raw_text": "other", "segments": []}
    if case == "not-empty":
        digest.source_text_hash = sha256(b"real").hexdigest()
    before = proof_state(db_session)
    objects = dict(store.objects)

    with pytest.raises(ValueError) as error:
        repair_empty_source_digest(
            db=db_session, digest_id=digest.id, artifact_store=store, now=_NOW
        )

    expected = {
        "deleted": "source_deleted",
        "absent": "no_recorded_source",
        "mismatch": ARTIFACT_VERIFICATION_FAILED,
        "not-empty": "not an empty-source",
    }[case]
    assert_that(str(error.value)).contains(expected)
    assert_that(proof_state(db_session)).is_equal_to(before)
    assert_that(store.objects).is_equal_to(objects)
    assert_that(store.deleted).is_empty()


def test_repair_refuses_when_verified_proof_already_exists(
    db_session: Session,
) -> None:
    """A source already proven elsewhere is not recorded a second time."""
    store = RecordingArtifactStore()
    digest, artifact = _empty_proof(db_session, store, slug="proven")
    if artifact is None:
        raise AssertionError
    db_session.add(
        TranscriptDigest(
            transcript_id=digest.transcript_id,
            episode_id=digest.episode_id,
            digest_key=(
                f"transcript:{digest.transcript_id}:purge:{artifact.source_text_hash}"
            ),
            source_text_hash=artifact.source_text_hash,
            provider=digest.provider,
            summary_text="proof",
            generated_at=_NOW,
            purged_at=_NOW,
        ),
    )
    before = proof_state(db_session)

    with pytest.raises(ValueError, match="already exists"):
        repair_empty_source_digest(
            db=db_session, digest_id=digest.id, artifact_store=store, now=_NOW
        )

    assert_that(proof_state(db_session)).is_equal_to(before)


def _later_artifact(
    db: Session,
    store: RecordingArtifactStore,
    digest: TranscriptDigest,
    storage_key: str,
) -> TranscriptArtifact:
    """Attach a retained artifact stored after the empty proof was written."""
    store.put_json(
        storage_key=storage_key,
        payload={"raw_text": "attached later", "segments": []},
    )
    artifact = TranscriptArtifact(
        transcript_id=digest.transcript_id,
        episode_id=digest.episode_id,
        storage_key=storage_key,
        storage_backend="recording",
        encryption_key_id="key-v1",
        provider=digest.provider,
        source_text_hash=sha256(b"attached later").hexdigest(),
        content_type="application/json",
        byte_size=1,
        stored_at=_NOW,
    )
    db.add(artifact)
    db.flush()
    return artifact


@pytest.mark.parametrize(
    "case", ["stored-later", "recorded-later", "reacquired", "second-retained"]
)
def test_repair_rejects_artifact_not_attributable_to_original_proof(
    db_session: Session,
    case: str,
) -> None:
    """Later, re-acquired or competing artifacts cannot be backdated as proof."""
    store = RecordingArtifactStore()
    with_original = case in {"recorded-later", "reacquired", "second-retained"}
    digest, artifact = _empty_proof(
        db_session, store, slug=case, with_artifact=with_original
    )
    if case == "stored-later":
        _later_artifact(db_session, store, digest, "transcripts/later/raw.json.enc")
    elif artifact is None:
        raise AssertionError
    elif case == "recorded-later":
        artifact.created_at = digest.created_at + timedelta(seconds=1)
    elif case == "reacquired":
        artifact.reacquired_from_digest_id = digest.id
    else:
        _later_artifact(db_session, store, digest, "transcripts/second/raw.json.enc")
    before = proof_state(db_session)
    objects = dict(store.objects)

    with pytest.raises(ValueError, match="unattributable_artifact"):
        repair_empty_source_digest(
            db=db_session, digest_id=digest.id, artifact_store=store, now=_NOW
        )

    assert_that(proof_state(db_session)).is_equal_to(before)
    assert_that(store.objects).is_equal_to(objects)
    assert_that(store.deleted).is_empty()
    inventory = inventory_empty_source_digests(db=db_session)
    assert_that(inventory[0].status).is_equal_to(
        EmptySourceDigestStatus.UNATTRIBUTABLE_ARTIFACT
    )
    expected_hash = (
        sha256(_SOURCE.encode("utf-8")).hexdigest()
        if case == "second-retained"
        else None
    )
    assert_that(inventory[0].recorded_artifact_hash).is_equal_to(expected_hash)
