"""LLM media-mention extraction over cleaned transcripts.

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

from podex.models.media import MediaType
from podex.services.prompts_default import DEFAULT_EXTRACTION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

DEFAULT_EXTRACTION_MODEL = "claude-opus-4-8"
_MAX_OUTPUT_TOKENS = 4000
_MIN_TRANSCRIPT_CHARS = 100


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
    """Outcome of one extraction run: items plus any non-fatal errors."""

    items: list[ExtractedMedia] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Return True if extraction completed without errors."""
        return len(self.errors) == 0


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
        max_chars: int = 150000,
    ) -> ExtractionResult:
        """Extract media mentions from a full transcript.

        Args:
            segments: Transcript segments with ``text`` keys.
            max_chars: Maximum characters sent to the model.

        Returns:
            ExtractionResult with items and any errors encountered.
        """
        result = ExtractionResult()

        full_text = " ".join(
            seg.get("text", "").strip() for seg in segments if seg.get("text")
        )
        if not full_text or len(full_text) < _MIN_TRANSCRIPT_CHARS:
            return result

        if len(full_text) > max_chars:
            logger.warning(
                "Transcript truncated from %d to %d chars", len(full_text), max_chars
            )
            full_text = full_text[:max_chars]

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=self.system_prompt,
                messages=[{"role": "user", "content": f"TRANSCRIPT:\n\n{full_text}"}],
            )
        except anthropic.APIStatusError as exc:
            error_msg = f"API request failed: {exc.status_code}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            return result
        except anthropic.APIConnectionError as exc:
            error_msg = f"Network error: {exc}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            return result

        response_text = next(
            (block.text for block in response.content if block.type == "text"),
            "",
        ).strip()
        if not response_text:
            return result

        items, parse_errors = self._parse_json_response(response_text)
        result.errors.extend(parse_errors)

        seen_titles: set[str] = set()
        for item in items:
            if not isinstance(item, dict) or "title" not in item:
                continue

            title = str(item["title"]).strip()
            title_lower = title.lower()
            if title_lower in seen_titles:
                continue
            seen_titles.add(title_lower)

            media_type_str = item.get("type", "article")
            try:
                media_type = MediaType(media_type_str)
            except ValueError:
                media_type = MediaType.ARTICLE

            result.items.append(
                ExtractedMedia(
                    title=title,
                    media_type=media_type,
                    creator=item.get("creator"),
                    year=item.get("year"),
                    confidence=float(item.get("confidence", 0.8)),
                ),
            )

        logger.info("Extracted %d media items", len(result.items))
        return result

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
            errors.append(f"Expected JSON array, got {type(parsed).__name__}")
            return [], errors

        start = response_text.find("[")
        if start < 0:
            error_msg = f"No JSON array in response: {response_text[:200]}"
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
            error_msg = f"No complete JSON array found: {response_text[:200]}"
            logger.warning(error_msg)
            errors.append(error_msg)
            return [], errors

        try:
            return json.loads(response_text[start:end]), errors
        except json.JSONDecodeError as exc:
            error_msg = f"Failed to parse JSON: {exc}"
            logger.warning(error_msg)
            errors.append(error_msg)
            return [], errors
