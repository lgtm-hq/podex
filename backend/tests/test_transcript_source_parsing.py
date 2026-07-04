"""Tests for Podscripts transcript HTML parsing."""

from assertpy import assert_that

from podex.models import Episode, Podcast
from podex.services.transcript_source import (
    PodscriptsSource,
    parse_podscripts_transcript_html,
)


def test_parse_podscripts_transcript_html_uses_timestamped_transcript_nodes() -> None:
    """Verify transcript extraction uses provider transcript elements only."""
    html = """
    <main>
      <span class="episode_date">Episode Date: March 23, 2026</span>
      <div class="podcast-transcript">
        <div class="single-sentence">
          <span class="pod_timestamp_indicator">00:01:02</span>
          <span class="transcript-text">This is genuine spoken transcript content
            long enough to be validated and persisted for downstream processing.</span>
          <span class="transcript-text">It continues with useful context for search.</span>
        </div>
        <div class="single-sentence">
          <span class="pod_timestamp_indicator">01:02:03</span>
          <span class="transcript-text">A later segment provides additional evidence.</span>
        </div>
      </div>
    </main>
    """

    parsed = parse_podscripts_transcript_html(html)

    assert_that(parsed).is_not_none()
    assert parsed is not None
    assert_that(parsed.raw_text).does_not_contain("Episode Date")
    assert_that(parsed.segments).is_length(2)
    assert_that(parsed.segments[0]["start"]).is_equal_to(62.0)
    assert_that(parsed.segments[1]["start"]).is_equal_to(3723.0)


def test_parse_podscripts_transcript_html_rejects_page_metadata() -> None:
    """Verify non-transcript page content cannot be persisted as a transcript."""
    html = """
    <main>
      <span class="episode_date">Episode Date: March 23, 2026</span>
      <article>Podcast title and navigation links only.</article>
    </main>
    """

    assert_that(parse_podscripts_transcript_html(html)).is_none()


def test_parse_podscripts_transcript_html_rejects_implausibly_short_content() -> None:
    """Verify a transcript container without meaningful content fails closed."""
    html = """
    <div class="podcast-transcript">
      <div class="single-sentence">
        <span class="pod_timestamp_indicator">00:00:01</span>
        <span class="transcript-text">Trailer.</span>
      </div>
    </div>
    """

    assert_that(parse_podscripts_transcript_html(html)).is_none()


def test_podscripts_source_reuses_discovered_episode_url() -> None:
    """Verify known provider URLs avoid a second episode-list search."""
    episode_url = "https://podscripts.co/podcasts/crime-junkie/murdered-mary"
    episode = Episode(
        podcast=Podcast(
            name="Crime Junkie",
            slug="crime-junkie",
            podscripts_slug="crime-junkie",
        ),
        title="Murdered: Mary",
        episode_url=episode_url,
    )

    with PodscriptsSource(delay=0) as source:
        result = source._find_episode_url("crime-junkie", episode)

    assert_that(result).is_equal_to(episode_url)
