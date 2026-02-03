"""LLM-based media mention extraction service.

Uses Claude to intelligently identify media references in transcript text.
Optimized to process entire transcripts in a single API call.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

from podex.models.media import MediaType

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Base exception for extraction errors."""

    pass


class ExtractionParseError(ExtractionError):
    """Raised when LLM response cannot be parsed."""

    def __init__(self, message: str, response_text: str | None = None):
        super().__init__(message)
        self.response_text = response_text


class ExtractionAPIError(ExtractionError):
    """Raised when API call fails after retries."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ExtractedMedia:
    """A media item extracted from transcript."""

    title: str
    media_type: MediaType
    creator: str | None = None  # Author, director, host, etc.
    year: int | None = None
    confidence: float = 0.8


@dataclass
class ExtractionResult:
    """Result of extraction including any errors encountered."""

    items: list[ExtractedMedia] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Return True if extraction completed without errors."""
        return len(self.errors) == 0


EXTRACTION_SYSTEM_PROMPT = """<role>
You are a media and entity extraction system for podcast transcripts.
Your job is to identify all mentions of media, people, and places.
</role>

<task>
Extract ALL references to media, people, and places from the transcript.
Return a JSON array of items. Include both in-depth discussions AND casual mentions.
We track mention frequency for statistics, so completeness matters.
</task>

<media_types>
- book: Books, audiobooks, written works
- movie: Films, feature movies
- tv_show: TV series, streaming shows
- documentary: Documentary films or series
- podcast: Other podcasts mentioned
- standup_special: Comedy specials, stand-up shows
- study: Scientific studies, research papers, academic works
- article: Named articles (newspapers, magazines, online)
- person: Notable people (guests, celebrities, historical figures, experts)
- place: Specific locations discussed (cities, countries, venues, landmarks)
</media_types>

<output_schema>
For each item, provide:
- title: The name (for people, use their full name if known)
- type: One of the media_types above
- creator: Author/director/creator if applicable (null for person/place)
- year: Year if known (null otherwise)
- confidence: 0.0-1.0 score based on the rubric below
</output_schema>

<confidence_rubric>
- 0.9-1.0: Explicitly named with full title/name, clearly identified
- 0.7-0.8: Clearly referenced but title may be paraphrased or incomplete
- 0.5-0.6: Brief mention or casual name-drop, but identifiable
- Below 0.5: Too ambiguous - do not include
</confidence_rubric>

<rules>
1. Extract ALL named references, including brief mentions (we track frequency)
2. For unnamed references ("that book about X"), skip them
3. Deduplicate - include each unique item only once per transcript
4. For people: include anyone named, from casual mentions to main subjects
5. For places: include specific named locations, not generic references
6. When uncertain about type, use your best judgment based on context
</rules>

<examples>
Input: "Have you read 1984? George Orwell predicted the surveillance state."
Output: [
  {"title": "1984", "type": "book", "creator": "George Orwell",
   "year": 1949, "confidence": 0.95}
]

Input: "I was talking to Elon about this on his show."
Output: [
  {"title": "Elon Musk", "type": "person", "creator": null,
   "year": null, "confidence": 0.9}
]

Input: "When I was in Austin, Texas last month..."
Output: [
  {"title": "Austin, Texas", "type": "place", "creator": null,
   "year": null, "confidence": 0.85}
]

Input: "That Huberman Lab episode on sleep was fascinating."
Output: [
  {"title": "Huberman Lab", "type": "podcast",
   "creator": "Andrew Huberman", "year": null, "confidence": 0.95},
  {"title": "Andrew Huberman", "type": "person",
   "creator": null, "year": null, "confidence": 0.9}
]

Input: "I read some book about habits, can't remember the name"
Output: []
</examples>

<response_format>
Return ONLY a valid JSON array. No explanation or other text.
If no items found, return: []
</response_format>"""


class LLMExtractor:
    """Extract media mentions using Claude API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
    ):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY required for LLM extraction")
        self.model = model
        self.client = httpx.Client(
            base_url="https://api.anthropic.com/v1",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=120.0,  # Longer timeout for full transcripts
        )

    def extract_from_transcript(
        self,
        segments: list[dict],
        max_chars: int = 150000,
    ) -> ExtractionResult:
        """Extract media mentions from a full transcript.

        Args:
            segments: Transcript segments with 'text' keys
            max_chars: Maximum characters to send (to stay within token limits)

        Returns:
            ExtractionResult with items and any errors encountered
        """
        result = ExtractionResult()

        # Combine all segments into full text
        full_text = " ".join(
            seg.get("text", "").strip() for seg in segments if seg.get("text")
        )

        if not full_text or len(full_text) < 100:
            return result

        # Truncate if too long (leave room for prompt)
        if len(full_text) > max_chars:
            logger.warning(
                f"Transcript truncated from {len(full_text)} to {max_chars} chars"
            )
            full_text = full_text[:max_chars]

        logger.info(f"Processing transcript: {len(full_text):,} characters")

        # Retry logic for rate limits
        max_retries = 3
        response = None
        last_error = None

        for attempt in range(max_retries):
            try:
                response = self.client.post(
                    "/messages",
                    json={
                        "model": self.model,
                        "max_tokens": 4000,
                        "system": EXTRACTION_SYSTEM_PROMPT,
                        "messages": [
                            {"role": "user", "content": f"TRANSCRIPT:\n\n{full_text}"}
                        ],
                    },
                )

                if response.status_code == 429:
                    wait_time = 60 * (attempt + 1)  # 60s, 120s, 180s
                    logger.warning(
                        f"Rate limited. Waiting {wait_time}s before retry..."
                    )
                    time.sleep(wait_time)
                    continue

                response.raise_for_status()
                break
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    wait_time = 60 * (attempt + 1)
                    logger.warning(
                        f"Rate limited. Waiting {wait_time}s before retry..."
                    )
                    time.sleep(wait_time)
                    continue
                error_msg = f"API request failed: {e.response.status_code}"
                logger.error(error_msg)
                result.errors.append(error_msg)
                return result
            except httpx.RequestError as e:
                error_msg = f"Network error: {e}"
                logger.error(error_msg)
                result.errors.append(error_msg)
                return result

        if response is None:
            error_msg = f"All {max_retries} retries failed. Last error: {last_error}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            return result

        data = response.json()

        # Extract the text content from Claude's response
        content = data.get("content", [])
        if not content:
            return result

        response_text = content[0].get("text", "").strip()
        logger.info(f"LLM response preview: {response_text[:300]}...")

        # Parse the JSON response
        items, parse_errors = self._parse_json_response(response_text)
        result.errors.extend(parse_errors)

        # Convert to ExtractedMedia objects
        seen_titles: set[str] = set()

        for item in items:
            if not isinstance(item, dict) or "title" not in item:
                continue

            title = item["title"].strip()
            title_lower = title.lower()

            # Deduplicate
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
                )
            )

        logger.info(f"Extracted {len(result.items)} media items")
        return result

    def _parse_json_response(self, response_text: str) -> tuple[list, list[str]]:
        """Parse JSON from LLM response, handling various formats.

        Returns:
            Tuple of (parsed items, list of error messages)
        """
        errors: list[str] = []

        # Try direct parse first
        try:
            parsed = json.loads(response_text)
            if isinstance(parsed, list):
                return parsed, errors
            else:
                errors.append(f"Expected JSON array, got {type(parsed).__name__}")
                return [], errors
        except json.JSONDecodeError:
            pass

        # Find the first complete JSON array
        start = response_text.find("[")
        if start < 0:
            error_msg = f"No JSON array in response: {response_text[:200]}"
            logger.warning(error_msg)
            errors.append(error_msg)
            return [], errors

        # Find matching closing bracket
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
        except json.JSONDecodeError as e:
            error_msg = f"Failed to parse JSON: {e}"
            logger.warning(error_msg)
            errors.append(error_msg)
            return [], errors

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> LLMExtractor:
        return self

    def __exit__(self, *args) -> None:
        self.close()
