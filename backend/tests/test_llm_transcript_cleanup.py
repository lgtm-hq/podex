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


def test_text_after_the_limit_is_not_lost() -> None:
    """Reproduction: a marker placed after ``max_chars`` survives cleanup."""
    long_text = "a" * 150 + "TAIL-MARKER" + "b" * 50

    result, _model = _clean(json.dumps({"corrections": []}), long_text, max_chars=150)

    assert_that(result.cleaned_text).is_equal_to(long_text)
    assert_that(result.cleaned_text).contains("TAIL-MARKER")


_LIMIT = 150
_CLEANED_HEAD = "HEAD-CLEANED-BY-MODEL"


@pytest.mark.parametrize(
    ("text", "head_chars"),
    [
        ("x" * (_LIMIT - 1), _LIMIT - 1),
        ("x" * _LIMIT, _LIMIT),
        ("x" * _LIMIT + "Y", _LIMIT),
        ("a" * _LIMIT + "TAIL-MARKER" + "b" * (3 * _LIMIT - 11), _LIMIT),
    ],
    ids=["under-limit", "at-limit", "one-over", "four-times-limit"],
)
def test_cleaned_head_is_followed_by_the_uncleaned_tail(
    text: str,
    head_chars: int,
) -> None:
    """The model's head comes first, then every character after the cut."""
    result, model = _clean(
        json.dumps({"cleaned_text": _CLEANED_HEAD, "corrections": []}),
        text,
        max_chars=_LIMIT,
    )

    tail = text[head_chars:]
    assert_that(model.transcripts_sent()).is_equal_to([text[:head_chars]])
    assert_that(result.cleaned_text).is_equal_to(_CLEANED_HEAD + tail)
    assert_that(result.uncleaned_chars).is_equal_to(len(tail))
    assert_that(result.partial).is_equal_to(bool(tail))
    assert_that(result.success).is_true()
    assert_that(result.fully_cleaned).is_equal_to(not tail)


def test_marker_after_the_cut_is_returned_after_the_cleaned_head() -> None:
    """A marker beyond the limit appears exactly once, after the model's head."""
    long_text = "a" * _LIMIT + "TAIL-MARKER" + "b" * 50

    result, _model = _clean(
        json.dumps({"cleaned_text": _CLEANED_HEAD}),
        long_text,
        max_chars=_LIMIT,
    )

    assert_that(result.cleaned_text).starts_with(_CLEANED_HEAD)
    assert_that(result.cleaned_text.count("TAIL-MARKER")).is_equal_to(1)
    assert_that(result.cleaned_text).ends_with("TAIL-MARKER" + "b" * 50)


@pytest.mark.parametrize(
    ("text", "head"),
    [
        ("a" * 145 + " " + "b" * 20, "a" * 145),
        ("a" * 140 + "\n" + "b" * 30, "a" * 140),
        ("a" * _LIMIT + " " + "b" * 20, "a" * _LIMIT),
    ],
    ids=["mid-word-after-space", "mid-word-after-line-break", "cut-on-space"],
)
def test_cut_moves_back_to_whitespace_so_no_word_is_split(text: str, head: str) -> None:
    """The tail starts at the last whitespace at or before the limit."""
    result, model = _clean(json.dumps({"corrections": []}), text, max_chars=_LIMIT)

    assert_that(model.transcripts_sent()).is_equal_to([head])
    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.uncleaned_chars).is_equal_to(len(text) - len(head))
    assert_that(text[len(head)].isspace()).is_true()


def test_reply_without_cleaned_text_returns_the_whole_long_input() -> None:
    """The fallback keeps the unchanged head and the uncleaned tail."""
    long_text = "a" * _LIMIT + "TAIL-MARKER" + "b" * 50

    result, _model = _clean(json.dumps({"corrections": []}), long_text, max_chars=150)

    assert_that(result.cleaned_text).is_equal_to(long_text)
    assert_that(result.success).is_true()
    assert_that(result.partial).is_true()
    assert_that(result.fully_cleaned).is_false()
    assert_that(result.uncleaned_chars).is_equal_to(61)


@pytest.mark.parametrize(
    ("reply", "errors"),
    [("", []), ("not json", [_INVALID_JSON])],
    ids=["empty-reply", "invalid-json"],
)
def test_long_input_without_usable_reply_is_returned_whole_and_partial(
    reply: str,
    errors: list[str],
) -> None:
    """Without a usable reply the original text is kept and the cut reported."""
    long_text = "a" * _LIMIT + "TAIL-MARKER" + "b" * 50

    result, _model = _clean(reply, long_text, max_chars=_LIMIT)

    assert_that(result.cleaned_text).is_equal_to(long_text)
    assert_that(result.errors).is_equal_to(errors)
    assert_that(result.partial).is_true()
    assert_that(result.uncleaned_chars).is_equal_to(61)
    assert_that(result.fully_cleaned).is_false()


def test_request_failure_on_long_input_keeps_the_whole_text() -> None:
    """A provider error returns the original input and reports no success."""
    long_text = "a" * _LIMIT + "TAIL-MARKER" + "b" * 50

    def unavailable(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, json={"type": "error"})

    with httpx2.Client(transport=httpx2.MockTransport(unavailable)) as http_client:
        client: anthropic.Anthropic = anthropic.Anthropic(
            api_key=f"synthetic-{token_hex(4)}",
            http_client=http_client,
            max_retries=0,
        )
        result = TranscriptCleaner(client=client).cleanup_transcript(
            long_text,
            max_chars=_LIMIT,
        )

    assert_that(result.cleaned_text).is_equal_to(long_text)
    assert_that(result.errors).is_length(1)
    assert_that(result.success).is_false()
    assert_that(result.partial).is_true()
    assert_that(result.fully_cleaned).is_false()


def test_cut_at_whitespace_is_logged_with_the_head_length(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The existing warning reports the characters actually sent."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    _clean(
        json.dumps({"corrections": []}),
        "a" * 145 + " " + "b" * 20,
        max_chars=_LIMIT,
    )

    assert_that(
        [(r.levelno, r.getMessage()) for r in caplog.records if r.name == _LOGGER],
    ).is_equal_to([(logging.WARNING, "Transcript truncated from 166 to 145 chars")])


def test_short_transcript_is_not_partial() -> None:
    """Text skipped for being short is returned whole and fully covered."""
    result, model = _clean(json.dumps({"cleaned_text": "y"}), "tiny", max_chars=2)

    assert_that(model.sent).is_empty()
    assert_that(result.cleaned_text).is_equal_to("tiny")
    assert_that(result.uncleaned_chars).is_zero()
    assert_that(result.fully_cleaned).is_true()
