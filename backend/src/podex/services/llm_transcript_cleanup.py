"""LLM transcript cleanup for speech-to-text errors.

Prompts follow the #29 load-don't-embed boundary: the system prompt is
injected by the caller (tuned prompts are podex-ops data, resolved through
:mod:`podex.services.prompt_config`) and falls back to the generic prompt in
:mod:`podex.services.prompts_default`.

The model never returns transcript text. It proposes corrections (exact
original text, replacement, reason) and this module applies the ones that pass
the rules in :func:`_check_correction` and :func:`_select_corrections` to the
original text. Every character no applied correction covers is returned as it
was. See ADR 0021.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import Any, NamedTuple

import anthropic

from podex.services.failure_codes import FailureReason
from podex.services.failure_descriptors import describe_failure, log_failure
from podex.services.llm_response import (
    MALFORMED_RESPONSE_ERRORS,
    first_response_text,
)
from podex.services.prompts_default import DEFAULT_CLEANUP_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

DEFAULT_CLEANUP_MODEL = "claude-opus-4-8"
_MAX_OUTPUT_TOKENS = 8000
_MIN_TRANSCRIPT_CHARS = 100
_MAX_TERMINOLOGY_TERMS = 50
_MAX_LOOKBACK_CHARS = 1000
# A transcript is read in at most this many requests of ``max_chars`` each.
MAX_PIECES = 5
# Consecutive pieces share up to this many characters (at most a tenth of
# ``max_chars``) so that an error across a cut is seen whole by one request.
MAX_OVERLAP_CHARS = 200
# Corrections accepted from one reply, and the size of each one.
MAX_CORRECTIONS_PER_REPLY = 200
MAX_ORIGINAL_CHARS = 300
MAX_REPLACEMENT_GROWTH_CHARS = 40
MAX_REASON_CHARS = 200
_CUT_OFF_STOP_REASON = "max_tokens"


class CorrectionRejection(StrEnum):
    """Why a proposed correction was not applied."""

    MALFORMED = auto()
    OVER_LIMIT = auto()
    ORIGINAL_TOO_LONG = auto()
    EMPTY_REPLACEMENT = auto()
    UNCHANGED = auto()
    OVERSIZED_REPLACEMENT = auto()
    NOT_FOUND = auto()
    AMBIGUOUS = auto()
    AT_CUT = auto()
    DUPLICATE = auto()
    OVERLAP = auto()


@dataclass(frozen=True)
class CleanupCorrection:
    """A correction applied to the transcript.

    Attributes:
        original: Exact input text that was replaced.
        corrected: Replacement text proposed by the model.
        reason: The model's short reason, cut to ``MAX_REASON_CHARS``.
        start: Index of ``original`` in the input transcript.
    """

    original: str
    corrected: str
    reason: str
    start: int

    @property
    def end(self) -> int:
        """Return the index just after ``original`` in the input."""
        return self.start + len(self.original)


@dataclass
class CleanupResult:
    """Outcome of one cleanup run: corrected text plus correction counts.

    ``cleaned_text`` is the input with the applied corrections substituted at
    their positions; every other character is copied from the input
    unchanged. ``success`` only says that no error occurred.

    Attributes:
        cleaned_text: The input with applied corrections.
        corrections_made: Corrections applied, in input order.
        errors: Allowlisted failure descriptors; empty on success.
        cleaned_chars: Input characters inside a piece whose reply was used.
        uncleaned_chars: Input characters no used reply covered, including
            ``unread_chars``.
        unread_chars: Input characters after the last piece whose reply
            arrived (or that was blank and skipped), because the piece bound
            was reached, a request failed or the input was shorter than the
            minimum. These are returned exactly as they were.
        rejections: Count of proposed corrections not applied, by reason.
    """

    cleaned_text: str
    corrections_made: list[CleanupCorrection] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cleaned_chars: int = 0
    uncleaned_chars: int = 0
    unread_chars: int = 0
    rejections: dict[CorrectionRejection, int] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Return True if cleanup completed without errors."""
        return len(self.errors) == 0

    @property
    def partial(self) -> bool:
        """Return True if some input was cleaned and some was not."""
        return self.cleaned_chars > 0 and self.uncleaned_chars > 0

    @property
    def fully_cleaned(self) -> bool:
        """Return True if a used reply covered every input character."""
        return self.success and self.cleaned_chars > 0 and self.uncleaned_chars == 0

    @property
    def correction_count(self) -> int:
        """Return the number of corrections applied."""
        return len(self.corrections_made)

    @property
    def corrections_rejected(self) -> int:
        """Return the number of proposed corrections not applied."""
        return sum(self.rejections.values())

    @property
    def corrections_proposed(self) -> int:
        """Return the number of corrections the used replies proposed."""
        return self.correction_count + self.corrections_rejected


class _Piece(NamedTuple):
    """Half-open range ``[start, end)`` of the input sent in one request."""

    start: int
    end: int


class _Reply(NamedTuple):
    """Usable corrections from one reply, or why it was not used."""

    items: list[object] | None
    error: str | None
    stop: bool


def _reject(
    rejections: dict[CorrectionRejection, int],
    reason: CorrectionRejection,
    count: int = 1,
) -> None:
    """Add ``count`` rejections for ``reason``."""
    rejections[reason] = rejections.get(reason, 0) + count


def _split_at_limit(text: str, max_chars: int) -> tuple[str, str]:
    """Split text into the piece sent for cleanup and the text after it.

    Text within the limit is sent whole. Longer text is cut at the last
    character ``str.isspace`` accepts among indices ``max_chars - lookback``
    to ``max_chars``, where ``lookback`` is ``min(max_chars // 10, 1000)``;
    the rest then starts with that character. Without such a character the
    cut falls exactly at ``max_chars``, which may split a word.

    Args:
        text: Transcript text from the start of the piece.
        max_chars: Maximum characters sent to the model.

    Returns:
        Tuple of (head, rest); ``head + rest == text``.
    """
    if len(text) <= max_chars:
        return text, ""
    lookback = min(max_chars // 10, _MAX_LOOKBACK_CHARS)
    for index in range(max_chars, max_chars - lookback - 1, -1):
        if text[index].isspace():
            return text[:index], text[index:]
    return text[:max_chars], text[max_chars:]


def _next_start(text: str, end: int, overlap: int) -> int:
    """Return where the piece after one ending at ``end`` starts.

    It starts at the first whitespace among the ``overlap`` characters before
    ``end``, so its first word is whole, or at ``end`` when there is none.

    Args:
        text: Full transcript text.
        end: End of the previous piece.
        overlap: Most characters the two pieces may share.

    Returns:
        Start index of the next piece.
    """
    for index in range(end - overlap, end):
        if text[index].isspace():
            return index
    return end


def _plan_pieces(text: str, max_chars: int) -> list[_Piece]:
    """Cut the input into at most ``MAX_PIECES`` consecutive pieces.

    Each piece is cut as :func:`_split_at_limit` describes. The next piece
    starts as :func:`_next_start` describes, so neighbours share at most
    ``min(max_chars // 10, MAX_OVERLAP_CHARS)`` characters. A piece is at
    least ``max_chars - 2 * (max_chars // 10)`` characters longer than that
    overlap, so every piece advances.

    Args:
        text: Full transcript text.
        max_chars: Maximum characters of one piece.

    Returns:
        Pieces in input order; text after the last one is not read.
    """
    overlap = min(max_chars // 10, MAX_OVERLAP_CHARS)
    pieces: list[_Piece] = []
    start = 0
    while start < len(text) and len(pieces) < MAX_PIECES:
        head, _rest = _split_at_limit(text[start:], max_chars)
        end = start + len(head)
        pieces.append(_Piece(start, end))
        if end >= len(text):
            break
        start = _next_start(text, end, overlap)
    return pieces


def _covered_chars(pieces: list[_Piece]) -> int:
    """Return how many input characters the pieces cover together.

    Args:
        pieces: Pieces in input order; neighbours may overlap.

    Returns:
        Size of their union.
    """
    total = 0
    reach = 0
    for piece in pieces:
        start = max(piece.start, reach)
        total += max(piece.end - start, 0)
        reach = max(reach, piece.end)
    return total


def _correction_fields(item: object) -> tuple[str, str, str] | None:
    """Return (original, corrected, reason) from a well-formed item.

    An item is well formed when it is an object whose ``original`` is a
    non-empty string, whose ``corrected`` is a string and whose ``reason`` is
    a string, null or absent. Other keys are ignored.

    Args:
        item: One entry of the reply's ``corrections`` list.

    Returns:
        The three fields, with the reason cut to ``MAX_REASON_CHARS``, or
        None when the item is malformed.
    """
    if not isinstance(item, dict):
        return None
    original = item.get("original")
    corrected = item.get("corrected")
    reason = item.get("reason")
    if reason is None:
        reason = ""
    if (
        isinstance(original, str)
        and original
        and isinstance(corrected, str)
        and isinstance(reason, str)
    ):
        return original, corrected, reason[:MAX_REASON_CHARS]
    return None


def _replacement_rejection(
    original: str,
    corrected: str,
) -> CorrectionRejection | None:
    """Apply the size rules to one correction.

    Args:
        original: Text the correction replaces.
        corrected: Its replacement.

    Returns:
        The rule the correction breaks, or None.
    """
    if len(original) > MAX_ORIGINAL_CHARS:
        return CorrectionRejection.ORIGINAL_TOO_LONG
    if not corrected.strip():
        return CorrectionRejection.EMPTY_REPLACEMENT
    if corrected == original:
        return CorrectionRejection.UNCHANGED
    if len(corrected) > len(original) + MAX_REPLACEMENT_GROWTH_CHARS:
        return CorrectionRejection.OVERSIZED_REPLACEMENT
    return None


def _locate(original: str, piece_text: str) -> int | CorrectionRejection:
    """Find the only occurrence of ``original`` in the piece.

    Occurrences are counted with overlaps, so ``"aa"`` occurs twice in
    ``"aaa"``.

    Args:
        original: Text the correction replaces.
        piece_text: Text of the piece the model read.

    Returns:
        Its index in the piece, or why it cannot be placed.
    """
    position = piece_text.find(original)
    if position < 0:
        return CorrectionRejection.NOT_FOUND
    if piece_text.find(original, position + 1) >= 0:
        return CorrectionRejection.AMBIGUOUS
    return position


def _mid_word_cut(text: str, index: int) -> bool:
    """Return True when a piece boundary at ``index`` splits a word.

    Args:
        text: Full transcript text.
        index: Piece start or end in the input.

    Returns:
        Whether non-whitespace characters lie on both sides of ``index``.
    """
    return (
        0 < index < len(text)
        and not text[index - 1].isspace()
        and not text[index].isspace()
    )


def _check_correction(
    item: object,
    text: str,
    piece: _Piece,
) -> CleanupCorrection | CorrectionRejection:
    """Turn one proposed correction into a placed correction or a rejection.

    A correction that reaches a piece boundary which splits a word is
    rejected as ``AT_CUT``: the model saw only part of that word.

    Args:
        item: One entry of the reply's ``corrections`` list.
        text: Full transcript text.
        piece: Piece the model read.

    Returns:
        The correction placed in the input, or why it was rejected.
    """
    fields = _correction_fields(item)
    if fields is None:
        return CorrectionRejection.MALFORMED
    original, corrected, reason = fields
    rejection = _replacement_rejection(original, corrected)
    if rejection is not None:
        return rejection
    position = _locate(original, text[piece.start : piece.end])
    if isinstance(position, CorrectionRejection):
        return position
    start = piece.start + position
    end = start + len(original)
    if (start == piece.start and _mid_word_cut(text, start)) or (
        end == piece.end and _mid_word_cut(text, end)
    ):
        return CorrectionRejection.AT_CUT
    return CleanupCorrection(
        original=original,
        corrected=corrected,
        reason=reason,
        start=start,
    )


def _place_corrections(
    items: list[object],
    text: str,
    piece: _Piece,
    rejections: dict[CorrectionRejection, int],
) -> list[CleanupCorrection]:
    """Check one reply's corrections; count the ones rejected.

    Only the first ``MAX_CORRECTIONS_PER_REPLY`` entries are considered; the
    rest are rejected as ``OVER_LIMIT``.

    Args:
        items: The reply's ``corrections`` list.
        text: Full transcript text.
        piece: Piece the model read.
        rejections: Counts to update.

    Returns:
        Corrections placed in the input.
    """
    if len(items) > MAX_CORRECTIONS_PER_REPLY:
        _reject(
            rejections,
            CorrectionRejection.OVER_LIMIT,
            len(items) - MAX_CORRECTIONS_PER_REPLY,
        )
        logger.warning(
            "Cleanup reply proposed %d corrections; only %d considered",
            len(items),
            MAX_CORRECTIONS_PER_REPLY,
        )
    placed: list[CleanupCorrection] = []
    for item in items[:MAX_CORRECTIONS_PER_REPLY]:
        checked = _check_correction(item, text, piece)
        if isinstance(checked, CorrectionRejection):
            _reject(rejections, checked)
        else:
            placed.append(checked)
    return placed


def _select_corrections(
    placed: list[CleanupCorrection],
    rejections: dict[CorrectionRejection, int],
) -> list[CleanupCorrection]:
    """Drop duplicates and overlapping corrections.

    A correction with the same position, original and replacement as an
    earlier one (the same fix proposed by two overlapping pieces) is counted
    as ``DUPLICATE``. Of the rest, every correction whose original shares a
    character with another's is rejected as ``OVERLAP``; neither wins.
    Corrections that only touch end to start both apply.

    Args:
        placed: Corrections placed in the input, from every used reply.
        rejections: Counts to update.

    Returns:
        Non-overlapping corrections in input order.
    """
    unique: dict[tuple[int, str, str], CleanupCorrection] = {}
    for correction in placed:
        key = (correction.start, correction.original, correction.corrected)
        if key in unique:
            _reject(rejections, CorrectionRejection.DUPLICATE)
        else:
            unique[key] = correction
    ordered = sorted(unique.values(), key=lambda c: (c.start, c.end))
    overlapping: set[int] = set()
    for index, correction in enumerate(ordered):
        for later in range(index + 1, len(ordered)):
            if ordered[later].start >= correction.end:
                break
            overlapping.update((index, later))
    if overlapping:
        _reject(rejections, CorrectionRejection.OVERLAP, len(overlapping))
    return [c for i, c in enumerate(ordered) if i not in overlapping]


def _apply_corrections(text: str, corrections: list[CleanupCorrection]) -> str:
    """Substitute non-overlapping corrections at their input positions.

    Args:
        text: Original transcript.
        corrections: Non-overlapping corrections in input order.

    Returns:
        The text with each original replaced; all else unchanged.
    """
    parts: list[str] = []
    cursor = 0
    for correction in corrections:
        parts.append(text[cursor : correction.start])
        parts.append(correction.corrected)
        cursor = correction.end
    parts.append(text[cursor:])
    return "".join(parts)


def _parse_json_response(response_text: str) -> dict[str, Any] | None:
    """Parse a JSON object from model output, tolerating surrounding prose.

    Args:
        response_text: Raw text returned by the model.

    Returns:
        The parsed object, or None when no valid JSON object is found.
    """
    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    start = response_text.find("{")
    if start < 0:
        return None

    brace_count = 0
    end = start
    for i, char in enumerate(response_text[start:], start):
        if char == "{":
            brace_count += 1
        elif char == "}":
            brace_count -= 1
            if brace_count == 0:
                end = i + 1
                break

    if end <= start:
        return None

    try:
        fallback = json.loads(response_text[start:end])
    except json.JSONDecodeError:
        return None
    return fallback if isinstance(fallback, dict) else None


def _parse_corrections(response_text: str) -> tuple[list[object] | None, str | None]:
    """Return the reply's ``corrections`` list, or why the reply is unusable.

    A reply that carries ``cleaned_text`` has the shape of the prompt used
    before corrections only; it is reported and nothing in it is applied.

    Args:
        response_text: Text of the reply's first text block.

    Returns:
        Tuple of (corrections, error); exactly one is meaningful.
    """
    parsed = _parse_json_response(response_text)
    if parsed is None:
        return None, describe_failure("cleanup", reason=FailureReason.INVALID_JSON)
    if "cleaned_text" in parsed:
        return None, describe_failure(
            "cleanup", reason=FailureReason.LEGACY_RESPONSE_SHAPE
        )
    corrections = parsed.get("corrections")
    if not isinstance(corrections, list):
        return None, describe_failure(
            "cleanup", reason=FailureReason.INVALID_CORRECTIONS
        )
    return corrections, None


def _cleanup_context(
    *,
    podcast_name: str | None,
    host_name: str | None,
    guest_name: str | None,
    terminology: list[str] | None,
) -> str:
    """Format the optional context attached to transcript cleanup.

    Args:
        podcast_name: Podcast display name.
        host_name: Optional host name.
        guest_name: Optional guest name.
        terminology: Known terms limited to the existing prompt allowance.

    Returns:
        Context lines, or the existing fallback when no context is supplied.
    """
    context_parts = []
    if podcast_name:
        context_parts.append(f"Podcast: {podcast_name}")
    if host_name:
        context_parts.append(f"Host: {host_name}")
    if guest_name:
        context_parts.append(f"Guest: {guest_name}")
    if terminology:
        terms = terminology[:_MAX_TERMINOLOGY_TERMS]
        context_parts.append(f"Known terms and names: {', '.join(terms)}")
    return "\n".join(context_parts) if context_parts else "No additional context."


# This adapter implements one protocol operation; extra methods have no purpose.
# pylint: disable-next=too-few-public-methods
class TranscriptCleaner:
    """Fix speech-to-text errors in transcripts via the Anthropic API.

    Args:
        client: Anthropic SDK client; constructed from environment
            credentials when omitted. Inject a stub in tests.
        model: Model id used for cleanup requests.
        system_prompt: Injected (podex-ops) system prompt; falls back to the
            generic prompt in :mod:`podex.services.prompts_default`.
    """

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str = DEFAULT_CLEANUP_MODEL,
        system_prompt: str | None = None,
    ) -> None:
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.system_prompt = system_prompt or DEFAULT_CLEANUP_SYSTEM_PROMPT

    # Keep the existing named command inputs compatible with callers.
    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def cleanup_transcript(
        self,
        transcript_text: str,
        podcast_name: str | None = None,
        host_name: str | None = None,
        guest_name: str | None = None,
        terminology: list[str] | None = None,
        max_chars: int = 100000,
    ) -> CleanupResult:
        """Clean up a transcript using the configured model.

        Args:
            transcript_text: The raw transcript text to clean.
            podcast_name: Name of the podcast for context.
            host_name: Name of the host.
            guest_name: Name of the guest (if any).
            terminology: Known terms that may be misspelled.
            max_chars: Maximum characters of one request. Longer input is
                read in at most ``MAX_PIECES`` pieces (see
                :func:`_plan_pieces`); text after the last is kept as it is
                and counted in ``unread_chars``.

        Returns:
            CleanupResult with corrected text and correction counts.
        """
        result = CleanupResult(
            cleaned_text=transcript_text,
            uncleaned_chars=len(transcript_text),
            unread_chars=len(transcript_text),
        )

        if len(transcript_text) < _MIN_TRANSCRIPT_CHARS:
            return result

        pieces = _plan_pieces(transcript_text, max_chars)
        if pieces[-1].end < len(transcript_text):
            logger.warning(
                "Transcript cleanup reads %d of %d chars in %d pieces",
                pieces[-1].end,
                len(transcript_text),
                len(pieces),
            )

        context = _cleanup_context(
            podcast_name=podcast_name,
            host_name=host_name,
            guest_name=guest_name,
            terminology=terminology,
        )
        placed, used = self._read_pieces(transcript_text, pieces, context, result)

        result.corrections_made = _select_corrections(placed, result.rejections)
        result.cleaned_text = _apply_corrections(
            transcript_text, result.corrections_made
        )
        result.cleaned_chars = _covered_chars(used)
        result.uncleaned_chars = len(transcript_text) - result.cleaned_chars
        if used:
            logger.info(
                "Applied %d of %d proposed corrections",
                result.correction_count,
                result.corrections_proposed,
            )
        return result

    def _read_pieces(
        self,
        text: str,
        pieces: list[_Piece],
        context: str,
        result: CleanupResult,
    ) -> tuple[list[CleanupCorrection], list[_Piece]]:
        """Send each piece and place the corrections of every usable reply.

        A blank piece is not sent. A failed request stops reading: its piece
        and the text after it are unread. A reply that is received but not
        used (cut off, not JSON, old shape, no corrections list) leaves its
        piece uncleaned and reading continues.

        Args:
            text: Full transcript text.
            pieces: Pieces from :func:`_plan_pieces`.
            context: Formatted request context.
            result: Result whose errors, rejections and ``unread_chars`` are
                updated.

        Returns:
            Tuple of (placed corrections, pieces whose reply was used).
        """
        placed: list[CleanupCorrection] = []
        used: list[_Piece] = []
        for piece in pieces:
            piece_text = text[piece.start : piece.end]
            if piece_text.strip():
                reply = self._read_piece(piece_text, context)
                if reply.error is not None:
                    result.errors.append(reply.error)
                if reply.stop:
                    break
                if reply.items is not None:
                    placed.extend(
                        _place_corrections(reply.items, text, piece, result.rejections)
                    )
                    used.append(piece)
            result.unread_chars = len(text) - piece.end
        return placed, used

    def _read_piece(self, piece_text: str, context: str) -> _Reply:
        """Request corrections for one piece and decode the reply.

        Args:
            piece_text: Text of the piece.
            context: Formatted request context.

        Returns:
            The reply's corrections, or the error and whether to stop.
        """
        response_text, truncated, request_error = self._request_text(
            f"CONTEXT:\n{context}\n\n"
            "TRANSCRIPT TO CLEAN:\n"
            f"<transcript>\n{piece_text}\n</transcript>\n\n"
            "Please list corrections for any transcription errors in the "
            "transcript. Return as JSON.",
        )
        if request_error is not None:
            return _Reply(items=None, error=request_error, stop=True)
        if truncated:
            error = describe_failure("cleanup", reason=FailureReason.TRUNCATED_RESPONSE)
            return _Reply(items=None, error=error, stop=False)
        if not response_text:
            return _Reply(items=None, error=None, stop=False)
        items, parse_error = _parse_corrections(response_text)
        return _Reply(items=items, error=parse_error, stop=False)

    def _request_text(self, user_message: str) -> tuple[str, bool, str | None]:
        """Send one cleanup request and decode the first text block.

        Args:
            user_message: Fully formatted user turn for the request.

        Returns:
            Tuple of (response text, whether the reply stopped at the token
            cap, error message); the error is None on success.
        """
        # Keep this task-specific Anthropic request contract explicit.
        # pylint: disable=duplicate-code
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=self.system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            truncated = getattr(response, "stop_reason", None) == _CUT_OFF_STOP_REASON
            return first_response_text(response), truncated, None
        # pylint: enable=duplicate-code
        except anthropic.APIStatusError as exc:
            error = describe_failure("cleanup", exc)
            log_failure(logger, error, exc)
            return "", False, error
        except anthropic.APIConnectionError as exc:
            error = describe_failure("cleanup", exc)
            log_failure(logger, error, exc)
            return "", False, error
        except MALFORMED_RESPONSE_ERRORS as exc:
            error = describe_failure(
                "cleanup", exc, reason=FailureReason.MALFORMED_RESPONSE
            )
            log_failure(logger, error, exc)
            return "", False, error
