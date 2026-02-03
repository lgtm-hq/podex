"""Episode iteration utilities for batch processing."""

from __future__ import annotations

import logging
from collections.abc import Generator
from dataclasses import dataclass
from enum import StrEnum, auto

from sqlalchemy.orm import Query, Session

from podex.models import Episode, Podcast, Transcript

logger = logging.getLogger(__name__)


class ProcessingStage(StrEnum):
    """Stages of episode processing."""

    TRANSCRIPTION = auto()
    EXTRACTION = auto()
    CLEANUP = auto()


@dataclass
class EpisodeFilter:
    """Filter criteria for episode iteration."""

    podcast_slug: str | None = None
    podcast_id: int | None = None
    stage: ProcessingStage | None = None
    status: str | None = None
    has_transcript: bool | None = None
    episode_min: int | None = None
    episode_max: int | None = None
    limit: int | None = None
    offset: int = 0


@dataclass
class ProcessingStats:
    """Statistics for processing stages."""

    pending: int = 0
    in_progress: int = 0
    completed: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.pending + self.in_progress + self.completed + self.failed


class EpisodeIterator:
    """Iterate over episodes with filtering and progress tracking."""

    def __init__(self, session: Session):
        self.session = session

    def _apply_common_filters(
        self,
        query: Query[Episode],
        podcast_slug: str | None = None,
        episode_min: int | None = None,
        episode_max: int | None = None,
        title_pattern: str | None = None,
    ) -> Query[Episode]:
        """Apply common filters to a query."""
        if podcast_slug:
            query = query.join(Podcast).filter(Podcast.slug == podcast_slug)

        # Filter by title pattern (e.g., "Joe Rogan Experience #%" for main episodes)
        if title_pattern:
            query = query.filter(Episode.title.like(title_pattern))

        # If filtering by episode number, exclude episodes without numbers
        if episode_min is not None or episode_max is not None:
            query = query.filter(Episode.episode_number.isnot(None))

        if episode_min is not None:
            query = query.filter(Episode.episode_number >= episode_min)

        if episode_max is not None:
            query = query.filter(Episode.episode_number <= episode_max)

        return query

    def _get_status_field(self, stage: ProcessingStage) -> str:
        """Get the status field name for a processing stage."""
        return {
            ProcessingStage.TRANSCRIPTION: "transcript_status",
            ProcessingStage.EXTRACTION: "extraction_status",
            ProcessingStage.CLEANUP: "cleanup_status",
        }[stage]

    def get_pending(
        self,
        stage: ProcessingStage,
        podcast_slug: str | None = None,
        episode_min: int | None = None,
        episode_max: int | None = None,
        title_pattern: str | None = None,
        limit: int | None = None,
    ) -> list[Episode]:
        """Get episodes pending for a specific processing stage."""
        query = self.session.query(Episode)
        query = self._apply_common_filters(
            query, podcast_slug, episode_min, episode_max, title_pattern
        )

        if stage == ProcessingStage.TRANSCRIPTION:
            query = query.filter(Episode.transcript_status == "pending")
            query = query.filter(Episode.youtube_id.isnot(None))
        elif stage == ProcessingStage.EXTRACTION:
            query = query.filter(Episode.extraction_status == "pending")
            # Must have transcript
            query = query.join(Transcript).filter(Transcript.raw_text.isnot(None))
        elif stage == ProcessingStage.CLEANUP:
            query = query.filter(Episode.cleanup_status == "pending")
            query = query.join(Transcript).filter(Transcript.raw_text.isnot(None))

        query = query.order_by(Episode.episode_number.asc())

        if limit:
            query = query.limit(limit)

        return query.all()

    def get_in_progress(
        self,
        stage: ProcessingStage,
        podcast_slug: str | None = None,
        episode_min: int | None = None,
        episode_max: int | None = None,
        title_pattern: str | None = None,
    ) -> list[Episode]:
        """Get episodes currently being processed."""
        query = self.session.query(Episode)
        query = self._apply_common_filters(
            query, podcast_slug, episode_min, episode_max, title_pattern
        )

        status_field = self._get_status_field(stage)
        query = query.filter(getattr(Episode, status_field) == "in_progress")

        return query.all()

    def get_completed(
        self,
        stage: ProcessingStage,
        podcast_slug: str | None = None,
        episode_min: int | None = None,
        episode_max: int | None = None,
        title_pattern: str | None = None,
        limit: int | None = None,
    ) -> list[Episode]:
        """Get completed episodes for a stage."""
        query = self.session.query(Episode)
        query = self._apply_common_filters(
            query, podcast_slug, episode_min, episode_max, title_pattern
        )

        status_field = self._get_status_field(stage)
        query = query.filter(getattr(Episode, status_field) == "completed")
        query = query.order_by(Episode.episode_number.asc())

        if limit:
            query = query.limit(limit)

        return query.all()

    def get_failed(
        self,
        stage: ProcessingStage,
        podcast_slug: str | None = None,
        episode_min: int | None = None,
        episode_max: int | None = None,
        title_pattern: str | None = None,
    ) -> list[Episode]:
        """Get failed episodes for a stage."""
        query = self.session.query(Episode)
        query = self._apply_common_filters(
            query, podcast_slug, episode_min, episode_max, title_pattern
        )

        status_field = self._get_status_field(stage)
        query = query.filter(getattr(Episode, status_field) == "failed")

        return query.all()

    def get_stats(
        self,
        stage: ProcessingStage,
        podcast_slug: str | None = None,
        episode_min: int | None = None,
        episode_max: int | None = None,
        title_pattern: str | None = None,
    ) -> ProcessingStats:
        """Get processing statistics for a stage."""
        return ProcessingStats(
            pending=len(
                self.get_pending(
                    stage, podcast_slug, episode_min, episode_max, title_pattern
                )
            ),
            in_progress=len(
                self.get_in_progress(
                    stage, podcast_slug, episode_min, episode_max, title_pattern
                )
            ),
            completed=len(
                self.get_completed(
                    stage, podcast_slug, episode_min, episode_max, title_pattern
                )
            ),
            failed=len(
                self.get_failed(
                    stage, podcast_slug, episode_min, episode_max, title_pattern
                )
            ),
        )

    def get_all_stats(
        self,
        podcast_slug: str | None = None,
        episode_min: int | None = None,
        episode_max: int | None = None,
        title_pattern: str | None = None,
    ) -> dict[str, ProcessingStats]:
        """Get processing statistics for all stages."""
        return {
            stage.value: self.get_stats(
                stage, podcast_slug, episode_min, episode_max, title_pattern
            )
            for stage in ProcessingStage
        }

    def iterate_for_processing(
        self,
        stage: ProcessingStage,
        podcast_slug: str | None = None,
        episode_min: int | None = None,
        episode_max: int | None = None,
        title_pattern: str | None = None,
        limit: int | None = None,
        mark_in_progress: bool = True,
    ) -> Generator[Episode, None, None]:
        """Iterate over episodes, optionally marking them as in-progress.

        Usage:
            iterator = EpisodeIterator(session)
            for episode in iterator.iterate_for_processing(
                ProcessingStage.TRANSCRIPTION,
                podcast_slug="jre",
                episode_min=171,
                episode_max=180,
                title_pattern="Joe Rogan Experience #%",
            ):
                # Process episode
                episode.transcript_status = "completed"
                session.commit()
        """
        episodes = self.get_pending(
            stage,
            podcast_slug,
            episode_min,
            episode_max,
            title_pattern,
            limit,
        )
        status_field = self._get_status_field(stage)

        for episode in episodes:
            if mark_in_progress:
                setattr(episode, status_field, "in_progress")
                self.session.commit()

            yield episode

    def mark_completed(self, episode: Episode, stage: ProcessingStage) -> None:
        """Mark an episode as completed for a stage."""
        status_field = self._get_status_field(stage)
        setattr(episode, status_field, "completed")
        self.session.commit()

    def mark_failed(self, episode: Episode, stage: ProcessingStage) -> None:
        """Mark an episode as failed for a stage."""
        status_field = self._get_status_field(stage)
        setattr(episode, status_field, "failed")
        self.session.commit()

    def reset_in_progress(
        self,
        stage: ProcessingStage,
        podcast_slug: str | None = None,
    ) -> int:
        """Reset all in-progress episodes back to pending (for crash recovery)."""
        episodes = self.get_in_progress(stage, podcast_slug)
        status_field = self._get_status_field(stage)

        for episode in episodes:
            setattr(episode, status_field, "pending")

        self.session.commit()
        return len(episodes)
