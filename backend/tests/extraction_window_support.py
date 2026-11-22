"""Window planning and model doubles shared by the windowed extraction tests.

Segments built here are 99 characters long, so segment ``k`` occupies
``[k * 100, k * 100 + 99)`` of the joined text and window positions can be
stated as literal numbers in the tests.
"""

import json
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
from assertpy import assert_that

from podex.services.llm_extraction import LLMExtractor

SEGMENT_CHARS = 99


STRIDE = SEGMENT_CHARS + 1


FILLER = ("the hosts talk about their week and the weather " * 3)[:SEGMENT_CHARS]


MENTION = re.compile(r"I recommend ([A-Za-z]+ [0-9]+) wholeheartedly\.")


WRAP_START = "<transcript>\n"


WRAP_END = "\n</transcript>"


API_KEY = "test-key-not-a-credential"


FOUR_WINDOWS = [
    (0, 149_999),
    (147_900, 297_899),
    (295_800, 445_799),
    (443_700, 449_999),
]


def text_reply(text: str, stop_reason: object = "end_turn") -> SimpleNamespace:
    """Return a Messages API response double with one text block."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
    )


def found_items(text: str, index: int) -> list[dict[str, Any]]:
    """Return one item per complete mention sentence in ``text``."""
    return [
        {"title": title, "type": "book", "confidence": round(0.9 - index / 20, 2)}
        for title in MENTION.findall(text)
    ]


def transcript_of(content: object) -> str:
    """Strip the transcript tags from one request's user content."""
    text = str(content)
    assert_that(text).starts_with(WRAP_START).ends_with(WRAP_END)
    return text[len(WRAP_START) : -len(WRAP_END)]


def window_offsets(full_text: str, texts: list[str]) -> list[tuple[int, int]]:
    """Return the ``(start, end)`` of each window text in ``full_text``."""
    ranges: list[tuple[int, int]] = []
    position = 0
    for text in texts:
        start = full_text.index(text, position)
        ranges.append((start, start + len(text)))
        position = start + 1
    return ranges


class WindowModel:
    """Model double that names every complete mention sentence it is sent.

    A mention is found only when its whole sentence is inside the window, so
    a sentence cut by a window boundary is found only by a window holding it
    whole. ``replies`` overrides the reply (or raises) for a 0-based window.
    """

    def __init__(
        self, replies: dict[int, SimpleNamespace | BaseException] | None = None
    ):
        self.replies = replies or {}
        self.texts: list[str] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        """Record the window text and reply with the mentions it holds."""
        text = transcript_of(kwargs["messages"][0]["content"])
        index = len(self.texts)
        self.texts.append(text)
        override = self.replies.get(index)
        if isinstance(override, BaseException):
            raise override
        if override is not None:
            return override
        return text_reply(json.dumps(found_items(text, index)))

    def offsets(self, full_text: str) -> list[tuple[int, int]]:
        """Return the ``(start, end)`` of each sent window in ``full_text``."""
        return window_offsets(full_text, self.texts)


def sdk_body(
    text: str = "[]",
    *,
    stop_reason: object = "end_turn",
    content: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a Messages API success body."""
    return {
        "id": "msg_window",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-8",
        "content": [{"type": "text", "text": text}] if content is None else content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def status_answer(code: int, error_type: str) -> Callable[[str], httpx2.Response]:
    """Return an answer that is a provider error envelope."""
    body = {"type": "error", "error": {"type": error_type, "message": "test"}}
    return lambda _text: httpx2.Response(code, json=body)


def success_answer(**kwargs: Any) -> Callable[[str], httpx2.Response]:
    """Return an answer that is a fixed success body."""
    return lambda _text: httpx2.Response(200, json=sdk_body(**kwargs))


class Provider:
    """Provider endpoint behind the real SDK's transport seam.

    Windows are numbered by distinct request text, so SDK retries of one
    window keep its number. Unscripted windows answer with their mentions.
    """

    def __init__(
        self, answers: dict[int, Callable[[str], httpx2.Response]] | None = None
    ):
        self.answers = answers or {}
        self.texts: list[str] = []
        self.requests = 0
        self.sent_chars = 0

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Record the request and answer it for its window."""
        text = transcript_of(json.loads(request.content)["messages"][0]["content"])
        self.requests += 1
        self.sent_chars += len(text)
        if not self.texts or self.texts[-1] != text:
            self.texts.append(text)
        index = len(self.texts) - 1
        answer = self.answers.get(index)
        if answer is not None:
            return answer(text)
        return httpx2.Response(200, json=sdk_body(json.dumps(found_items(text, index))))

    def offsets(self, full_text: str) -> list[tuple[int, int]]:
        """Return the ``(start, end)`` of each window sent."""
        return window_offsets(full_text, self.texts)


def run_model(
    segments: list[dict[str, str]],
    model: WindowModel,
    **kwargs: int,
) -> Any:
    """Run the extractor over ``segments`` against ``model``."""
    client = SimpleNamespace(messages=model)
    return LLMExtractor(client=client).extract_from_transcript(segments, **kwargs)


def run_sdk(segments: list[dict[str, str]], provider: Provider) -> Any:
    """Run the extractor through the real SDK client and a mock transport."""
    client = anthropic.Anthropic(
        api_key=API_KEY,
        http_client=httpx2.Client(transport=httpx2.MockTransport(provider)),
    )
    return LLMExtractor(client=client).extract_from_transcript(segments)


def padded(text: str, *, at_end: bool = False) -> str:
    """Pad ``text`` with filler to exactly one segment length."""
    pad = "~" * (SEGMENT_CHARS - len(text) - 1)
    return f"{pad} {text}" if at_end else f"{text} {pad}"


def mention(title: str) -> str:
    """Return the sentence the model double recognises for ``title``."""
    return f"I recommend {title} wholeheartedly."


def filler(index: int, length: int) -> str:
    """Return distinct filler of ``length`` characters for segment ``index``."""
    return (f"seg{index:05d} " + FILLER * 4)[: length - 1] + "."


def make_segments(
    count: int, marks: dict[int, str] | None = None
) -> list[dict[str, str]]:
    """Build ``count`` distinct segments of equal length, with ``marks``."""
    marks = marks or {}
    return [
        {"text": marks.get(index, filler(index, SEGMENT_CHARS))}
        for index in range(count)
    ]


def digit_text(start: int, length: int) -> str:
    """Return ``length`` distinct digits without whitespace."""
    return "".join(f"{index:07d}" for index in range(start, start + length // 7 + 1))[
        :length
    ]


def joined(segments: list[dict[str, str]]) -> str:
    """Join segments the way the extractor does."""
    return " ".join(seg["text"].strip() for seg in segments if seg["text"])


def plan(segments: list[dict[str, str]], **kwargs: int) -> list[tuple[int, int]]:
    """Return the windows the extractor reads for ``segments``."""
    model = WindowModel()
    run_model(segments, model, **kwargs)
    return model.offsets(joined(segments))


def boundary_marks(windows: list[tuple[int, int]]) -> dict[int, str]:
    """Place one mention before, in, across and after each window boundary.

    Segment ``k`` occupies ``[k * STRIDE, k * STRIDE + SEGMENT_CHARS)``.
    """
    marks: dict[int, str] = {}
    for number, ((_, end), (next_start, _)) in enumerate(
        zip(windows, windows[1:], strict=False), start=1
    ):
        last = (end - SEGMENT_CHARS) // STRIDE
        marks[(next_start - 1000) // STRIDE] = padded(mention(f"Before {number}"))
        marks[(next_start + end) // 2 // STRIDE] = padded(mention(f"Inside {number}"))
        marks[last] = padded("I recommend Across", at_end=True)
        marks[last + 1] = padded(f"{number} wholeheartedly.")
        marks[(end + 1000) // STRIDE] = padded(mention(f"After {number}"))
    return marks


def marked_four_windows() -> list[dict[str, str]]:
    """Return 4,500 segments with mentions around each of three boundaries."""
    return make_segments(4500, boundary_marks(FOUR_WINDOWS))


def titles_of(result: Any) -> list[str]:
    """Return the titles of a result's items."""
    return [item.title for item in result.items]


def book_item(**fields: Any) -> dict[str, Any]:
    """Return one reply item of type book."""
    return {"type": "book", **fields}


def items_reply(*items: dict[str, Any]) -> SimpleNamespace:
    """Return a reply listing ``items``."""
    return text_reply(json.dumps(list(items)))
