"""YouTube Data API client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx


@dataclass
class YouTubeVideo:
    """Normalized YouTube video metadata."""

    youtube_id: str
    title: str
    published_at: datetime | None
    duration_seconds: int | None
    thumbnail_url: str | None


class YouTubeClient:
    """Minimal YouTube Data API client for channel uploads."""

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self._client = client or httpx.Client(
            base_url="https://www.googleapis.com/youtube/v3"
        )

    def close(self) -> None:
        if self._client:
            self._client.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {**params, "key": self.api_key}
        response = self._client.get(path, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_uploads_playlist_id(self, channel_id: str) -> str:
        data = self._get(
            "channels",
            {"part": "contentDetails", "id": channel_id},
        )
        items = data.get("items", [])
        if not items:
            raise ValueError("Channel not found")
        return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    def list_playlist_items(
        self, playlist_id: str, page_token: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "part": "contentDetails,snippet",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token
        return self._get("playlistItems", params)

    def list_videos(self, video_ids: list[str]) -> dict[str, Any]:
        params = {
            "part": "contentDetails,snippet",
            "id": ",".join(video_ids),
            "maxResults": 50,
        }
        return self._get("videos", params)

    def fetch_channel_videos(
        self, channel_id: str, max_pages: int | None = None
    ) -> list[YouTubeVideo]:
        playlist_id = self.get_uploads_playlist_id(channel_id)
        videos: list[YouTubeVideo] = []
        page_token: str | None = None
        pages = 0

        while True:
            items_response = self.list_playlist_items(
                playlist_id, page_token=page_token
            )
            items = items_response.get("items", [])
            video_ids = [item["contentDetails"]["videoId"] for item in items]

            if video_ids:
                videos_response = self.list_videos(video_ids)
                videos.extend(self._normalize_videos(videos_response))

            page_token = items_response.get("nextPageToken")
            pages += 1
            if not page_token or (max_pages is not None and pages >= max_pages):
                break

        return videos

    def _normalize_videos(self, data: dict[str, Any]) -> list[YouTubeVideo]:
        items = data.get("items", [])
        results: list[YouTubeVideo] = []
        for item in items:
            snippet = item.get("snippet", {})
            content_details = item.get("contentDetails", {})
            published_at = parse_published_at(snippet.get("publishedAt"))
            duration_seconds = parse_duration(content_details.get("duration"))
            thumbnail_url = _pick_thumbnail(snippet.get("thumbnails", {}))
            results.append(
                YouTubeVideo(
                    youtube_id=item.get("id"),
                    title=snippet.get("title", ""),
                    published_at=published_at,
                    duration_seconds=duration_seconds,
                    thumbnail_url=thumbnail_url,
                )
            )
        return results


def parse_duration(duration: str | None) -> int | None:
    if not duration:
        return None
    if not duration.startswith("PT"):
        return None

    hours = minutes = seconds = 0
    buffer = ""
    for char in duration[2:]:
        if char.isdigit():
            buffer += char
            continue
        if not buffer:
            continue
        value = int(buffer)
        buffer = ""
        if char == "H":
            hours = value
        elif char == "M":
            minutes = value
        elif char == "S":
            seconds = value
    return hours * 3600 + minutes * 60 + seconds


def parse_published_at(published_at: str | None) -> datetime | None:
    if not published_at:
        return None
    try:
        return datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None


def _pick_thumbnail(thumbnails: dict[str, Any]) -> str | None:
    for key in ("maxres", "standard", "high", "medium", "default"):
        url = thumbnails.get(key, {}).get("url")
        if url:
            return url
    return None
