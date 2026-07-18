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
    """Outcome of one cleanup run: cleaned text plus corrections made."""

    cleaned_text: str
    corrections_made: list[CleanupCorrection] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Return True if cleanup completed without errors."""
        return len(self.errors) == 0

    @property
    def correction_count(self) -> int:
        """Return the number of corrections applied."""
        return len(self.corrections_made)


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
            max_chars: Maximum characters sent to the model.

        Returns:
            CleanupResult with cleaned text and corrections made.
        """
        result = CleanupResult(cleaned_text=transcript_text)

        if len(transcript_text) < _MIN_TRANSCRIPT_CHARS:
            return result

        if len(transcript_text) > max_chars:
            logger.warning(
                "Transcript truncated from %d to %d chars",
                len(transcript_text),
                max_chars,
            )
            transcript_text = transcript_text[:max_chars]

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
        context = (
            "\n".join(context_parts) if context_parts else "No additional context."
        )

        user_message = (
            f"CONTEXT:\n{context}\n\n"
            "TRANSCRIPT TO CLEAN:\n"
            f"<transcript>\n{transcript_text}\n</transcript>\n\n"
            "Please analyze the transcript and fix any transcription errors. "
            "Return as JSON."
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=self.system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.APIStatusError as exc:
            result.errors.append(f"API error: {exc.status_code}")
            return result
        except anthropic.APIConnectionError as exc:
            result.errors.append(f"Network error: {exc}")
            return result

        response_text = next(
            (block.text for block in response.content if block.type == "text"),
            "",
        ).strip()
        if not response_text:
            return result

        parsed = self._parse_json_response(response_text)
        if parsed is None:
            result.errors.append("Failed to parse JSON from response")
            return result

        result.cleaned_text = str(parsed.get("cleaned_text", transcript_text))
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
