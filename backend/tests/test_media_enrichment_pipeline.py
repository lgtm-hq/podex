"""Tests for media enrichment pipeline helpers."""

from datetime import UTC, datetime

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Media, MediaAlias
from podex.services.enrichment.base import (
    EnrichmentSource,
    VerifiedEnrichmentResult,
)
from podex.services.media_enrichment_pipeline import enrich_pending_media


def test_enrich_pending_media_applies_external_refs_and_aliases(
    db_session: Session,
) -> None:
    """Verify enrichment batch applies references, verification, and aliases."""
    now = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)
    media = Media(type="study", title="Original Study")
    db_session.add(media)
    db_session.flush()

    result = enrich_pending_media(
        db=db_session,
        loader=lambda item: VerifiedEnrichmentResult(
            source=EnrichmentSource.CROSSREF,
            description="Verified study",
            external_ids={"doi": "10.1000/example"},
            metadata={
                "title": "Canonical Study",
                "also_known_as": ["Study Alias"],
            },
            confidence=0.96,
            verified_by=[EnrichmentSource.CROSSREF, EnrichmentSource.PUBMED],
            doi_verified=True,
        ),
        now=now,
    )
    db_session.commit()

    assert_that(result.processed_count).is_equal_to(1)
    assert_that(result.enriched_count).is_equal_to(1)
    assert_that(media.description).is_equal_to("Verified study")
    assert_that(media.doi).is_equal_to("10.1000/example")
    assert_that(media.verification_sources).is_equal_to(["crossref", "pubmed"])
    assert_that(media.doi_verified).is_true()
    assert_that(media.enriched_at).is_equal_to(now.replace(tzinfo=None))
    aliases = (
        db_session.query(MediaAlias)
        .filter(MediaAlias.media_id == media.id)
        .order_by(MediaAlias.alias.asc())
        .all()
    )
    assert_that([alias.alias for alias in aliases]).contains(
        "Canonical Study",
        "Original Study",
        "Study Alias",
    )


def test_enrich_pending_media_skips_low_confidence_results(
    db_session: Session,
) -> None:
    """Verify enrichment batch does not apply low-confidence results."""
    media = Media(type="book", title="Low Confidence Book")
    db_session.add(media)
    db_session.flush()

    result = enrich_pending_media(
        db=db_session,
        loader=lambda item: VerifiedEnrichmentResult(
            source=EnrichmentSource.GOOGLE_BOOKS,
            external_ids={"google_books_id": "low"},
            confidence=0.2,
        ),
        min_confidence=0.7,
    )

    assert_that(result.processed_count).is_equal_to(1)
    assert_that(result.enriched_count).is_zero()
    assert_that(media.google_books_id).is_none()
