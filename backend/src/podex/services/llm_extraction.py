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
from dataclasses import dataclass, field, replace
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
# Least text consecutive windows share; they share at most 3,999. A spoken
# sentence naming a work, with the words that identify it, is a few hundred
# characters; 2,000 characters is about two minutes of speech. Every passage
# of up to 2,000 characters that ends before the last window sent ends lies
# whole inside one window.
EXTRACTION_WINDOW_OVERLAP_CHARS = 2_000
# Inside one segment, a window is cut at whitespace only within this many
# characters of the cut limit; past that it is cut at the limit itself.
_WHITESPACE_LOOKBACK_CHARS = 1_000
# Most windows per transcript. Any two consecutive windows that are not the
# last advance the read end by at least 146,002 characters, so eight windows
# read at least 588,007 characters; with segments of ordinary length (a few
# hundred characters) they read about 1.18 million. The SDK client retries a
# failed request twice by default, so one transcript makes at most 24 HTTP
# requests carrying at most 3.6 million characters, each asking for at most
# 4,000 output tokens.
MAX_EXTRACTION_WINDOWS = 8
# Stop reasons meaning the reply was cut off before the model finished.
_TRUNCATED_STOP_REASONS = frozenset({"max_tokens", "model_context_window_exceeded"})
_REFUSAL_STOP_REASON = "refusal"
# Failures every later window would meet too: the key, permissions, model and
# request parameters are the same for every window, and windows are of the
# same size. After one of these no further window is sent.
_RUN_ENDING_ERRORS = (
    anthropic.BadRequestError,
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.NotFoundError,
)
# Exceptions that decoding and validating one untrusted reply can raise (an
# overflowing number, deep nesting, a value of the wrong type), or that a
# client raises for a reply it cannot build. They fail that window only.
# Other exceptions propagate unchanged, as for a single request before
# windowing.
_REPLY_ERRORS = (ArithmeticError, RecursionError, TypeError, ValueError)


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

    The transcript is read in consecutive, overlapping windows. A window
    fails when its request fails, its reply is refused, empty, cut off at the
    output token cap, has a stop reason that is not a string, or cannot be
    decoded into a JSON array. A failed window adds exactly one descriptor to
    ``errors`` and contributes no items; the items of the other windows are
    kept. After a failure every window would repeat (bad request,
    authentication, permission, unknown model) no further window is sent.

    Items are merged across windows by case-insensitive title. The item with
    the highest confidence is kept (the first found on a tie) and any field it
    lacks is filled from the others, most confident first. Each item keeps the
    position where its title first appeared.

    Attributes:
        items: Merged, validated items.
        errors: Allowlisted failure descriptors, one per failed window.
        transcript_chars: Length of the joined transcript text.
        windows: Requests sent for this transcript, not counting SDK retries.
        unread_chars: Characters never sent in any request: the text past
            the last window sent (because of the window bound or a failure
            that ends the run) and text below the minimum length.
        window_limit_reached: Whether the transcript needed more windows than
            the bound allows.
        unextracted_ranges: Ascending, disjoint ``(start, end)`` offsets into
            the joined segment text (segments stripped and joined by one
            space) of text inside no window whose reply was used. There are at
            most ``max_windows + 1`` ranges.
    """

    items: list[ExtractedMedia] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    transcript_chars: int = 0
    windows: int = 0
    unread_chars: int = 0
    window_limit_reached: bool = False
    unextracted_ranges: list[tuple[int, int]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Return True when no window failed.

        This does not say that the whole transcript was read: it stays true
        when the window bound left text unread or the text was too short to
        send, since no request failed. Use :attr:`fully_covered` for that.
        """
        return len(self.errors) == 0

    @property
    def failed_windows(self) -> int:
        """Return the number of windows whose reply was not used."""
        return len(self.errors)

    @property
    def unextracted_chars(self) -> int:
        """Return the number of characters in :attr:`unextracted_ranges`."""
        return sum(end - start for start, end in self.unextracted_ranges)

    @property
    def fully_covered(self) -> bool:
        """Return True when every character was in a window whose reply was used.

        An empty transcript is covered; text below the minimum length is not.
        """
        return not self.unextracted_ranges


@dataclass(frozen=True)
class _WindowOutcome:
    """Items or the failure of one window, and whether the run must end."""

    items: list[ExtractedMedia] = field(default_factory=list)
    error: str | None = None
    ends_run: bool = False


def _request_failure(
    error: BaseException,
    *,
    reason: FailureReason = FailureReason.REQUEST_FAILED,
) -> str:
    """Describe and log an exception that failed one window.

    Args:
        error: SDK, decoding or reply failure.
        reason: Fixed reason recorded with the exception class.

    Returns:
        The allowlisted diagnostic for the failed window.
    """
    message: str = describe_failure("extraction", error, reason=reason)
    log_failure(logger, message, error, level=logging.ERROR)
    return message


def _reply_failure(reason: FailureReason) -> _WindowOutcome:
    """Describe and log a reply that cannot be used, without its content.

    Args:
        reason: Fixed reason for the unusable reply.

    Returns:
        The failed outcome for the window.
    """
    message: str = describe_failure("extraction", reason=reason)
    logger.warning(message)
    return _WindowOutcome(error=message)


def _unusable_reply_reason(
    stop_reason: object,
    response_text: str,
) -> FailureReason | None:
    """Classify a reply that must not be parsed.

    Args:
        stop_reason: The reply's stop reason as received.
        response_text: The reply's first text block, stripped.

    Returns:
        The failure reason, or None when the reply may be parsed.
    """
    if stop_reason is not None and not isinstance(stop_reason, str):
        return FailureReason.MALFORMED_RESPONSE
    if stop_reason in _TRUNCATED_STOP_REASONS:
        return FailureReason.TRUNCATED_RESPONSE
    if stop_reason == _REFUSAL_STOP_REASON:
        return FailureReason.REFUSED_RESPONSE
    if not response_text:
        return FailureReason.EMPTY_RESPONSE
    return None


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
        character in range and at most 1,000 characters before ``high``;
        otherwise ``high``.
    """
    index = bisect_right(boundaries, high) - 1
    if index >= 0 and boundaries[index] >= low:
        return boundaries[index]
    floor = max(low, high - _WHITESPACE_LOOKBACK_CHARS)
    for position in range(high, floor - 1, -1):
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

    A window that does not reach the end of the text ends at the last segment
    separator between 6,001 and ``window_chars`` characters after its start.
    When no separator lies there, it ends at the last whitespace within the
    final 1,000 characters of that range, or else at ``window_chars``
    exactly. The next window starts after the last separator between 4,000
    and 2,001 characters before that end; failing that, after the last
    whitespace between 3,001 and 2,001 characters before it; failing that,
    2,001 characters before it. Consecutive windows therefore share 2,000 to
    3,999 characters, and every passage of up to 2,000 characters before the
    last end lies whole inside one window.

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


def _unextracted(used: list[tuple[int, int]], length: int) -> list[tuple[int, int]]:
    """Return the ranges of ``[0, length)`` outside every used window.

    Args:
        used: Windows whose reply was used, with ascending starts and ends.
        length: Length of the joined text.

    Returns:
        Ascending, disjoint ``(start, end)`` ranges.
    """
    gaps: list[tuple[int, int]] = []
    reach = 0
    for start, end in used:
        if start > reach:
            gaps.append((reach, start))
        reach = max(reach, end)
    if reach < length:
        gaps.append((reach, length))
    return gaps


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


def _merged_item(found: list[ExtractedMedia]) -> ExtractedMedia:
    """Merge items of one title found in several windows.

    Args:
        found: Items with the same case-insensitive title, in transcript order.

    Returns:
        The most confident item (the first found on a tie), with creator and
        year, when it lacks them, taken from the most confident other item
        that has them. Creator and year are the only fields an item may lack.
    """
    ranked = sorted(found, key=lambda item: -item.confidence)
    best = ranked[0]
    creator = next((item.creator for item in ranked if item.creator is not None), None)
    year = next((item.year for item in ranked if item.year is not None), None)
    return replace(best, creator=creator, year=year)


def _merged_items(per_window: list[list[ExtractedMedia]]) -> list[ExtractedMedia]:
    """Merge the items of all used windows by case-insensitive title.

    Args:
        per_window: Validated items of each used window, in transcript order.

    Returns:
        One item per title, in order of first appearance.
    """
    by_title: dict[str, list[ExtractedMedia]] = {}
    for items in per_window:
        for item in items:
            by_title.setdefault(item.title.lower(), []).append(item)
    return [_merged_item(found) for found in by_title.values()]


def _check_bounds(text_chars: int, *, max_chars: int, max_windows: int) -> None:
    """Reject bounds that cannot read a transcript of ``text_chars``.

    Args:
        text_chars: Length of a transcript long enough to send.
        max_chars: Most characters sent in one request.
        max_windows: Most requests for this transcript.

    Raises:
        ValueError: When ``max_windows`` is below one, or the text needs more
            than one window and ``max_chars`` is 8,000 or less.
    """
    if max_windows < 1:
        raise ValueError("max_windows must be at least one")
    if text_chars > max_chars and max_chars <= 4 * EXTRACTION_WINDOW_OVERLAP_CHARS:
        raise ValueError("max_chars must exceed 8,000 to read in windows")


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
        most ``max_chars`` characters each; see :func:`_plan_windows`. Empty
        text, and text below the 100-character minimum, returns without a
        request whatever the bounds.

        Args:
            segments: Transcript segments with ``text`` keys.
            max_chars: Most characters sent to the model in one request.
            max_windows: Most requests made for this transcript.

        Returns:
            ExtractionResult with items, coverage and any errors encountered.

        Raises:
            ValueError: When the text is long enough to send and
                ``max_windows`` is below one, or the text needs windows and
                ``max_chars`` is 8,000 or less.
        """
        full_text, boundaries = _joined_segments(segments)
        result = ExtractionResult(transcript_chars=len(full_text))
        if len(full_text) < _MIN_TRANSCRIPT_CHARS:
            if full_text:
                result.unread_chars = len(full_text)
                result.unextracted_ranges = [(0, len(full_text))]
            return result

        _check_bounds(len(full_text), max_chars=max_chars, max_windows=max_windows)
        windows = _plan_windows(
            full_text,
            boundaries,
            window_chars=max_chars,
            max_windows=max_windows,
        )
        result.window_limit_reached = windows[-1][1] < len(full_text)
        used = self._read_windows(full_text, windows, result)
        result.unread_chars = len(full_text) - windows[result.windows - 1][1]
        result.unextracted_ranges = _unextracted(used, len(full_text))
        if result.window_limit_reached:
            logger.warning(
                "Extraction window limit reached after %d windows; "
                "%d of %d characters not read",
                result.windows,
                result.unread_chars,
                result.transcript_chars,
            )
        if result.success:
            # Failed windows were already logged with their own diagnostics.
            logger.info(
                "Extracted %d media items from %d windows",
                len(result.items),
                result.windows,
            )
        return result

    def _read_windows(
        self,
        text: str,
        windows: list[tuple[int, int]],
        result: ExtractionResult,
    ) -> list[tuple[int, int]]:
        """Send windows in order, recording failures and merging items.

        Args:
            text: Joined transcript text.
            windows: Planned windows, in order.
            result: Result to fill with items, errors and the window count.

        Returns:
            The windows whose reply was used.
        """
        used: list[tuple[int, int]] = []
        per_window: list[list[ExtractedMedia]] = []
        for start, end in windows:
            result.windows += 1
            try:
                outcome = self._extract_window(text[start:end])
            except _REPLY_ERRORS as exc:
                outcome = _WindowOutcome(
                    error=_request_failure(
                        exc, reason=FailureReason.UNCLASSIFIED_FAILURE
                    )
                )
            if outcome.error is None:
                used.append((start, end))
                per_window.append(outcome.items)
            else:
                result.errors.append(outcome.error)
            if outcome.ends_run:
                break
        result.items = _merged_items(per_window)
        return used

    def _extract_window(self, text: str) -> _WindowOutcome:
        """Send one window and validate its reply.

        Args:
            text: Window text, sent inside ``<transcript>`` tags.

        Returns:
            The window's validated items, or its failure.
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
        except _RUN_ENDING_ERRORS as exc:
            return _WindowOutcome(error=_request_failure(exc), ends_run=True)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            return _WindowOutcome(error=_request_failure(exc))
        except MALFORMED_RESPONSE_ERRORS as exc:
            return _WindowOutcome(
                error=_request_failure(exc, reason=FailureReason.MALFORMED_RESPONSE)
            )

        unusable = _unusable_reply_reason(
            getattr(response, "stop_reason", None), response_text
        )
        if unusable is not None:
            return _reply_failure(unusable)

        items, parse_errors = self._parse_json_response(response_text)
        if parse_errors:
            return _WindowOutcome(error=parse_errors[0])
        return _WindowOutcome(items=_validated_items(items))

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
