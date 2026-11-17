"""Tests for reading long transcripts in pieces and for whole-result properties.

Requests go through the real Anthropic SDK with a mock HTTP transport.
"""

import hashlib
import json
import logging
from secrets import token_hex

import anthropic
import httpx2
import pytest
from assertpy import assert_that

from podex.services.llm_transcript_cleanup import (
    MAX_PIECES,
    CorrectionRejection,
    TranscriptCleaner,
)
from tests.transcript_cleanup_support import (
    LIMIT,
    LOGGER,
    NO_FIXES,
    applied,
    assert_only_corrections_changed,
    clean,
    drop_ambient_sdk_settings,
    fixes,
    rejection_counts,
    sentence_text,
    text_block,
)


@pytest.fixture(autouse=True)
def _no_ambient_sdk_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop ambient Anthropic settings such as a base URL or real key."""
    drop_ambient_sdk_settings(monkeypatch)


# --- Pieces --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("length", "requests"),
    [(LIMIT - 1, 1), (LIMIT, 1), (LIMIT + 1, 2)],
    ids=["under-limit", "at-limit", "one-over"],
)
def test_piece_count_at_the_boundary(length: int, requests: int) -> None:
    """Text within the limit is one request; one more character makes two."""
    text = sentence_text(length)

    result, model = clean(NO_FIXES, text, max_chars=LIMIT)

    assert_that(model.sent).is_length(requests)
    if requests == 1:
        assert_that(model.transcripts_sent()).is_equal_to([text])
    assert_that(result.fully_cleaned).is_true()
    assert_that(result.unread_chars).is_equal_to(0)


def test_long_transcript_is_read_in_consecutive_pieces() -> None:
    """Pieces are cut at whitespace, stay within the limit and cover the input."""
    text = sentence_text(LIMIT * 3 + 40)

    result, model = clean(NO_FIXES, text, max_chars=LIMIT)

    sent = model.transcripts_sent()
    assert_that(len(sent)).is_between(4, MAX_PIECES)
    position = 0
    for piece in sent:
        assert_that(len(piece)).is_less_than_or_equal_to(LIMIT)
        start = text.index(piece, max(position - LIMIT // 10, 0))
        assert_that(start).is_less_than_or_equal_to(position)
        assert_that(position - start).is_less_than_or_equal_to(LIMIT // 10)
        assert_that(start == 0 or text[start].isspace()).is_true()
        position = start + len(piece)
        assert_that(position == len(text) or text[position].isspace()).is_true()
    assert_that(position).is_equal_to(len(text))
    assert_that(result.fully_cleaned).is_true()
    assert_that(result.uncleaned_chars).is_equal_to(0)


def test_corrections_from_later_pieces_apply_at_their_place() -> None:
    """A correction in the third piece replaces text in the third piece only."""
    text = sentence_text(LIMIT * 2 + 20) + "Jon Smyth " + sentence_text(100)

    def answer(piece: str) -> str:
        return fixes(("Jon Smyth", "John Smith")) if "Jon Smyth" in piece else NO_FIXES

    result, model = clean(answer, text, max_chars=LIMIT)

    assert_that(len(model.sent)).is_greater_than(2)
    assert_that(result.cleaned_text).is_equal_to(
        text.replace("Jon Smyth", "John Smith"),
    )
    assert_that(applied(result)).is_equal_to(
        [("Jon Smyth", "John Smith", text.index("Jon Smyth"))],
    )


def test_piece_bound_keeps_and_reports_the_unread_rest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """At most ``MAX_PIECES`` requests; the rest is returned as it was."""
    text = sentence_text(LIMIT * (MAX_PIECES + 2)) + " TAIL-MARKER alot"
    caplog.set_level(logging.WARNING, logger=LOGGER)

    result, model = clean(fixes(("TAIL-MARKER alot", "x")), text, max_chars=LIMIT)

    sent = model.transcripts_sent()
    assert_that(sent).is_length(MAX_PIECES)
    read_end = text.index(sent[-1]) + len(sent[-1])
    assert_that(result.unread_chars).is_equal_to(len(text) - read_end)
    assert_that(result.uncleaned_chars).is_equal_to(len(text) - read_end)
    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.cleaned_text[read_end:]).is_equal_to(text[read_end:])
    assert_that(rejection_counts(result)).is_equal_to({"not_found": MAX_PIECES})
    assert_that(result.partial).is_true()
    assert_that(result.fully_cleaned).is_false()
    assert_that(result.success).is_true()
    assert_that(caplog.text).contains(
        f"Transcript cleanup reads {read_end} of {len(text)} chars in "
        f"{MAX_PIECES} pieces",
    )


def test_default_limit_reads_up_to_the_piece_bound() -> None:
    """With the default 100,000-character limit, five pieces are read."""
    text = sentence_text(100_000 * MAX_PIECES + 5_000)

    result, model = clean(NO_FIXES, text)

    sent = model.transcripts_sent()
    assert_that(sent).is_length(MAX_PIECES)
    assert_that(max(len(piece) for piece in sent)).is_less_than_or_equal_to(100_000)
    assert_that(result.unread_chars).is_greater_than_or_equal_to(5_000)
    assert_that(result.unread_chars).is_less_than(5_000 + 200 * MAX_PIECES + 1_000)


def test_error_straddling_a_cut_is_corrected_once() -> None:
    """The overlap lets the next piece see a name split by the cut."""
    text = sentence_text(140) + "xx Jon Smyth went home. " + sentence_text(100)

    def answer(piece: str) -> str:
        return fixes(("Jon Smyth", "John Smith")) if "Jon Smyth" in piece else NO_FIXES

    result, model = clean(answer, text, max_chars=LIMIT)

    assert_that(model.transcripts_sent()[0]).ends_with(" xx Jon")
    assert_that(model.transcripts_sent()[1]).contains("Jon Smyth")
    assert_that(result.cleaned_text).is_equal_to(
        text.replace("Jon Smyth", "John Smith"),
    )
    assert_that(result.correction_count).is_equal_to(1)


def test_same_fix_from_two_pieces_applies_once() -> None:
    """A correction proposed by both overlapping pieces is counted as a duplicate."""
    text = sentence_text(140) + "zzq x " + sentence_text(100)

    def answer(piece: str) -> str:
        return fixes(("zzq x", "zz x")) if "zzq x" in piece else NO_FIXES

    result, model = clean(answer, text, max_chars=LIMIT)

    assert_that(
        [piece for piece in model.transcripts_sent() if "zzq x" in piece],
    ).is_length(2)
    assert_that(result.cleaned_text).is_equal_to(text.replace("zzq x", "zz x"))
    assert_that(rejection_counts(result)).is_equal_to({"duplicate": 1})


def test_different_fixes_from_two_pieces_for_one_place_are_rejected() -> None:
    """Overlapping pieces disagreeing about the same text apply neither fix."""
    text = sentence_text(140) + "zzq x " + sentence_text(100)
    seen: list[str] = []

    def answer(piece: str) -> str:
        if "zzq x" not in piece:
            return NO_FIXES
        seen.append(piece)
        return fixes(("zzq x", "zz x" if len(seen) == 1 else "zzz x"))

    result, _model = clean(answer, text, max_chars=LIMIT)

    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(rejection_counts(result)).is_equal_to({"overlap": 2})


def test_correction_at_a_mid_word_cut_is_rejected() -> None:
    """Without whitespace near the limit, text touching the cut is not corrected."""
    word = "".join(chr(0x4E00 + index) for index in range(200))
    text = word + " " + sentence_text(100)

    def answer(piece: str) -> str:
        return fixes((piece[-3:], "XYZ"), (piece[:3], "QRS"))

    result, model = clean(answer, text, max_chars=LIMIT)

    assert_that(model.transcripts_sent()[:2]).is_equal_to(
        [word[:LIMIT], text[LIMIT : LIMIT + len(model.transcripts_sent()[1])]],
    )
    assert_that(result.rejections.get(CorrectionRejection.AT_CUT)).is_equal_to(2)
    assert_that(result.cleaned_text[: LIMIT + 3]).is_equal_to(
        "QRS" + word[3 : LIMIT + 3],
    )


def test_request_failure_stops_reading_and_keeps_earlier_corrections() -> None:
    """A failed request ends cleanup; earlier pieces' corrections still apply."""
    text = "qat " + sentence_text(LIMIT * 3)
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        if len(calls) == 2:
            return httpx2.Response(503, json={"type": "error"})
        body = json.loads(request.content)
        content: str = body["messages"][0]["content"]
        piece = content.split("<transcript>\n", 1)[1].split("\n</transcript>", 1)[0]
        reply = fixes(("qat ", "cat ")) if piece.startswith("qat ") else NO_FIXES
        return httpx2.Response(
            200,
            json={"type": "message", "content": text_block(reply)},
        )

    with httpx2.Client(transport=httpx2.MockTransport(handler)) as http_client:
        client: anthropic.Anthropic = anthropic.Anthropic(
            api_key=f"synthetic-{token_hex(4)}",
            http_client=http_client,
            max_retries=0,
        )
        result = TranscriptCleaner(client=client).cleanup_transcript(
            text,
            max_chars=LIMIT,
        )

    assert_that(calls).is_length(2)
    assert_that(result.errors).is_length(1)
    assert_that(result.cleaned_text).is_equal_to("cat " + text[4:])
    assert_that(result.cleaned_chars).is_less_than_or_equal_to(LIMIT)
    assert_that(result.unread_chars).is_equal_to(len(text) - result.cleaned_chars)
    assert_that(result.success).is_false()
    assert_that(result.fully_cleaned).is_false()


def test_blank_piece_is_not_sent() -> None:
    """A piece with nothing but whitespace is skipped and the next is read."""
    text = " " * 200 + sentence_text(100)

    result, model = clean(NO_FIXES, text, max_chars=LIMIT)

    assert_that(model.transcripts_sent()).does_not_contain(" " * LIMIT)
    assert_that([piece.strip() for piece in model.transcripts_sent()]).does_not_contain(
        "",
    )
    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.unread_chars).is_equal_to(0)
    assert_that(result.partial).is_true()


def test_blank_input_is_not_sent() -> None:
    """Input of only whitespace has nothing to correct and is not sent."""
    text = " " * 120

    result, model = clean(NO_FIXES, text)

    assert_that(model.sent).is_empty()
    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.unread_chars).is_equal_to(0)
    assert_that(result.fully_cleaned).is_false()


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

    result, model = clean(fixes(("x" * length, "y" * length)), text)

    assert_that(model.sent).is_length(requests)
    assert_that(result.cleaned_text).is_equal_to(
        text if requests == 0 else "y" * length,
    )
    assert_that(result.unread_chars).is_equal_to(length if requests == 0 else 0)
    assert_that(result.success).is_true()


# --- Properties ----------------------------------------------------------------

_ALPHABET = ["a", "b", "漢", " ", "\n", "\t", "\r", "　", "é"]


def _generated_inputs(count: int) -> list[tuple[str, int]]:
    """Return fixed pseudo-varied (text, max_chars) pairs derived from SHA-256."""
    inputs: list[tuple[str, int]] = []
    for case in range(count):
        digest = b"".join(
            hashlib.sha256(f"{case}:{block}".encode()).digest() for block in range(13)
        )
        length = 100 + int.from_bytes(digest[:2], "big") % 301
        max_chars = 20 + int.from_bytes(digest[2:4], "big") % 280
        text = "".join(
            _ALPHABET[byte % len(_ALPHABET)] for byte in digest[4 : 4 + length]
        )
        inputs.append((text, max_chars))
    return inputs


def _arbitrary_fixes(piece: str) -> str:
    """Propose corrections for assorted substrings of ``piece``."""
    proposals: list[tuple[object, object]] = []
    for start in range(0, len(piece), 7):
        original = piece[start : start + 1 + start % 5]
        proposals.append((original, f"<{start}>"))
    proposals.append(("not in the text", "Z"))
    return fixes(*proposals)


def test_text_outside_applied_corrections_is_unchanged() -> None:
    """Property: only applied corrections change text, and only at their place."""
    for text, max_chars in _generated_inputs(120):
        result, model = clean(_arbitrary_fixes, text, max_chars=max_chars)

        assert_only_corrections_changed(text, result)
        assert_that(result.corrections_proposed).is_equal_to(
            result.correction_count + result.corrections_rejected,
        )
        for piece in model.transcripts_sent():
            assert_that(len(piece)).is_less_than_or_equal_to(max_chars)
            assert_that(text).contains(piece)
        assert_that(result.cleaned_chars + result.uncleaned_chars).is_equal_to(
            len(text),
        )


def test_no_corrections_returns_the_input_exactly() -> None:
    """Property: a model that proposes nothing returns every input unchanged."""
    for text, max_chars in _generated_inputs(120):
        result, _model = clean(NO_FIXES, text, max_chars=max_chars)

        assert_that(result.cleaned_text).is_equal_to(text)
        assert_that(result.unread_chars).is_between(0, len(text))
