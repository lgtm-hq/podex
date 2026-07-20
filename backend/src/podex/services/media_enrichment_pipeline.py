"""Pipeline helpers for applying media enrichment in batches."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from podex.models import Media, MediaAliasSourceType
from podex.services.enrichment.base import EnrichmentResult, VerifiedEnrichmentResult
from podex.services.media_alias_repository import ensure_media_alias
from podex.services.media_enrichment import MediaEnricher, apply_external_ids


@dataclass(frozen=True, slots=True)
class MediaEnrichmentRunItemData:
    """Single media enrichment pipeline result."""

    media_id: int
    enriched: bool
    source: str | None
    confidence: float | None
    external_ids: dict[str, str | int]
    verification_sources: list[str]


@dataclass(frozen=True, slots=True)
class MediaEnrichmentRunData:
    """Batch media enrichment pipeline result."""

    processed_count: int
    enriched_count: int
    items: list[MediaEnrichmentRunItemData]


EnrichmentLoader = Callable[[Media], EnrichmentResult | None]


def enrich_pending_media(
    *,
    db: Session,
    enricher: MediaEnricher | None = None,
    loader: EnrichmentLoader | None = None,
    limit: int = 25,
    min_confidence: float = 0.7,
    now: datetime | None = None,
) -> MediaEnrichmentRunData:
    """Enrich media records that still lack external verification.

    Args:
        db: Database session.
        enricher: Optional configured media enricher.
        loader: Optional test or alternate enrichment loader.
        limit: Maximum media records to process.
        min_confidence: Minimum enrichment confidence to apply.
        now: Deterministic enrichment timestamp.

    Returns:
        Batch enrichment summary.
    """
    owns_enricher = enricher is None and loader is None
    if owns_enricher:
        enricher = MediaEnricher()

    effective_now = now or datetime.now(UTC)
    try:
        media_items = (
            db.query(Media)
            .filter(
                or_(
                    Media.enriched_at.is_(None),
                    Media.verification_sources.is_(None),
                ),
            )
            .order_by(Media.created_at.asc(), Media.id.asc())
            .limit(limit)
            .all()
        )
        run_items: list[MediaEnrichmentRunItemData] = []

        for media in media_items:
            if loader is not None:
                result = loader(media)
            else:
                if enricher is None:
                    raise RuntimeError("Media enricher was not configured")
                result = enricher.enrich(media, min_confidence=min_confidence)
            if result is None or result.confidence < min_confidence:
                run_items.append(
                    MediaEnrichmentRunItemData(
                        media_id=media.id,
                        enriched=False,
                        source=None,
                        confidence=None,
                        external_ids={},
                        verification_sources=[],
                    )
                )
                continue

            if enricher is not None:
                enricher.apply_enrichment(media=media, result=result)
                media.enriched_at = effective_now
            else:
                _apply_enrichment_result(
                    media=media,
                    result=result,
                    now=effective_now,
                )

            ensure_media_alias(
                db=db,
                media=media,
                alias=media.title,
                source=MediaAliasSourceType.ENRICHMENT,
                is_primary=True,
            )
            for alias in _extract_enrichment_aliases(metadata=result.metadata):
                ensure_media_alias(
                    db=db,
                    media=media,
                    alias=alias,
                    source=MediaAliasSourceType.ENRICHMENT,
                )

            run_items.append(
                MediaEnrichmentRunItemData(
                    media_id=media.id,
                    enriched=True,
                    source=result.source.value,
                    confidence=result.confidence,
                    external_ids=result.external_ids,
                    verification_sources=media.verification_sources or [],
                )
            )

        db.flush()
        return MediaEnrichmentRunData(
            processed_count=len(run_items),
            enriched_count=sum(1 for item in run_items if item.enriched),
            items=run_items,
        )
    finally:
        if owns_enricher and enricher is not None:
            enricher.close()


def _apply_enrichment_result(
    *,
    media: Media,
    result: EnrichmentResult,
    now: datetime,
) -> None:
    """Apply enrichment result fields without constructing provider clients."""
    if result.cover_url and not media.cover_url:
        media.cover_url = result.cover_url
    if result.description and not media.description:
        media.description = result.description

    apply_external_ids(media=media, external_ids=result.external_ids)

    media.metadata_json = _merge_metadata(
        existing=media.metadata_json,
        incoming=result.metadata,
    )
    media.enriched_at = now
    media.enrichment_source = result.source.value
    media.enrichment_confidence = result.confidence

    if isinstance(result, VerifiedEnrichmentResult):
        media.verification_sources = [source.value for source in result.verified_by]
        media.doi_verified = result.doi_verified
    elif media.verification_sources is None:
        media.verification_sources = [result.source.value]


def _merge_metadata(
    *,
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any] | None:
    """Merge enrichment metadata while preserving existing values."""
    if not existing and not incoming:
        return existing
    return {
        **incoming,
        **(existing or {}),
    }


def _extract_enrichment_aliases(
    *,
    metadata: dict[str, Any],
) -> list[str]:
    """Extract alias-like title values from provider metadata."""
    aliases: list[str] = []
    for key in ("title", "subtitle", "original_title", "original_name"):
        value = metadata.get(key)
        if isinstance(value, str):
            aliases.append(value)

    for key in ("aliases", "also_known_as", "alternative_titles"):
        value = metadata.get(key)
        if isinstance(value, list):
            aliases.extend(item for item in value if isinstance(item, str))

    return aliases
