"""Tests for media enrichment pipeline helpers."""

from datetime import UTC, datetime
from typing import cast

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Media, MediaAlias
from podex.services.enrichment.base import (
    EnrichmentResult,
    EnrichmentSource,
    VerifiedEnrichmentResult,
)
from podex.services.media_enrichment import MediaEnricher
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


class _FakeEnricher:
    """Enricher double recording calls and delegating apply logic."""

    def __init__(self, result: EnrichmentResult | None) -> None:
        self.result = result
        self.close_calls = 0
        self.seen_min_confidence: float | None = None

    def enrich(
        self,
        media: Media,
        min_confidence: float = 0.7,
    ) -> EnrichmentResult | None:
        """Record the threshold and return the canned result."""
        del media
        self.seen_min_confidence = min_confidence
        return self.result

    def apply_enrichment(self, media: Media, result: EnrichmentResult) -> None:
        """Delegate to the real MediaEnricher application logic."""
        MediaEnricher.apply_enrichment(
            cast("MediaEnricher", self),
            media,
            result,
        )

    def close(self) -> None:
        """Record closure."""
        self.close_calls += 1


def _ordinary_result(confidence: float = 0.9) -> EnrichmentResult:
    """Build an ordinary (unverified) enrichment result."""
    return EnrichmentResult(
        source=EnrichmentSource.ITUNES,
        description="A podcast.",
        external_ids={"apple_podcasts_id": "ap1", "isbn_13": "978"},
        metadata={"title": "Canonical Podcast"},
        confidence=confidence,
    )


def test_enrich_pending_media_does_not_close_injected_enricher(
    db_session: Session,
) -> None:
    """A caller-provided enricher is never closed by the pipeline."""
    media = Media(type="podcast", title="Injected Podcast")
    db_session.add(media)
    db_session.flush()
    fake = _FakeEnricher(_ordinary_result())

    result = enrich_pending_media(
        db=db_session,
        enricher=cast("MediaEnricher", fake),
    )

    assert_that(result.enriched_count).is_equal_to(1)
    assert_that(fake.close_calls).is_zero()


def test_enrich_pending_media_closes_owned_enricher(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    """An internally created enricher is closed exactly once."""
    media = Media(type="podcast", title="Owned Podcast")
    db_session.add(media)
    db_session.flush()
    created: list[_FakeEnricher] = []

    def _factory() -> _FakeEnricher:
        fake = _FakeEnricher(None)
        created.append(fake)
        return fake

    monkeypatch.setattr(
        "podex.services.media_enrichment_pipeline.MediaEnricher",
        _factory,
    )

    result = enrich_pending_media(db=db_session)

    assert_that(result.enriched_count).is_zero()
    assert_that(created).is_length(1)
    assert_that(created[0].close_calls).is_equal_to(1)


def test_enrich_pending_media_closes_owned_enricher_on_error(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    """The owned enricher is closed even when enrichment raises."""
    media = Media(type="podcast", title="Raising Podcast")
    db_session.add(media)
    db_session.flush()

    class _RaisingEnricher(_FakeEnricher):
        def enrich(
            self,
            media: Media,
            min_confidence: float = 0.7,
        ) -> EnrichmentResult | None:
            del media, min_confidence
            raise RuntimeError("enrichment failed")

    created: list[_FakeEnricher] = []

    def _factory() -> _FakeEnricher:
        fake = _RaisingEnricher(None)
        created.append(fake)
        return fake

    monkeypatch.setattr(
        "podex.services.media_enrichment_pipeline.MediaEnricher",
        _factory,
    )

    with pytest.raises(RuntimeError, match="enrichment failed"):
        enrich_pending_media(db=db_session)

    assert_that(created).is_length(1)
    assert_that(created[0].close_calls).is_equal_to(1)


def test_enrich_pending_media_passes_min_confidence_and_now(
    db_session: Session,
) -> None:
    """The enricher path honors min_confidence and the deterministic now."""
    now = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
    media = Media(type="podcast", title="Threshold Podcast")
    db_session.add(media)
    db_session.flush()
    fake = _FakeEnricher(_ordinary_result(confidence=0.5))

    result = enrich_pending_media(
        db=db_session,
        enricher=cast("MediaEnricher", fake),
        min_confidence=0.4,
        now=now,
    )
    db_session.commit()

    assert_that(fake.seen_min_confidence).is_equal_to(0.4)
    assert_that(result.enriched_count).is_equal_to(1)
    assert_that(media.enriched_at).is_equal_to(now.replace(tzinfo=None))


def test_enrich_pending_media_persists_extra_external_ids(
    db_session: Session,
) -> None:
    """Unmapped provider identifiers survive both persistence paths."""
    media = Media(type="podcast", title="Extra Ids Podcast")
    db_session.add(media)
    db_session.flush()

    enrich_pending_media(
        db=db_session,
        loader=lambda item: _ordinary_result(),
    )
    db_session.commit()

    assert_that(media.metadata_json or {}).contains_key("external_ids")
    assert_that((media.metadata_json or {})["external_ids"]).is_equal_to(
        {"apple_podcasts_id": "ap1", "isbn_13": "978"},
    )


def test_enrich_pending_media_second_run_processes_zero(
    db_session: Session,
) -> None:
    """A successful enricher-path run leaves nothing pending."""
    media = Media(type="podcast", title="Once Only Podcast")
    db_session.add(media)
    db_session.flush()
    fake = _FakeEnricher(_ordinary_result())

    first = enrich_pending_media(
        db=db_session,
        enricher=cast("MediaEnricher", fake),
    )
    db_session.commit()

    assert_that(first.enriched_count).is_equal_to(1)
    assert_that(media.enriched_at).is_not_none()
    assert_that(media.verification_sources).is_equal_to(["itunes"])

    def _unexpected(item: Media) -> EnrichmentResult | None:
        raise AssertionError("no media should still be pending")

    second = enrich_pending_media(db=db_session, loader=_unexpected)

    assert_that(second.processed_count).is_zero()
