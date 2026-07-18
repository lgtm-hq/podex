"""Tests for the LLM extraction/cleanup services and the prompt seam."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
from assertpy import assert_that

from podex.models.media import MediaType
from podex.services.llm_extraction import LLMExtractor
from podex.services.llm_transcript_cleanup import TranscriptCleaner
from podex.services.prompt_config import PromptConfig, PromptConfigManager
from podex.services.prompts_default import (
    DEFAULT_CLEANUP_SYSTEM_PROMPT,
    DEFAULT_EXTRACTION_SYSTEM_PROMPT,
)

_LONG_TEXT = "word " * 60


class _FakeMessages:
    """Messages endpoint double returning canned text or raising."""

    def __init__(self, text: str | None = None, error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        """Record the request and return a single-text-block response."""
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.text or "")],
        )


def _fake_client(
    text: str | None = None,
    error: Exception | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(messages=_FakeMessages(text=text, error=error))


def _status_error(code: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIStatusError(
        "boom",
        response=httpx.Response(code, request=request),
        body=None,
    )


def test_extractor_uses_default_prompt_and_parses_items() -> None:
    """The generic fallback prompt is sent and items are deduplicated."""
    payload = json.dumps(
        [
            {"title": "Dune", "type": "book", "creator": "Herbert", "year": 1965},
            {"title": "dune", "type": "book"},
            {"title": "Somewhere", "type": "mystery_type", "confidence": 0.6},
        ],
    )
    client = _fake_client(text=f"Here you go:\n{payload}")

    result = LLMExtractor(client=client).extract_from_transcript(
        [{"text": _LONG_TEXT}],
    )

    assert_that(result.success).is_true()
    # The unknown "mystery_type" item is rejected (untrusted input), not
    # coerced — only the deduplicated book survives.
    assert_that(result.items).is_length(1)
    assert_that(result.items[0].media_type).is_equal_to(MediaType.BOOK)
    sent = client.messages.calls[0]
    assert_that(sent["system"]).is_equal_to(DEFAULT_EXTRACTION_SYSTEM_PROMPT)
    assert_that(sent["model"]).is_equal_to("claude-opus-4-8")


def test_extractor_prefers_injected_prompt() -> None:
    """An injected (podex-ops) prompt overrides the generic fallback."""
    client = _fake_client(text="[]")
    extractor = LLMExtractor(client=client, system_prompt="injected ops prompt")

    extractor.extract_from_transcript([{"text": _LONG_TEXT}])

    assert_that(client.messages.calls[0]["system"]).is_equal_to(
        "injected ops prompt",
    )


def test_extractor_skips_short_transcripts() -> None:
    """Transcripts under the minimum length never reach the API."""
    client = _fake_client(text="[]")

    result = LLMExtractor(client=client).extract_from_transcript(
        [{"text": "too short"}],
    )

    assert_that(result.items).is_empty()
    assert_that(client.messages.calls).is_empty()


def test_extractor_records_api_errors() -> None:
    """API and network failures surface as result errors, not exceptions."""
    api_err = LLMExtractor(client=_fake_client(error=_status_error(500)))
    net_err = LLMExtractor(
        client=_fake_client(
            error=anthropic.APIConnectionError(
                request=httpx.Request(
                    "POST",
                    "https://api.anthropic.com/v1/messages",
                ),
            ),
        ),
    )

    r1 = api_err.extract_from_transcript([{"text": _LONG_TEXT}])
    r2 = net_err.extract_from_transcript([{"text": _LONG_TEXT}])

    assert_that(r1.errors[0]).contains("500")
    assert_that(r2.errors[0]).contains("Network error")


def test_extractor_reports_unparseable_output() -> None:
    """Non-JSON output produces a parse error instead of raising."""
    result = LLMExtractor(
        client=_fake_client(text="no json array at all"),
    ).extract_from_transcript([{"text": _LONG_TEXT}])

    assert_that(result.success).is_false()
    assert_that(result.errors[0]).contains("No JSON array")


def test_cleaner_applies_corrections_and_context() -> None:
    """Cleanup parses corrections and forwards podcast context."""
    payload = json.dumps(
        {
            "cleaned_text": "fixed transcript",
            "corrections": [
                {"original": "alot", "corrected": "a lot", "reason": "boundary"},
            ],
        },
    )
    client = _fake_client(text=payload)

    result = TranscriptCleaner(client=client).cleanup_transcript(
        _LONG_TEXT,
        podcast_name="Example Show",
        host_name="Host Example",
        guest_name="Guest Example",
        terminology=["podex"],
    )

    assert_that(result.cleaned_text).is_equal_to("fixed transcript")
    assert_that(result.correction_count).is_equal_to(1)
    sent = client.messages.calls[0]
    assert_that(sent["system"]).is_equal_to(DEFAULT_CLEANUP_SYSTEM_PROMPT)
    user_message = sent["messages"][0]["content"]
    assert_that(user_message).contains("Example Show")
    assert_that(user_message).contains("podex")


def test_cleaner_short_text_and_parse_failure() -> None:
    """Short inputs short-circuit; unparseable output records an error."""
    untouched = TranscriptCleaner(client=_fake_client(text="{}"))
    short = untouched.cleanup_transcript("tiny")
    assert_that(short.cleaned_text).is_equal_to("tiny")
    assert_that(untouched.client.messages.calls).is_empty()

    broken = TranscriptCleaner(
        client=_fake_client(text="not json"),
    ).cleanup_transcript(_LONG_TEXT)
    assert_that(broken.success).is_false()
    assert_that(broken.cleaned_text).is_equal_to(_LONG_TEXT)


def test_cleaner_records_api_error() -> None:
    """An API failure keeps the original text and records the error."""
    result = TranscriptCleaner(
        client=_fake_client(error=_status_error(429)),
    ).cleanup_transcript(_LONG_TEXT)

    assert_that(result.errors[0]).contains("429")
    assert_that(result.cleaned_text).is_equal_to(_LONG_TEXT)


def test_prompt_config_composes_and_overrides(tmp_path: Path) -> None:
    """Injected configs compose context prompts; custom prompts override."""
    config = {
        "podcast": {
            "name": "Example Show",
            "slug": "example-show",
            "host": "Host Example",
            "common_terminology": ["podex"],
        },
        "episodes": {
            "1": {"guest": "Guest Example"},
            "2": {"custom_prompt": "fully custom"},
        },
    }
    path = tmp_path / "example-show.json"
    path.write_text(json.dumps(config))

    manager = PromptConfigManager(config_dir=tmp_path)
    composed = manager.get_prompt("example-show", episode_number=1)
    custom = manager.get_prompt("example-show", episode_number=2)

    assert_that(composed).contains("Example Show")
    assert_that(composed).contains("Guest Example")
    assert_that(custom).is_equal_to("fully custom")
    assert_that(manager.get_prompt("missing-show")).is_none()
    assert_that(manager.list_configured_podcasts()).is_equal_to(["example-show"])


def test_prompt_config_load_for_podcast_missing_dir(tmp_path: Path) -> None:
    """A missing config file yields None rather than raising."""
    assert_that(
        PromptConfig.load_for_podcast("nope", config_dir=tmp_path),
    ).is_none()


def test_prompt_config_includes_guest_context(tmp_path: Path) -> None:
    """Guest context, co-hosts, and terminology all reach the prompt."""
    config = {
        "podcast": {
            "name": "Example Show",
            "slug": "example-show",
            "host": "Host Example",
            "co_hosts": ["Co-host Example"],
            "common_terminology": ["podex"],
        },
        "episodes": {
            "3": {
                "guests": ["Guest A", "Guest B"],
                "terminology": ["alembic"],
            },
            "4": {
                "guest": "Solo Guest",
                "guest_context": {
                    "name": "Solo Guest",
                    "profession": "researcher",
                    "background": "signal processing",
                    "terminology": ["fourier"],
                    "topics": ["audio"],
                },
            },
        },
    }
    path = tmp_path / "example-show.json"
    path.write_text(json.dumps(config))
    manager = PromptConfigManager(config_dir=tmp_path)

    multi = manager.get_prompt("example-show", episode_number=3)
    contextual = manager.get_prompt("example-show", episode_number=4)
    episode = manager.get_episode_config("example-show", episode_number=4)

    assert_that(multi).contains("Guest A")
    assert_that(multi).contains("Co-host Example")
    assert_that(multi).contains("alembic")
    assert_that(contextual).contains("researcher")
    assert_that(contextual).contains("fourier")
    assert_that(episode).is_not_none()
    manager.clear_cache()


def test_prompt_config_invalid_json_returns_none(tmp_path: Path) -> None:
    """A corrupt config file logs and yields None instead of raising."""
    (tmp_path / "broken.json").write_text("{not json")

    assert_that(
        PromptConfig.load_for_podcast("broken", config_dir=tmp_path),
    ).is_none()


def test_extractor_rejects_out_of_schema_items() -> None:
    """Injected junk items outside the schema are dropped, not coerced."""
    payload = json.dumps(
        [
            {"title": "Valid", "type": "book", "confidence": 0.9},
            {"title": "Bad confidence", "type": "book", "confidence": 5.0},
            {"title": "Bad year", "type": "book", "year": 99},
            {"title": "x" * 600, "type": "book"},
            {"title": "Bad type", "type": "exploit"},
            {"title": "Bad creator", "type": "book", "creator": "c" * 300},
        ],
    )
    client = _fake_client(text=payload)

    result = LLMExtractor(client=client).extract_from_transcript(
        [{"text": _LONG_TEXT}],
    )

    assert_that(result.items).is_length(1)
    assert_that(result.items[0].title).is_equal_to("Valid")


def test_extractor_wraps_transcript_in_delimiters() -> None:
    """The transcript reaches the model inside <transcript> tags."""
    client = _fake_client(text="[]")

    LLMExtractor(client=client).extract_from_transcript(
        [{"text": _LONG_TEXT}],
    )

    content = client.messages.calls[0]["messages"][0]["content"]
    assert_that(content).starts_with("<transcript>")
    assert_that(content).ends_with("</transcript>")


def test_cleaner_wraps_transcript_in_delimiters() -> None:
    """Cleanup requests also delimit the untrusted transcript."""
    client = _fake_client(text="{}")

    TranscriptCleaner(client=client).cleanup_transcript(_LONG_TEXT)

    content = client.messages.calls[0]["messages"][0]["content"]
    assert_that(content).contains("<transcript>")
    assert_that(content).contains("</transcript>")
