"""Tests for transcript cleanup's handling of model output and long input.

Requests go through the real Anthropic SDK; only its HTTP transport is
replaced with ``httpx2.MockTransport``, so nothing leaves the process.
"""

import hashlib
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
    """Messages endpoint stand-in that answers every request with ``reply``.

    A string reply becomes one text block, a list is used as the content
    blocks, and ``None`` echoes the transcript it received as ``cleaned_text``.
    """

    def __init__(self, reply: str | list[dict[str, Any]] | None) -> None:
        self.reply = reply
        self.sent: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Record the decoded request body and return the configured content."""
        self.sent.append(json.loads(request.content))
        if self.reply is None:
            echo = json.dumps({"cleaned_text": self.transcripts_sent()[-1]})
            content: list[dict[str, Any]] = [{"type": "text", "text": echo}]
        elif isinstance(self.reply, list):
            content = self.reply
        else:
            content = [{"type": "text", "text": self.reply}]
        return httpx2.Response(
            200,
            json={"type": "message", "role": "assistant", "content": content},
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


def _clean(
    reply: str | list[dict[str, Any]] | None,
    text: str = _TRANSCRIPT,
    **kwargs: Any,
) -> tuple[
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


def test_non_text_cleaned_value_keeps_the_original() -> None:
    """A non-string ``cleaned_text`` is not used; the transcript is kept."""
    result, _model = _clean(json.dumps({"cleaned_text": 12345, "corrections": []}))

    assert_that(result.cleaned_text).is_equal_to(_TRANSCRIPT)
    assert_that(result.head_cleaned).is_false()


def test_text_after_the_limit_is_not_lost() -> None:
    """Reproduction: a marker placed after ``max_chars`` survives cleanup."""
    long_text = "a" * 150 + "TAIL-MARKER" + "b" * 50

    result, _model = _clean(json.dumps({"corrections": []}), long_text, max_chars=150)

    assert_that(result.cleaned_text).is_equal_to(long_text)
    assert_that(result.cleaned_text).contains("TAIL-MARKER")


_LIMIT = 150
_CLEANED_HEAD = "HEAD-CLEANED-BY-MODEL"
_OVER_LIMIT = _TRANSCRIPT + "and then " + "more words follow here " * 3


@pytest.mark.parametrize(
    ("text", "head_chars"),
    [
        ("x" * (_LIMIT - 1), _LIMIT - 1),
        ("x" * _LIMIT, _LIMIT),
        ("x" * _LIMIT + "Y", _LIMIT),
        ("a" * _LIMIT + "TAIL-MARKER" + "b" * (3 * _LIMIT - 11), _LIMIT),
        ("漢" * 120, 120),
        ("漢" * (_LIMIT + 1), _LIMIT),
    ],
    ids=[
        "under-limit",
        "at-limit",
        "one-over",
        "four-times-limit",
        "multibyte-within-limit",
        "multibyte-one-over",
    ],
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
    assert_that(result.head_cleaned).is_true()
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


# Limit 150 looks back min(150 // 10, 1000) = 15 characters: indices 135-150.
@pytest.mark.parametrize(
    ("text", "head_chars"),
    [
        ("a" * 145 + " " + "b" * 20, 145),
        ("a" * 140 + "\n" + "b" * 30, 140),
        ("a" * _LIMIT + " " + "b" * 20, _LIMIT),
        ("aa bb " * 30 + "c" * 60, 149),
        ("a" * 140 + " " + "b" * 9 + " " + "c" * 20, _LIMIT),
        ("a" * 140 + "\t" + "b" * 30, 140),
        ("a" * 140 + "　" + "b" * 30, 140),
        ("a" * 135 + " " + "b" * 30, 135),
        ("a" * 134 + " " + "b" * 40, _LIMIT),
        (" " + "a" * 300, _LIMIT),
        ("a " + "b" * 300, _LIMIT),
        ("a" * 149 + "\r\n" + "b" * 20, _LIMIT),
        ("x" * _LIMIT + " ", _LIMIT),
        ("漢" * (_LIMIT + 1), _LIMIT),
    ],
    ids=[
        "mid-word-after-space",
        "mid-word-after-line-break",
        "cut-on-space",
        "last-whitespace-not-first",
        "scan-starts-at-the-limit",
        "tab-is-whitespace",
        "ideographic-space-is-whitespace",
        "whitespace-at-window-start",
        "whitespace-just-before-window",
        "whitespace-only-at-index-0",
        "whitespace-only-at-index-1",
        "crlf-straddles-the-cut",
        "whitespace-only-tail",
        "multibyte-counted-in-characters",
    ],
)
def test_cut_position(text: str, head_chars: int) -> None:
    """The head ends at the window's last whitespace, otherwise at the limit."""
    result, model = _clean(None, text, max_chars=_LIMIT)

    assert_that(model.transcripts_sent()).is_equal_to([text[:head_chars]])
    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.head_cleaned).is_true()
    assert_that(result.uncleaned_chars).is_equal_to(len(text) - head_chars)
    assert_that(result.partial).is_true()
    assert_that(result.fully_cleaned).is_false()


@pytest.mark.parametrize(
    ("text", "head_chars"),
    [
        ("x" * 100_000, 100_000),
        ("x" * 100_000 + "y", 100_000),
        ("x" * 99_000 + " " + "x" * 2_000, 99_000),
        ("x" * 98_999 + " " + "x" * 2_000, 100_000),
        ("第一集 标题\n" + "汉" * 200_000, 100_000),
        ("  " + "x" * 150_000, 100_000),
    ],
    ids=[
        "at-default-limit",
        "one-over-default-limit",
        "whitespace-at-window-start",
        "whitespace-just-before-window",
        "whitespace-only-near-the-start",
        "leading-spaces-only",
    ],
)
def test_default_limit_and_look_back_cap(text: str, head_chars: int) -> None:
    """The default limit is 100,000 characters and looks back at most 1,000."""
    result, model = _clean(json.dumps({"cleaned_text": _CLEANED_HEAD}), text)

    assert_that(model.transcripts_sent()).is_equal_to([text[:head_chars]])
    assert_that(result.cleaned_text).is_equal_to(_CLEANED_HEAD + text[head_chars:])
    assert_that(result.uncleaned_chars).is_equal_to(len(text) - head_chars)


def test_blank_head_of_non_blank_input_is_not_sent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A head with nothing to clean is not sent; the input is returned whole."""
    text = " " * 200 + "x" * 50
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    result, model = _clean(json.dumps({"cleaned_text": "x"}), text, max_chars=_LIMIT)

    assert_that(model.sent).is_empty()
    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.success).is_true()
    assert_that(result.head_cleaned).is_false()
    assert_that(result.uncleaned_chars).is_equal_to(len(text))
    assert_that(result.fully_cleaned).is_false()
    assert_that(
        [(r.levelno, r.getMessage()) for r in caplog.records if r.name == _LOGGER],
    ).is_equal_to(
        [
            (logging.WARNING, "Transcript truncated from 250 to 150 chars"),
            (logging.WARNING, "Transcript head is blank; cleanup request skipped"),
        ],
    )


def test_blank_input_is_still_sent_and_a_blank_reply_is_used() -> None:
    """Blank input keeps today's request; blank text then counts as cleaned."""
    text = " " * 120

    result, model = _clean(json.dumps({"cleaned_text": ""}), text, max_chars=_LIMIT)

    assert_that(model.transcripts_sent()).is_equal_to([text])
    assert_that(result.cleaned_text).is_equal_to("")
    assert_that(result.head_cleaned).is_true()
    assert_that(result.fully_cleaned).is_true()


def _text_block(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text}]


_UNUSED_REPLIES = {
    "corrections-only": _text_block(json.dumps({"corrections": []})),
    "empty-object": _text_block("{}"),
    "empty-text": _text_block(""),
    "blank-text": _text_block("  \n "),
    "no-content": [],
    "tool-use-only": [
        {"type": "tool_use", "id": "toolu_synthetic", "name": "noop", "input": {}},
    ],
    "cleaned-empty": _text_block(json.dumps({"cleaned_text": ""})),
    "cleaned-blank": _text_block(json.dumps({"cleaned_text": " \n"})),
    "cleaned-null": _text_block(json.dumps({"cleaned_text": None})),
    "cleaned-number": _text_block(json.dumps({"cleaned_text": 12345})),
    "cleaned-false": _text_block(json.dumps({"cleaned_text": False})),
    "cleaned-list": _text_block(json.dumps({"cleaned_text": ["a"]})),
}


@pytest.mark.parametrize("text", [_TRANSCRIPT, _OVER_LIMIT], ids=["within", "over"])
@pytest.mark.parametrize(
    "reply",
    list(_UNUSED_REPLIES.values()),
    ids=list(_UNUSED_REPLIES),
)
def test_reply_without_usable_text_keeps_the_whole_input(
    reply: list[dict[str, Any]],
    text: str,
) -> None:
    """Truth table: the model's text is not used, so nothing counts as cleaned."""
    assert_that(len(_TRANSCRIPT)).is_less_than_or_equal_to(_LIMIT)
    assert_that(len(_OVER_LIMIT)).is_greater_than(_LIMIT)

    result, model = _clean(reply, text, max_chars=_LIMIT)

    assert_that(model.sent).is_length(1)
    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.success).is_true()
    assert_that(result.head_cleaned).is_false()
    assert_that(result.uncleaned_chars).is_equal_to(len(text))
    assert_that(result.partial).is_false()
    assert_that(result.fully_cleaned).is_false()


@pytest.mark.parametrize("text", [_TRANSCRIPT, _OVER_LIMIT], ids=["within", "over"])
def test_usable_reply_is_used_within_and_over_the_limit(text: str) -> None:
    """Truth table counterpart: usable text replaces the head only."""
    result, model = _clean(
        _text_block(json.dumps({"cleaned_text": _CLEANED_HEAD})),
        text,
        max_chars=_LIMIT,
    )

    tail = text[len(model.transcripts_sent()[0]) :]
    assert_that(result.cleaned_text).is_equal_to(_CLEANED_HEAD + tail)
    assert_that(result.head_cleaned).is_true()
    assert_that(result.uncleaned_chars).is_equal_to(len(tail))
    assert_that(result.partial).is_equal_to(bool(tail))
    assert_that(result.fully_cleaned).is_equal_to(not tail)


@pytest.mark.parametrize("text", ["x" * 120, _OVER_LIMIT], ids=["within", "over"])
def test_invalid_reply_is_not_success_and_not_cleaned(text: str) -> None:
    """Output that is not JSON keeps the input and is never fully cleaned."""
    result, _model = _clean("not json", text, max_chars=_LIMIT)

    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.errors).is_equal_to([_INVALID_JSON])
    assert_that(result.head_cleaned).is_false()
    assert_that(result.uncleaned_chars).is_equal_to(len(text))
    assert_that(result.partial).is_false()
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
    assert_that(result.head_cleaned).is_false()
    assert_that(result.uncleaned_chars).is_equal_to(len(long_text))
    assert_that(result.fully_cleaned).is_false()


@pytest.mark.parametrize(
    ("fields", "fully_cleaned"),
    [
        ({"head_cleaned": True}, True),
        ({"errors": ["e"], "head_cleaned": True}, False),
        ({"uncleaned_chars": 1, "head_cleaned": True}, False),
        ({}, False),
    ],
    ids=["clean", "with-error", "with-tail", "head-not-cleaned"],
)
def test_fully_cleaned_needs_success_whole_coverage_and_a_cleaned_head(
    fields: dict[str, Any],
    fully_cleaned: bool,
) -> None:
    """``fully_cleaned`` combines all three conditions.

    Through the cleaner an error always leaves the head uncleaned, so the
    success condition is pinned on the result type directly.
    """
    result = CleanupResult(cleaned_text="x", **fields)

    assert_that(result.fully_cleaned).is_equal_to(fully_cleaned)


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


def test_short_transcript_is_reported_as_not_cleaned() -> None:
    """Text skipped for being short is returned whole and not counted as cleaned."""
    result, model = _clean(json.dumps({"cleaned_text": "y"}), "tiny", max_chars=2)

    assert_that(model.sent).is_empty()
    assert_that(result.cleaned_text).is_equal_to("tiny")
    assert_that(result.head_cleaned).is_false()
    assert_that(result.uncleaned_chars).is_equal_to(4)
    assert_that(result.fully_cleaned).is_false()


_ALPHABET = ["a", "b", "漢", " ", "\n", "\t", "\r", "　", "é"]


def _generated_inputs(count: int) -> list[tuple[str, int]]:
    """Return fixed pseudo-varied (text, max_chars) pairs derived from SHA-256."""
    inputs: list[tuple[str, int]] = []
    for case in range(count):
        digest = b"".join(
            hashlib.sha256(f"{case}:{block}".encode()).digest() for block in range(13)
        )
        length = 90 + int.from_bytes(digest[:2], "big") % 311
        max_chars = 1 + int.from_bytes(digest[2:4], "big") % 300
        text = "".join(
            _ALPHABET[byte % len(_ALPHABET)] for byte in digest[4 : 4 + length]
        )
        inputs.append((text, max_chars))
    return inputs


def test_echoed_head_always_returns_the_whole_input() -> None:
    """Property: when the model echoes its input, the result is the input."""
    for text, max_chars in _generated_inputs(150):
        result, model = _clean(None, text, max_chars=max_chars)

        assert_that(result.cleaned_text).is_equal_to(text)
        for head in model.transcripts_sent():
            assert_that(len(head)).is_less_than_or_equal_to(max_chars)
            assert_that(text).starts_with(head)


def test_any_reply_keeps_the_whole_tail() -> None:
    """Property: whatever the head becomes, the full tail follows it."""
    for text, max_chars in _generated_inputs(150):
        result, model = _clean(
            json.dumps({"cleaned_text": "Z"}), text, max_chars=max_chars
        )

        sent = model.transcripts_sent()
        if result.head_cleaned:
            tail = text[len(sent[0]) :]
            assert_that(result.cleaned_text).is_equal_to("Z" + tail)
            assert_that(result.uncleaned_chars).is_equal_to(len(tail))
        else:
            assert_that(result.cleaned_text).is_equal_to(text)
            assert_that(result.uncleaned_chars).is_equal_to(len(text))
