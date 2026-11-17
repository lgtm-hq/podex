"""LLM media-mention extraction over cleaned transcripts.

Prompts follow the #29 load-don't-embed boundary: the system prompt is
injected by the caller (tuned prompts are podex-ops data, resolved through
:mod:`podex.services.prompt_config`) and falls back to the generic prompt in
:mod:`podex.services.prompts_default`.
"""

from __future__ import annotations

import json
import logging
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

import anthropic

from podex.models.media import MediaType
from podex.services.failure_codes import FailureReason
from podex.services.failure_descriptors import describe_failure, log_failure
from podex.services.llm_response import (
    MALFORMED_RESPONSE_ERRORS,
    first_response_text,
)
from podex.services.prompts_default import DEFAULT_EXTRACTION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

DEFAULT_EXTRACTION_MODEL = "claude-opus-4-8"
_MAX_OUTPUT_TOKENS = 4000
_MIN_TRANSCRIPT_CHARS = 100
_MAX_TITLE_CHARS = 500
_MAX_CREATOR_CHARS = 255
_MIN_YEAR = 1000
_MAX_YEAR = 2100

# Most characters sent to the model in one request. Text up to this length is
# read in one request, as before windowing existed.
EXTRACTION_WINDOW_CHARS = 150_000
# Least text consecutive windows share. A spoken sentence naming a work, with
# the words that identify it, is a few hundred characters; 2,000 characters
# (about two minutes of speech) keeps several such sentences on both sides of
# a cut while adding at most 4,000 characters (under 3%) per extra window.
EXTRACTION_WINDOW_OVERLAP_CHARS = 2_000
# Most requests per transcript. Eight windows read at least 1.17 million
# characters (roughly 20 hours of speech) and cap one transcript at eight
# requests of at most 150,000 input characters and 4,000 output tokens each.
MAX_EXTRACTION_WINDOWS = 8
# Stop reasons meaning the reply was cut off before the model finished.
_TRUNCATED_STOP_REASONS = frozenset({"max_tokens", "model_context_window_exceeded"})


def _validated_creator(value: object) -> str | None:
    """Normalize a creator without exceeding the persisted schema limit.

    Args:
        value: Untrusted creator value from the parsed response.

    Returns:
        A nonempty creator name, or None for absent or blank input.

    Raises:
        ValueError: When the normalized name exceeds the schema limit.
    """
    creator = str(value).strip() or None if value is not None else None
    if creator is not None and len(creator) > _MAX_CREATOR_CHARS:
        raise ValueError("creator exceeds the schema limit")
    return creator


def _validate_item(*, item: dict[str, Any], title: str) -> ExtractedMedia | None:
    """Validate an extracted item against the persistence schema.

    Transcripts are untrusted input, so items that fall outside the schema
    (unknown type, out-of-range confidence or year, oversized strings) are
    rejected rather than coerced.

    Args:
        item: Raw parsed item from model output.
        title: Pre-stripped title text.

    Returns:
        A validated ExtractedMedia, or None when the item is rejected.
    """
    if not title or len(title) > _MAX_TITLE_CHARS:
        return None

    try:
        media_type = MediaType(str(item.get("type", "")))
        creator = _validated_creator(item.get("creator"))
    except ValueError:
        return None

    year = item.get("year")
    if year is not None:
        if not isinstance(year, int) or not _MIN_YEAR <= year <= _MAX_YEAR:
            return None

    try:
        confidence = float(item.get("confidence", 0.8))
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None

    return ExtractedMedia(
        title=title,
        media_type=media_type,
        creator=creator,
        year=year,
        confidence=confidence,
    )


class ExtractionError(Exception):
    """Base error for LLM extraction failures."""


@dataclass
class ExtractedMedia:
    """A single media/person/place item extracted from a transcript."""

    title: str
    media_type: MediaType
    creator: str | None = None
    year: int | None = None
    confidence: float = 0.8


@dataclass
class ExtractionResult:
    """Outcome of one extraction run: items plus coverage and failures.

    The transcript is read in consecutive, overlapping windows. Items are
    merged across windows with one item per case-insensitive title, the same
    rule a single reply follows. A window whose request fails, whose reply
    stops at the output token cap, or whose reply cannot be parsed adds one
    descriptor to ``errors`` and contributes no items; the items of the other
    windows are kept.

    Attributes:
        items: Validated items, one per case-insensitive title, in the order
            first found.
        errors: Allowlisted failure descriptors, one per failed window.
        transcript_chars: Length of the joined transcript text.
        windows: Requests sent for this transcript.
        failed_windows: Windows whose reply was not used.
        unread_chars: Characters at the end of the transcript that were never
            sent because the window bound was reached.
        unextracted_chars: Characters not inside any window whose reply was
            used: the unread tail, text only failed windows held, and text
            below the minimum length, which is never sent.
    """

    items: list[ExtractedMedia] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    transcript_chars: int = 0
    windows: int = 0
    failed_windows: int = 0
    unread_chars: int = 0
    unextracted_chars: int = 0

    @property
    def success(self) -> bool:
        """Return True if extraction completed without errors."""
        return len(self.errors) == 0

    @property
    def window_limit_reached(self) -> bool:
        """Return True when the window bound left the end of the text unread."""
        return self.unread_chars > 0

    @property
    def fully_covered(self) -> bool:
        """Return True when every character was in a window whose reply was used.

        An empty transcript is covered; text below the minimum length is not.
        """
        return self.unextracted_chars == 0


def _request_failure(error: BaseException, *, malformed: bool = False) -> str:
    """Describe and log an SDK failure for one window.

    Args:
        error: SDK or decoding failure.
        malformed: Whether decoding the response failed.

    Returns:
        The allowlisted diagnostic for the failed window.
    """
    message: str = describe_failure(
        "extraction",
        error,
        reason=(
            FailureReason.MALFORMED_RESPONSE
            if malformed
            else FailureReason.REQUEST_FAILED
        ),
    )
    log_failure(logger, message, error, level=logging.ERROR)
    return message


def _joined_segments(segments: list[dict[str, str]]) -> tuple[str, list[int]]:
    """Join segment texts and record where each segment ends.

    Args:
        segments: Transcript segments with ``text`` keys.

    Returns:
        The joined text and the ascending offsets of the single spaces that
        separate consecutive segments.
    """
    pieces = [seg.get("text", "").strip() for seg in segments if seg.get("text")]
    boundaries: list[int] = []
    position = 0
    for piece in pieces[:-1]:
        position += len(piece)
        boundaries.append(position)
        position += 1
    return " ".join(pieces), boundaries


def _cut(text: str, boundaries: list[int], *, low: int, high: int) -> int:
    """Choose a cut position within ``[low, high]``.

    Args:
        text: Joined transcript text.
        boundaries: Ascending offsets of segment separators.
        low: First acceptable position.
        high: Last acceptable position; a valid index into ``text``.

    Returns:
        The last segment separator in range; otherwise the last whitespace
        character in range; otherwise ``high``.
    """
    index = bisect_right(boundaries, high) - 1
    if index >= 0 and boundaries[index] >= low:
        return boundaries[index]
    for position in range(high, low - 1, -1):
        if text[position].isspace():
            return position
    return high


def _plan_windows(
    text: str,
    boundaries: list[int],
    *,
    window_chars: int,
    max_windows: int,
) -> list[tuple[int, int]]:
    """Split text into consecutive windows that overlap at each cut.

    A window ends at the last segment separator that leaves it longer than
    three overlaps; only when no separator qualifies (one segment fills the
    window) does it end at whitespace, or at the window length when there is
    none. The next window starts at the last separator (else whitespace, else
    hard position) between two overlaps and one overlap before that end. So
    consecutive windows share at least one overlap and at most two, and each
    window reaches at least one overlap beyond the previous end. Any span of
    up to one overlap therefore lies whole inside some window.

    Args:
        text: Joined transcript text.
        boundaries: Ascending offsets of segment separators.
        window_chars: Most characters in one window.
        max_windows: Most windows planned.

    Returns:
        Half-open ``(start, end)`` ranges in ascending order. The last end is
        ``len(text)`` unless the window bound was reached.
    """
    overlap = EXTRACTION_WINDOW_OVERLAP_CHARS
    windows: list[tuple[int, int]] = []
    start = 0
    while len(windows) < max_windows:
        if len(text) - start <= window_chars:
            windows.append((start, len(text)))
            break
        end = _cut(
            text,
            boundaries,
            low=start + 3 * overlap + 1,
            high=start + window_chars,
        )
        windows.append((start, end))
        resume = _cut(text, boundaries, low=end - 2 * overlap, high=end - overlap - 1)
        start = resume + 1 if text[resume].isspace() else resume
    return windows


def _covered_chars(windows: list[tuple[int, int]]) -> int:
    """Count characters inside at least one of the ascending windows.

    Args:
        windows: Half-open ranges with ascending starts and ends.

    Returns:
        Size of the union of the ranges.
    """
    covered = 0
    reach = 0
    for start, end in windows:
        start = max(start, reach)
        if end > start:
            covered += end - start
            reach = end
    return covered


def _validated_items(items: list[Any]) -> list[ExtractedMedia]:
    """Validate one reply's items, keeping the first of each title.

    Args:
        items: Parsed items from one reply.

    Returns:
        Validated items in reply order.
    """
    validated_items: list[ExtractedMedia] = []
    seen_titles: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or "title" not in item:
            continue

        title = str(item["title"]).strip()
        title_lower = title.lower()
        if title_lower in seen_titles:
            continue
        seen_titles.add(title_lower)

        validated = _validate_item(item=item, title=title)
        if validated is None:
            continue
        validated_items.append(validated)
    return validated_items


def _merge_items(
    merged: list[ExtractedMedia],
    seen_titles: set[str],
    items: list[ExtractedMedia],
) -> None:
    """Append items whose case-insensitive title has not been seen yet.

    Args:
        merged: Items kept so far, extended in place.
        seen_titles: Lower-cased titles kept so far, extended in place.
        items: Validated items from the next window.
    """
    for item in items:
        title_lower = item.title.lower()
        if title_lower in seen_titles:
            continue
        seen_titles.add(title_lower)
        merged.append(item)


# This adapter implements one protocol operation; extra methods have no purpose.
# pylint: disable-next=too-few-public-methods
class LLMExtractor:
    """Extract media mentions from transcripts via the Anthropic API.

    Args:
        client: Anthropic SDK client; constructed from environment
            credentials when omitted. Inject a stub in tests.
        model: Model id used for extraction requests.
        system_prompt: Injected (podex-ops) system prompt; falls back to the
            generic prompt in :mod:`podex.services.prompts_default`.
    """

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str = DEFAULT_EXTRACTION_MODEL,
        system_prompt: str | None = None,
    ) -> None:
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.system_prompt = system_prompt or DEFAULT_EXTRACTION_SYSTEM_PROMPT

    def extract_from_transcript(
        self,
        segments: list[dict[str, str]],
        max_chars: int = EXTRACTION_WINDOW_CHARS,
        max_windows: int = MAX_EXTRACTION_WINDOWS,
    ) -> ExtractionResult:
        """Extract media mentions from a full transcript.

        Text up to ``max_chars`` is sent in one request, exactly as before.
        Longer text is read in up to ``max_windows`` overlapping windows of at
        most ``max_chars`` characters each; see :func:`_plan_windows`.

        Args:
            segments: Transcript segments with ``text`` keys.
            max_chars: Most characters sent to the model in one request.
            max_windows: Most requests made for this transcript.

        Returns:
            ExtractionResult with items, coverage and any errors encountered.

        Raises:
            ValueError: When ``max_chars`` is not more than four overlaps or
                ``max_windows`` is below one.
        """
        if max_chars <= 4 * EXTRACTION_WINDOW_OVERLAP_CHARS:
            raise ValueError("max_chars must exceed four window overlaps")
        if max_windows < 1:
            raise ValueError("max_windows must be at least one")

        full_text, boundaries = _joined_segments(segments)
        result = ExtractionResult(transcript_chars=len(full_text))
        if len(full_text) < _MIN_TRANSCRIPT_CHARS:
            result.unextracted_chars = len(full_text)
            return result

        windows = _plan_windows(
            full_text,
            boundaries,
            window_chars=max_chars,
            max_windows=max_windows,
        )
        read: list[tuple[int, int]] = []
        seen_titles: set[str] = set()
        for start, end in windows:
            items, errors = self._extract_window(full_text[start:end])
            result.windows += 1
            if errors:
                result.failed_windows += 1
                result.errors.extend(errors)
                continue
            read.append((start, end))
            _merge_items(result.items, seen_titles, items)

        result.unread_chars = len(full_text) - windows[-1][1]
        result.unextracted_chars = len(full_text) - _covered_chars(read)
        if result.window_limit_reached:
            logger.warning(
                "Extraction stopped after %d windows; %d of %d chars not read",
                result.windows,
                result.unread_chars,
                result.transcript_chars,
            )
        if not result.failed_windows:
            # Failed windows were already logged with their own diagnostics.
            logger.info(
                "Extracted %d media items from %d windows",
                len(result.items),
                result.windows,
            )
        return result

    def _extract_window(self, text: str) -> tuple[list[ExtractedMedia], list[str]]:
        """Send one window and validate its reply.

        Args:
            text: Window text, sent inside ``<transcript>`` tags.

        Returns:
            Tuple of (validated items, error descriptors). Items are empty
            whenever errors are not.
        """
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=self.system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": ("<transcript>\n" + text + "\n</transcript>"),
                    },
                ],
            )
            response_text = first_response_text(response)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            return [], [_request_failure(exc)]
        except MALFORMED_RESPONSE_ERRORS as exc:
            return [], [_request_failure(exc, malformed=True)]

        if getattr(response, "stop_reason", None) in _TRUNCATED_STOP_REASONS:
            message = describe_failure(
                "extraction", reason=FailureReason.TRUNCATED_RESPONSE
            )
            logger.warning(message)
            return [], [message]

        if not response_text:
            return [], []

        items, parse_errors = self._parse_json_response(response_text)
        if parse_errors:
            return [], parse_errors
        return _validated_items(items), []

    def _parse_json_response(
        self,
        response_text: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Parse a JSON array from model output, tolerating surrounding prose.

        Args:
            response_text: Raw text returned by the model.

        Returns:
            Tuple of (parsed items, list of error messages).
        """
        errors: list[str] = []

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return parsed, errors
        if parsed is not None:
            errors.append(
                describe_failure("extraction", reason=FailureReason.EXPECTED_JSON_ARRAY)
            )
            return [], errors

        start = response_text.find("[")
        if start < 0:
            error_msg = describe_failure(
                "extraction", reason=FailureReason.MISSING_JSON_ARRAY
            )
            logger.warning(error_msg)
            errors.append(error_msg)
            return [], errors

        depth = 0
        end = start
        for i, char in enumerate(response_text[start:], start):
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        if end <= start:
            error_msg = describe_failure(
                "extraction", reason=FailureReason.INCOMPLETE_JSON_ARRAY
            )
            logger.warning(error_msg)
            errors.append(error_msg)
            return [], errors

        try:
            return json.loads(response_text[start:end]), errors
        except json.JSONDecodeError as exc:
            error_msg = describe_failure(
                "extraction", exc, reason=FailureReason.INVALID_JSON
            )
            log_failure(logger, error_msg, exc)
            errors.append(error_msg)
            return [], errors
