<!-- markdownlint-disable MD041 -->

# 0020. Media extraction reads the whole transcript in bounded windows

- Status: proposed
- Date: 2026-11-17
- Tags: ingestion, extraction, transcripts, cost

## Context

Media extraction sent one request per transcript and capped the text it sent at
`max_chars` (150,000 characters). Before this decision, longer text was cut, a
warning was logged and the remainder was never sent, so mentions near the end of
long episodes were never extracted. `ExtractionResult` did not say so. Unlike
cleanup (ADR 0014), the unread text cannot simply be passed through: text that
is not sent is not extracted.

Two options were considered: read the transcript in consecutive windows, or keep
one request and only report the unread amount so the caller can schedule the
remainder. Accuracy comes first here, so windows are used. Windows change the
number of requests, and so the token and cost bounds per transcript. The reply's
stop reason was also ignored: a reply cut off at the output token cap was parsed
as if it were complete.

## Decision

Extraction joins the segment texts as before. Text of up to `max_chars`
characters is sent whole in one request, unchanged. Longer text is read in
consecutive windows of at most `max_chars` characters (150,000) that overlap at
each cut. The model, output token cap (4,000), thinking settings, non-streaming
requests, prompt and `<transcript>` wrapping are unchanged. Each window is
wrapped and sent exactly like a whole transcript; the model is not told that it
sees an excerpt.

Windows are cut at segment separators. A window ends at the last separator that
leaves it longer than three overlaps. Only when no separator qualifies (one
segment fills the window) is it cut at the last whitespace, or failing that at
`max_chars`. The next window starts at the last separator between two overlaps
and one overlap before that end, with the same whitespace and hard-position
fallbacks. Windows therefore share at least one overlap (2,000 characters) and
at most two. Each window ends at least one overlap past the end of the window
before it, so any span of up to 2,000 characters lies whole in some window. A
sentence naming a work, with the words that identify it, is a few hundred
characters. 2,000 characters is about two minutes of speech and adds under 3% of
input per extra window.

Each transcript makes at most `max_windows` requests (8 by default). Eight
windows read at least 1.17 million characters, about 20 hours of speech. The
input sent per transcript is at most 8 × 150,000 characters and the output at
most 8 × 4,000 tokens. Text past the last allowed window is never sent.

Items are merged across windows by the rule a single reply already follows: one
item per case-insensitive title, the first found in transcript order kept. Items
carry no timestamp or position, the default prompt asks for each item once per
transcript, and review candidate persistence finds context from the segments
itself. So a work found in two overlapping windows is reported once, and a work
named at distant points in the episode is also reported once, as it was when the
whole transcript fit one request.

A reply whose stop reason is `max_tokens` or `model_context_window_exceeded` is
not parsed. It is recorded as `extraction: truncated_response`, a new
`FailureReason`. A failed window (request failure, malformed response, truncated
reply, reply without a usable JSON array) adds one descriptor to `errors` and
contributes no items. The other windows' items are kept.

`ExtractionResult` states coverage:

- `transcript_chars`: length of the joined text.
- `windows` and `failed_windows`: requests sent and requests whose reply was not
  used.
- `unread_chars`: characters past the last window, never sent because the bound
  was reached. `window_limit_reached` is `unread_chars > 0`.
- `unextracted_chars`: characters inside no window whose reply was used. This is
  the unread tail, plus text that only failed windows held, plus the whole text
  when it is below the 100-character minimum and not sent.
- `fully_covered`: `unextracted_chars == 0`. An empty transcript is covered.

`success` keeps its meaning (no errors). A partly failed run therefore has
`success` false, items from the windows that worked, one descriptor per failed
window, and `fully_covered` false with `unextracted_chars` saying how much text
the result does not reflect. A run that hit the bound without failures has
`success` true and `fully_covered` false.

## Consequences

Cost per transcript grows with transcript length up to the bound instead of
staying at one request. Transcripts that fit one request cost exactly as before.
Callers that need to know whether a result reflects the whole transcript must
read `fully_covered`, `unextracted_chars` or `unread_chars`; `success` alone
does not show it. The pipeline stage outcome (planned in #694) should record
these fields; no schema records extraction outcomes today.

A failed window is not retried here, and a truncated reply's complete items are
not salvaged. Retry and re-reading the unextracted ranges are left to the
caller. Changing the window size, overlap or bound changes cost per transcript
and should update this record. A model that needs more context than 2,000
characters around a mention can still miss a work cut at a window boundary, and
a mention whose identifying words sit further apart than the overlap is not
guaranteed to land whole in one window.

Reverting is a code revert; nothing is stored.
