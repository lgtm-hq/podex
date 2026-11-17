"""Windowed extraction: replies, merging, failures and run bounds.

Failures are scripted per window, through a model double or through the real
SDK client behind a mock transport, to check that one window's reply never
discards another window's items and that coverage reports what was not read.
"""

import json
import time
from collections.abc import Callable
from dataclasses import fields
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from assertpy import assert_that

from podex.models.media import MediaType
from podex.services.llm_extraction import ExtractedMedia
from tests.extraction_window_support import (
    STRIDE,
    Provider,
    WindowModel,
    book_item,
    filler,
    items_reply,
    make_segments,
    marked_four_windows,
    mention,
    padded,
    run_model,
    run_sdk,
    status_answer,
    success_answer,
    text_reply,
    titles_of,
)

_PRIVATE_TEXT = "PRIVATE-REFUSAL-EXPLANATION"


def test_one_mention_at_distant_points_is_reported_once() -> None:
    """Repeats of one work in distant windows, cased differently, merge."""
    first = 100
    last = (295_800 + 445_799) // 2 // STRIDE
    segments = make_segments(
        4500,
        {first: padded(mention("Dune 1965")), last: padded(mention("dune 1965"))},
    )
    model = WindowModel()

    result = run_model(segments, model)

    assert_that(model.texts[0]).contains(mention("Dune 1965"))
    assert_that(model.texts[1]).does_not_contain(mention("dune 1965"))
    assert_that(model.texts[2]).contains(mention("dune 1965"))
    assert_that(titles_of(result)).is_equal_to(["Dune 1965"])
    assert_that(result.items[0].confidence).is_equal_to(0.9)


def test_reply_stopped_at_the_token_cap_is_a_failure_not_a_parse() -> None:
    """A reply cut off at the output cap is recorded with its own reason."""
    complete = json.dumps([{"title": "Dune", "type": "book"}])
    segments = [{"text": filler(0, 99)}, {"text": mention("Ignored 1")}]

    for stop_reason in ("max_tokens", "model_context_window_exceeded"):
        model = WindowModel({0: text_reply(complete, stop_reason=stop_reason)})
        result = run_model(segments, model)

        assert_that(result.items).is_empty()
        assert_that(result.errors).is_equal_to(["extraction: truncated_response"])
        assert_that(result.failed_windows).is_equal_to(1)
        assert_that(result.fully_covered).is_false()
        assert_that(result.unextracted_chars).is_equal_to(result.transcript_chars)


def test_truncated_window_keeps_the_other_windows_mentions() -> None:
    """A capped reply in one window does not discard the other windows."""
    model = WindowModel({1: text_reply('[{"title": "Cut', stop_reason="max_tokens")})

    result = run_model(marked_four_windows(), model)

    assert_that(result.errors).is_equal_to(["extraction: truncated_response"])
    assert_that(titles_of(result)).contains("Before 1", "After 3")
    assert_that(result.failed_windows).is_equal_to(1)
    assert_that(result.unextracted_ranges).is_equal_to([(149_999, 295_800)])


def test_one_failing_window_keeps_the_others_and_reports_its_text() -> None:
    """A failed request among several keeps other items and counts its text."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error: Exception = anthropic.APIStatusError(
        "boom", response=httpx2.Response(500, request=request), body=None
    )
    model = WindowModel({2: error})

    result = run_model(marked_four_windows(), model)

    assert_that(result.windows).is_equal_to(4)
    assert_that(result.failed_windows).is_equal_to(1)
    assert_that(result.errors).is_equal_to(
        ["extraction: APIStatusError (HTTP 500); request_failed"]
    )
    assert_that(result.success).is_false()
    assert_that(result.fully_covered).is_false()
    assert_that(result.unextracted_ranges).is_equal_to([(297_899, 443_700)])
    assert_that(result.unextracted_chars).is_equal_to(145_801)
    assert_that(result.unread_chars).is_zero()
    titles = titles_of(result)
    assert_that(titles).contains("Before 1", "Across 1", "After 3", "Inside 3")
    # Only the failed window held these whole.
    assert_that(titles).contains("Inside 2", "Inside 3")
    assert_that(titles).does_not_contain("Across 2", "After 2", "Before 3")


def test_unparsable_window_keeps_the_others() -> None:
    """A reply without a JSON array fails only its own window."""
    model = WindowModel({0: text_reply("no array here")})

    result = run_model(marked_four_windows(), model)

    assert_that(result.errors).is_equal_to(["extraction: missing_json_array"])
    assert_that(titles_of(result)).contains("After 1", "After 3")
    assert_that(titles_of(result)).does_not_contain("Before 1")
    assert_that(result.unextracted_ranges).is_equal_to([(0, 147_900)])


def test_one_reply_keeps_the_first_item_of_each_title() -> None:
    """Within one reply the first item of a title wins, as before windows."""
    model = WindowModel(
        {
            0: items_reply(
                book_item(title="Dune", confidence=0.6),
                book_item(
                    title="dune", creator="Frank Herbert", year=1965, confidence=0.95
                ),
            )
        }
    )

    result = run_model([{"text": filler(0, 200)}], model)

    assert_that(result.items).is_equal_to(
        [ExtractedMedia(title="Dune", media_type=MediaType.BOOK, confidence=0.6)]
    )


@pytest.mark.parametrize(
    ("first", "third", "expected"),
    [
        (
            book_item(title="Dune", confidence=0.55),
            book_item(
                title="Dune", creator="Frank Herbert", year=1965, confidence=0.95
            ),
            ExtractedMedia("Dune", MediaType.BOOK, "Frank Herbert", 1965, 0.95),
        ),
        (
            book_item(title="Dune", creator="Frank Herbert", confidence=0.9),
            book_item(title="dune", year=1965, confidence=0.7),
            ExtractedMedia("Dune", MediaType.BOOK, "Frank Herbert", 1965, 0.9),
        ),
        (
            book_item(title="Dune", year=1965, confidence=0.6),
            book_item(title="DUNE", creator="Frank Herbert", confidence=0.8),
            ExtractedMedia("DUNE", MediaType.BOOK, "Frank Herbert", 1965, 0.8),
        ),
        (
            book_item(title="Dune", creator="Frank Herbert", year=1965, confidence=0.9),
            book_item(
                title="Dune", creator="Brian Herbert", year=1999, confidence=0.95
            ),
            ExtractedMedia("Dune", MediaType.BOOK, "Brian Herbert", 1999, 0.95),
        ),
        (
            book_item(title="Dune", creator="Frank Herbert", confidence=0.8),
            book_item(title="Dune", creator="Brian Herbert", year=1999, confidence=0.8),
            ExtractedMedia("Dune", MediaType.BOOK, "Frank Herbert", 1999, 0.8),
        ),
        (
            book_item(title="Dune", confidence=0.6),
            {"title": "Dune", "type": "movie", "year": 2021, "confidence": 0.9},
            ExtractedMedia("Dune", MediaType.MOVIE, None, 2021, 0.9),
        ),
    ],
    ids=[
        "higher-confidence-later",
        "missing-fields-on-each-side",
        "missing-fields-in-the-best",
        "conflict-highest-confidence-wins",
        "tie-first-found-wins",
        "type-of-highest-confidence",
    ],
)
def test_merge_keeps_the_most_confident_item_and_fills_missing_fields(
    first: dict[str, Any],
    third: dict[str, Any],
    expected: ExtractedMedia,
) -> None:
    """Across windows the most confident item wins and gaps are filled."""
    model = WindowModel(
        {
            0: items_reply(book_item(title="Arrival", confidence=0.9), first),
            1: items_reply(),
            2: items_reply(third),
            3: items_reply(),
        }
    )

    result = run_model(make_segments(4500), model)

    assert_that(result.items).is_equal_to(
        [
            ExtractedMedia("Arrival", MediaType.BOOK, confidence=0.9),
            expected,
        ]
    )


def test_merge_fills_every_optional_item_field() -> None:
    """The merge fills creator and year, the only fields an item may lack."""
    optional = {
        item_field.name
        for item_field in fields(ExtractedMedia)
        if item_field.default is None
    }

    assert_that(optional).is_equal_to({"creator", "year"})


def test_merge_keeps_first_appearance_order() -> None:
    """An item keeps the position of its first appearance in the transcript."""
    model = WindowModel(
        {
            0: items_reply(book_item(title="Arrival", confidence=0.5)),
            1: items_reply(
                book_item(title="Dune", confidence=0.6),
                book_item(title="Arrival", confidence=0.95),
            ),
            2: items_reply(book_item(title="Contact", confidence=0.7)),
            3: items_reply(book_item(title="Dune", confidence=0.99)),
        }
    )

    result = run_model(make_segments(4500), model)

    assert_that([(item.title, item.confidence) for item in result.items]).is_equal_to(
        [("Arrival", 0.95), ("Dune", 0.99), ("Contact", 0.7)]
    )


@pytest.mark.parametrize(
    "stop_reason",
    [["max_tokens"], {"type": "max_tokens"}, 7, True],
    ids=["list", "object", "number", "boolean"],
)
def test_malformed_stop_reason_fails_only_its_window(stop_reason: object) -> None:
    """A stop reason that is not a string is a malformed reply for its window."""
    provider = Provider({1: success_answer(text="[]", stop_reason=stop_reason)})

    result = run_sdk(marked_four_windows(), provider)

    assert_that(provider.requests).is_equal_to(4)
    assert_that(result.errors).is_equal_to(["extraction: malformed_response"])
    assert_that(result.failed_windows).is_equal_to(1)
    assert_that(titles_of(result)).contains("Before 1", "After 3")
    assert_that(result.unextracted_ranges).is_equal_to([(149_999, 295_800)])
    assert_that(result.success).is_false()


def test_malformed_stop_reason_in_a_single_request_is_reported() -> None:
    """A single request with a non-string stop reason returns, not raises."""
    payload = json.dumps([{"title": "Dune", "type": "book"}])
    provider = Provider({0: success_answer(text=payload, stop_reason=["max_tokens"])})

    result = run_sdk([{"text": filler(0, 200)}], provider)

    assert_that(result.items).is_empty()
    assert_that(result.errors).is_equal_to(["extraction: malformed_response"])


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (success_answer(content=[], stop_reason="refusal"), "refused_response"),
        (
            success_answer(
                text=f"I can't help: {_PRIVATE_TEXT}", stop_reason="refusal"
            ),
            "refused_response",
        ),
        (
            success_answer(
                text='[{"title": "X 1", "type": "book"}]', stop_reason="refusal"
            ),
            "refused_response",
        ),
        (success_answer(content=[], stop_reason="end_turn"), "empty_response"),
        (success_answer(text="   ", stop_reason="end_turn"), "empty_response"),
        (
            success_answer(
                content=[{"type": "thinking", "thinking": "x", "signature": "s"}]
            ),
            "empty_response",
        ),
    ],
    ids=[
        "refusal-no-content",
        "refusal-with-text",
        "refusal-with-array",
        "no-content",
        "blank-text",
        "no-text-block",
    ],
)
def test_refused_or_empty_reply_is_a_failed_window(
    caplog: pytest.LogCaptureFixture,
    answer: Callable[[str], httpx2.Response],
    error: str,
) -> None:
    """A refusal or a reply without text is not a successful empty answer."""
    provider = Provider({1: answer})

    result = run_sdk(marked_four_windows(), provider)

    assert_that(result.errors).is_equal_to([f"extraction: {error}"])
    assert_that(result.failed_windows).is_equal_to(1)
    assert_that(result.unextracted_ranges).is_equal_to([(149_999, 295_800)])
    assert_that(result.unextracted_chars).is_equal_to(145_801)
    assert_that(result.fully_covered).is_false()
    assert_that(result.success).is_false()
    assert_that(titles_of(result)).contains("Before 1", "After 3")
    assert_that(titles_of(result)).does_not_contain("X 1")
    assert_that(str(result.errors) + caplog.text).does_not_contain(_PRIVATE_TEXT)


def test_empty_array_is_a_successful_window_without_items() -> None:
    """The JSON array ``[]`` is a valid answer with no items."""
    provider = Provider({1: success_answer(text="[]")})

    result = run_sdk(marked_four_windows(), provider)

    assert_that(result.errors).is_empty()
    assert_that(result.fully_covered).is_true()
    assert_that(result.success).is_true()


@pytest.mark.parametrize(
    ("code", "error_type", "name"),
    [
        (400, "invalid_request_error", "BadRequestError"),
        (401, "authentication_error", "AuthenticationError"),
        (403, "permission_error", "PermissionDeniedError"),
        (404, "not_found_error", "NotFoundError"),
    ],
)
def test_failure_that_repeats_for_every_window_stops_the_run(
    code: int,
    error_type: str,
    name: str,
) -> None:
    """After a failure every window would repeat, no further window is sent."""
    provider = Provider({2: status_answer(code, error_type)})

    result = run_sdk(marked_four_windows(), provider)

    assert_that(provider.requests).is_equal_to(3)
    assert_that(result.windows).is_equal_to(3)
    assert_that(result.errors).is_equal_to(
        [f"extraction: {name} (HTTP {code}); request_failed"]
    )
    assert_that(result.unread_chars).is_equal_to(4_200)
    assert_that(result.window_limit_reached).is_false()
    assert_that(result.unextracted_ranges).is_equal_to([(297_899, 449_999)])
    assert_that(titles_of(result)).contains("Before 1", "Before 2", "Inside 2")
    assert_that(titles_of(result)).does_not_contain("After 2", "After 3")


def test_failure_in_the_first_window_that_repeats_sends_nothing_more() -> None:
    """An authentication failure on the first window ends the run there."""
    provider = Provider({0: status_answer(401, "authentication_error")})

    result = run_sdk(marked_four_windows(), provider)

    assert_that(provider.requests).is_equal_to(1)
    assert_that(result.items).is_empty()
    assert_that(result.unread_chars).is_equal_to(300_000)
    assert_that(result.unextracted_ranges).is_equal_to([(0, 449_999)])


def test_transient_failure_is_retried_and_later_windows_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 500 is retried twice by the SDK and does not stop later windows."""
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    provider = Provider({1: status_answer(500, "api_error")})

    result = run_sdk(marked_four_windows(), provider)

    assert_that(provider.requests).is_equal_to(6)
    assert_that(sleeps).is_length(2)
    assert_that(result.errors).is_equal_to(
        ["extraction: InternalServerError (HTTP 500); request_failed"]
    )
    assert_that(result.windows).is_equal_to(4)
    assert_that(titles_of(result)).contains("Before 1", "After 3")


def test_retries_bound_requests_and_characters_per_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eight failing windows make at most 24 requests and 3.6M characters."""
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    provider = Provider(dict.fromkeys(range(8), status_answer(500, "api_error")))

    result = run_sdk(make_segments(14_000), provider)

    assert_that(provider.requests).is_equal_to(24)
    assert_that(sleeps).is_length(16)
    assert_that(provider.sent_chars).is_equal_to(3_599_976)
    assert_that(provider.sent_chars).is_less_than_or_equal_to(3_600_000)
    assert_that(result.errors).is_length(8)
    assert_that(result.unextracted_ranges).is_equal_to([(0, 1_399_999)])


@pytest.mark.parametrize(
    ("reply", "name"),
    [
        (
            text_reply(
                '[{"title": "Big 1", "type": "book", "confidence": 1' + "0" * 400 + "}]"
            ),
            "OverflowError",
        ),
        (text_reply("[" * 100_000 + "]" * 100_000), "RecursionError"),
    ],
    ids=["overflowing-number", "deep-nesting"],
)
def test_reply_that_breaks_decoding_fails_only_its_window(
    reply: SimpleNamespace,
    name: str,
) -> None:
    """A reply that raises while it is decoded fails its window, not the run."""
    model = WindowModel({1: reply})

    result = run_model(marked_four_windows(), model)

    assert_that(result.errors).is_equal_to(
        [f"extraction: {name}; unclassified_failure"]
    )
    assert_that(titles_of(result)).contains("Before 1", "After 3")
    assert_that(result.unextracted_ranges).is_equal_to([(149_999, 295_800)])


def test_client_type_error_fails_its_window() -> None:
    """A client that raises ``TypeError`` fails the window, not the run."""
    model = WindowModel({0: TypeError("client fault")})

    result = run_model(marked_four_windows(), model)

    assert_that(result.errors).is_equal_to(
        ["extraction: TypeError; unclassified_failure"]
    )
    assert_that(titles_of(result)).contains("After 3")


def test_other_exceptions_propagate_as_for_a_single_request() -> None:
    """Exceptions outside the decoding families propagate unchanged."""
    model = WindowModel({1: OSError("client fault")})

    with pytest.raises(OSError):
        run_model(marked_four_windows(), model)

    assert_that(model.texts).is_length(2)
