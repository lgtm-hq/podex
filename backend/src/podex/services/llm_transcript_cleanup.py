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


@dataclass
class CleanupCorrection:
    """A single correction the model applied to the transcript."""

    original: str
    corrected: str
    reason: str


@dataclass
class CleanupResult:
    """Outcome of one cleanup run: cleaned text plus corrections made.

    ``cleaned_text`` always covers the whole input. When the input is longer
    than the request limit, only its head is sent to the model; the rest is
    appended verbatim after the cleaned head and counted in
    ``uncleaned_chars``. On a failed request ``cleaned_text`` is the original
    input and ``success`` is False.

    Attributes:
        cleaned_text: Cleaned head followed by any uncleaned tail.
        corrections_made: Corrections the model reported for the head.
        errors: Allowlisted failure descriptors; empty on success.
        uncleaned_chars: Trailing input characters never sent to the model.
    """

    cleaned_text: str
    corrections_made: list[CleanupCorrection] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    uncleaned_chars: int = 0

    @property
    def success(self) -> bool:
        """Return True if cleanup completed without errors."""
        return len(self.errors) == 0

    @property
    def partial(self) -> bool:
        """Return True if the end of the input was not sent for cleanup."""
        return self.uncleaned_chars > 0

    @property
    def fully_cleaned(self) -> bool:
        """Return True if cleanup succeeded and covered the whole input."""
        return self.success and not self.partial

    @property
    def correction_count(self) -> int:
        """Return the number of corrections applied."""
        return len(self.corrections_made)


def _split_at_limit(text: str, max_chars: int) -> tuple[str, str]:
    """Split text into the head sent for cleanup and the verbatim tail.

    Text within the limit is sent whole. Longer text is cut at the last
    whitespace character at or before index ``max_chars``, so the tail starts
    with that whitespace and no word is split between head and tail. Without
    such whitespace the cut falls exactly at ``max_chars``.

    Args:
        text: Full transcript text.
        max_chars: Maximum characters sent to the model.

    Returns:
        Tuple of (head, tail); the tail is empty when nothing is cut.
    """
    if len(text) <= max_chars:
        return text, ""
    cut = max_chars
    for index in range(max_chars, 0, -1):
        if text[index].isspace():
            cut = index
            break
    return text[:cut], text[cut:]


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
                cleaned up to this limit and its remainder is returned
                uncleaned; see :class:`CleanupResult`.

        Returns:
            CleanupResult with cleaned text and corrections made.
        """
        result = CleanupResult(cleaned_text=transcript_text)

        if len(transcript_text) < _MIN_TRANSCRIPT_CHARS:
            return result

        head, tail = _split_at_limit(transcript_text, max_chars)
        if tail:
            logger.warning(
                "Transcript truncated from %d to %d chars",
                len(transcript_text),
                len(head),
            )
            result.uncleaned_chars = len(tail)

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

        result.cleaned_text = str(parsed.get("cleaned_text", head)) + tail
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
