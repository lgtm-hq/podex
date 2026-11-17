<!-- markdownlint-disable MD041 -->

# 0014. Partial transcript cleanup keeps the uncleaned tail

- Status: proposed
- Date: 2026-11-16
- Tags: ingestion, extraction, transcripts

## Context

Transcript cleanup sends one request per transcript and caps the text it sends
at `max_chars` (100,000 characters by default). Before this decision, input
longer than the cap was cut, the remainder was discarded and the result still
reported success. Extraction and chunking would then run on a shortened
transcript without knowing it. Episode transcripts often exceed the cap. A reply
whose `cleaned_text` was missing, empty or not a string was also reported as a
success, and a non-string value was converted with `str()`, which could replace
the transcript with text such as `None`.

Three options were considered: clean in consecutive windows; clean the head and
append the uncleaned tail; or report the cut and let the caller decide. Windowed
cleaning changes the number of requests, and so the token and cost bounds per
transcript.

## Decision

Cleanup cleans the head exactly as before (same prompt, model, single request
and limit) and returns the model's text for the head followed by the original,
uncleaned tail. The tail is never dropped. The model's text replaces the head
only when the reply's `cleaned_text` is a string that is not blank, or the head
itself is blank. Otherwise (request failure, invalid JSON, empty reply, no text
block, missing, non-string or blank `cleaned_text`) the result is the whole
original input. The model's text for the head is used as returned, so the model
can still reword or omit text within the head.

The cut is deterministic. Text within the limit is sent whole. Longer text is
cut at the last character that `str.isspace` accepts (space, tab, line break,
ideographic space and so on) among indices `max_chars - lookback` to
`max_chars`, where `lookback` is `min(max_chars // 10, 1000)`. The tail then
starts with that character. When the window holds no such character, the cut
falls exactly at `max_chars`. The look-back is bounded so that a transcript
whose only whitespace is near the start still sends close to `max_chars`
characters. If the head is blank but the input is not, the request is skipped
and the whole input is returned, since no cut within the limit would contain
anything to clean. Blank input keeps its existing request.

Head and tail are joined with nothing in between, so no input character is
dropped or duplicated by the join itself. The join is not seamless in every
case. At a hard cut a word can be split, and a model that completes the half
word produces a doubled fragment once the tail is appended. A base character and
its combining mark, or the `\r` and `\n` of a CRLF line break, can fall on
either side of the cut.

The result says what was cleaned. `head_cleaned` is true only when the model's
text replaced the head. `uncleaned_chars` counts the characters of
`cleaned_text` copied from the input without cleaning: the tail when the head
was cleaned, otherwise the whole input. `partial` is true when the head was
cleaned and an uncleaned tail follows. `fully_cleaned` is
`success and not partial and head_cleaned`. `success` keeps its meaning (no
errors), so callers that check it do not discard text. The existing truncation
warning is kept.

## Consequences

`cleaned_text` can mix model-cleaned and raw text, or be entirely raw. Consumers
that need to know must read `head_cleaned`, `partial` or `uncleaned_chars`; a
check of `success` alone does not show whether anything was cleaned. The
pipeline stage outcome (planned in #694) should record these fields; no schema
records cleanup outcomes today.

When the look-back window has no whitespace, a word can be split at the cut, as
described above. Text below the 100-character minimum is not sent and is
reported as not cleaned.

Windowed cleaning remains possible later. It would change only how much of the
input is cleaned; the result fields above keep their meaning. Reverting this
decision is a code revert; nothing is stored.
