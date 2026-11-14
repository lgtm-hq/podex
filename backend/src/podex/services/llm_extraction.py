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
    """Outcome of one extraction run: items plus any non-fatal errors."""

    items: list[ExtractedMedia] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Return True if extraction completed without errors."""
        return len(self.errors) == 0


def _failed_extraction(
    error: BaseException, *, malformed: bool = False
) -> ExtractionResult:
    """Record an SDK failure as the existing extraction result contract.

    Args:
        error: SDK or decoding failure.
        malformed: Whether decoding the response failed.

    Returns:
        An empty extraction with the diagnostic recorded once.
    """
    message = describe_failure(
        "extraction",
        error,
        reason="malformed_response" if malformed else "request_failed",
    )
    log_failure(logger, message, error, level=logging.ERROR)
    return ExtractionResult(errors=[message])


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
                messages=[
                    {
                        "role": "user",
                        "content": ("<transcript>\n" + full_text + "\n</transcript>"),
                    },
                ],
            )
            response_text = first_response_text(response)
        except anthropic.APIStatusError as exc:
            return _failed_extraction(exc)
        except anthropic.APIConnectionError as exc:
            return _failed_extraction(exc)
        except MALFORMED_RESPONSE_ERRORS as exc:
            return _failed_extraction(exc, malformed=True)

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

            validated = _validate_item(item=item, title=title)
            if validated is None:
                continue
            result.items.append(validated)

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
            errors.append(describe_failure("extraction", reason="expected_json_array"))
            return [], errors

        start = response_text.find("[")
        if start < 0:
            error_msg = describe_failure("extraction", reason="missing_json_array")
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
            error_msg = describe_failure("extraction", reason="incomplete_json_array")
            logger.warning(error_msg)
            errors.append(error_msg)
            return [], errors

        try:
            return json.loads(response_text[start:end]), errors
        except json.JSONDecodeError as exc:
            error_msg = describe_failure("extraction", exc, reason="invalid_json")
            log_failure(logger, error_msg, exc)
            errors.append(error_msg)
            return [], errors
