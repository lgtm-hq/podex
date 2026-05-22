"""Transcript retention policy primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum, auto
from hashlib import sha256


class TranscriptLifecycleTier(StrEnum):
    """Raw transcript lifecycle tiers."""

    HOT = auto()
    WARM = auto()
    COLD = auto()
    PURGED = auto()


class TranscriptRetentionBlocker(StrEnum):
    """Reasons a raw transcript cannot be purged yet."""

    ALREADY_PURGED = auto()
    ACTIVE_RETENTION_WINDOW = auto()
    DIGEST_REQUIRED = auto()
    LOW_CONFIDENCE = auto()
    MISSING_CONFIDENCE = auto()
    RETENTION_EXEMPT_SAMPLE = auto()


class TranscriptConfidenceBand(StrEnum):
    """Confidence bands used for stratified retention sampling."""

    UNKNOWN = auto()
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()


class TranscriptAgeBucket(StrEnum):
    """Age buckets used for stratified retention sampling."""

    RECENT = auto()
    MID_LIFE = auto()
    OLDER = auto()


@dataclass(frozen=True, slots=True)
class TranscriptRetentionPolicy:
    """Configurable policy for raw transcript lifecycle decisions."""

    hot_retention_period: timedelta = timedelta(days=30)
    warm_retention_period: timedelta = timedelta(days=180)
    min_purge_confidence: float = 0.85
    require_digest_before_purge: bool = True
    retention_sample_rate: float = 0.075
    sample_version: str = "retention-sample-v1"
    low_confidence_max: float = 0.5

    def __post_init__(self) -> None:
        """Validate policy thresholds."""
        if self.hot_retention_period < timedelta():
            raise ValueError("hot_retention_period must be non-negative")
        if self.warm_retention_period < self.hot_retention_period:
            raise ValueError(
                "warm_retention_period must be greater than or equal to "
                "hot_retention_period",
            )
        if not 0 <= self.min_purge_confidence <= 1:
            raise ValueError("min_purge_confidence must be between 0 and 1")
        if not 0 <= self.retention_sample_rate <= 1:
            raise ValueError("retention_sample_rate must be between 0 and 1")
        if not 0 <= self.low_confidence_max <= 1:
            raise ValueError("low_confidence_max must be between 0 and 1")
        if self.low_confidence_max > self.min_purge_confidence:
            raise ValueError(
                "low_confidence_max must be less than or equal to min_purge_confidence",
            )


@dataclass(frozen=True, slots=True)
class TranscriptRetentionState:
    """Pure input state for a raw transcript retention evaluation."""

    acquired_at: datetime
    extraction_confidence: float | None = None
    digest_created_at: datetime | None = None
    purged_at: datetime | None = None
    retention_exempt_sample: bool = False
    source_retention_opt_out: bool = False


@dataclass(frozen=True, slots=True)
class TranscriptRetentionDecision:
    """Policy decision for a transcript retention evaluation."""

    tier: TranscriptLifecycleTier
    purge_eligible: bool
    purge_blockers: tuple[TranscriptRetentionBlocker, ...]
    retention_suppressed: bool
    age: timedelta


@dataclass(frozen=True, slots=True)
class TranscriptRetentionSampleKey:
    """Stable sampling identity and dimensions for a transcript."""

    transcript_key: str
    source_key: str
    topic_key: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptRetentionSampleDecision:
    """Deterministic retention sample assignment."""

    retention_exempt: bool
    confidence_band: TranscriptConfidenceBand
    age_bucket: TranscriptAgeBucket
    sample_version: str
    score: float


@dataclass(frozen=True, slots=True)
class TranscriptRetentionService:
    """Evaluate raw transcript lifecycle tiers and purge eligibility."""

    policy: TranscriptRetentionPolicy = field(default_factory=TranscriptRetentionPolicy)

    def classify_tier(
        self,
        *,
        state: TranscriptRetentionState,
        now: datetime,
    ) -> TranscriptLifecycleTier:
        """Classify a transcript into its lifecycle tier.

        Args:
            state: Current transcript retention state.
            now: Timestamp used for age-sensitive decisions.

        Returns:
            Lifecycle tier implied by the policy and state.
        """
        if state.purged_at is not None:
            return TranscriptLifecycleTier.PURGED

        if state.source_retention_opt_out:
            return TranscriptLifecycleTier.COLD

        age = _age_at(acquired_at=state.acquired_at, now=now)
        if age < self.policy.hot_retention_period:
            return TranscriptLifecycleTier.HOT
        if age < self.policy.warm_retention_period:
            return TranscriptLifecycleTier.WARM
        return TranscriptLifecycleTier.COLD

    def evaluate(
        self,
        *,
        state: TranscriptRetentionState,
        now: datetime,
    ) -> TranscriptRetentionDecision:
        """Evaluate lifecycle tier and purge eligibility.

        Args:
            state: Current transcript retention state.
            now: Timestamp used for age-sensitive decisions.

        Returns:
            Full retention decision including purge blockers.
        """
        tier = self.classify_tier(state=state, now=now)
        blockers = self._purge_blockers(state=state, tier=tier)

        return TranscriptRetentionDecision(
            tier=tier,
            purge_eligible=len(blockers) == 0,
            purge_blockers=tuple(blockers),
            retention_suppressed=state.source_retention_opt_out
            and tier != TranscriptLifecycleTier.PURGED,
            age=_age_at(acquired_at=state.acquired_at, now=now),
        )

    def choose_sample(
        self,
        *,
        sample_key: TranscriptRetentionSampleKey,
        acquired_at: datetime,
        now: datetime,
        extraction_confidence: float | None,
    ) -> TranscriptRetentionSampleDecision:
        """Choose whether a transcript belongs to the retention sample.

        Args:
            sample_key: Stable transcript sampling identity and strata.
            acquired_at: Transcript acquisition timestamp.
            now: Timestamp used to derive age strata.
            extraction_confidence: Latest extraction confidence, when known.

        Returns:
            Deterministic sampling decision.
        """
        confidence_band = self.confidence_band(
            extraction_confidence=extraction_confidence,
        )
        age_bucket = self.age_bucket(acquired_at=acquired_at, now=now)
        score = _stable_score(
            parts=(
                self.policy.sample_version,
                sample_key.source_key,
                sample_key.topic_key or "",
                confidence_band.value,
                age_bucket.value,
                sample_key.transcript_key,
            ),
        )

        return TranscriptRetentionSampleDecision(
            retention_exempt=score < self.policy.retention_sample_rate,
            confidence_band=confidence_band,
            age_bucket=age_bucket,
            sample_version=self.policy.sample_version,
            score=score,
        )

    def confidence_band(
        self,
        *,
        extraction_confidence: float | None,
    ) -> TranscriptConfidenceBand:
        """Map extraction confidence to a sampling band.

        Args:
            extraction_confidence: Latest extraction confidence, when known.

        Returns:
            Sampling confidence band.
        """
        if extraction_confidence is None:
            return TranscriptConfidenceBand.UNKNOWN
        if extraction_confidence < self.policy.low_confidence_max:
            return TranscriptConfidenceBand.LOW
        if extraction_confidence < self.policy.min_purge_confidence:
            return TranscriptConfidenceBand.MEDIUM
        return TranscriptConfidenceBand.HIGH

    def age_bucket(
        self,
        *,
        acquired_at: datetime,
        now: datetime,
    ) -> TranscriptAgeBucket:
        """Map transcript age to a sampling bucket.

        Args:
            acquired_at: Transcript acquisition timestamp.
            now: Timestamp used for age-sensitive decisions.

        Returns:
            Sampling age bucket.
        """
        age = _age_at(acquired_at=acquired_at, now=now)
        if age < self.policy.hot_retention_period:
            return TranscriptAgeBucket.RECENT
        if age < self.policy.warm_retention_period:
            return TranscriptAgeBucket.MID_LIFE
        return TranscriptAgeBucket.OLDER

    def _purge_blockers(
        self,
        *,
        state: TranscriptRetentionState,
        tier: TranscriptLifecycleTier,
    ) -> list[TranscriptRetentionBlocker]:
        """Build the purge blocker list for a transcript state."""
        blockers: list[TranscriptRetentionBlocker] = []

        if tier == TranscriptLifecycleTier.PURGED:
            return [TranscriptRetentionBlocker.ALREADY_PURGED]

        if state.source_retention_opt_out:
            return blockers

        if tier != TranscriptLifecycleTier.COLD:
            blockers.append(TranscriptRetentionBlocker.ACTIVE_RETENTION_WINDOW)

        if state.retention_exempt_sample:
            blockers.append(TranscriptRetentionBlocker.RETENTION_EXEMPT_SAMPLE)

        if state.extraction_confidence is None:
            blockers.append(TranscriptRetentionBlocker.MISSING_CONFIDENCE)
        elif state.extraction_confidence < self.policy.min_purge_confidence:
            blockers.append(TranscriptRetentionBlocker.LOW_CONFIDENCE)

        if self.policy.require_digest_before_purge and state.digest_created_at is None:
            blockers.append(TranscriptRetentionBlocker.DIGEST_REQUIRED)

        return blockers


def _age_at(
    *,
    acquired_at: datetime,
    now: datetime,
) -> timedelta:
    """Calculate non-negative transcript age.

    Args:
        acquired_at: Transcript acquisition timestamp.
        now: Timestamp used for age-sensitive decisions.

    Returns:
        Transcript age clamped to zero for clock skew.
    """
    return max(now - acquired_at, timedelta())


def _stable_score(
    *,
    parts: tuple[str, ...],
) -> float:
    """Calculate a stable score between 0 and 1 for sampling.

    Args:
        parts: Stable sampling key components.

    Returns:
        Floating point score in the range [0, 1).
    """
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF + 1)
