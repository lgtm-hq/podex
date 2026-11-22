"""Tests for reading long transcripts in pieces and for whole-result properties.

Requests go through the real Anthropic SDK with a mock HTTP transport. The cut
rule kept from ADR 0014 is pinned case by case; limits are literals.
"""

import hashlib
import json
import logging

import httpx2
import pytest
from assertpy import assert_that

from podex.services import llm_transcript_cleanup
from podex.services.llm_transcript_cleanup import CleanupResult
from tests.transcript_cleanup_support import (
    NO_FIXES,
    TRANSCRIPT,
    applied,
    assert_only_corrections_changed,
    clean,
    clean_with,
    drop_ambient_sdk_settings,
    fixes,
    piece_text,
    rejection_counts,
    sentence_text,
    text_block,
)

_LIMIT = 150
_LOGGER = llm_transcript_cleanup.__name__


@pytest.fixture(autouse=True)
def _no_ambient_sdk_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop ambient Anthropic settings such as a base URL or real key."""
    drop_ambient_sdk_settings(monkeypatch)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [
        (record.levelno, record.getMessage())
        for record in caplog.records
        if record.name == _LOGGER and record.levelno >= logging.WARNING
    ]


def _assert_read_whole(text: str, result: CleanupResult) -> None:
    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(result.unread_chars).is_equal_to(0)


# --- The cut rule kept from ADR 0014 ---------------------------------------------

_HEAD_CASES = [
    ("x" * (_LIMIT - 1), _LIMIT - 1),
    ("x" * _LIMIT, _LIMIT),
    ("x" * _LIMIT + "Y", _LIMIT),
    ("a" * _LIMIT + "TAIL-MARKER" + "b" * (3 * _LIMIT - 11), _LIMIT),
    ("漢" * 120, 120),
    ("漢" * (_LIMIT + 1), _LIMIT),
]


@pytest.mark.parametrize(
    ("text", "head_chars"),
    _HEAD_CASES,
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
    """The first piece ends at the cut; the rest is read and kept in place."""
    result, model = clean(NO_FIXES, text, max_chars=_LIMIT)

    assert_that(model.transcripts_sent()[0]).is_equal_to(text[:head_chars])
    assert_that(len(model.sent) > 1).is_equal_to(head_chars < len(text))
    _assert_read_whole(text, result)
    assert_that(result.fully_cleaned).is_true()


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
    """The first piece ends at the window's last whitespace, otherwise the limit."""
    result, model = clean(NO_FIXES, text, max_chars=_LIMIT)

    assert_that(model.transcripts_sent()[0]).is_equal_to(text[:head_chars])
    _assert_read_whole(text, result)


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
    result, model = clean(NO_FIXES, text)

    assert_that(model.transcripts_sent()[0]).is_equal_to(text[:head_chars])
    _assert_read_whole(text, result)


def test_transcript_at_the_limit_is_sent_whole(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A transcript exactly ``max_chars`` long is neither cut nor reported."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    _result, model = clean(NO_FIXES, TRANSCRIPT, max_chars=len(TRANSCRIPT))

    assert_that(model.transcripts_sent()).is_equal_to([TRANSCRIPT])
    assert_that([r for r in caplog.records if r.name == _LOGGER]).is_empty()


def test_long_transcript_is_truncated_before_sending(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No request carries more than ``max_chars``; a full read logs no warning."""
    long_text = "a" * 150 + "TAIL-MARKER" + "b" * 50
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    result, model = clean(NO_FIXES, long_text, max_chars=_LIMIT)

    sent = model.transcripts_sent()
    assert_that(sent).is_length(2)
    assert_that([len(piece) <= _LIMIT for piece in sent]).does_not_contain(False)
    _assert_read_whole(long_text, result)
    assert_that([r for r in caplog.records if r.name == _LOGGER]).is_empty()


def test_cut_at_whitespace_is_logged_with_the_head_length(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only text left unread is reported, with the characters actually read."""
    text = sentence_text(_LIMIT * 7)
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    result, model = clean(NO_FIXES, text, max_chars=_LIMIT)

    last = model.transcripts_sent()[-1]
    read = text.index(last) + len(last)
    assert_that(result.unread_chars).is_equal_to(len(text) - read)
    assert_that(_warnings(caplog)).is_equal_to(
        [
            (
                logging.WARNING,
                f"Transcript cleanup reads {read} of {len(text)} chars in 5 pieces",
            ),
        ],
    )


def test_text_after_the_limit_is_not_lost() -> None:
    """Reproduction: a marker placed after ``max_chars`` survives cleanup."""
    long_text = "a" * 150 + "TAIL-MARKER" + "b" * 50

    result, _model = clean(NO_FIXES, long_text, max_chars=_LIMIT)

    assert_that(result.cleaned_text).is_equal_to(long_text)


def test_marker_after_the_cut_is_returned_after_the_cleaned_head() -> None:
    """A marker beyond the limit appears exactly once, where it was."""
    long_text = "a" * _LIMIT + "TAIL-MARKER" + "b" * 50

    result, _model = clean(fixes(("TAIL-MARKER", "tail-marker")), long_text)

    assert_that(result.cleaned_text.count("TAIL-MARKER")).is_equal_to(0)
    assert_that(result.cleaned_text).ends_with("tail-marker" + "b" * 50)


def test_blank_head_of_non_blank_input_is_not_sent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A blank piece is not sent; the pieces after it are."""
    text = " " * 200 + "x" * 50
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    result, model = clean(NO_FIXES, text, max_chars=_LIMIT)

    assert_that([piece.strip() for piece in model.transcripts_sent()]).does_not_contain(
        "",
    )
    assert_that(model.sent).is_not_empty()
    _assert_read_whole(text, result)
    assert_that(result.partial).is_true()
    assert_that(_warnings(caplog)).is_empty()


def test_blank_input_is_not_sent() -> None:
    """Input of only whitespace has nothing to correct and is not sent."""
    text = " " * 120

    result, model = clean(NO_FIXES, text)

    assert_that(model.sent).is_empty()
    _assert_read_whole(text, result)
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
    text = "x" * (length - 1) + "q"

    result, model = clean(fixes(("q", "Q")), text)

    assert_that(model.sent).is_length(requests)
    assert_that(result.cleaned_text).is_equal_to(
        text if requests == 0 else text[:-1] + "Q",
    )
    assert_that(result.unread_chars).is_equal_to(length if requests == 0 else 0)
    assert_that(result.success).is_true()


def test_short_transcript_is_reported_as_not_cleaned() -> None:
    """Text skipped for being short is returned whole and not counted as cleaned."""
    result, model = clean(NO_FIXES, "tiny", max_chars=2)

    assert_that(model.sent).is_empty()
    assert_that(result.cleaned_text).is_equal_to("tiny")
    assert_that(result.cleaned_chars).is_equal_to(0)
    assert_that(result.uncleaned_chars).is_equal_to(4)
    assert_that(result.fully_cleaned).is_false()


# --- Pieces --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("length", "requests"),
    [(_LIMIT - 1, 1), (_LIMIT, 1), (_LIMIT + 1, 2)],
    ids=["under-limit", "at-limit", "one-over"],
)
def test_piece_count_at_the_boundary(length: int, requests: int) -> None:
    """Text within the limit is one request; one more character makes two."""
    text = sentence_text(length)

    result, model = clean(NO_FIXES, text, max_chars=_LIMIT)

    assert_that(model.sent).is_length(requests)
    if requests == 1:
        assert_that(model.transcripts_sent()).is_equal_to([text])
    assert_that(result.fully_cleaned).is_true()


def test_long_transcript_is_read_in_consecutive_pieces() -> None:
    """Pieces are cut at whitespace, stay within the limit and cover the input."""
    text = sentence_text(_LIMIT * 3 + 40)

    result, model = clean(NO_FIXES, text, max_chars=_LIMIT)

    sent = model.transcripts_sent()
    assert_that(len(sent)).is_between(4, 5)
    position = 0
    for piece in sent:
        assert_that(len(piece)).is_less_than_or_equal_to(_LIMIT)
        start = text.index(piece)
        assert_that(start).is_less_than_or_equal_to(position)
        assert_that(position - start).is_less_than_or_equal_to(_LIMIT // 10)
        assert_that(start == 0 or text[start].isspace()).is_true()
        position = start + len(piece)
        assert_that(position == len(text) or text[position].isspace()).is_true()
    assert_that(position).is_equal_to(len(text))
    assert_that(result.fully_cleaned).is_true()


def test_corrections_from_later_pieces_apply_at_their_place() -> None:
    """A correction in the third piece replaces text in the third piece only."""
    text = sentence_text(_LIMIT * 2 + 20) + "Jon Smyth " + sentence_text(100)

    def answer(piece: str) -> str:
        return fixes(("Jon Smyth", "John Smith")) if "Jon Smyth" in piece else NO_FIXES

    result, model = clean(answer, text, max_chars=_LIMIT)

    assert_that(len(model.sent)).is_greater_than(2)
    assert_that(applied(result)).is_equal_to(
        [("Jon Smyth", "John Smith", text.index("Jon Smyth"))],
    )


def test_at_most_five_pieces_and_the_unread_rest_is_kept() -> None:
    """At most five requests; the rest is returned as it was and reported."""
    text = sentence_text(_LIMIT * 7) + " TAIL-MARKER alot"

    result, model = clean(
        fixes(("TAIL-MARKER alot", "TAIL-MARKER a lot")), text, max_chars=_LIMIT
    )

    sent = model.transcripts_sent()
    assert_that(sent).is_length(5)
    read_end = text.index(sent[-1]) + len(sent[-1])
    assert_that(result.unread_chars).is_equal_to(len(text) - read_end)
    assert_that(result.uncleaned_chars).is_equal_to(len(text) - read_end)
    assert_that(result.cleaned_text).is_equal_to(text)
    assert_that(rejection_counts(result)).is_equal_to({"not_found": 5})
    assert_that(result.partial).is_true()
    assert_that(result.fully_cleaned).is_false()
    assert_that(result.success).is_true()


def test_default_limit_reads_up_to_five_pieces() -> None:
    """With the default 100,000-character limit, five pieces are read."""
    text = sentence_text(505_000)

    result, model = clean(NO_FIXES, text)

    sent = model.transcripts_sent()
    assert_that(sent).is_length(5)
    assert_that(max(len(piece) for piece in sent)).is_less_than_or_equal_to(100_000)
    assert_that(result.unread_chars).is_between(5_000, 5_000 + 200 * 5 + 1_000)


@pytest.mark.parametrize("max_chars", [1, 5, 9, 10, 50, 99])
def test_limits_under_a_hundred_send_one_request(max_chars: int) -> None:
    """A limit below 100 reads one piece, as before pieces existed."""
    text = sentence_text(500)

    result, model = clean(NO_FIXES, text, max_chars=max_chars)

    assert_that(model.sent).is_length(1)
    assert_that(len(model.transcripts_sent()[0])).is_less_than_or_equal_to(max_chars)
    assert_that(result.unread_chars).is_equal_to(
        len(text) - len(model.transcripts_sent()[0]),
    )


def test_limit_of_a_hundred_reads_several_pieces() -> None:
    """From a limit of 100, a longer transcript is read in pieces."""
    _result, model = clean(NO_FIXES, sentence_text(300), max_chars=100)

    assert_that(len(model.sent)).is_greater_than(1)


# --- Overlap between pieces -------------------------------------------------------


def test_error_straddling_a_cut_is_corrected_once() -> None:
    """The overlap lets the next piece see a name split by the cut."""
    text = sentence_text(140) + "xx Jon Smyth went home. " + sentence_text(100)

    def answer(piece: str) -> str:
        return fixes(("Jon Smyth", "John Smith")) if "Jon Smyth" in piece else NO_FIXES

    result, model = clean(answer, text, max_chars=_LIMIT)

    assert_that(model.transcripts_sent()[0]).ends_with(" xx Jon")
    assert_that(model.transcripts_sent()[1]).contains("Jon Smyth")
    assert_that(result.cleaned_text).is_equal_to(
        text.replace("Jon Smyth", "John Smith"),
    )
    assert_that(result.correction_count).is_equal_to(1)


def test_competing_fixes_in_the_overlap_keep_the_owning_piece() -> None:
    """Reproduction: two contexts for one error no longer cancel each other.

    Piece one ends at "said alot" and quotes its left context; piece two
    quotes its right context. The overlapping text belongs to piece two, so
    its fix applies and piece one's is dropped.
    """
    text = sentence_text(140) + "said alot about it " + sentence_text(100)
    replies = iter(
        [
            fixes(("said alot", "said a lot")),
            fixes(("alot about", "a lot about")),
        ],
    )

    def answer(_piece: str) -> str:
        return next(replies, NO_FIXES)

    result, model = clean(answer, text, max_chars=_LIMIT)

    assert_that(model.transcripts_sent()[0]).ends_with("said alot")
    assert_that(model.transcripts_sent()[1]).contains("alot about")
    assert_that(result.cleaned_text).is_equal_to(
        text.replace("said alot about", "said a lot about"),
    )
    assert_that(rejection_counts(result)).is_equal_to({"superseded": 1})


def test_same_fix_from_two_pieces_applies_once() -> None:
    """A fix proposed by both overlapping pieces is taken from its owner only."""
    text = sentence_text(140) + "zzq x " + sentence_text(100)

    def answer(piece: str) -> str:
        return fixes(("zzq x", "zz x")) if "zzq x" in piece else NO_FIXES

    result, model = clean(answer, text, max_chars=_LIMIT)

    assert_that(
        [piece for piece in model.transcripts_sent() if "zzq x" in piece],
    ).is_length(2)
    assert_that(result.cleaned_text).is_equal_to(text.replace("zzq x", "zz x"))
    assert_that(rejection_counts(result)).is_equal_to({"not_owned": 1})


def test_different_fixes_for_one_place_keep_the_owner_s() -> None:
    """Overlapping pieces disagreeing about the same text: the owner's applies."""
    text = sentence_text(140) + "zzq x " + sentence_text(100)
    seen: list[str] = []

    def answer(piece: str) -> str:
        if "zzq x" not in piece:
            return NO_FIXES
        seen.append(piece)
        return fixes(("zzq x", "zz x" if len(seen) == 1 else "zzz x"))

    result, _model = clean(answer, text, max_chars=_LIMIT)

    assert_that(result.cleaned_text).is_equal_to(text.replace("zzq x", "zzz x"))
    assert_that(rejection_counts(result)).is_equal_to({"not_owned": 1})


def test_correction_at_a_mid_word_cut_is_rejected() -> None:
    """Without whitespace near the limit, text touching the cut is not corrected."""
    word = "".join(chr(0x4E00 + index) for index in range(200))
    text = word + " " + sentence_text(100)

    def answer(piece: str) -> str:
        return fixes((piece[-3:], "XYZ"), (piece[:3], "QRS"))

    result, model = clean(answer, text, max_chars=_LIMIT)

    assert_that(model.transcripts_sent()[0]).is_equal_to(word[:_LIMIT])
    assert_that(rejection_counts(result).get("at_cut")).is_equal_to(2)
    assert_that(result.cleaned_text[: _LIMIT + 3]).is_equal_to(
        "QRS" + word[3 : _LIMIT + 3],
    )


def test_request_failure_stops_reading_and_keeps_earlier_corrections() -> None:
    """A failed request ends cleanup; earlier pieces' corrections still apply."""
    text = "qat " + sentence_text(_LIMIT * 3)
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        if len(calls) == 2:
            return httpx2.Response(503, json={"type": "error"})
        piece = piece_text(json.loads(request.content))
        reply = fixes(("qat ", "cat ")) if piece.startswith("qat ") else NO_FIXES
        return httpx2.Response(
            200,
            json={
                "type": "message",
                "content": text_block(reply),
                "stop_reason": "end_turn",
            },
        )

    result = clean_with(handler, text, max_chars=_LIMIT)

    assert_that(calls).is_length(2)
    assert_that(result.errors).is_length(1)
    assert_that(result.cleaned_text).is_equal_to("cat " + text[4:])
    assert_that(result.cleaned_chars).is_less_than_or_equal_to(_LIMIT)
    assert_that(result.unread_chars).is_equal_to(len(text) - result.cleaned_chars)
    assert_that(result.success).is_false()
    assert_that(result.fully_cleaned).is_false()


# --- Properties ----------------------------------------------------------------

_ALPHABET = ["a", "b", "漢", " ", "\n", "\t", "\r", "　", "é"]


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


def _proposals(piece: str, step: int) -> str:
    """Propose corrections for substrings of ``piece`` every ``step`` characters."""
    proposals: list[tuple[object, object]] = []
    for start in range(0, len(piece), step):
        original = piece[start : start + 1 + start % 5]
        proposals.append((original, f"<{start}>"))
    proposals.append(("not in the text", "Z"))
    return fixes(*proposals)


@pytest.mark.parametrize("step", [3, 40], ids=["dense", "sparse"])
def test_any_reply_changes_only_corrected_text(step: int) -> None:
    """Property: only accepted corrections change text, within the budget."""
    for text, max_chars in _generated_inputs(120):
        result, _model = clean(
            lambda piece: _proposals(piece, step),
            text,
            max_chars=max_chars,
        )

        assert_only_corrections_changed(text, result)
        covered = sum(len(c.original) for c in result.corrections_made)
        assert_that(covered * 100).is_less_than_or_equal_to(15 * result.cleaned_chars)
        assert_that(result.corrections_proposed).is_equal_to(
            result.correction_count + result.corrections_rejected,
        )
        assert_that(result.cleaned_chars + result.uncleaned_chars).is_equal_to(
            len(text),
        )


def test_echoed_head_always_returns_the_whole_input() -> None:
    """Property: a model that proposes nothing returns every input unchanged."""
    for text, max_chars in _generated_inputs(150):
        result, model = clean(NO_FIXES, text, max_chars=max_chars)

        assert_that(result.cleaned_text).is_equal_to(text)
        for piece in model.transcripts_sent():
            assert_that(len(piece)).is_less_than_or_equal_to(max_chars)
            assert_that(text).contains(piece)
