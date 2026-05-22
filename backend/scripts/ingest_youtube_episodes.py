#!/usr/bin/env python3
"""Ingest episodes from YouTube into the database."""

from __future__ import annotations

import sys
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from typing import TYPE_CHECKING

from podex.config import get_settings
from podex.database import SessionLocal
from podex.models import Episode, Podcast

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
from podex.services.youtube import YouTubeClient, YouTubeVideo

DEFAULT_PODCAST_SLUG = "jre"
DEFAULT_PODCAST_NAME = "The Joe Rogan Experience"


def ensure_podcast(session: Session) -> Podcast:
    podcast = (
        session.query(Podcast).filter(Podcast.slug == DEFAULT_PODCAST_SLUG).first()
    )
    if podcast:
        return podcast

    new_podcast = Podcast(name=DEFAULT_PODCAST_NAME, slug=DEFAULT_PODCAST_SLUG)
    session.add(new_podcast)
    session.commit()
    return new_podcast


def upsert_episode(session: Session, podcast_id: int, video: YouTubeVideo) -> Episode:
    episode = (
        session.query(Episode).filter(Episode.youtube_id == video.youtube_id).first()
    )
    if episode:
        episode.title = video.title
        episode.published_at = video.published_at
        episode.duration_seconds = video.duration_seconds
        episode.thumbnail_url = video.thumbnail_url
        return episode

    new_episode = Episode(
        podcast_id=podcast_id,
        title=video.title,
        youtube_id=video.youtube_id,
        published_at=video.published_at,
        duration_seconds=video.duration_seconds,
        thumbnail_url=video.thumbnail_url,
    )
    session.add(new_episode)
    return new_episode


def main() -> None:
    settings = get_settings()
    if not settings.youtube_api_key or not settings.youtube_channel_id:
        raise RuntimeError(
            "Missing YOUTUBE_API_KEY or YOUTUBE_CHANNEL_ID in environment."
        )

    client = YouTubeClient(settings.youtube_api_key)
    try:
        videos = client.fetch_channel_videos(settings.youtube_channel_id)
    finally:
        client.close()

    with SessionLocal() as session:
        podcast = ensure_podcast(session)
        for video in videos:
            upsert_episode(session, podcast.id, video)
        session.commit()

    print(f"Ingested {len(videos)} videos for channel {settings.youtube_channel_id}.")


if __name__ == "__main__":
    main()
