"""Closed, code-owned reasons for ingestion and extraction diagnostics."""

from collections.abc import Callable
from enum import StrEnum, auto
from typing import TypeVar


class FailureReason(StrEnum):
    """Failure kinds which may cross the diagnostic persistence boundary."""

    REQUEST_FAILED = auto()
    MALFORMED_RESPONSE = auto()
    EXPECTED_JSON_ARRAY = auto()
    MISSING_JSON_ARRAY = auto()
    INCOMPLETE_JSON_ARRAY = auto()
    INVALID_JSON = auto()
    UNCLASSIFIED_FAILURE = auto()
    CONFIGURATION = auto()
    EMBEDDING_COUNT_MISMATCH = auto()
    EMBEDDING_DIMENSIONS_MISMATCH = auto()
    INVALID_ADDRESS = auto()
    BLOCKED_ADDRESS = auto()
    INVALID_URL = auto()
    UNSUPPORTED_URL = auto()
    URL_CREDENTIALS = auto()
    DNS_BUSY = auto()
    DNS_FAILURE = auto()
    DNS_WORKER_UNAVAILABLE = auto()
    DNS_NO_ADDRESSES = auto()
    TOTAL_DEADLINE = auto()
    TIMEOUT = auto()
    CONNECTION_FAILED = auto()
    CONNECT_TIMEOUT = auto()
    TLS_FAILED = auto()
    REDIRECT_MISSING_LOCATION = auto()
    REDIRECT_INVALID_URL = auto()
    REDIRECT_DOWNGRADE = auto()
    REDIRECT_LIMIT = auto()
    HTTP_STATUS = auto()
    UNSUPPORTED_ENCODING = auto()
    RESPONSE_TOO_LARGE = auto()
    DECOMPRESSED_RESPONSE_TOO_LARGE = auto()
    INVALID_GZIP = auto()
    INCOMPLETE_GZIP = auto()


FailureReduction = tuple[
    Callable[..., BaseException],
    tuple[object, ...],
    dict[str, object],
]
FailureT = TypeVar("FailureT", bound=BaseException)


def rebuild_failure(kind: type[FailureT], arguments: tuple[object, ...]) -> FailureT:
    """Rebuild an exception without requiring keyword-only metadata at init.

    Pickle and copy restore the instance dictionary after this allocation,
    preserving reason, status, notes and additional state.
    """
    restored: FailureT = kind.__new__(kind, *arguments)
    BaseException.__init__(restored, *arguments)
    return restored


class PipelineFailure(ValueError):
    """A code-owned validation failure carrying structured diagnostic metadata.

    Args:
        message: Fixed diagnostic chosen by the raising code.
        reason: Closed failure kind, independent of message text.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: FailureReason,
    ) -> None:
        super().__init__(message)
        self.failure_reason = reason

    def __reduce__(self) -> FailureReduction:
        """Preserve metadata when the exception is copied or transferred."""
        return rebuild_failure, (type(self), self.args), self.__dict__
