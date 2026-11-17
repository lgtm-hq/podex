"""Shared stand-ins for the transcript cleanup tests.

Requests go through the real Anthropic SDK; only its HTTP transport is
replaced with ``httpx2.MockTransport``, so nothing leaves the process.
"""

import json
import os
from collections.abc import Callable
from secrets import token_hex
from typing import Any

import anthropic
import httpx2
import pytest
from assertpy import assert_that

from podex.services import llm_transcript_cleanup
from podex.services.llm_transcript_cleanup import CleanupResult, TranscriptCleaner

TRANSCRIPT = "the speaker said alot about podcasts and books " * 3
LOGGER = llm_transcript_cleanup.__name__
LIMIT = 150

Content = str | list[dict[str, Any]]
Answer = Content | Callable[[str], Content]


class Model:
    """Messages endpoint stand-in.

    ``answer`` is the reply for every request, or a function of the
    transcript piece sent. A string becomes one text block and a list is used
    as the content blocks. ``stop_reason`` is returned with every reply.
    """

    def __init__(self, answer: Answer, stop_reason: str = "end_turn") -> None:
        self.answer = answer
        self.stop_reason = stop_reason
        self.sent: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Record the decoded request body and return the configured content."""
        self.sent.append(json.loads(request.content))
        reply = self.answer
        if callable(reply):
            reply = reply(self.transcripts_sent()[-1])
        content = reply if isinstance(reply, list) else text_block(reply)
        return httpx2.Response(
            200,
            json={
                "type": "message",
                "role": "assistant",
                "content": content,
                "stop_reason": self.stop_reason,
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


def drop_ambient_sdk_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop ambient Anthropic settings such as a base URL or real key.

    Args:
        monkeypatch: Fixture used to remove the variables for one test.
    """
    for name in list(os.environ):
        if name.startswith("ANTHROPIC_"):
            monkeypatch.delenv(name)


def text_block(text: str) -> list[dict[str, Any]]:
    """Return one text content block.

    Args:
        text: Block text.

    Returns:
        Content block list.
    """
    return [{"type": "text", "text": text}]


def fixes(*corrections: tuple[object, object] | tuple[object, object, object]) -> str:
    """Return a corrections-only reply.

    Args:
        *corrections: (original, corrected) or (original, corrected, reason).

    Returns:
        JSON reply text.
    """
    items = [
        {
            "original": fix[0],
            "corrected": fix[1],
            "reason": fix[2] if len(fix) > 2 else "transcription",
        }
        for fix in corrections
    ]
    return json.dumps({"corrections": items})


NO_FIXES = json.dumps({"corrections": []})


def clean(
    answer: Answer,
    text: str = TRANSCRIPT,
    *,
    stop_reason: str = "end_turn",
    **kwargs: Any,
) -> tuple[CleanupResult, Model]:
    """Run cleanup against a model that answers ``answer``.

    Args:
        answer: Reply, or a function of the piece sent.
        text: Transcript to clean.
        stop_reason: Stop reason of every reply.
        **kwargs: Further ``cleanup_transcript`` arguments.

    Returns:
        The result and the model that recorded the requests.
    """
    model = Model(answer, stop_reason)
    with httpx2.Client(transport=httpx2.MockTransport(model)) as http_client:
        client: anthropic.Anthropic = anthropic.Anthropic(
            api_key=f"synthetic-{token_hex(4)}",
            http_client=http_client,
            max_retries=0,
        )
        result = TranscriptCleaner(client=client).cleanup_transcript(text, **kwargs)
    return result, model


def rejection_counts(result: CleanupResult) -> dict[str, int]:
    """Return rejection counts keyed by reason name.

    Args:
        result: Cleanup result.

    Returns:
        Counts by reason value.
    """
    return {str(reason): count for reason, count in result.rejections.items()}


def applied(result: CleanupResult) -> list[tuple[str, str, int]]:
    """Return (original, corrected, start) of each applied correction.

    Args:
        result: Cleanup result.

    Returns:
        Applied corrections in input order.
    """
    return [(c.original, c.corrected, c.start) for c in result.corrections_made]


def assert_only_corrections_changed(text: str, result: CleanupResult) -> None:
    """Assert every character outside an applied correction is the input's.

    Args:
        text: Input transcript.
        result: Cleanup result for ``text``.
    """
    cleaned = result.cleaned_text
    cursor = 0
    out = 0
    for correction in result.corrections_made:
        gap = correction.start - cursor
        assert_that(cleaned[out : out + gap]).is_equal_to(
            text[cursor : correction.start]
        )
        assert_that(text[correction.start : correction.end]).is_equal_to(
            correction.original,
        )
        out += gap
        assert_that(cleaned[out : out + len(correction.corrected)]).is_equal_to(
            correction.corrected,
        )
        out += len(correction.corrected)
        cursor = correction.end
    assert_that(cleaned[out:]).is_equal_to(text[cursor:])


def sentence_text(length: int) -> str:
    """Return ``length`` characters of distinct words separated by spaces.

    Args:
        length: Characters to return.

    Returns:
        Text whose words all differ.
    """
    return "".join(f"w{index:05d} " for index in range(length // 7 + 1))[:length]
