"""Shared derivative summary model primitives."""

from enum import StrEnum, auto


class DerivativeSummaryKind(StrEnum):
    """Kinds of narrative summaries generated for derivative surfaces."""

    OVERVIEW = auto()
    DISCOVERY = auto()
    RETENTION_DIGEST = auto()


class DerivativeSummaryStatus(StrEnum):
    """Lifecycle states for generated derivative summaries."""

    READY = auto()
    FAILED = auto()
