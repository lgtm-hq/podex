"""Tests for how transcript cleanup reads a reply: shape, stop reason, limits.

Requests go through the real Anthropic SDK with a mock HTTP transport.
"""

import json
from typing import Any

import httpx2
import pytest
from assertpy import assert_that

from podex.services import llm_transcript_cleanup
from tests.transcript_cleanup_support import (
    NO_FIXES,
    TRANSCRIPT,
    clean,
    clean_with,
    drop_ambient_sdk_settings,
    fixes,
    padded,
    rejection_counts,
    sentence_text,
    text_block,
)

_INVALID_JSON = "cleanup: invalid_json"
_TRUNCATED = "cleanup: truncated_response"
_LEGACY = "cleanup: legacy_response_shape"
_INVALID_CORRECTIONS = "cleanup: invalid_corrections"
_UNEXPECTED_STOP = "cleanup: unexpected_stop_reason"
_LIMIT = 150
_OVER_LIMIT = TRANSCRIPT + "and then " + "more words follow here " * 3


@pytest.fixture(autouse=True)
def _no_ambient_sdk_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop ambient Anthropic settings such as a base URL or real key."""
    drop_ambient_sdk_settings(monkeypatch)


# --- Output that is not a corrections object ---------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        "I could not process this transcript.",
        '{"corrections": [unterminated',
        "{corrections: 'single quotes are not JSON'}",
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
    result, model = clean(reply)

    assert_that(result.cleaned_text).is_equal_to(TRANSCRIPT)
    assert_that(result.corrections_made).is_empty()
    assert_that(result.errors).is_equal_to([_INVALID_JSON])
    assert_that(result.success).is_false()
    assert_that(result.cleaned_chars).is_equal_to(0)
    assert_that(model.sent).is_length(1)


def test_object_inside_surrounding_prose_is_used() -> None:
    """A JSON object wrapped in prose is parsed, including nested braces."""
    text = padded("alot {braces}")
    payload = fixes(("alot {braces}", "a lot {braces}", "spacing"))

    result, _model = clean(f"Here are the corrections:\n{payload}\nThanks", text)

    assert_that(result.success).is_true()
    assert_that(result.cleaned_text).is_equal_to(text.replace("alot", "a lot"))


def test_first_complete_object_is_used_when_prose_follows_with_braces() -> None:
    """Parsing stops at the brace that closes the first object."""
    text = padded("first second")
    reply = (
        f"Answer: {fixes(('first', 'First'))} "
        f"and an unrelated note {fixes(('second', 'Second'))}"
    )

    result, _model = clean(reply, text)

    assert_that(result.cleaned_text).is_equal_to(text.replace("first", "First"))


@pytest.mark.parametrize(
    "corrections",
    [None, 5, "alot -> a lot", {"original": "alot"}, True],
    ids=["null", "number", "string", "object", "boolean"],
)
def test_non_list_corrections_is_reported_not_raised(corrections: object) -> None:
    """A ``corrections`` value that is not a list counts as no corrections."""
    result, _model = clean(json.dumps({"corrections": corrections}))

    assert_that(result.cleaned_text).is_equal_to(TRANSCRIPT)
    assert_that(result.errors).is_equal_to([_INVALID_CORRECTIONS])
    assert_that(result.corrections_proposed).is_equal_to(0)
    assert_that(result.cleaned_chars).is_equal_to(0)


def test_corrections_that_are_not_objects_are_skipped() -> None:
    """Only objects with a non-empty string original and string replacement apply."""
    text = padded("alot qat")
    reply = json.dumps(
        {
            "corrections": [
                "alot -> a lot",
                ["alot", "a lot"],
                None,
                {"original": "alot"},
                {"corrected": "a lot"},
                {"original": 5, "corrected": "five"},
                {"original": "alot", "corrected": 5},
                {"original": "", "corrected": "x"},
                {"original": "alot", "corrected": "a lot", "reason": 7},
                {"original": "alot", "corrected": "a lot"},
                {"original": "qat", "corrected": "cat", "reason": None},
            ],
        },
    )

    result, _model = clean(reply, text)

    assert_that(result.cleaned_text).is_equal_to(
        text.replace("alot qat", "a lot cat"),
    )
    assert_that(rejection_counts(result)).is_equal_to({"malformed": 9})
    assert_that([c.reason for c in result.corrections_made]).is_equal_to(["", ""])
    assert_that(result.success).is_true()


def test_object_without_cleaned_text_keeps_the_original() -> None:
    """A reply with an empty corrections list leaves the transcript as it was."""
    result, _model = clean(json.dumps({"corrections": []}))

    assert_that(result.success).is_true()
    assert_that(result.cleaned_text).is_equal_to(TRANSCRIPT)
    assert_that(result.corrections_made).is_empty()


@pytest.mark.parametrize(
    "payload",
    [
        {"cleaned_text": "REWRITTEN BY MODEL"},
        {
            "cleaned_text": "REWRITTEN BY MODEL",
            "corrections": [{"original": "alot", "corrected": "a lot"}],
        },
        {"cleaned_text": None, "corrections": []},
    ],
    ids=["text-only", "text-and-corrections", "null-text"],
)
def test_non_text_cleaned_value_keeps_the_original(payload: dict[str, Any]) -> None:
    """A reply with ``cleaned_text`` changes nothing and records its own reason."""
    text = padded("alot")

    result, _model = clean(json.dumps(payload), text)

    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.errors).is_equal_to([_LEGACY])
    assert_that(result.corrections_made).is_empty()
    assert_that(result.corrections_proposed).is_equal_to(0)
    assert_that(result.cleaned_chars).is_equal_to(0)


def test_quoted_key_name_inside_a_correction_is_not_the_old_shape() -> None:
    """Text that merely mentions ``"cleaned_text":`` is a normal correction."""
    text = padded('the "cleaned_text": key was misheard as keey')

    result, _model = clean(
        fixes(('"cleaned_text": key was', '"cleaned_text": key is')),
        text,
    )

    assert_that(result.errors).is_empty()
    assert_that(result.correction_count).is_equal_to(1)


# --- Truth table: replies that clean nothing -----------------------------------

_UNUSED_REPLIES: dict[str, tuple[list[dict[str, Any]], str | None]] = {
    "corrections-only": (text_block(NO_FIXES), None),
    "empty-object": (text_block("{}"), _INVALID_CORRECTIONS),
    "empty-text": (text_block(""), None),
    "blank-text": (text_block("  \n "), None),
    "no-content": ([], None),
    "tool-use-only": (
        [{"type": "tool_use", "id": "toolu_synthetic", "name": "noop", "input": {}}],
        None,
    ),
    "cleaned-empty": (text_block(json.dumps({"cleaned_text": ""})), _LEGACY),
    "cleaned-blank": (text_block(json.dumps({"cleaned_text": " \n"})), _LEGACY),
    "cleaned-null": (text_block(json.dumps({"cleaned_text": None})), _LEGACY),
    "cleaned-number": (text_block(json.dumps({"cleaned_text": 12345})), _LEGACY),
    "cleaned-false": (text_block(json.dumps({"cleaned_text": False})), _LEGACY),
    "cleaned-list": (text_block(json.dumps({"cleaned_text": ["a"]})), _LEGACY),
}


@pytest.mark.parametrize("text", [TRANSCRIPT, _OVER_LIMIT], ids=["within", "over"])
@pytest.mark.parametrize(
    ("reply", "error"),
    list(_UNUSED_REPLIES.values()),
    ids=list(_UNUSED_REPLIES),
)
def test_reply_without_usable_text_keeps_the_whole_input(
    reply: list[dict[str, Any]],
    error: str | None,
    text: str,
) -> None:
    """Truth table: no reply here changes the text.

    An empty corrections list is a usable reply that cleans its piece; a reply
    without text is not an error but cleans nothing; the old shape stops after
    the first piece.
    """
    assert_that(len(TRANSCRIPT)).is_less_than_or_equal_to(_LIMIT)
    assert_that(len(_OVER_LIMIT)).is_greater_than(_LIMIT)

    result, model = clean(reply, text, max_chars=_LIMIT)

    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(set(result.errors)).is_equal_to(set() if error is None else {error})
    if error == _LEGACY:
        assert_that(model.sent).is_length(1)
    used = reply == text_block(NO_FIXES)
    assert_that(result.cleaned_chars).is_equal_to(len(text) if used else 0)
    assert_that(result.fully_cleaned).is_equal_to(used)
    assert_that(result.partial).is_false()


@pytest.mark.parametrize("text", [TRANSCRIPT, _OVER_LIMIT], ids=["within", "over"])
def test_usable_reply_is_used_within_and_over_the_limit(text: str) -> None:
    """Truth table counterpart: a usable correction applies wherever it is."""
    text += " qat"

    def answer(piece: str) -> str:
        return fixes(("qat", "cat")) if "qat" in piece else NO_FIXES

    result, _model = clean(answer, text, max_chars=_LIMIT)

    assert_that(result.cleaned_text).is_equal_to(text[:-3] + "cat")
    assert_that(result.success).is_true()
    assert_that(result.fully_cleaned).is_true()


@pytest.mark.parametrize("text", ["x" * 120, _OVER_LIMIT], ids=["within", "over"])
def test_invalid_reply_is_not_success_and_not_cleaned(text: str) -> None:
    """Output that is not JSON keeps the input and is never fully cleaned."""
    result, model = clean("not json", text, max_chars=_LIMIT)

    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.errors).is_equal_to([_INVALID_JSON] * len(model.sent))
    assert_that(result.cleaned_chars).is_equal_to(0)
    assert_that(result.uncleaned_chars).is_equal_to(len(text))
    assert_that(result.partial).is_false()
    assert_that(result.fully_cleaned).is_false()


def test_reply_without_text_is_not_an_error_and_cleans_nothing() -> None:
    """An empty reply keeps the input, records no error and counts as uncleaned."""
    result, _model = clean(text_block(""))

    assert_that(result.cleaned_text).is_equal_to(TRANSCRIPT)
    assert_that(result.success).is_true()
    assert_that(result.cleaned_chars).is_equal_to(0)
    assert_that(result.uncleaned_chars).is_equal_to(len(TRANSCRIPT))
    assert_that(result.fully_cleaned).is_false()


# --- Stop reason ---------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        fixes(("speaker said alot about podcasts", "speaker said a lot about it")),
        '{"corrections": [{"original": "alot", "corr',
    ],
    ids=["complete-json", "cut-json"],
)
def test_reply_at_the_token_cap_is_truncated_not_invalid(reply: str) -> None:
    """A reply that stopped at the cap is reported as such; nothing applies."""
    result, _model = clean(reply, stop_reason="max_tokens")

    assert_that(result.errors).is_equal_to([_TRUNCATED])
    assert_that(result.cleaned_text).is_equal_to(TRANSCRIPT)
    assert_that(result.corrections_proposed).is_equal_to(0)
    assert_that(result.cleaned_chars).is_equal_to(0)
    assert_that(result.fully_cleaned).is_false()


def test_truncated_piece_does_not_stop_the_next_one() -> None:
    """Other pieces are still read after one reply is cut off."""
    text = sentence_text(_LIMIT * 2 + 50)

    result, model = clean(NO_FIXES, text, stop_reason="max_tokens", max_chars=_LIMIT)

    assert_that(len(model.sent)).is_greater_than(1)
    assert_that(result.errors).is_equal_to([_TRUNCATED] * len(model.sent))
    assert_that(result.unread_chars).is_equal_to(0)
    assert_that(result.uncleaned_chars).is_equal_to(len(text))


@pytest.mark.parametrize("stop_reason", ["end_turn", "stop_sequence"])
def test_normal_stop_reasons_are_used(stop_reason: str) -> None:
    """Replies that ended normally are applied."""
    result, _model = clean(
        fixes(("alot", "a lot")),
        padded("alot"),
        stop_reason=stop_reason,
    )

    assert_that(result.errors).is_empty()
    assert_that(result.correction_count).is_equal_to(1)


@pytest.mark.parametrize(
    "stop_reason",
    [
        "refusal",
        "pause_turn",
        "tool_use",
        "model_context_window_exceeded",
        "x" * 10_000,
        None,
        5,
        ["end_turn"],
        {"reason": "end_turn"},
    ],
    ids=[
        "refusal",
        "pause-turn",
        "tool-use",
        "context-window",
        "long-unknown",
        "null",
        "number",
        "list",
        "object",
    ],
)
def test_other_stop_reasons_apply_nothing(stop_reason: object) -> None:
    """Only a reply that ended normally is used; other stops never raise."""
    text = padded("alot")

    result, _model = clean(fixes(("alot", "a lot")), text, stop_reason=stop_reason)

    assert_that(result.errors).is_equal_to([_UNEXPECTED_STOP])
    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.corrections_proposed).is_equal_to(0)
    assert_that(result.cleaned_chars).is_equal_to(0)


# --- Old reply shape -------------------------------------------------------------


@pytest.mark.parametrize("stop_reason", ["max_tokens", "end_turn"])
def test_old_shape_stops_reading_even_when_cut_off(stop_reason: str) -> None:
    """Reproduction: an old-shape reply is reported once and reading stops."""
    text = sentence_text(250_000)

    result, model = clean(
        '{"cleaned_text": "w00000 w00001 w00002',
        text,
        stop_reason=stop_reason,
    )

    first = model.transcripts_sent()[0]
    assert_that(model.sent).is_length(1)
    assert_that(result.errors).is_equal_to([_LEGACY])
    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.unread_chars).is_equal_to(len(text) - len(first))
    assert_that(result.cleaned_chars).is_equal_to(0)


def test_old_shape_in_a_later_piece_keeps_earlier_corrections() -> None:
    """Earlier pieces' corrections apply; pieces after the old shape are unread."""
    text = "qat " + sentence_text(_LIMIT * 4)

    def answer(piece: str) -> str:
        if piece.startswith("qat "):
            return fixes(("qat ", "cat "))
        return json.dumps({"cleaned_text": piece})

    result, model = clean(answer, text, max_chars=_LIMIT)

    second_end = text.index(model.transcripts_sent()[1]) + len(
        model.transcripts_sent()[1],
    )
    assert_that(model.sent).is_length(2)
    assert_that(result.cleaned_text).is_equal_to("cat " + text[4:])
    assert_that(result.errors).is_equal_to([_LEGACY])
    assert_that(result.unread_chars).is_equal_to(len(text) - second_end)


# --- Replies the JSON decoder refuses ----------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        '{"corrections": [], "n": ' + "9" * 5000 + "}",
        "[" * 100_000,
    ],
    ids=["long-integer", "deep-open-brackets"],
)
def test_decoder_limits_are_an_invalid_reply(reply: str) -> None:
    """A reply the decoder cannot read is recorded and never raises."""
    result, _model = clean(reply)

    assert_that(result.errors).is_equal_to([_INVALID_JSON])
    assert_that(result.cleaned_text).is_equal_to(TRANSCRIPT)


def test_deeply_nested_reply_never_raises() -> None:
    """Deep nesting is either decoded (one malformed item) or refused; never raised.

    Whether the decoder refuses 100,000 levels depends on the interpreter and
    platform, so both outcomes are accepted here.
    """
    reply = '{"corrections": ' + "[" * 100_000 + "]" * 100_000 + "}"

    result, _model = clean(reply)

    assert_that(result.cleaned_text).is_equal_to(TRANSCRIPT)
    assert_that(result.correction_count).is_equal_to(0)
    assert_that([[], [_INVALID_JSON]]).contains(result.errors)


def test_decoder_recursion_error_is_an_invalid_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``RecursionError`` from the decoder is recorded as invalid JSON."""

    def refuse(_text: str) -> object:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(llm_transcript_cleanup, "_json_loads", refuse)
    text = padded("alot")

    result, _model = clean(fixes(("alot", "a lot")), text)

    assert_that(result.errors).is_equal_to([_INVALID_JSON])
    assert_that(result.cleaned_text).is_equal_to(text)


def test_decoder_limit_in_a_later_piece_keeps_other_pieces() -> None:
    """Corrections from the other pieces survive a piece the decoder refuses."""
    text = "qat " + sentence_text(_LIMIT * 2) + " zzq"
    replies = iter(
        [
            fixes(("qat ", "cat ")),
            '{"corrections": [], "n": ' + "9" * 5000 + "}",
            fixes((" zzq", " zzz")),
        ],
    )

    result, model = clean(lambda _piece: next(replies), text, max_chars=_LIMIT)

    assert_that(model.sent).is_length(3)
    assert_that(result.errors).is_equal_to([_INVALID_JSON])
    assert_that(result.cleaned_text).is_equal_to("cat " + text[4:-4] + " zzz")


# --- Request failure -------------------------------------------------------------


def test_request_failure_on_long_input_keeps_the_whole_text() -> None:
    """A provider error on the first piece returns the input and fails."""
    long_text = "a" * _LIMIT + "TAIL-MARKER" + "b" * 50

    def unavailable(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, json={"type": "error"})

    result = clean_with(unavailable, long_text, max_chars=_LIMIT)

    assert_that(result.cleaned_text).is_equal_to(long_text)
    assert_that(result.errors).is_length(1)
    assert_that(result.success).is_false()
    assert_that(result.cleaned_chars).is_equal_to(0)
    assert_that(result.unread_chars).is_equal_to(len(long_text))
    assert_that(result.uncleaned_chars).is_equal_to(len(long_text))
    assert_that(result.fully_cleaned).is_false()
