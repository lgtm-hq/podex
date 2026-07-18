"""Data contracts for transcript acquisition results.

Acquisition providers (podscripts, Whisper, and future sources) return these
shapes; retention and artifact storage consume them. The provider
implementations live in the transcription theme and are ported separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TranscriptResult:
    """Result of a transcription."""

    provider: str
    raw_text: str
    segments: list[dict[str, str | float]] = field(default_factory=list)
    fetched_at: datetime | None = None
    audio_duration_seconds: float = 0.0
    model_used: str = ""


@dataclass
class TranscriptAcquisitionResult:
    """Result from attempting to acquire a transcript."""

    success: bool
    result: TranscriptResult | None
    source: str
    error: str | None = None
    should_store_raw: bool = True
    source_retention_opt_out: bool = False
