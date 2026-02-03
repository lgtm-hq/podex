"""Tests for YouTube service."""

from datetime import UTC, datetime

import httpx
from assertpy import assert_that

from podex.services.youtube import YouTubeClient


def test_fetch_channel_videos_with_mocked_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "channels" in url:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"contentDetails": {"relatedPlaylists": {"uploads": "PL123"}}}
                    ]
                },
            )
        if "playlistItems" in url:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"contentDetails": {"videoId": "vid1"}},
                        {"contentDetails": {"videoId": "vid2"}},
                    ]
                },
            )
        if "videos" in url:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "vid1",
                            "snippet": {
                                "title": "Episode 1",
                                "publishedAt": "2026-02-03T00:00:00Z",
                                "thumbnails": {"high": {"url": "http://thumb/1"}},
                            },
                            "contentDetails": {"duration": "PT1H2M3S"},
                        },
                        {
                            "id": "vid2",
                            "snippet": {
                                "title": "Episode 2",
                                "publishedAt": "2026-02-04T00:00:00Z",
                                "thumbnails": {"high": {"url": "http://thumb/2"}},
                            },
                            "contentDetails": {"duration": "PT45M"},
                        },
                    ]
                },
            )
        raise AssertionError(f"Unexpected URL: {url}")

    transport = httpx.MockTransport(handler)
    client = YouTubeClient(
        "test-key",
        client=httpx.Client(
            transport=transport,
            base_url="https://www.googleapis.com/youtube/v3",
        ),
    )

    videos = client.fetch_channel_videos("channel")

    assert_that(videos).is_length(2)
    assert_that(videos[0].youtube_id).is_equal_to("vid1")
    assert_that(videos[0].title).is_equal_to("Episode 1")
    assert_that(videos[0].published_at).is_equal_to(datetime(2026, 2, 3, tzinfo=UTC))
    assert_that(videos[0].duration_seconds).is_equal_to(3723)
    assert_that(videos[0].thumbnail_url).is_equal_to("http://thumb/1")
    assert_that(videos[1].duration_seconds).is_equal_to(2700)
