"""LLM-based transcript cleanup service.

Uses Claude to fix proper nouns, technical terms, and obvious errors.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


CLEANUP_SYSTEM_PROMPT = """You are a transcript editor. Your task is to fix errors in \
AI-generated speech-to-text transcripts.

Fix the following types of errors:
1. Proper nouns (names of people, places, products, companies)
2. Technical terminology specific to the topic being discussed
3. Obvious homophones (e.g., "their/there/they're" based on context)
4. Clear word boundary errors (e.g., "a lot" vs "alot", compound words)

DO NOT:
- Add or remove content beyond fixing transcription errors
- Change the speaker's meaning or intent
- "Improve" grammar beyond obvious transcription errors
- Change colloquial speech patterns or informal language
- Add punctuation beyond what's needed for clarity

Context will be provided about:
- Podcast name and host(s)
- Guest information (if available)
- Known terminology that may appear

Return a JSON object with:
{
  "cleaned_text": "The corrected transcript text",
  "corrections": [
    {"original": "original text", "corrected": "fixed text", "reason": "brief reason"}
  ]
}

Only include corrections that were actually made. If no corrections are needed, \
return the original text with an empty corrections array."""


@dataclass
class CleanupCorrection:
    """A single correction made during cleanup."""

    original: str
    corrected: str
    reason: str


@dataclass
class CleanupResult:
    """Result of transcript cleanup."""

    cleaned_text: str
    corrections_made: list[CleanupCorrection] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    @property
    def correction_count(self) -> int:
        return len(self.corrections_made)


class TranscriptCleaner:
    """Clean up transcripts using Claude API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
    ):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY required for transcript cleanup")
        self.model = model
        self.client = httpx.Client(
            base_url="https://api.anthropic.com/v1",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=180.0,
        )

    def cleanup_transcript(
        self,
        transcript_text: str,
        podcast_name: str | None = None,
        host_name: str | None = None,
        guest_name: str | None = None,
        terminology: list[str] | None = None,
        max_chars: int = 100000,
    ) -> CleanupResult:
        """Clean up a transcript using Claude.

        Args:
            transcript_text: The raw transcript text to clean
            podcast_name: Name of the podcast for context
            host_name: Name of the host
            guest_name: Name of the guest (if any)
            terminology: List of known terms that may be misspelled
            max_chars: Maximum characters to process (for token limits)

        Returns:
            CleanupResult with cleaned text and corrections made
        """
        result = CleanupResult(cleaned_text=transcript_text)

        if len(transcript_text) < 100:
            return result

        # Truncate if too long
        if len(transcript_text) > max_chars:
            logger.warning(
                f"Transcript truncated from {len(transcript_text)} to {max_chars} chars"
            )
            transcript_text = transcript_text[:max_chars]

        # Build context
        context_parts = []
        if podcast_name:
            context_parts.append(f"Podcast: {podcast_name}")
        if host_name:
            context_parts.append(f"Host: {host_name}")
        if guest_name:
            context_parts.append(f"Guest: {guest_name}")
        if terminology:
            # Limit terminology to avoid token bloat
            terms = terminology[:50]
            context_parts.append(f"Known terms and names: {', '.join(terms)}")

        context = (
            "\n".join(context_parts) if context_parts else "No additional context."
        )

        user_message = f"""CONTEXT:
{context}

TRANSCRIPT TO CLEAN:
{transcript_text}

Please analyze the transcript and fix any transcription errors. Return as JSON."""

        # Retry logic
        max_retries = 3
        response = None

        for attempt in range(max_retries):
            try:
                response = self.client.post(
                    "/messages",
                    json={
                        "model": self.model,
                        "max_tokens": 8000,
                        "system": CLEANUP_SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": user_message}],
                    },
                )

                if response.status_code == 429:
                    wait_time = 60 * (attempt + 1)
                    logger.warning(f"Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue

                response.raise_for_status()
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    time.sleep(60 * (attempt + 1))
                    continue
                result.errors.append(f"API error: {e.response.status_code}")
                return result
            except httpx.RequestError as e:
                result.errors.append(f"Network error: {e}")
                return result

        if response is None:
            result.errors.append("All retries failed")
            return result

        # Parse response
        data = response.json()
        content = data.get("content", [])
        if not content:
            return result

        response_text = content[0].get("text", "").strip()

        # Extract JSON from response
        parsed = self._parse_json_response(response_text)
        if parsed is None:
            result.errors.append("Failed to parse JSON from response")
            return result

        # Extract cleaned text
        result.cleaned_text = parsed.get("cleaned_text", transcript_text)

        # Extract corrections
        corrections = parsed.get("corrections", [])
        for corr in corrections:
            if isinstance(corr, dict):
                result.corrections_made.append(
                    CleanupCorrection(
                        original=corr.get("original", ""),
                        corrected=corr.get("corrected", ""),
                        reason=corr.get("reason", ""),
                    )
                )

        logger.info(f"Made {len(result.corrections_made)} corrections")
        return result

    def _parse_json_response(self, response_text: str) -> dict | None:
        """Parse JSON from Claude's response, handling various formats."""
        # Try direct parse first
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON object in response
        start = response_text.find("{")
        if start < 0:
            return None

        # Find matching closing brace
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
            return json.loads(response_text[start:end])
        except json.JSONDecodeError:
            return None

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> TranscriptCleaner:
        return self

    def __exit__(self, *args) -> None:
        self.close()
