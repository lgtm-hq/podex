"""Tests for ops media canonicalization commands."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from assertpy import assert_that
from sqlalchemy import Column, func, select
from sqlalchemy.orm import Session

from podex.api.query_helpers import (
    episode_count_by_media_subquery,
    episode_count_by_podcast_subquery,
    mention_count_by_episode_subquery,
    mention_count_by_media_subquery,
)
from podex.models import (
    AccountAlertEvent,
    AccountAlertRule,
    AccountSavedMedia,
    AccountUser,
    Base,
    Episode,
    GraphTriple,
    GraphTripleObjectKind,
    Media,
    MediaAlias,
    MediaAliasSourceType,
    MediaExternalRef,
    MediaExternalRefSource,
    MediaRelation,
    MediaRelationType,
    MediaSummary,
    MediaType,
    Mention,
    MentionCandidate,
    MentionCandidateProvenance,
    MentionCandidateProvenanceEventType,
    Podcast,
    ReviewItem,
)
from podex.services import ops_media_commands
from podex.services.account_alerts import evaluate_alert_rules, set_alert_rule_enabled
from podex.services.account_digests import send_pending_digest
from podex.services.graph_relations import (
    GraphTripleInputData,
    stable_media_relation_key,
    upsert_graph_triple,
    upsert_media_external_ref,
    upsert_media_relation,
)
from podex.services.media_alias_repository import ensure_media_alias
from podex.services.ops_media_commands import (
    SplitOpsMediaInputData,
    UpdateOpsMediaInputData,
    UpsertOpsMediaExternalRefInputData,
    add_ops_media_alias,
    get_ops_media_detail,
    merge_ops_media,
    preview_ops_media_merge,
    split_ops_media,
    update_ops_media,
    upsert_ops_media_external_ref,
)
from tests.conftest import create_media_alert_rule

_MERGE_POLICY_BY_MEDIA_COLUMN = {
    "account_saved_media.media_id": "move",
    "graph_triples.object_media_id": "move",
    "graph_triples.subject_media_id": "move",
    "media_aliases.media_id": "move",
    "media_external_refs.media_id": "move",
    "media_relations.object_media_id": "move",
    "media_relations.subject_media_id": "move",
    "media_summaries.media_id": "delete",
    "mention_candidate_provenance.media_id": "move",
    "mention_candidates.media_id": "move",
    "mentions.media_id": "move",
    "review_items.target_media_id": "move",
}


def test_preview_ops_media_merge_reports_fields_aliases_and_mentions(
    db_session: Session,
) -> None:
    """Verify merge preview reports non-mutating canonicalization effects."""
    podcast = Podcast(name="Preview Podcast", slug="preview-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Preview Episode")
    source = Media(type="book", title="Source Title", author="Source Author")
    target = Media(type="book", title="Target Title")
    db_session.add_all([episode, source, target])
    db_session.flush()
    ensure_media_alias(
        db=db_session,
        media=source,
        alias="Alternate Source",
        source=MediaAliasSourceType.MANUAL,
    )
    db_session.add(Mention(episode_id=episode.id, media_id=source.id))
    db_session.flush()

    preview = preview_ops_media_merge(
        db=db_session,
        source_media_id=source.id,
        target_media_id=target.id,
    )

    assert_that(preview).is_not_none()
    if preview is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(preview.mentions_to_move).is_equal_to(1)
    assert_that([change.field for change in preview.field_changes]).contains("author")
    assert_that([alias.alias for alias in preview.alias_additions]).contains(
        "Source Title",
        "Alternate Source",
    )
    assert_that(
        db_session.query(Media).filter(Media.id == source.id).count(),
    ).is_equal_to(1)


def test_merge_ops_media_moves_source_aliases_to_target(
    db_session: Session,
) -> None:
    """Verify canonical merges preserve source aliases on the target."""
    source = Media(type="book", title="Source Title")
    target = Media(type="book", title="Target Title")
    db_session.add_all([source, target])
    db_session.flush()
    ensure_media_alias(
        db=db_session,
        media=source,
        alias="Alternate Source",
        source=MediaAliasSourceType.MANUAL,
    )
    db_session.commit()

    result = merge_ops_media(
        db=db_session,
        source_media_id=source.id,
        target_media_id=target.id,
    )
    db_session.commit()

    assert_that(result).is_not_none()
    if result is None:  # pragma: no cover - narrowed above
        raise AssertionError
    aliases = (
        db_session.query(MediaAlias)
        .filter(MediaAlias.media_id == target.id)
        .order_by(MediaAlias.alias.asc())
        .all()
    )
    assert_that([alias.alias for alias in aliases]).contains(
        "Alternate Source",
        "Source Title",
        "Target Title",
    )
    assert_that(db_session.query(Media).filter(Media.id == source.id).count()).is_zero()


def test_update_split_alias_and_external_ref_services(
    db_session: Session,
) -> None:
    """Edit, alias, external-ref, and split services operate directly."""

    podcast = Podcast(name="Show", slug="ops-split-show")
    db_session.add(podcast)
    db_session.commit()
    episode = Episode(podcast_id=podcast.id, title="Ep")
    media = Media(type=MediaType.BOOK, title="Dune", author="Frank Herbert")
    db_session.add_all([episode, media])
    db_session.commit()
    mentions = [
        Mention(episode_id=episode.id, media_id=media.id, context="one"),
        Mention(episode_id=episode.id, media_id=media.id, context="two"),
    ]
    db_session.add_all(mentions)
    db_session.commit()

    updated = update_ops_media(
        db=db_session,
        media_id=media.id,
        payload=UpdateOpsMediaInputData(
            provided_fields=frozenset({"description", "year"}),
            description="Edited description.",
            year=1965,
        ),
    )
    assert_that(updated).is_not_none()

    alias_detail = add_ops_media_alias(
        db=db_session,
        media_id=media.id,
        alias="Dune (Novel)",
    )
    assert_that(alias_detail).is_not_none()

    ref_detail = upsert_ops_media_external_ref(
        db=db_session,
        media_id=media.id,
        payload=UpsertOpsMediaExternalRefInputData(
            source=MediaExternalRefSource.WIKIPEDIA,
            external_id="Dune_(novel)",
            url="https://en.wikipedia.org/wiki/Dune_(novel)",
        ),
    )
    assert_that(ref_detail).is_not_none()

    split = split_ops_media(
        db=db_session,
        media_id=media.id,
        payload=SplitOpsMediaInputData(
            mention_ids=(mentions[0].id,),
            type=MediaType.MOVIE,
            title="Dune (1984 film)",
        ),
    )
    assert_that(split).is_not_none()
    if split is not None:
        assert_that(split.mentions_moved).is_equal_to(1)

    detail = get_ops_media_detail(db=db_session, media_id=media.id)
    assert_that(detail).is_not_none()
    assert_that(get_ops_media_detail(db=db_session, media_id=99999)).is_none()


def test_media_relation_upserts_and_query_helpers(db_session: Session) -> None:
    """Relation upserts are idempotent; query helpers decode identifiers."""

    a = Media(type=MediaType.BOOK, title="Dune")
    b = Media(type=MediaType.MOVIE, title="Dune (1984)")
    db_session.add_all([a, b])
    db_session.commit()

    first = upsert_media_relation(
        db=db_session,
        subject_media_id=b.id,
        object_media_id=a.id,
        relation_type=MediaRelationType.ADAPTED_FROM,
        source="review",
        confidence=0.9,
        evidence_text="  adapted from the novel  ",
    )
    second = upsert_media_relation(
        db=db_session,
        subject_media_id=b.id,
        object_media_id=a.id,
        relation_type=MediaRelationType.ADAPTED_FROM,
        source="review",
        confidence=0.95,
    )
    assert_that(second.id).is_equal_to(first.id)
    assert_that(second.confidence).is_equal_to(0.95)

    ref = upsert_media_external_ref(
        db=db_session,
        media=a,
        source=MediaExternalRefSource.WIKIPEDIA,
        external_id="Dune_(novel)",
        url="https://en.wikipedia.org/wiki/Dune_(novel)",
    )
    again = upsert_media_external_ref(
        db=db_session,
        media=a,
        source=MediaExternalRefSource.WIKIPEDIA,
        external_id="Dune_(novel)",
        url="https://en.wikipedia.org/wiki/Dune_(novel)?rev=2",
    )
    assert_that(again.id).is_equal_to(ref.id)

    subquery = mention_count_by_media_subquery(db_session)
    assert_that(subquery).is_not_none()


def test_query_helper_subqueries_and_edit_edge_cases(
    db_session: Session,
) -> None:
    """Remaining query helpers build; edit edge cases behave."""

    assert_that(episode_count_by_media_subquery(db_session)).is_not_none()
    assert_that(episode_count_by_podcast_subquery(db_session)).is_not_none()
    assert_that(mention_count_by_episode_subquery(db_session)).is_not_none()

    media = Media(type=MediaType.BOOK, title="Edge")
    db_session.add(media)
    db_session.commit()

    missing = update_ops_media(
        db=db_session,
        media_id=99999,
        payload=UpdateOpsMediaInputData(),
    )
    assert_that(missing).is_none()
    assert_that(
        add_ops_media_alias(db=db_session, media_id=99999, alias="X"),
    ).is_none()

    try:
        update_ops_media(
            db=db_session,
            media_id=media.id,
            payload=UpdateOpsMediaInputData(
                provided_fields=frozenset({"title"}),
                title="   ",
            ),
        )
        cleared_ok = True
    except ValueError:
        cleared_ok = False
    assert_that(cleared_ok).is_false()

    typed = update_ops_media(
        db=db_session,
        media_id=media.id,
        payload=UpdateOpsMediaInputData(
            provided_fields=frozenset({"type", "imdb_id", "tmdb_id"}),
            type=MediaType.MOVIE,
            imdb_id="tt5555555",
            tmdb_id=99,
        ),
    )
    assert_that(typed).is_not_none()


def test_merge_repoints_derivative_data_to_target(db_session: Session) -> None:
    """Merging re-points refs, relations, and triples; drops source summaries."""

    source = Media(type=MediaType.BOOK, title="Dune (dupe)")
    target = Media(type=MediaType.BOOK, title="Dune")
    other = Media(type=MediaType.MOVIE, title="Dune (1984)")
    db_session.add_all([source, target, other])
    db_session.commit()

    db_session.add(
        MediaExternalRef(
            media_id=source.id,
            source="wikipedia",
            external_id="Dune_(novel)",
        ),
    )
    upsert_media_relation(
        db=db_session,
        subject_media_id=other.id,
        object_media_id=source.id,
        relation_type=MediaRelationType.ADAPTED_FROM,
        source="review",
    )
    upsert_graph_triple(
        db=db_session,
        payload=GraphTripleInputData(
            subject_media_id=source.id,
            predicate="published_year",
            source="test",
            object_value="1965",
        ),
    )
    db_session.add(
        MediaSummary(
            media_id=source.id,
            summary_key="dupe:overview:v1",
            summary_kind="overview",
            pipeline_version="v1",
            prompt_version="p1",
            source_text_hash="e" * 64,
            source_model="claude-opus-4-8",
            summary_text="Summary of the duplicate record.",
            generated_at=datetime(2026, 7, 18, tzinfo=UTC),
        ),
    )
    db_session.commit()

    result = merge_ops_media(
        db=db_session,
        source_media_id=source.id,
        target_media_id=target.id,
    )
    db_session.commit()

    assert_that(result).is_not_none()
    refs = db_session.query(MediaExternalRef).all()
    assert_that(refs).is_length(1)
    assert_that(refs[0].media_id).is_equal_to(target.id)
    relations = db_session.query(MediaRelation).all()
    assert_that(relations).is_length(1)
    assert_that(relations[0].object_media_id).is_equal_to(target.id)
    assert_that(relations[0].relation_key).is_equal_to(
        stable_media_relation_key(
            subject_media_id=other.id,
            object_media_id=target.id,
            relation_type=MediaRelationType.ADAPTED_FROM,
            source="review",
        ),
    )
    triples = db_session.query(GraphTriple).all()
    assert_that(triples).is_length(1)
    assert_that(triples[0].subject_media_id).is_equal_to(target.id)
    assert_that(triples[0].object_kind).is_equal_to(
        GraphTripleObjectKind.LITERAL.value,
    )
    assert_that(db_session.query(MediaSummary).count()).is_equal_to(0)


@pytest.mark.parametrize(
    "survivor_saved",
    [False, True],
    ids=["source-only", "source-and-survivor"],
)
def test_merge_repoints_saved_media(
    db_session: Session,
    survivor_saved: bool,
) -> None:
    """Saves move to the survivor and collapse to one row per user."""
    source = Media(type=MediaType.BOOK, title="Dune (dupe)")
    target = Media(type=MediaType.BOOK, title="Dune")
    user = AccountUser(email="saver@example.com")
    db_session.add_all([source, target, user])
    db_session.flush()
    db_session.add(AccountSavedMedia(user_id=user.id, media_id=source.id))
    if survivor_saved:
        db_session.add(AccountSavedMedia(user_id=user.id, media_id=target.id))
    db_session.commit()

    result = merge_ops_media(
        db=db_session,
        source_media_id=source.id,
        target_media_id=target.id,
    )
    db_session.commit()

    assert_that(result).is_not_none()
    saves = db_session.query(AccountSavedMedia).all()
    assert_that([(save.user_id, save.media_id) for save in saves]).is_equal_to(
        [(user.id, target.id)],
    )


def _media_reference_columns() -> dict[str, Column[int]]:
    """Map every ``table.column`` with a foreign key to ``media.id``."""
    return {
        f"{table.name}.{column.name}": column
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if any(fk.target_fullname == "media.id" for fk in column.foreign_keys)
    }


def _count_references(db: Session, column: Column[int], media_id: int) -> int:
    """Count rows whose media reference column holds ``media_id``."""
    return int(
        db.execute(
            select(func.count()).select_from(column.table).where(column == media_id),
        ).scalar_one(),
    )


def _seed_merge_pair(db: Session) -> tuple[Episode, Media, Media]:
    """Persist one episode and a same-type source/target pair with a mention each."""
    episode = persist_episode_for_merge(db)
    source = Media(type=MediaType.BOOK, title="Dune (dupe)")
    target = Media(type=MediaType.BOOK, title="Dune")
    db.add_all([source, target])
    db.flush()
    db.add_all(
        [
            Mention(episode_id=episode.id, media_id=source.id),
            Mention(episode_id=episode.id, media_id=target.id),
        ],
    )
    db.flush()
    return episode, source, target


def persist_episode_for_merge(db: Session) -> Episode:
    """Persist the podcast and episode that merge fixtures mention media in."""
    podcast = Podcast(name="Merge Show", slug="merge-show")
    db.add(podcast)
    db.flush()
    episode = Episode(podcast_id=podcast.id, title="Merge Episode")
    db.add(episode)
    db.flush()
    return episode


def _create_saving_user(db: Session, *media: Media) -> AccountUser:
    """Persist a user who has saved each given media record."""
    user = AccountUser(email="merge-reader@example.com")
    db.add(user)
    db.flush()
    db.add_all(AccountSavedMedia(user_id=user.id, media_id=item.id) for item in media)
    db.flush()
    return user


def _require_rule(rule: AccountAlertRule | None) -> AccountAlertRule:
    """Narrow an alert rule created for an eligible target."""
    if rule is None:  # pragma: no cover - fixtures always save the target first
        raise AssertionError("expected an alert rule")
    return rule


def test_merge_resolves_every_media_reference(db_session: Session) -> None:
    """Every media foreign key and alert target has a merge policy and no orphan."""
    episode, source, target = _seed_merge_pair(db_session)
    other = Media(type=MediaType.MOVIE, title="Dune (1984)")
    db_session.add(other)
    db_session.flush()
    ensure_media_alias(
        db=db_session,
        media=source,
        alias="Dune Novel",
        source=MediaAliasSourceType.MANUAL,
    )
    db_session.add(
        MediaExternalRef(media_id=source.id, source="wikipedia", external_id="Dune"),
    )
    for subject_id, object_id in ((source.id, other.id), (other.id, source.id)):
        upsert_media_relation(
            db=db_session,
            subject_media_id=subject_id,
            object_media_id=object_id,
            relation_type=MediaRelationType.ADAPTED_FROM,
            source="review",
        )
        upsert_graph_triple(
            db=db_session,
            payload=GraphTripleInputData(
                subject_media_id=subject_id,
                predicate=MediaRelationType.REFERENCES.value,
                source="test",
                object_media_id=object_id,
            ),
        )
    db_session.add(
        MediaSummary(
            media_id=source.id,
            summary_key="dupe:overview:v1",
            summary_kind="overview",
            pipeline_version="v1",
            prompt_version="p1",
            source_text_hash="f" * 64,
            source_model="test-model",
            summary_text="Summary of the duplicate record.",
            generated_at=datetime(2026, 7, 18, tzinfo=UTC),
        ),
    )
    candidate = MentionCandidate(
        episode_id=episode.id,
        media_id=source.id,
        media_type="book",
        raw_title="Dune",
    )
    db_session.add(candidate)
    db_session.flush()
    db_session.add_all(
        [
            MentionCandidateProvenance(
                mention_candidate_id=candidate.id,
                media_id=source.id,
                event_type=MentionCandidateProvenanceEventType.CREATED.value,
                raw_title="Dune",
            ),
            ReviewItem(mention_candidate_id=candidate.id, target_media_id=source.id),
        ],
    )
    user = _create_saving_user(db_session, source)
    _require_rule(
        create_media_alert_rule(db=db_session, user_id=user.id, media_id=source.id),
    )
    db_session.commit()
    columns = _media_reference_columns()
    assert_that(sorted(columns)).is_equal_to(sorted(_MERGE_POLICY_BY_MEDIA_COLUMN))
    for name, column in columns.items():
        assert_that(_count_references(db_session, column, source.id)).described_as(
            name,
        ).is_positive()

    merge_ops_media(
        db=db_session,
        source_media_id=source.id,
        target_media_id=target.id,
    )
    db_session.commit()

    assert_that(db_session.get(Media, source.id)).is_none()
    for name, column in columns.items():
        assert_that(_count_references(db_session, column, source.id)).described_as(
            name,
        ).is_zero()
        target_rows = _count_references(db_session, column, target.id)
        if _MERGE_POLICY_BY_MEDIA_COLUMN[name] == "move":
            assert_that(target_rows).described_as(name).is_positive()
        else:
            assert_that(target_rows).described_as(name).is_zero()
    rule_targets = db_session.execute(
        select(AccountAlertRule.target_type, AccountAlertRule.target_id),
    ).all()
    assert_that([tuple(row) for row in rule_targets]).is_equal_to(
        [("media", target.id)],
    )


@pytest.mark.parametrize("rules_enabled", [True, False], ids=["enabled", "paused"])
def test_merge_preserves_media_alert_events(
    db_session: Session,
    rules_enabled: bool,
) -> None:
    """Colliding rules consolidate without losing, orphaning or repeating events."""
    episode, source, target = _seed_merge_pair(db_session)
    user = _create_saving_user(db_session, source, target)
    source_rule = _require_rule(
        create_media_alert_rule(db=db_session, user_id=user.id, media_id=source.id),
    )
    target_rule = _require_rule(
        create_media_alert_rule(db=db_session, user_id=user.id, media_id=target.id),
    )
    db_session.add(Mention(episode_id=episode.id, media_id=target.id))
    db_session.flush()
    evaluate_alert_rules(db=db_session, user_id=user.id)
    delivered_digest = send_pending_digest(db=db_session, user=user, sender=Mock())
    db_session.add(Mention(episode_id=episode.id, media_id=source.id))
    db_session.flush()
    evaluate_alert_rules(db=db_session, user_id=user.id)
    for rule in (source_rule, target_rule):
        set_alert_rule_enabled(
            db=db_session,
            user_id=user.id,
            rule_id=rule.id,
            enabled=rules_enabled,
        )
    db_session.commit()
    if delivered_digest is None:  # pragma: no cover - one event was pending
        raise AssertionError("expected a delivered digest")
    events_before = {
        event.id: event.digest_id for event in db_session.query(AccountAlertEvent)
    }
    assert_that(sorted(events_before.values(), key=str)).is_equal_to(
        [delivered_digest.id, None],
    )

    merge_ops_media(
        db=db_session,
        source_media_id=source.id,
        target_media_id=target.id,
    )
    db_session.commit()

    rules = db_session.query(AccountAlertRule).all()
    assert_that([rule.id for rule in rules]).is_equal_to([target_rule.id])
    assert_that(rules[0].enabled).is_equal_to(rules_enabled)
    assert_that(rules[0].baseline_count).is_equal_to(4)
    events_after = db_session.query(AccountAlertEvent).all()
    assert_that({event.id: event.digest_id for event in events_after}).is_equal_to(
        events_before,
    )
    assert_that({event.rule_id for event in events_after}).is_equal_to(
        {target_rule.id},
    )

    pending_digest = send_pending_digest(db=db_session, user=user, sender=Mock())
    if pending_digest is None:  # pragma: no cover - asserted through body below
        raise AssertionError("expected the pending event to render")
    assert_that(pending_digest.event_count).is_equal_to(1)
    assert_that(pending_digest.body_text).contains("- 1 new mention of Dune\n")
    assert_that(evaluate_alert_rules(db=db_session, user_id=user.id)).is_empty()


@pytest.mark.parametrize("rule_side", ["source", "target"])
def test_merge_reconciles_single_alert_rule_baseline(
    db_session: Session,
    rule_side: str,
) -> None:
    """A rule on one side counts the other side's history as already seen."""
    episode, source, target = _seed_merge_pair(db_session)
    db_session.add(Mention(episode_id=episode.id, media_id=source.id))
    user = _create_saving_user(db_session, source, target)
    watched = source if rule_side == "source" else target
    rule = _require_rule(
        create_media_alert_rule(db=db_session, user_id=user.id, media_id=watched.id),
    )
    db_session.commit()

    merge_ops_media(
        db=db_session,
        source_media_id=source.id,
        target_media_id=target.id,
    )
    db_session.commit()

    assert_that((rule.target_id, rule.baseline_count)).is_equal_to((target.id, 3))
    assert_that(evaluate_alert_rules(db=db_session, user_id=user.id)).is_empty()
    db_session.add(Mention(episode_id=episode.id, media_id=target.id))
    db_session.flush()
    generated = evaluate_alert_rules(db=db_session, user_id=user.id)
    assert_that(
        [(item.event.previous_count, item.event.observed_count) for item in generated],
    ).is_equal_to([(3, 4)])


def test_merge_refuses_alert_rules_with_conflicting_enabled_state(
    db_session: Session,
) -> None:
    """A paused rule colliding with an enabled one blocks the merge unchanged."""
    _, source, target = _seed_merge_pair(db_session)
    user = _create_saving_user(db_session, source, target)
    source_rule = _require_rule(
        create_media_alert_rule(db=db_session, user_id=user.id, media_id=source.id),
    )
    _require_rule(
        create_media_alert_rule(db=db_session, user_id=user.id, media_id=target.id),
    )
    set_alert_rule_enabled(
        db=db_session,
        user_id=user.id,
        rule_id=source_rule.id,
        enabled=False,
    )
    db_session.commit()

    with pytest.raises(ValueError, match="disagree on enabled state"):
        merge_ops_media(
            db=db_session,
            source_media_id=source.id,
            target_media_id=target.id,
        )

    assert_that(db_session.dirty).is_empty()
    assert_that(db_session.deleted).is_empty()
    assert_that(db_session.query(AccountAlertRule).count()).is_equal_to(2)


def test_failed_merge_rolls_back_reference_changes(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after references moved leaves every reference on the source."""
    episode, source, target = _seed_merge_pair(db_session)
    user = _create_saving_user(db_session, source)
    rule = _require_rule(
        create_media_alert_rule(db=db_session, user_id=user.id, media_id=source.id),
    )
    candidate = MentionCandidate(
        episode_id=episode.id,
        media_id=source.id,
        media_type="book",
        raw_title="Dune",
    )
    db_session.add(candidate)
    db_session.commit()

    def fail_derivatives(**_: object) -> None:
        """Fail after account and extraction references were flushed."""
        raise RuntimeError("derivative repoint failed")

    monkeypatch.setattr(
        ops_media_commands,
        "_repoint_derivatives_to_target",
        fail_derivatives,
    )
    with pytest.raises(RuntimeError, match="derivative repoint failed"):
        merge_ops_media(
            db=db_session,
            source_media_id=source.id,
            target_media_id=target.id,
        )
    db_session.rollback()

    assert_that(db_session.get(Media, source.id)).is_not_none()
    assert_that(
        [save.media_id for save in db_session.query(AccountSavedMedia)],
    ).is_equal_to([source.id])
    assert_that((rule.target_id, rule.baseline_count)).is_equal_to((source.id, 1))
    assert_that(candidate.media_id).is_equal_to(source.id)
    assert_that(
        db_session.query(Mention).filter(Mention.media_id == source.id).count(),
    ).is_equal_to(1)
