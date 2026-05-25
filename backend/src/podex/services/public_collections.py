"""Published editorial collection read model."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from podex.models import EditorialCollection, EditorialCollectionItem, Media


@dataclass(frozen=True)
class EditorialCollectionDetailData:
    """Published collection together with its ordered public records."""

    collection: EditorialCollection
    items: list[Media]


def list_published_collections(*, db: Session) -> list[tuple[EditorialCollection, int]]:
    """List published curated collections with visible item counts."""
    collections = (
        db.query(EditorialCollection)
        .filter(EditorialCollection.published.is_(True))
        .order_by(
            EditorialCollection.featured.desc(),
            EditorialCollection.updated_at.desc(),
        )
        .all()
    )
    return [
        (
            collection,
            db.query(EditorialCollectionItem)
            .filter(EditorialCollectionItem.collection_id == collection.id)
            .count(),
        )
        for collection in collections
    ]


def get_published_collection(
    *,
    db: Session,
    slug: str,
) -> EditorialCollectionDetailData | None:
    """Get one published collection with its ordered public catalog items."""
    collection = (
        db.query(EditorialCollection)
        .filter(
            EditorialCollection.slug == slug,
            EditorialCollection.published.is_(True),
        )
        .first()
    )
    if collection is None:
        return None
    items = (
        db.query(Media)
        .join(EditorialCollectionItem, EditorialCollectionItem.media_id == Media.id)
        .filter(EditorialCollectionItem.collection_id == collection.id)
        .order_by(EditorialCollectionItem.position, EditorialCollectionItem.id)
        .all()
    )
    return EditorialCollectionDetailData(collection=collection, items=items)
