"""Windowed extraction: window planning, coverage and bounds.

Window positions, bounds and amounts are asserted as literal numbers so that a
change to the window size, overlap, minimum window, whitespace look-back or
window bound fails here rather than moving the expectation with it.
"""

import logging

import pytest
from assertpy import assert_that

from tests.extraction_window_support import (
    FOUR_WINDOWS,
    SEGMENT_CHARS,
    WindowModel,
    digit_text,
    filler,
    joined,
    make_segments,
    marked_four_windows,
    mention,
    padded,
    plan,
    run_model,
    titles_of,
)


def test_text_at_the_limit_is_one_complete_request() -> None:
    """Exactly 150,000 characters go out whole in one request."""
    segments = make_segments(1500)
    segments[-1] = {"text": filler(1499, SEGMENT_CHARS + 1)}
    text = joined(segments)
    assert_that(len(text)).is_equal_to(150_000)
    model = WindowModel()

    result = run_model(segments, model)

    assert_that(model.texts).is_equal_to([text])
    assert_that(result.windows).is_equal_to(1)
    assert_that(result.transcript_chars).is_equal_to(150_000)
    assert_that(result.fully_covered).is_true()
    assert_that(result.window_limit_reached).is_false()
    assert_that(result.unextracted_ranges).is_empty()
    assert_that(result.unread_chars).is_zero()


def test_one_character_over_the_limit_reads_the_end() -> None:
    """One character over the limit adds a window that reaches the end."""
    tail = "1 wholeheartedly. " + mention("After 1") + " "
    segments = make_segments(
        1500,
        {
            5: padded(mention("Before 1")),
            1498: padded("I recommend Across", at_end=True),
            1499: tail + filler(1499, SEGMENT_CHARS + 2 - len(tail)),
        },
    )
    text = joined(segments)
    assert_that(len(text)).is_equal_to(150_001)
    model = WindowModel()

    result = run_model(segments, model)

    assert_that(model.offsets(text)).is_equal_to([(0, 149_899), (147_800, 150_001)])
    assert_that(model.texts[0]).ends_with("I recommend Across")
    assert_that(sorted(titles_of(result))).is_equal_to(
        ["Across 1", "After 1", "Before 1"]
    )
    assert_that(result.fully_covered).is_true()


def test_marks_before_inside_across_and_after_each_boundary_are_found_once() -> None:
    """Every mention near every boundary of a long transcript is found once."""
    segments = marked_four_windows()
    model = WindowModel()

    result = run_model(segments, model)

    assert_that(model.offsets(joined(segments))).is_equal_to(FOUR_WINDOWS)
    expected = [
        f"{place} {number}"
        for number in range(1, 4)
        for place in ("Before", "Inside", "Across", "After")
    ]
    assert_that(sorted(titles_of(result))).is_equal_to(sorted(expected))
    assert_that(result.windows).is_equal_to(4)
    assert_that(result.fully_covered).is_true()
    assert_that(result.success).is_true()
    for number, (before, after) in enumerate(
        zip(model.texts, model.texts[1:], strict=False), 1
    ):
        assert_that(before).contains(mention(f"Inside {number}"))
        assert_that(after).contains(mention(f"Inside {number}"))
        assert_that(before).ends_with("I recommend Across")
        assert_that(after).contains(mention(f"Across {number}"))
        assert_that(after).does_not_contain(mention(f"Before {number}"))
        assert_that(before).does_not_contain(mention(f"After {number}"))


def test_windows_cut_at_segment_boundaries_and_overlap() -> None:
    """Windows start and end on segment boundaries and share 2,000-3,999 chars."""
    segments = [
        {"text": filler(index, 40 + (index * 37) % 260)} for index in range(3000)
    ]
    text = joined(segments)
    starts = {0}
    ends = {len(text)}
    position = 0
    for seg in segments:
        ends.add(position + len(seg["text"]))
        position += len(seg["text"]) + 1
        starts.add(position)

    windows = plan(segments)

    assert_that(len(windows)).is_greater_than(2)
    assert_that(windows[0][0]).is_zero()
    assert_that(windows[-1][1]).is_equal_to(len(text))
    for (start, end), (next_start, next_end) in zip(windows, windows[1:], strict=False):
        assert_that(starts).contains(start, next_start)
        assert_that(ends).contains(end, next_end)
        assert_that(end - start).is_less_than_or_equal_to(150_000)
        assert_that(end - next_start).is_between(2_000, 3_999)
        assert_that(next_end).is_greater_than(end)


def test_a_segment_that_fits_a_window_is_not_cut() -> None:
    """An early boundary is used rather than cutting a segment that fits."""
    words = "spoken words " * 20_000
    segments = [
        {"text": words[:60_000].strip()},
        {"text": words[:120_000].strip()},
        {"text": words[:50_000].strip()},
    ]
    text = joined(segments)

    windows = plan(segments)

    first, second = len(segments[0]["text"]), len(segments[1]["text"])
    assert_that(windows[0]).is_equal_to((0, first))
    assert_that(windows[1][0]).is_less_than_or_equal_to(first - 2_000)
    assert_that(windows[1][1]).is_greater_than_or_equal_to(first + 1 + second)
    assert_that(windows[-1][1]).is_equal_to(len(text))


@pytest.mark.parametrize(
    ("first_length", "windows"),
    [
        (6_001, [(0, 6_001), (4_000, 154_000), (151_999, 206_002)]),
        (6_000, [(0, 150_000), (147_999, 206_001)]),
    ],
    ids=["separator-at-minimum", "separator-below-minimum"],
)
def test_a_window_ends_at_a_separator_only_past_6001_characters(
    first_length: int,
    windows: list[tuple[int, int]],
) -> None:
    """A separator closer than three overlaps plus one is not a window end."""
    segments = [
        {"text": digit_text(0, first_length)},
        {"text": digit_text(1_000_000, 200_000)},
    ]

    assert_that(plan(segments)).is_equal_to(windows)


@pytest.mark.parametrize(
    ("second_length", "first_end"),
    [(9_999, 150_000), (10_000, 140_000)],
    ids=["separator-at-150000", "separator-at-150001"],
)
def test_a_window_ends_at_the_last_separator_within_150000_characters(
    second_length: int,
    first_end: int,
) -> None:
    """A separator at 150,000 ends the first window; one at 150,001 does not."""
    segments = [
        {"text": digit_text(0, 140_000)},
        {"text": digit_text(1_000_000, second_length)},
        {"text": digit_text(2_000_000, 100_000)},
    ]

    assert_that(plan(segments)[0][1]).is_equal_to(first_end)


@pytest.mark.parametrize(
    ("space_at", "windows"),
    [
        (149_000, [(0, 149_000), (146_999, 200_000)]),
        (148_999, [(0, 150_000), (147_999, 200_000)]),
    ],
    ids=["within-1000", "beyond-1000"],
)
def test_whitespace_cut_looks_back_at_most_1000_characters(
    space_at: int,
    windows: list[tuple[int, int]],
) -> None:
    """Inside one segment, whitespace more than 1,000 back is not a cut."""
    digits = digit_text(0, 200_000)
    text = digits[:space_at] + " " + digits[space_at + 1 :]

    assert_that(plan([{"text": text}])).is_equal_to(windows)


def test_window_bound_is_eight_and_reports_the_unread_amount(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Text past eight windows is never sent and is reported unread."""
    segments = make_segments(14_000, {13_999: padded(mention("Tail 1"))})
    assert_that(len(joined(segments))).is_equal_to(1_399_999)
    model = WindowModel()

    with caplog.at_level(logging.WARNING, logger="podex.services.llm_extraction"):
        result = run_model(segments, model)

    windows = model.offsets(joined(segments))
    assert_that(windows).is_length(8)
    assert_that(windows[-1]).is_equal_to((1_035_300, 1_185_299))
    assert_that(result.windows).is_equal_to(8)
    assert_that(result.window_limit_reached).is_true()
    assert_that(result.unread_chars).is_equal_to(214_700)
    assert_that(result.unextracted_ranges).is_equal_to([(1_185_299, 1_399_999)])
    assert_that(result.unextracted_chars).is_equal_to(214_700)
    assert_that(result.fully_covered).is_false()
    assert_that(result.success).is_true()
    assert_that(titles_of(result)).does_not_contain("Tail 1")
    assert_that([record.getMessage() for record in caplog.records]).is_equal_to(
        [
            "Extraction window limit reached after 8 windows; "
            "214700 of 1399999 characters not read",
        ],
    )


def test_a_smaller_window_bound_is_honoured() -> None:
    """The bound is a parameter; two windows read only two windows of text."""
    model = WindowModel()

    result = run_model(make_segments(4500), model, max_windows=2)

    assert_that(model.texts).is_length(2)
    assert_that(result.window_limit_reached).is_true()
    assert_that(result.unread_chars).is_equal_to(152_100)
    assert_that(result.unextracted_ranges).is_equal_to([(297_899, 449_999)])


def test_single_oversized_segment_is_cut_at_whitespace() -> None:
    """A segment longer than a window is cut between words, with overlap."""
    text = " ".join(f"word{index:06d}" for index in range(40_000))
    model = WindowModel()

    result = run_model([{"text": text}], model)

    windows = model.offsets(text)
    assert_that(len(windows)).is_greater_than(2)
    assert_that(windows[-1][1]).is_equal_to(len(text))
    for window_text in model.texts:
        assert_that(window_text).matches(r"^word[0-9]{6}( word[0-9]{6})*$")
    for (_, end), (next_start, _) in zip(windows, windows[1:], strict=False):
        assert_that(end - next_start).is_greater_than_or_equal_to(2_000)
    assert_that(result.fully_covered).is_true()


def test_segment_without_whitespace_is_cut_at_the_window_length() -> None:
    """With no whitespace to cut at, windows are cut at the window length."""
    text = "".join(f"{index:06d}" for index in range(70_000))

    windows = plan([{"text": text}])

    assert_that(windows).is_equal_to(
        [(0, 150_000), (147_999, 297_999), (295_998, 420_000)]
    )


def test_every_passage_up_to_2000_characters_is_whole_in_a_window() -> None:
    """Every 2,000-character passage of a two-window text lies in one window."""
    text = "".join(f"{index:06d}" for index in range(25_001))[:150_001]

    windows = plan([{"text": text}])

    assert_that(windows).is_equal_to([(0, 150_000), (147_999, 150_001)])
    split = [
        start
        for start in range(0, len(text) - 2_000 + 1)
        if not any(low <= start and start + 2_000 <= high for low, high in windows)
    ]
    assert_that(split).is_empty()


@pytest.mark.parametrize(
    "segments",
    [[], [{"text": ""}], [{"text": "   "}], [{"other": "value"}]],
    ids=["no-segments", "empty-text", "blank-text", "no-text-key"],
)
@pytest.mark.parametrize(
    "bounds",
    [{}, {"max_chars": 0}, {"max_chars": 8_000}, {"max_windows": 0}],
    ids=["default-bounds", "zero-chars", "small-chars", "zero-windows"],
)
def test_empty_transcript_is_covered_without_a_request(
    segments: list[dict[str, str]],
    bounds: dict[str, int],
) -> None:
    """An empty transcript sends nothing whatever the bounds."""
    model = WindowModel()

    result = run_model(segments, model, **bounds)

    assert_that(model.texts).is_empty()
    assert_that(result.items).is_empty()
    assert_that(result.windows).is_zero()
    assert_that(result.transcript_chars).is_zero()
    assert_that(result.fully_covered).is_true()
    assert_that(result.success).is_true()


@pytest.mark.parametrize(
    "bounds",
    [{}, {"max_chars": 0}, {"max_chars": 8_000}, {"max_windows": 0}],
    ids=["default-bounds", "zero-chars", "small-chars", "zero-windows"],
)
def test_text_below_the_minimum_is_reported_unread(bounds: dict[str, int]) -> None:
    """Text too short to send is not covered, whatever the bounds."""
    model = WindowModel()

    result = run_model([{"text": "too short"}], model, **bounds)

    assert_that(model.texts).is_empty()
    assert_that(result.unextracted_ranges).is_equal_to([(0, 9)])
    assert_that(result.unread_chars).is_equal_to(9)
    assert_that(result.fully_covered).is_false()
    assert_that(result.success).is_true()


def test_text_within_a_small_window_is_sent_whole() -> None:
    """A small ``max_chars`` that the text fits in sends it whole, as before."""
    model = WindowModel()

    result = run_model([{"text": filler(0, 200)}], model, max_chars=5_000)

    assert_that(model.texts).is_equal_to([filler(0, 200)])
    assert_that(result.fully_covered).is_true()


@pytest.mark.parametrize(
    "kwargs",
    [{"max_chars": 8_000}, {"max_windows": 0}],
    ids=["window-too-small", "no-windows"],
)
def test_bounds_that_cannot_make_progress_are_rejected(
    kwargs: dict[str, int],
) -> None:
    """Bounds that cannot read the text are rejected before any request."""
    model = WindowModel()

    with pytest.raises(ValueError):
        run_model(make_segments(100), model, **kwargs)

    assert_that(model.texts).is_empty()
