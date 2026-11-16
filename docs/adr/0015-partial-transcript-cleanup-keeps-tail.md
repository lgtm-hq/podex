<!-- markdownlint-disable MD041 -->

# 0015. Partial transcript cleanup keeps the uncleaned tail

- Status: proposed
- Date: 2026-11-16
- Tags: ingestion, extraction, transcripts

## Context

Transcript cleanup sends one request per transcript and caps the text it sends
at `max_chars` (100,000 characters by default). Before this decision, input
longer than the cap was cut, the remainder was discarded and the result still
reported success. Extraction and chunking would then run on a shortened
transcript without knowing it. Episode transcripts often exceed the cap.

Three options were considered: clean in consecutive windows; clean the head and
append the uncleaned tail; or report the cut and let the caller decide. Windowed
cleaning changes the number of requests, and so the token and cost bounds per
transcript.

## Decision

Cleanup never drops text. It cleans the head exactly as before (same prompt,
model, single request and limit) and returns the cleaned head followed by the
original, uncleaned tail, so `CleanupResult.cleaned_text` always covers the
whole input.

The cut is deterministic. Text within the limit is sent whole. Longer text is
cut at the last whitespace character at or before index `max_chars`, so the tail
starts with that whitespace (a space or a line break) and no word is split
between head and tail. When the head contains no whitespace, the cut falls
exactly at `max_chars`. Head and tail are joined with nothing in between, so no
input character is dropped or duplicated.

The result says when it is partial. `uncleaned_chars` counts the trailing
characters never sent to the model, `partial` is true when that count is above
zero, and `fully_cleaned` is true only for a successful result that covered the
whole input. `success` keeps its meaning (no errors), so callers that check it
do not discard partially cleaned text. A reply without `cleaned_text` returns
the full original input. The existing truncation warning is kept.

## Consequences

`cleaned_text` can mix model-cleaned and raw text. Consumers that need to know
must read `partial` or `uncleaned_chars`; a check of `success` alone does not
show whether the whole transcript was cleaned. The pipeline stage outcome
(planned in #694) should record these fields; no schema records cleanup outcomes
today.

A transcript with a long stretch without whitespace before the cap is cleaned
less, but it is never cut short, and `uncleaned_chars` reports the uncleaned
size exactly.

Windowed cleaning remains possible later. It would change only how much of the
input is cleaned; the result fields above keep their meaning. Reverting this
decision is a code revert; nothing is stored.
