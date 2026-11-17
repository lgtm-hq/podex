"""LLM transcript cleanup for speech-to-text errors.

Prompts follow the #29 load-don't-embed boundary: the system prompt is
injected by the caller (tuned prompts are podex-ops data, resolved through
:mod:`podex.services.prompt_config`) and falls back to the generic prompt in
:mod:`podex.services.prompts_default`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

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


@dataclass
class CleanupCorrection:
    """A single correction the model applied to the transcript."""

    original: str
    corrected: str
    reason: str


@dataclass
class CleanupResult:
    """Outcome of one cleanup run: cleaned text plus corrections made.

    Only the head of the input (at most the request limit) is sent to the
    model. ``cleaned_text`` is the model's text for that head followed by the
    original tail, or the whole original input when the model's text is not
    used: the request failed, the reply omitted ``cleaned_text``, gave a
    non-string, or gave blank text for a non-blank head. The tail is never
    dropped. The model's text for the head is used as returned, so it may
    differ from the head in length or wording. ``success`` only says that no
    error occurred.

    Attributes:
        cleaned_text: Model-cleaned head plus original tail, or the input.
        corrections_made: Corrections the model reported.
        errors: Allowlisted failure descriptors; empty on success.
        uncleaned_chars: Characters of ``cleaned_text`` copied from the input
            without cleaning: the tail when ``head_cleaned`` is true,
            otherwise the whole input.
        head_cleaned: Whether the model's text replaced the head.
    """

    cleaned_text: str
    corrections_made: list[CleanupCorrection] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    uncleaned_chars: int = 0
    head_cleaned: bool = False

    @property
    def success(self) -> bool:
        """Return True if cleanup completed without errors."""
        return len(self.errors) == 0

    @property
    def partial(self) -> bool:
        """Return True if the head was cleaned but an uncleaned tail follows."""
        return self.head_cleaned and self.uncleaned_chars > 0

    @property
    def fully_cleaned(self) -> bool:
        """Return True if the model's text covers the whole input."""
        return self.success and not self.partial and self.head_cleaned

    @property
    def correction_count(self) -> int:
        """Return the number of corrections applied."""
        return len(self.corrections_made)


def _split_at_limit(text: str, max_chars: int) -> tuple[str, str]:
    """Split text into the head sent for cleanup and the verbatim tail.

    Text within the limit is sent whole. Longer text is cut at the last
    character ``str.isspace`` accepts among indices ``max_chars - lookback``
    to ``max_chars``, where ``lookback`` is ``min(max_chars // 10, 1000)``;
    the tail then starts with that character. Without such a character the
    cut falls exactly at ``max_chars``, which may split a word: a model that
    completes the half word then yields a doubled fragment once the tail is
    appended. A combining mark, or the ``\n`` of a CRLF line break, can also
    end up on the other side of the cut from the character it belongs to.

    Args:
        text: Full transcript text.
        max_chars: Maximum characters sent to the model.

    Returns:
        Tuple of (head, tail); ``head + tail == text``.
    """
    if len(text) <= max_chars:
        return text, ""
    lookback = min(max_chars // 10, _MAX_LOOKBACK_CHARS)
    for index in range(max_chars, max_chars - lookback - 1, -1):
        if text[index].isspace():
            return text[:index], text[index:]
    return text[:max_chars], text[max_chars:]


def _apply_cleaned_head(
    result: CleanupResult,
    reply_head: object,
    head: str,
    tail: str,
) -> None:
    """Use the model's head when it is usable text; otherwise keep the input.

    Args:
        result: Result to update; it already holds the original input.
        reply_head: ``cleaned_text`` value from the reply, if any.
        head: Original head sent to the model.
        tail: Original tail that was not sent.
    """
    if not isinstance(reply_head, str):
        return
    if head.strip() and not reply_head.strip():
        return
    result.cleaned_text = reply_head + tail
    result.uncleaned_chars = len(tail)
    result.head_cleaned = True


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
            max_chars: Maximum characters sent to the model. Longer input is
                cleaned up to the cut described in :func:`_split_at_limit`
                and its remainder is returned uncleaned; see
                :class:`CleanupResult`.

        Returns:
            CleanupResult with cleaned text and corrections made.
        """
        result = CleanupResult(
            cleaned_text=transcript_text,
            uncleaned_chars=len(transcript_text),
        )

        if len(transcript_text) < _MIN_TRANSCRIPT_CHARS:
            return result

        head, tail = _split_at_limit(transcript_text, max_chars)
        if tail:
            logger.warning(
                "Transcript truncated from %d to %d chars",
                len(transcript_text),
                len(head),
            )
        if not head.strip() and transcript_text.strip():
            logger.warning("Transcript head is blank; cleanup request skipped")
            return result

        context = _cleanup_context(
            podcast_name=podcast_name,
            host_name=host_name,
            guest_name=guest_name,
            terminology=terminology,
        )

        response_text, request_error = self._request_text(
            f"CONTEXT:\n{context}\n\n"
            "TRANSCRIPT TO CLEAN:\n"
            f"<transcript>\n{head}\n</transcript>\n\n"
            "Please analyze the transcript and fix any transcription errors. "
            "Return as JSON.",
        )
        if request_error is not None:
            result.errors.append(request_error)
            return result

        if not response_text:
            return result

        parsed = self._parse_json_response(response_text)
        if parsed is None:
            result.errors.append(
                describe_failure("cleanup", reason=FailureReason.INVALID_JSON)
            )
            return result

        _apply_cleaned_head(result, parsed.get("cleaned_text"), head, tail)
        for corr in parsed.get("corrections", []):
            if isinstance(corr, dict):
                result.corrections_made.append(
                    CleanupCorrection(
                        original=corr.get("original", ""),
                        corrected=corr.get("corrected", ""),
                        reason=corr.get("reason", ""),
                    ),
                )

        logger.info("Made %d corrections", len(result.corrections_made))
        return result

    def _request_text(self, user_message: str) -> tuple[str, str | None]:
        """Send one cleanup request and decode the first text block.

        Args:
            user_message: Fully formatted user turn for the request.

        Returns:
            Tuple of (response text, error message); exactly one is meaningful.
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
            return first_response_text(response), None
        # pylint: enable=duplicate-code
        except anthropic.APIStatusError as exc:
            error = describe_failure("cleanup", exc)
            log_failure(logger, error, exc)
            return "", error
        except anthropic.APIConnectionError as exc:
            error = describe_failure("cleanup", exc)
            log_failure(logger, error, exc)
            return "", error
        except MALFORMED_RESPONSE_ERRORS as exc:
            error = describe_failure(
                "cleanup", exc, reason=FailureReason.MALFORMED_RESPONSE
            )
            log_failure(logger, error, exc)
            return "", error

    def _parse_json_response(self, response_text: str) -> dict[str, Any] | None:
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
