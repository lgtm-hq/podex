"""Tests for transcript cleanup's handling of model output and long input.

Requests go through the real Anthropic SDK; only its HTTP transport is
replaced with ``httpx2.MockTransport``, so nothing leaves the process.
"""

import json
import logging
import os
from secrets import token_hex
from typing import Any

import anthropic
import httpx2
import pytest
from assertpy import assert_that

from podex.services import llm_transcript_cleanup
from podex.services.llm_transcript_cleanup import CleanupResult, TranscriptCleaner

_TRANSCRIPT = "the speaker said alot about podcasts and books " * 3
_INVALID_JSON = "cleanup: invalid_json"
_LOGGER = llm_transcript_cleanup.__name__


class _Model:
    """Messages endpoint stand-in that answers every request with ``reply``."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.sent: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Record the decoded request body and return one text block."""
        self.sent.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": self.reply}],
            },
        )

    def transcripts_sent(self) -> list[str]:
        """Return the transcript text inside each request's delimiters."""
        transcripts: list[str] = []
        for body in self.sent:
            content: str = body["messages"][0]["content"]
            transcripts.append(
                content.split("<transcript>\n", 1)[1].split("\n</transcript>", 1)[0],
            )
        return transcripts


@pytest.fixture(autouse=True)
def _no_ambient_sdk_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop ambient Anthropic settings such as a base URL or real key."""
    for name in list(os.environ):
        if name.startswith("ANTHROPIC_"):
            monkeypatch.delenv(name)


def _clean(reply: str, text: str = _TRANSCRIPT, **kwargs: Any) -> tuple[
    CleanupResult,
    _Model,
]:
    """Run cleanup against a model that answers ``reply``."""
    model = _Model(reply)
    with httpx2.Client(transport=httpx2.MockTransport(model)) as http_client:
        client: anthropic.Anthropic = anthropic.Anthropic(
            api_key=f"synthetic-{token_hex(4)}",
            http_client=http_client,
            max_retries=0,
        )
        result = TranscriptCleaner(client=client).cleanup_transcript(text, **kwargs)
    return result, model


@pytest.mark.parametrize(
    "reply",
    [
        "I could not process this transcript.",
        '{"cleaned_text": "unterminated',
        "{cleaned_text: 'single quotes are not JSON'}",
        '"just a JSON string"',
        "Result: } then {",
    ],
    ids=[
        "prose",
        "unterminated-object",
        "invalid-object",
        "json-string",
        "reversed-braces",
    ],
)
def test_malformed_output_keeps_original_text_and_records_error(reply: str) -> None:
    """Output without a JSON object leaves the transcript unchanged and fails."""
    result, model = _clean(reply)

    assert_that(result.cleaned_text).is_equal_to(_TRANSCRIPT)
    assert_that(result.corrections_made).is_empty()
    assert_that(result.errors).is_equal_to([_INVALID_JSON])
    assert_that(result.success).is_false()
    assert_that(model.sent).is_length(1)


def test_object_inside_surrounding_prose_is_used() -> None:
    """A JSON object wrapped in prose is parsed, including nested braces."""
    payload = {
        "cleaned_text": "the speaker said a lot about {podcasts} and books",
        "corrections": [
            {"original": "alot", "corrected": "a lot", "reason": "spacing"},
        ],
    }
    reply = f"Here is the cleaned transcript:\n{json.dumps(payload)}\nLet me know!"

    result, _model = _clean(reply)

    assert_that(result.success).is_true()
    assert_that(result.cleaned_text).is_equal_to(payload["cleaned_text"])
    assert_that(result.correction_count).is_equal_to(1)
    correction = result.corrections_made[0]
    assert_that(
        (correction.original, correction.corrected, correction.reason),
    ).is_equal_to(("alot", "a lot", "spacing"))


def test_first_complete_object_is_used_when_prose_follows_with_braces() -> None:
    """Parsing stops at the brace that closes the first object."""
    reply = (
        'Answer: {"cleaned_text": "first"} '
        'and an unrelated note {"cleaned_text": "second"}'
    )

    result, _model = _clean(reply)

    assert_that(result.success).is_true()
    assert_that(result.cleaned_text).is_equal_to("first")


def test_corrections_that_are_not_objects_are_skipped() -> None:
    """Only object entries become corrections; missing fields default to empty."""
    reply = json.dumps(
        {
            "cleaned_text": "cleaned",
            "corrections": [
                "alot -> a lot",
                {"original": "the"},
                ["the", "the"],
                {"original": "alot", "corrected": "a lot", "reason": "spacing"},
            ],
        },
    )

    result, _model = _clean(reply)

    assert_that(result.success).is_true()
    assert_that(
        [(c.original, c.corrected, c.reason) for c in result.corrections_made],
    ).is_equal_to([("the", "", ""), ("alot", "a lot", "spacing")])


def test_object_without_cleaned_text_keeps_the_original() -> None:
    """A reply that omits ``cleaned_text`` leaves the transcript as it was."""
    result, _model = _clean(json.dumps({"corrections": []}))

    assert_that(result.success).is_true()
    assert_that(result.cleaned_text).is_equal_to(_TRANSCRIPT)
    assert_that(result.corrections_made).is_empty()


def test_long_transcript_is_truncated_before_sending(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No request carries more than ``max_chars`` characters, and the cut is logged."""
    long_text = "a" * 150 + "TAIL-MARKER" + "b" * 50
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    _result, model = _clean(json.dumps({"corrections": []}), long_text, max_chars=150)

    sent = model.transcripts_sent()
    assert_that(sent).is_not_empty()
    assert_that([len(transcript) <= 150 for transcript in sent]).does_not_contain(
        False,
    )
    assert_that(
        [(r.levelno, r.getMessage()) for r in caplog.records if r.name == _LOGGER],
    ).is_equal_to([(logging.WARNING, "Transcript truncated from 211 to 150 chars")])


def test_transcript_at_the_limit_is_sent_whole(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A transcript exactly ``max_chars`` long is neither cut nor reported."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    _result, model = _clean(
        json.dumps({"corrections": []}),
        _TRANSCRIPT,
        max_chars=len(_TRANSCRIPT),
    )

    assert_that(model.transcripts_sent()).is_equal_to([_TRANSCRIPT])
    assert_that([r for r in caplog.records if r.name == _LOGGER]).is_empty()


def test_terminology_is_limited_to_fifty_terms() -> None:
    """At most fifty known terms are sent as context."""
    terms = [f"term{index:02d}" for index in range(60)]

    _result, model = _clean(json.dumps({"corrections": []}), terminology=terms)

    content: str = model.sent[0]["messages"][0]["content"]
    assert_that(content).contains(f"Known terms and names: {', '.join(terms[:50])}\n")
    assert_that(content).does_not_contain("term50")


def test_request_without_context_says_so() -> None:
    """With no podcast, people or terms the context block says there is none."""
    _result, model = _clean(json.dumps({"corrections": []}))

    content: str = model.sent[0]["messages"][0]["content"]
    assert_that(content).starts_with("CONTEXT:\nNo additional context.\n\n")


@pytest.mark.parametrize(
    ("length", "requests"),
    [(99, 0), (100, 1)],
    ids=["below-minimum", "at-minimum"],
)
def test_transcripts_shorter_than_a_hundred_characters_are_not_sent(
    length: int,
    requests: int,
) -> None:
    """Text under 100 characters is returned untouched without a request."""
    text = "x" * length

    result, model = _clean(json.dumps({"cleaned_text": "y" * length}), text)

    assert_that(model.sent).is_length(requests)
    assert_that(result.cleaned_text).is_equal_to(
        text if requests == 0 else "y" * length
    )
    assert_that(result.success).is_true()


def test_non_text_cleaned_value_is_returned_as_text() -> None:
    """A non-string ``cleaned_text`` in the reply is converted to a string."""
    result, _model = _clean(json.dumps({"cleaned_text": 12345, "corrections": []}))

    assert_that(result.cleaned_text).is_instance_of(str)
    assert_that(result.cleaned_text).is_equal_to("12345")
