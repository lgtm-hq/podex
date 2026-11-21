"""LLM transcript cleanup for speech-to-text errors.

Prompts follow the #29 load-don't-embed boundary: the system prompt is
injected by the caller (tuned prompts are podex-ops data, resolved through
:mod:`podex.services.prompt_config`) and falls back to the generic prompt in
:mod:`podex.services.prompts_default`.

The model never returns transcript text. It proposes corrections (exact
original text, replacement, reason) and this module applies the ones that pass
the rules below to the original text. Every character no applied correction
covers is returned as it was. See ADR 0021.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
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
# A limit below this reads one piece only, as before pieces existed: a piece
# shorter than the shortest transcript worth sending is not worth a request.
MIN_PIECED_LIMIT = 100
# Consecutive pieces share up to this many characters (at most a tenth of
# ``max_chars``) so that an error across a cut is seen whole by one request.
MAX_OVERLAP_CHARS = 200
# Corrections considered from one reply.
MAX_CORRECTIONS_PER_REPLY = 200
# An original holds the misheard words plus the neighbours that make it
# unique: about ten words. Longer originals rewrite sentences, not words.
MAX_ORIGINAL_CHARS = 80
# A replacement may be this much longer than its original (a name spelt out
# in full) and must keep at least this share of the original's length.
MAX_REPLACEMENT_GROWTH_CHARS = 40
MIN_REPLACEMENT_PERCENT = 50
# Accepted corrections may cover at most this share of the text a piece owns.
# Speech-to-text word error rates on conversational audio are a few percent;
# the names, terms and word boundaries cleanup fixes are a part of those, and
# each fix quotes a few neighbouring words, roughly tripling what it covers.
MAX_CHANGED_PERCENT = 15
MAX_REASON_CHARS = 200
_CUT_OFF_STOP_REASON = "max_tokens"
_COMPLETE_STOP_REASONS = frozenset({"end_turn", "stop_sequence"})
# Unicode categories never accepted in a replacement unless the original
# already holds that character: controls, format characters (bidirectional
# controls, zero-width characters, byte order mark, soft hyphen), surrogates.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
_OLD_SHAPE_KEY = re.compile(r'(?<!\\)"cleaned_text"\s*:')
# The decoder used for replies; whether deep nesting raises depends on the
# interpreter and platform, so tests replace it to reach that path.
_json_loads = json.loads


class CorrectionRejection(StrEnum):
    """Why a proposed correction was not applied."""

    MALFORMED = auto()
    OVER_LIMIT = auto()
    ORIGINAL_TOO_LONG = auto()
    EMPTY_REPLACEMENT = auto()
    UNCHANGED = auto()
    UNSAFE_REPLACEMENT = auto()
    OVERSIZED_REPLACEMENT = auto()
    UNDERSIZED_REPLACEMENT = auto()
    NOT_FOUND = auto()
    AMBIGUOUS = auto()
    AT_CUT = auto()
    NOT_OWNED = auto()
    OVER_BUDGET = auto()
    OVERLAP = auto()
    SUPERSEDED = auto()


@dataclass(frozen=True)
class CleanupCorrection:
    """A correction applied to the transcript.

    Attributes:
        original: Exact input text that was replaced.
        corrected: Replacement text proposed by the model.
        reason: The model's short reason without control, format or
            surrogate characters, cut to ``MAX_REASON_CHARS``.
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
            was reached, a request failed, the reply had the old shape or the
            input was shorter than the minimum. These are returned exactly as
            they were.
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
        """Return the number of corrections in replies whose list was read."""
        return self.correction_count + self.corrections_rejected


class _Piece(NamedTuple):
    """Half-open range ``[start, end)`` of the input."""

    start: int
    end: int


class _Reply(NamedTuple):
    """Usable corrections from one reply, or why it was not used.

    Attributes:
        items: The reply's ``corrections`` list, or None when not usable.
        error: Failure descriptor, if any.
        stop: Whether to stop reading further pieces.
        received: Whether a reply arrived for the piece.
    """

    items: list[object] | None
    error: str | None
    stop: bool
    received: bool


def _reject(
    rejections: dict[CorrectionRejection, int],
    reason: CorrectionRejection,
    count: int = 1,
) -> None:
    """Add ``count`` rejections for ``reason``."""
    if count:
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
    ``min(max_chars // 10, MAX_OVERLAP_CHARS)`` characters. A limit below
    ``MIN_PIECED_LIMIT`` reads the first piece only.

    Args:
        text: Full transcript text.
        max_chars: Maximum characters of one piece.

    Returns:
        Pieces in input order; text after the last one is not read.
    """
    most = MAX_PIECES if max_chars >= MIN_PIECED_LIMIT else 1
    overlap = min(max_chars // 10, MAX_OVERLAP_CHARS)
    pieces: list[_Piece] = []
    start = 0
    while start < len(text) and len(pieces) < most:
        head, _rest = _split_at_limit(text[start:], max_chars)
        end = start + len(head)
        pieces.append(_Piece(start, end))
        if end >= len(text):
            break
        start = _next_start(text, end, overlap)
    return pieces


def _cores(pieces: list[_Piece]) -> list[_Piece]:
    """Split the read text into the part each piece owns.

    Neighbouring pieces divide the text they share at its midpoint; the
    first piece owns from the start and the last to its end.

    Args:
        pieces: Pieces in input order.

    Returns:
        One non-overlapping range per piece, together covering all pieces.
    """
    cores: list[_Piece] = []
    low = pieces[0].start if pieces else 0
    for index, piece in enumerate(pieces):
        high = piece.end
        if index + 1 < len(pieces):
            high = (pieces[index + 1].start + piece.end) // 2
        cores.append(_Piece(low, high))
        low = high
    return cores


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


def _without_unsafe(value: str) -> str:
    """Drop control, format and surrogate characters.

    Args:
        value: Text from the reply.

    Returns:
        The text without characters in ``_UNSAFE_CATEGORIES``.
    """
    return "".join(
        char for char in value if unicodedata.category(char) not in _UNSAFE_CATEGORIES
    )


def _correction_fields(item: object) -> tuple[str, str, str] | None:
    """Return (original, corrected, reason) from a well-formed item.

    An item is well formed when it is an object whose ``original`` is a
    non-empty string, whose ``corrected`` is a string and whose ``reason`` is
    a string, null or absent. Other keys are ignored.

    Args:
        item: One entry of the reply's ``corrections`` list.

    Returns:
        The three fields, the reason made safe and cut to
        ``MAX_REASON_CHARS``, or None when the item is malformed.
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
        return original, corrected, _without_unsafe(reason)[:MAX_REASON_CHARS]
    return None


def _replacement_rejection(
    original: str,
    corrected: str,
) -> CorrectionRejection | None:
    """Apply the size and content rules to one correction.

    A replacement may contain a control, format or surrogate character only
    when the original contains that same character.

    Args:
        original: Text the correction replaces.
        corrected: Its replacement.

    Returns:
        The first rule the correction breaks, in the order listed, or None.
    """
    rules = (
        (len(original) > MAX_ORIGINAL_CHARS, CorrectionRejection.ORIGINAL_TOO_LONG),
        (not corrected.strip(), CorrectionRejection.EMPTY_REPLACEMENT),
        (corrected == original, CorrectionRejection.UNCHANGED),
        (
            any(
                unicodedata.category(char) in _UNSAFE_CATEGORIES
                and char not in original
                for char in corrected
            ),
            CorrectionRejection.UNSAFE_REPLACEMENT,
        ),
        (
            len(corrected) > len(original) + MAX_REPLACEMENT_GROWTH_CHARS,
            CorrectionRejection.OVERSIZED_REPLACEMENT,
        ),
        (
            len(corrected) * 100 < len(original) * MIN_REPLACEMENT_PERCENT,
            CorrectionRejection.UNDERSIZED_REPLACEMENT,
        ),
    )
    return next((rejection for broken, rejection in rules if broken), None)


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


def _drop_overlapping(
    corrections: list[CleanupCorrection],
    rejections: dict[CorrectionRejection, int],
) -> list[CleanupCorrection]:
    """Reject every correction of one reply that overlaps another of it.

    Args:
        corrections: Corrections from one reply.
        rejections: Counts to update.

    Returns:
        The non-overlapping ones in input order.
    """
    ordered = sorted(corrections, key=lambda c: (c.start, c.end))
    overlapping: set[int] = set()
    for index, correction in enumerate(ordered):
        for later in range(index + 1, len(ordered)):
            if ordered[later].start >= correction.end:
                break
            overlapping.update((index, later))
    _reject(rejections, CorrectionRejection.OVERLAP, len(overlapping))
    return [c for i, c in enumerate(ordered) if i not in overlapping]


def _accept_reply(
    placed: list[CleanupCorrection],
    core: _Piece,
    rejections: dict[CorrectionRejection, int],
) -> list[CleanupCorrection] | None:
    """Keep the corrections a piece owns, within its change budget.

    A correction whose start lies outside the piece's core belongs to the
    neighbour and is rejected as ``NOT_OWNED``. When the owned corrections
    together cover more than ``MAX_CHANGED_PERCENT`` of the core, none of them
    is applied (``OVER_BUDGET``). Overlapping owned corrections are both
    rejected (``OVERLAP``).

    Args:
        placed: Corrections from one reply, placed in the input.
        core: Range the piece owns.
        rejections: Counts to update.

    Returns:
        Accepted corrections, or None when the reply exceeded its budget.
    """
    owned = [c for c in placed if core.start <= c.start < core.end]
    _reject(rejections, CorrectionRejection.NOT_OWNED, len(placed) - len(owned))
    covered = sum(len(c.original) for c in owned)
    if covered * 100 > MAX_CHANGED_PERCENT * (core.end - core.start):
        _reject(rejections, CorrectionRejection.OVER_BUDGET, len(owned))
        return None
    return _drop_overlapping(owned, rejections)


def _resolve_across_pieces(
    accepted: list[CleanupCorrection],
    rejections: dict[CorrectionRejection, int],
) -> list[CleanupCorrection]:
    """Settle overlaps between corrections from neighbouring pieces.

    Corrections from one piece no longer overlap, so any overlap left is
    between pieces. The correction that starts later wins: the overlapping
    text begins at its start, which its own piece owns. The other is rejected
    as ``SUPERSEDED``.

    Args:
        accepted: Corrections accepted from every piece.
        rejections: Counts to update.

    Returns:
        Non-overlapping corrections in input order.
    """
    kept: list[CleanupCorrection] = []
    for correction in sorted(accepted, key=lambda c: c.start, reverse=True):
        if kept and correction.end > kept[-1].start:
            _reject(rejections, CorrectionRejection.SUPERSEDED)
        else:
            kept.append(correction)
    kept.reverse()
    return kept


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


def _loads_object(candidate: str) -> dict[str, Any] | None:
    """Decode a JSON object, treating any decoder refusal as no object.

    The decoder raises ``ValueError`` for invalid JSON and for integers longer
    than the interpreter's digit limit, and may raise ``RecursionError`` for
    very deep nesting (whether it does depends on the interpreter and
    platform).

    Args:
        candidate: Text to decode.

    Returns:
        The object, or None.
    """
    try:
        parsed = _json_loads(candidate)
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_json_response(response_text: str) -> dict[str, Any] | None:
    """Parse a JSON object from model output, tolerating surrounding prose.

    Args:
        response_text: Raw text returned by the model.

    Returns:
        The parsed object, or None when no valid JSON object is found.
    """
    parsed = _loads_object(response_text)
    if parsed is not None:
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
    return _loads_object(response_text[start:end])


def _parse_corrections(response_text: str) -> tuple[list[object] | None, str | None]:
    """Return the reply's ``corrections`` list, or why the reply is unusable.

    Args:
        response_text: Text of the reply's first text block.

    Returns:
        Tuple of (corrections, error); exactly one is meaningful.
    """
    parsed = _parse_json_response(response_text)
    if parsed is None:
        return None, describe_failure("cleanup", reason=FailureReason.INVALID_JSON)
    corrections = parsed.get("corrections")
    if not isinstance(corrections, list):
        return None, describe_failure(
            "cleanup", reason=FailureReason.INVALID_CORRECTIONS
        )
    return corrections, None


def _stop_failure(stop_reason: object) -> FailureReason | None:
    """Return why a reply that stopped this way is not used, if it is not.

    Args:
        stop_reason: The reply's ``stop_reason``, of any type.

    Returns:
        None for a reply that ended normally, otherwise the failure reason.
    """
    if not isinstance(stop_reason, str):
        return FailureReason.UNEXPECTED_STOP_REASON
    if stop_reason == _CUT_OFF_STOP_REASON:
        return FailureReason.TRUNCATED_RESPONSE
    if stop_reason not in _COMPLETE_STOP_REASONS:
        return FailureReason.UNEXPECTED_STOP_REASON
    return None


def _decode_reply(response_text: str, stop_reason: object) -> _Reply:
    """Decide what one received reply contributes.

    The old reply shape is recognised first, from a ``"cleaned_text":`` key
    anywhere in the text (even in a reply cut off at the token cap), and
    stops reading: every later piece would get the same reply. Then only a
    reply that ended normally is parsed.

    Args:
        response_text: Text of the reply's first text block.
        stop_reason: The reply's ``stop_reason``.

    Returns:
        The reply's corrections, or the error and whether to stop.
    """
    if _OLD_SHAPE_KEY.search(response_text):
        error = describe_failure("cleanup", reason=FailureReason.LEGACY_RESPONSE_SHAPE)
        return _Reply(items=None, error=error, stop=True, received=True)
    stop_failure = _stop_failure(stop_reason)
    if stop_failure is not None:
        error = describe_failure("cleanup", reason=stop_failure)
        return _Reply(items=None, error=error, stop=False, received=True)
    if not response_text:
        return _Reply(items=None, error=None, stop=False, received=True)
    items, parse_error = _parse_corrections(response_text)
    return _Reply(items=items, error=parse_error, stop=False, received=True)


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
                read in at most ``MAX_PIECES`` pieces (one below
                ``MIN_PIECED_LIMIT``; see :func:`_plan_pieces`); text after
                the last is kept as it is and counted in ``unread_chars``.

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
        accepted, used = self._read_pieces(transcript_text, pieces, context, result)

        result.corrections_made = _resolve_across_pieces(accepted, result.rejections)
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
        """Send each piece and accept the corrections of every usable reply.

        A blank piece is not sent. A failed request stops reading: its piece
        and the text after it are unread. An old-shape reply also stops
        reading, after its piece. Any other reply that is received but not
        used (cut off, other stop reason, not JSON, no corrections list, over
        budget) leaves its piece uncleaned and reading continues.

        Args:
            text: Full transcript text.
            pieces: Pieces from :func:`_plan_pieces`.
            context: Formatted request context.
            result: Result whose errors, rejections and ``unread_chars`` are
                updated.

        Returns:
            Tuple of (accepted corrections, pieces whose reply was used).
        """
        accepted: list[CleanupCorrection] = []
        used: list[_Piece] = []
        for piece, core in zip(pieces, _cores(pieces), strict=True):
            piece_text = text[piece.start : piece.end]
            if not piece_text.strip():
                result.unread_chars = len(text) - piece.end
                continue
            reply = self._read_piece(piece_text, context)
            if reply.error is not None:
                result.errors.append(reply.error)
            if reply.received:
                result.unread_chars = len(text) - piece.end
            if reply.stop:
                break
            if reply.items is None:
                continue
            placed = _place_corrections(reply.items, text, piece, result.rejections)
            kept = _accept_reply(placed, core, result.rejections)
            if kept is None:
                result.errors.append(
                    describe_failure(
                        "cleanup", reason=FailureReason.CORRECTION_BUDGET_EXCEEDED
                    )
                )
                continue
            accepted.extend(kept)
            used.append(piece)
        return accepted, used

    def _read_piece(self, piece_text: str, context: str) -> _Reply:
        """Request corrections for one piece and decode the reply.

        Args:
            piece_text: Text of the piece.
            context: Formatted request context.

        Returns:
            The reply's corrections, or the error and whether to stop.
        """
        response_text, stop_reason, request_error = self._request_text(
            f"CONTEXT:\n{context}\n\n"
            "TRANSCRIPT TO CLEAN:\n"
            f"<transcript>\n{piece_text}\n</transcript>\n\n"
            "Please list corrections for any transcription errors in the "
            "transcript. Return as JSON.",
        )
        if request_error is not None:
            return _Reply(items=None, error=request_error, stop=True, received=False)
        return _decode_reply(response_text, stop_reason)

    def _request_text(self, user_message: str) -> tuple[str, object, str | None]:
        """Send one cleanup request and decode the first text block.

        Args:
            user_message: Fully formatted user turn for the request.

        Returns:
            Tuple of (response text, the reply's stop reason, error message);
            the error is None on success.
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
            stop_reason: object = getattr(response, "stop_reason", None)
            return first_response_text(response), stop_reason, None
        # pylint: enable=duplicate-code
        except anthropic.APIStatusError as exc:
            error = describe_failure("cleanup", exc)
            log_failure(logger, error, exc)
            return "", None, error
        except anthropic.APIConnectionError as exc:
            error = describe_failure("cleanup", exc)
            log_failure(logger, error, exc)
            return "", None, error
        except MALFORMED_RESPONSE_ERRORS as exc:
            error = describe_failure(
                "cleanup", exc, reason=FailureReason.MALFORMED_RESPONSE
            )
            log_failure(logger, error, exc)
            return "", None, error
