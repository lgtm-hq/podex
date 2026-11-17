"""Content derived indirectly from a taken-down episode: summaries, alerts, caches.

Media summaries are built from mention context across episodes, account alerts
and digests count mentions, and catalog stats are cached. Each must stop
reflecting suppressed content once the takedown commits.
"""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy import insert, inspect
from sqlalchemy.orm import Session

from podex.models import (
    AccountUser,
    Episode,
    Media,
    MediaSummary,
    MediaType,
    Mention,
    TakedownRequest,
)
from podex.services.account_alerts import create_alert_rule, evaluate_alert_rules
from podex.services.account_digests import send_pending_digest
from podex.services.account_saves import save_media
from podex.services.derivative_summaries import (
    GeneratedSummaryData,
    generate_media_summary,
)
from podex.services.ops_media_commands import merge_ops_media
from tests.conftest import SeededGraph, seed_catalog_graph
from tests.takedown_support import (
    deliver_nothing,
    sign_in_reader_with_alert,
    take_down_episode,
    take_down_mention,
    take_down_named,
)


def _media(db: Session, media_id: int) -> Media:
    return db.query(Media).filter(Media.id == media_id).one()


def _summary_provider() -> Mock:
    provider = Mock(model_name="summary-model")
    provider.summarize.return_value = GeneratedSummaryData(summary_text="summary")
    return provider


def _media_summary(media_id: int, text: str) -> MediaSummary:
    return MediaSummary(
        media_id=media_id,
        summary_key=f"media:{media_id}:{text}",
        summary_kind="overview",
        pipeline_version="v1",
        prompt_version="p1",
        source_text_hash="f" * 64,
        source_model="summary-model",
        summary_text=text,
        generated_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


def test_media_summary_omits_derivative_suppressed_episode_text(
    db_session: Session,
) -> None:
    """A derivative-only takedown keeps its episode's text out of media summaries."""
    graph = seed_catalog_graph(db_session)
    open_episode = Episode(podcast_id=graph.podcast_id, title="Open")
    db_session.add(open_episode)
    db_session.flush()
    db_session.add(
        Mention(
            episode_id=open_episode.id,
            media_id=graph.media_id,
            context="open context",
        ),
    )
    db_session.commit()
    take_down_episode(db_session, graph.episode_id, "suppress_derivatives")
    provider = _summary_provider()

    generate_media_summary(
        db=db_session,
        media=_media(db_session, graph.media_id),
        provider=provider,
        pipeline_version="v1",
        prompt_version="p1",
    )
    db_session.commit()

    source_text = provider.summarize.call_args.args[0].source_text
    assert_that(source_text).contains("open context")
    assert_that(source_text).does_not_contain("a great book")


@pytest.mark.parametrize(
    ("subject", "actions"),
    [
        ("episode", ["suppress_derivatives"]),
        ("episode", ["unpublish_mentions"]),
        ("podcast", ["suppress_derivatives"]),
        ("mention", ["unpublish_mentions"]),
    ],
)
def test_takedown_drops_media_summaries_built_from_its_text(
    db_session: Session,
    subject: str,
    actions: list[str],
) -> None:
    """Summaries of media mentioned in the covered content are removed."""
    graph = seed_catalog_graph(db_session)
    unrelated = Media(type=MediaType.MOVIE, title="Arrival")
    db_session.add(unrelated)
    db_session.flush()
    db_session.add_all(
        [
            _media_summary(graph.media_id, "built from a great book"),
            _media_summary(unrelated.id, "unrelated"),
        ],
    )
    db_session.commit()
    take_down_named(
        db_session,
        subject,
        {
            "episode": graph.episode_id,
            "podcast": graph.podcast_id,
            "mention": graph.mention_id,
        },
        actions,
    )

    remaining = [row.summary_text for row in db_session.query(MediaSummary)]
    assert_that(remaining).is_equal_to(["unrelated"])


def test_raw_only_takedown_keeps_media_summaries(db_session: Session) -> None:
    """Raw retention actions do not touch derived summaries."""
    graph = seed_catalog_graph(db_session)
    db_session.add(_media_summary(graph.media_id, "kept"))
    db_session.commit()

    take_down_episode(db_session, graph.episode_id, "suppress_raw_transcript")

    assert_that(db_session.query(MediaSummary).count()).is_equal_to(1)


def _alerting_user(db: Session, graph: SeededGraph) -> AccountUser:
    """Create a user with a media alert baselined on the current count."""
    user = AccountUser(email="reader@example.com")
    db.add(user)
    db.flush()
    save_media(db=db, user_id=user.id, media_id=graph.media_id)
    rule = create_alert_rule(
        db=db,
        user_id=user.id,
        target_type="media",
        target_id=graph.media_id,
        event_type="new_mention",
    )
    if rule is None:
        raise AssertionError("alert rule was not created")
    db.commit()
    return user


def test_alerts_and_digests_ignore_suppressed_mentions(db_session: Session) -> None:
    """A hidden mention generates no alert, digest line or export entry."""
    graph = seed_catalog_graph(db_session)
    take_down_mention(db_session, graph.mention_id)
    user = _alerting_user(db_session, graph)
    db_session.execute(
        insert(Mention).values(
            episode_id=graph.episode_id,
            media_id=graph.media_id,
            context="hidden restored",
        ),
    )
    db_session.commit()

    events = evaluate_alert_rules(db=db_session, user_id=user.id)
    sender = Mock()
    digest = send_pending_digest(db=db_session, user=user, sender=sender)
    db_session.commit()

    assert_that(events).is_empty()
    assert_that(digest).is_none()
    assert_that(sender.mock_calls).is_empty()


def test_pending_alert_for_taken_down_activity_is_not_delivered(
    db_session: Session,
) -> None:
    """An event raised before the takedown is not mailed about hidden activity."""
    graph = seed_catalog_graph(db_session)
    user = _alerting_user(db_session, graph)
    second = Mention(
        episode_id=graph.episode_id,
        media_id=graph.media_id,
        context="new public mention",
    )
    db_session.add(second)
    db_session.commit()
    assert_that(evaluate_alert_rules(db=db_session, user_id=user.id)).is_length(1)
    db_session.commit()
    take_down_mention(db_session, second.id)

    deliver_nothing(db_session, user)


def test_account_api_hides_suppressed_activity(
    client: TestClient,
    db_session: Session,
) -> None:
    """Signed-in alert, digest and export routes report no hidden activity."""
    graph = seed_catalog_graph(db_session)
    take_down_mention(db_session, graph.mention_id)
    reader = sign_in_reader_with_alert(client, media_id=graph.media_id)
    headers = reader.headers
    db_session.execute(
        insert(Mention).values(
            episode_id=graph.episode_id,
            media_id=graph.media_id,
            context="hidden restored",
        ),
    )
    db_session.commit()

    evaluated = client.post("/api/v2/me/alerts/evaluate", headers=headers)
    sent = client.post("/api/v2/me/digests/send", headers=headers)
    exported = client.get("/api/v2/me/export", headers=headers)

    assert_that(evaluated.json()["generated"]).is_zero()
    assert_that(sent.status_code).is_equal_to(200)
    assert_that(reader.sender.deliveries).is_empty()
    assert_that(exported.text).does_not_contain("new mention")


def test_first_stats_read_after_takedown_is_current(
    client: TestClient,
    db_session: Session,
) -> None:
    """A warm stats cache does not survive a committed takedown."""
    graph = seed_catalog_graph(db_session)
    assert_that(client.get("/api/v2/stats").json()["mentions"]).is_equal_to(1)

    take_down_mention(db_session, graph.mention_id)

    assert_that(client.get("/api/v2/stats").json()["mentions"]).is_zero()


def test_merge_baselines_count_only_published_mentions(db_session: Session) -> None:
    """A hidden mention on the merge target does not inflate alert baselines."""
    graph = seed_catalog_graph(db_session)
    source = Media(type=MediaType.BOOK, title="Dune (dupe)")
    public_episode = Episode(podcast_id=graph.podcast_id, title="Public")
    hidden_episode = Episode(podcast_id=graph.podcast_id, title="Hidden")
    db_session.add_all([source, public_episode, hidden_episode])
    db_session.flush()
    db_session.add(Mention(episode_id=public_episode.id, media_id=source.id))
    db_session.commit()
    take_down_episode(db_session, hidden_episode.id, "unpublish_mentions")
    db_session.execute(
        insert(Mention).values(episode_id=hidden_episode.id, media_id=graph.media_id),
    )
    user = AccountUser(email="reader@example.com")
    db_session.add(user)
    db_session.flush()
    save_media(db=db_session, user_id=user.id, media_id=source.id)
    create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="media",
        target_id=source.id,
        event_type="new_mention",
    )
    db_session.commit()

    merge_ops_media(
        db=db_session, source_media_id=source.id, target_media_id=graph.media_id
    )
    db_session.add(Mention(episode_id=public_episode.id, media_id=graph.media_id))
    db_session.commit()

    assert_that(evaluate_alert_rules(db=db_session, user_id=user.id)).is_length(1)


def test_summary_that_quoted_no_mention_is_kept(db_session: Session) -> None:
    """Stored provenance showing no mention context keeps the summary."""
    graph = seed_catalog_graph(db_session)
    quoted_nothing = _media_summary(graph.media_id, "identity only")
    quoted_nothing.metadata_json = {
        "source": "media_metadata_mentions",
        "mention_context_count": 0,
    }
    quoted = _media_summary(graph.media_id, "quoted mentions")
    quoted.metadata_json = {
        "source": "media_metadata_mentions",
        "mention_context_count": 1,
    }
    db_session.add_all([quoted_nothing, quoted])
    db_session.commit()

    take_down_mention(db_session, graph.mention_id)

    remaining = [row.summary_text for row in db_session.query(MediaSummary)]
    assert_that(remaining).is_equal_to(["identity only"])


def test_loaded_summary_is_not_left_stale_and_count_is_recorded(
    db_session: Session,
) -> None:
    """A loaded summary is deleted through the session; the case counts it."""
    graph = seed_catalog_graph(db_session)
    loaded = _media_summary(graph.media_id, "built from a great book")
    db_session.add(loaded)
    db_session.commit()
    assert_that(db_session.get(MediaSummary, loaded.id)).is_same_as(loaded)

    take_down_mention(db_session, graph.mention_id)

    assert_that(inspect(loaded).was_deleted).is_true()
    assert_that(db_session.get(MediaSummary, loaded.id)).is_none()
    case = db_session.query(TakedownRequest).one()
    assert_that(case.metadata_json).contains_entry({"media_summaries_dropped": 1})
