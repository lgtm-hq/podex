"""Windowed extraction: whole-transcript coverage, bounds and partial results."""

import json
import re
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2 as httpx
import pytest
from assertpy import assert_that

from podex.services.llm_extraction import (
    EXTRACTION_WINDOW_CHARS,
    EXTRACTION_WINDOW_OVERLAP_CHARS,
    MAX_EXTRACTION_WINDOWS,
    LLMExtractor,
)

_SEGMENT_CHARS = 99
_STRIDE = _SEGMENT_CHARS + 1
_FILLER = ("the hosts talk about their week and the weather " * 3)[:_SEGMENT_CHARS]
_MENTION = re.compile(r"I recommend ([A-Z][a-z]+ [0-9]+) wholeheartedly\.")
_WRAP_START = "<transcript>\n"
_WRAP_END = "\n</transcript>"


def _reply(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    """Return a Messages API response double with one text block."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
    )


class _WindowModel:
    """Model double that names every complete mention sentence it is sent.

    A mention is found only when its whole sentence is inside the window, so
    a sentence cut by a window boundary is found only by a window holding it
    whole. ``replies`` overrides the reply (or raises) for a 0-based window.
    """

    def __init__(self, replies: dict[int, SimpleNamespace | Exception] | None = None):
        self.replies = replies or {}
        self.texts: list[str] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        """Record the window text and reply with the mentions it holds."""
        content = str(kwargs["messages"][0]["content"])
        assert_that(content).starts_with(_WRAP_START).ends_with(_WRAP_END)
        text = content[len(_WRAP_START) : -len(_WRAP_END)]
        index = len(self.texts)
        self.texts.append(text)
        override = self.replies.get(index)
        if isinstance(override, Exception):
            raise override
        if override is not None:
            return override
        found = [
            {"title": title, "type": "book", "confidence": round(0.9 - index / 20, 2)}
            for title in _MENTION.findall(text)
        ]
        return _reply(json.dumps(found))

    def offsets(self, full_text: str) -> list[tuple[int, int]]:
        """Return the ``(start, end)`` of each sent window in ``full_text``."""
        ranges: list[tuple[int, int]] = []
        position = 0
        for text in self.texts:
            start = full_text.index(text, position)
            ranges.append((start, start + len(text)))
            position = start + 1
        return ranges


def _run(
    segments: list[dict[str, str]],
    model: _WindowModel,
    **kwargs: int,
) -> Any:
    """Run the extractor over ``segments`` against ``model``."""
    client = SimpleNamespace(messages=model)
    return LLMExtractor(client=client).extract_from_transcript(segments, **kwargs)


def _padded(text: str, *, at_end: bool = False) -> str:
    """Pad ``text`` with filler to exactly one segment length."""
    pad = "~" * (_SEGMENT_CHARS - len(text) - 1)
    return f"{pad} {text}" if at_end else f"{text} {pad}"


def _mention(title: str) -> str:
    """Return the sentence the model double recognises for ``title``."""
    return f"I recommend {title} wholeheartedly."


def _segments(count: int, marks: dict[int, str] | None = None) -> list[dict[str, str]]:
    """Build ``count`` distinct segments of equal length, with ``marks``."""
    marks = marks or {}
    return [
        {"text": marks.get(index, _filler(index, _SEGMENT_CHARS))}
        for index in range(count)
    ]


def _filler(index: int, length: int) -> str:
    """Return distinct filler of ``length`` characters for segment ``index``."""
    return (f"seg{index:05d} " + _FILLER * 4)[: length - 1] + "."


def _joined(segments: list[dict[str, str]]) -> str:
    """Join segments the way the extractor does."""
    return " ".join(seg["text"].strip() for seg in segments if seg["text"])


def _plan(segments: list[dict[str, str]], **kwargs: int) -> list[tuple[int, int]]:
    """Return the windows the extractor reads for ``segments``."""
    model = _WindowModel()
    _run(segments, model, **kwargs)
    return model.offsets(_joined(segments))


def _boundary_marks(windows: list[tuple[int, int]]) -> dict[int, str]:
    """Place one mention before, in, across and after each window boundary.

    Segment ``k`` occupies ``[k * _STRIDE, k * _STRIDE + _SEGMENT_CHARS)``.
    """
    marks: dict[int, str] = {}
    for number, ((_, end), (next_start, _)) in enumerate(
        zip(windows, windows[1:], strict=False), start=1
    ):
        last = (end - _SEGMENT_CHARS) // _STRIDE
        marks[(next_start - 1000) // _STRIDE] = _padded(_mention(f"Before {number}"))
        marks[(next_start + end) // 2 // _STRIDE] = _padded(
            _mention(f"Inside {number}")
        )
        marks[last] = _padded("I recommend Across", at_end=True)
        marks[last + 1] = _padded(f"{number} wholeheartedly.")
        marks[(end + 1000) // _STRIDE] = _padded(_mention(f"After {number}"))
    return marks


def _titles(result: Any) -> list[str]:
    """Return the titles of a result's items."""
    return [item.title for item in result.items]


def test_text_at_the_limit_is_one_complete_request() -> None:
    """Exactly ``max_chars`` characters go out whole in one request."""
    segments = _segments(1500)
    segments[-1] = {"text": _filler(1499, _SEGMENT_CHARS + 1)}
    text = _joined(segments)
    assert_that(len(text)).is_equal_to(EXTRACTION_WINDOW_CHARS)
    model = _WindowModel()

    result = _run(segments, model)

    assert_that(model.texts).is_equal_to([text])
    assert_that(result.windows).is_equal_to(1)
    assert_that(result.transcript_chars).is_equal_to(EXTRACTION_WINDOW_CHARS)
    assert_that(result.fully_covered).is_true()
    assert_that(result.window_limit_reached).is_false()
    assert_that(result.unextracted_chars).is_zero()


def test_one_character_over_the_limit_reads_the_end() -> None:
    """One character over the limit adds a window that reaches the end."""
    tail = "1 wholeheartedly. " + _mention("After 1") + " "
    segments = _segments(
        1500,
        {
            5: _padded(_mention("Before 1")),
            1498: _padded("I recommend Across", at_end=True),
            1499: tail + _filler(1499, _SEGMENT_CHARS + 2 - len(tail)),
        },
    )
    text = _joined(segments)
    assert_that(len(text)).is_equal_to(EXTRACTION_WINDOW_CHARS + 1)
    model = _WindowModel()

    result = _run(segments, model)

    windows = model.offsets(text)
    assert_that(windows).is_length(2)
    assert_that(windows[0][1]).is_equal_to(1499 * _STRIDE - 1)
    assert_that(windows[-1][1]).is_equal_to(len(text))
    assert_that(model.texts[0]).ends_with("I recommend Across")
    assert_that(sorted(_titles(result))).is_equal_to(
        ["Across 1", "After 1", "Before 1"]
    )
    assert_that(result.fully_covered).is_true()


def test_marks_before_inside_across_and_after_each_boundary_are_found_once() -> None:
    """Every mention near every boundary of a long transcript is found once."""
    windows = _plan(_segments(4500))
    assert_that(windows).is_length(4)
    segments = _segments(4500, _boundary_marks(windows))
    model = _WindowModel()

    result = _run(segments, model)

    assert_that(model.offsets(_joined(segments))).is_equal_to(windows)
    expected = [
        f"{place} {number}"
        for number in range(1, 4)
        for place in ("Before", "Inside", "Across", "After")
    ]
    assert_that(sorted(_titles(result))).is_equal_to(sorted(expected))
    assert_that(result.windows).is_equal_to(4)
    assert_that(result.fully_covered).is_true()
    assert_that(result.success).is_true()
    for number, (before, after) in enumerate(
        zip(model.texts, model.texts[1:], strict=False), 1
    ):
        assert_that(before).contains(_mention(f"Inside {number}"))
        assert_that(after).contains(_mention(f"Inside {number}"))
        assert_that(before).ends_with("I recommend Across")
        assert_that(after).contains(_mention(f"Across {number}"))
        assert_that(after).does_not_contain(_mention(f"Before {number}"))
        assert_that(before).does_not_contain(_mention(f"After {number}"))


def test_windows_cut_at_segment_boundaries_and_overlap() -> None:
    """Windows start and end on segment boundaries and share 2,000-4,000 chars."""
    segments = [
        {"text": _filler(index, 40 + (index * 37) % 260)} for index in range(3000)
    ]
    text = _joined(segments)
    starts = {0}
    ends = {len(text)}
    position = 0
    for seg in segments:
        ends.add(position + len(seg["text"]))
        position += len(seg["text"]) + 1
        starts.add(position)

    windows = _plan(segments)

    assert_that(len(windows)).is_greater_than(2)
    assert_that(windows[0][0]).is_zero()
    assert_that(windows[-1][1]).is_equal_to(len(text))
    for (start, end), (next_start, next_end) in zip(windows, windows[1:], strict=False):
        assert_that(starts).contains(start, next_start)
        assert_that(ends).contains(end, next_end)
        assert_that(end - start).is_less_than_or_equal_to(EXTRACTION_WINDOW_CHARS)
        assert_that(end - next_start).is_between(
            EXTRACTION_WINDOW_OVERLAP_CHARS, 2 * EXTRACTION_WINDOW_OVERLAP_CHARS
        )
        assert_that(next_end).is_greater_than(end + EXTRACTION_WINDOW_OVERLAP_CHARS)


def test_a_segment_that_fits_a_window_is_not_cut() -> None:
    """An early boundary is used rather than cutting a segment that fits."""
    words = "spoken words " * 20_000
    segments = [
        {"text": words[:60_000].strip()},
        {"text": words[:120_000].strip()},
        {"text": words[:50_000].strip()},
    ]
    text = _joined(segments)

    windows = _plan(segments)

    first, second = len(segments[0]["text"]), len(segments[1]["text"])
    assert_that(windows[0]).is_equal_to((0, first))
    assert_that(windows[1][0]).is_less_than_or_equal_to(
        first - EXTRACTION_WINDOW_OVERLAP_CHARS
    )
    assert_that(windows[1][1]).is_greater_than_or_equal_to(first + 1 + second)
    assert_that(windows[-1][1]).is_equal_to(len(text))


def test_one_mention_at_distant_points_is_reported_once_from_its_first_window() -> None:
    """Repeats of one work merge to its first item, as within one reply."""
    windows = _plan(_segments(4500))
    first = (windows[0][0] + 10_000) // _STRIDE
    last = sum(windows[2]) // 2 // _STRIDE
    segments = _segments(
        4500,
        {first: _padded(_mention("Dune 1965")), last: _padded(_mention("dune 1965"))},
    )
    model = _WindowModel()

    result = _run(segments, model)

    assert_that(model.texts[0]).contains(_mention("Dune 1965"))
    assert_that(model.texts[2]).contains(_mention("dune 1965"))
    assert_that(_titles(result)).is_equal_to(["Dune 1965"])
    assert_that(result.items[0].confidence).is_equal_to(0.9)


def test_window_bound_is_reported_with_the_unread_amount() -> None:
    """Text past the window bound is never sent and is reported unread."""
    windows_needed = _plan(_segments(14_000), max_windows=50)
    assert_that(len(windows_needed)).is_greater_than(MAX_EXTRACTION_WINDOWS)
    segments = _segments(14_000, {13_999: _padded(_mention("Tail 1"))})
    text = _joined(segments)
    model = _WindowModel()

    result = _run(segments, model)

    windows = model.offsets(text)
    assert_that(windows).is_equal_to(windows_needed[:MAX_EXTRACTION_WINDOWS])
    assert_that(result.windows).is_equal_to(MAX_EXTRACTION_WINDOWS)
    assert_that(result.window_limit_reached).is_true()
    assert_that(result.unread_chars).is_equal_to(len(text) - windows[-1][1])
    assert_that(result.unextracted_chars).is_equal_to(result.unread_chars)
    assert_that(result.fully_covered).is_false()
    assert_that(result.success).is_true()
    assert_that(_titles(result)).does_not_contain("Tail 1")
    sent = sum(end - start for start, end in windows)
    assert_that(sent).is_less_than_or_equal_to(
        MAX_EXTRACTION_WINDOWS * EXTRACTION_WINDOW_CHARS
    )


def test_a_smaller_window_bound_is_honoured() -> None:
    """The bound is a parameter; two windows read only two windows of text."""
    model = _WindowModel()

    result = _run(_segments(4500), model, max_windows=2)

    assert_that(model.texts).is_length(2)
    assert_that(result.window_limit_reached).is_true()
    assert_that(result.unread_chars).is_greater_than(100_000)


def test_reply_stopped_at_the_token_cap_is_a_failure_not_a_parse() -> None:
    """A reply cut off at the output cap is recorded with its own reason."""
    complete = json.dumps([{"title": "Dune", "type": "book"}])
    segments = [{"text": _filler(0, 99)}, {"text": _mention("Ignored 1")}]

    for stop_reason in ("max_tokens", "model_context_window_exceeded"):
        model = _WindowModel({0: _reply(complete, stop_reason=stop_reason)})
        result = _run(segments, model)

        assert_that(result.items).is_empty()
        assert_that(result.errors).is_equal_to(["extraction: truncated_response"])
        assert_that(result.failed_windows).is_equal_to(1)
        assert_that(result.fully_covered).is_false()
        assert_that(result.unextracted_chars).is_equal_to(result.transcript_chars)


def test_truncated_window_keeps_the_other_windows_mentions() -> None:
    """A capped reply in one window does not discard the other windows."""
    windows = _plan(_segments(4500))
    segments = _segments(4500, _boundary_marks(windows))
    model = _WindowModel({1: _reply('[{"title": "Cut', stop_reason="max_tokens")})

    result = _run(segments, model)

    assert_that(result.errors).is_equal_to(["extraction: truncated_response"])
    assert_that(result.items).is_not_empty()
    assert_that(result.failed_windows).is_equal_to(1)


def test_one_failing_window_keeps_the_others_and_reports_its_text() -> None:
    """A failed request among several keeps other items and counts its text."""
    windows = _plan(_segments(4500))
    segments = _segments(4500, _boundary_marks(windows))
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error: Exception = anthropic.APIStatusError(
        "boom", response=httpx.Response(500, request=request), body=None
    )
    model = _WindowModel({2: error})

    result = _run(segments, model)

    assert_that(result.windows).is_equal_to(4)
    assert_that(result.failed_windows).is_equal_to(1)
    assert_that(result.errors).is_equal_to(
        ["extraction: APIStatusError (HTTP 500); request_failed"]
    )
    assert_that(result.success).is_false()
    assert_that(result.fully_covered).is_false()
    only_failed = windows[3][0] - windows[1][1]
    assert_that(result.unextracted_chars).is_equal_to(only_failed)
    assert_that(result.unread_chars).is_zero()
    titles = _titles(result)
    assert_that(titles).contains("Before 1", "Across 1", "After 3", "Inside 3")
    # Only the failed window held these whole.
    assert_that(titles).contains("Inside 2", "Inside 3")
    assert_that(titles).does_not_contain("Across 2", "After 2", "Before 3")


def test_unparsable_window_keeps_the_others() -> None:
    """A reply without a JSON array fails only its own window."""
    windows = _plan(_segments(4500))
    segments = _segments(4500, _boundary_marks(windows))
    model = _WindowModel({0: _reply("no array here")})

    result = _run(segments, model)

    assert_that(result.errors).is_equal_to(["extraction: missing_json_array"])
    assert_that(_titles(result)).contains("After 1", "After 3")
    assert_that(_titles(result)).does_not_contain("Before 1")


def test_single_oversized_segment_is_cut_at_whitespace() -> None:
    """A segment longer than a window is cut between words, with overlap."""
    text = " ".join(f"word{index:06d}" for index in range(40_000))
    model = _WindowModel()

    result = _run([{"text": text}], model)

    windows = model.offsets(text)
    assert_that(len(windows)).is_greater_than(2)
    assert_that(windows[-1][1]).is_equal_to(len(text))
    for window_text in model.texts:
        assert_that(window_text).matches(r"^word[0-9]{6}( word[0-9]{6})*$")
    for (_, end), (next_start, _) in zip(windows, windows[1:], strict=False):
        assert_that(end - next_start).is_greater_than_or_equal_to(
            EXTRACTION_WINDOW_OVERLAP_CHARS
        )
    assert_that(result.fully_covered).is_true()


def test_segment_without_whitespace_is_cut_at_the_window_length() -> None:
    """With no whitespace to cut at, windows are cut at the window length."""
    text = "".join(f"{index:06d}" for index in range(70_000))
    model = _WindowModel()

    result = _run([{"text": text}], model)

    windows = model.offsets(text)
    assert_that(windows[0]).is_equal_to((0, EXTRACTION_WINDOW_CHARS))
    overlap = EXTRACTION_WINDOW_OVERLAP_CHARS
    assert_that(windows[1][0]).is_equal_to(EXTRACTION_WINDOW_CHARS - overlap - 1)
    assert_that(windows[-1][1]).is_equal_to(len(text))
    assert_that(result.fully_covered).is_true()


@pytest.mark.parametrize(
    "segments",
    [[], [{"text": ""}], [{"text": "   "}], [{"other": "value"}]],
    ids=["no-segments", "empty-text", "blank-text", "no-text-key"],
)
def test_empty_transcript_is_covered_without_a_request(
    segments: list[dict[str, str]],
) -> None:
    """An empty transcript sends nothing and has nothing left unread."""
    model = _WindowModel()

    result = _run(segments, model)

    assert_that(model.texts).is_empty()
    assert_that(result.items).is_empty()
    assert_that(result.windows).is_zero()
    assert_that(result.transcript_chars).is_zero()
    assert_that(result.fully_covered).is_true()
    assert_that(result.success).is_true()


def test_text_below_the_minimum_is_reported_unread() -> None:
    """Text too short to send is not reported as covered."""
    model = _WindowModel()

    result = _run([{"text": "too short"}], model)

    assert_that(model.texts).is_empty()
    assert_that(result.unextracted_chars).is_equal_to(len("too short"))
    assert_that(result.fully_covered).is_false()
    assert_that(result.success).is_true()


@pytest.mark.parametrize(
    "kwargs",
    [{"max_chars": 4 * EXTRACTION_WINDOW_OVERLAP_CHARS}, {"max_windows": 0}],
    ids=["window-too-small", "no-windows"],
)
def test_invalid_bounds_are_rejected(kwargs: dict[str, int]) -> None:
    """Bounds that cannot make progress are rejected before any request."""
    model = _WindowModel()

    with pytest.raises(ValueError):
        _run(_segments(10), model, **kwargs)

    assert_that(model.texts).is_empty()
