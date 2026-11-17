<!-- markdownlint-disable MD041 -->

# 0021. Transcript cleanup applies corrections, not the model's text

- Status: proposed
- Date: 2026-11-17
- Tags: ingestion, extraction, transcripts
- Supersedes: the part of
  [ADR 0014](./0014-partial-transcript-cleanup-keeps-tail.md) that uses the
  model's text for the head as returned

## Context

ADR 0014 sends the head of a transcript (up to `max_chars`, 100,000 characters
by default) and asks the model to return all of it in `cleaned_text`, plus a
list of corrections. The model's text then replaces the head as returned. That
has three costs:

- The reply cannot hold it. Replies are capped at 8,000 tokens, roughly 32,000
  characters, so a long head is cut off. The stop reason was not read, and the
  cut reply was recorded as `invalid_json`.
- Most of the cost is retyping. Output tokens cost five times input tokens, and
  the output was as long as the input.
- Nothing checks the returned text. A dropped sentence or a reworded phrase
  replaces the original unnoticed.

A `corrections` value of `null` or a number also raised `TypeError`.

Issue #876 compared four options: send less, allow a bigger reply, ask for
corrections only, and echo the text back in windows. Corrections only was
chosen. The model, the 8,000-token cap, the thinking settings and the single
non-streaming request per piece are unchanged; the model is chosen separately,
from measured quality (#929).

## Decision

**Reply.** The model returns `{"corrections": [...]}`, each item holding the
exact `original` text, the `corrected` replacement and a short `reason`. It does
not return the transcript. The default prompt asks for this shape and keeps its
instructions that the transcript is untrusted data. A reply that contains a
`cleaned_text` key (the old shape, for example from a tuned prompt that has not
been updated) is recorded as `legacy_response_shape` and nothing in it is
applied. A `corrections` value that is missing or not a list is recorded as
`invalid_corrections` and treated as no corrections; it never raises.

**Applying.** The application applies corrections to the original text. Each
accepted correction is placed at a position in the original, and all accepted
corrections are substituted in one pass at those positions. Corrections are
never applied one after another to changed text, so one correction's replacement
can never be matched by another. Every character outside an applied correction
is copied from the input unchanged, byte for byte. No other path puts model text
into the result.

A proposed correction is rejected and counted, by reason, when:

| Reason                  | Rule                                                                                                                        |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `malformed`             | Not an object, `original` not a non-empty string, `corrected` not a string, or `reason` neither a string nor null.          |
| `over_limit`            | Beyond the first 200 entries of one reply.                                                                                  |
| `original_too_long`     | `original` longer than 300 characters.                                                                                      |
| `empty_replacement`     | `corrected` empty or only whitespace. Deleting text is not a transcription fix.                                             |
| `unchanged`             | `corrected` equals `original`.                                                                                              |
| `oversized_replacement` | `corrected` more than 40 characters longer than `original`.                                                                 |
| `not_found`             | `original` does not occur in the piece the model read.                                                                      |
| `ambiguous`             | `original` occurs more than once in that piece (occurrences may overlap, so `aa` occurs twice in `aaa`).                    |
| `at_cut`                | `original` reaches a piece boundary that splits a word, so the model saw only part of that word.                            |
| `duplicate`             | Same position, original and replacement as a correction already accepted (the same fix proposed by two overlapping pieces). |
| `overlap`               | Shares a character with another accepted correction. Every correction in the overlap is rejected; none wins.                |

Reasons are cut to 200 characters. Corrections that only touch (one ends where
the next starts) both apply.

**Repeated originals: require a unique quote.** An original must occur exactly
once in the piece the model read, and the prompt asks the model to quote enough
surrounding words to make it unique and to report each occurrence separately.
The alternative, applying a correction at every occurrence, is simpler and
rejects less, but it changes places the model never judged. A name misheard in
one sentence ("Jon" for "John") can be right in another, and a short original
such as `their` occurs all over a transcript. Rejecting an ambiguous correction
loses one fix; applying it everywhere can silently damage text elsewhere, which
is the failure this decision exists to remove.

**Long transcripts.** A transcript longer than `max_chars` is read in
consecutive pieces, at most five per transcript, so at most five requests and
about `5 × max_chars` characters of input (500,000 by default). Each piece is
cut as ADR 0014 cuts the head: at the last whitespace among the final
`min(max_chars // 10, 1000)` characters, otherwise at `max_chars`. The next
piece starts at the first whitespace among the last `min(max_chars // 10, 200)`
characters of the previous piece, or exactly at its end when there is none.
Neighbouring pieces therefore share a short overlap, so an error that straddles
a cut is seen whole by the later piece. The same fix proposed by both pieces is
applied once (`duplicate`); different fixes for the same text are both rejected
(`overlap`). A blank piece is not sent.

ADR 0014's rule for text that is not cleaned is kept: it is never dropped, it is
returned exactly as it was and it is reported. Text after the fifth piece is
counted in `unread_chars` and a warning is logged.

**Stop reason.** The reply's `stop_reason` is read. A reply that stopped at the
token cap (`max_tokens`) is recorded as `truncated_response`, not
`invalid_json`, and none of its corrections are applied, even ones that parse: a
cut reply may have lost corrections the model meant to pair with them, and the
cap is not expected to be reached by a corrections-only reply. Reading continues
with the next piece.

A failed request (provider error, connection error, malformed response) stops
reading. Corrections from earlier pieces are still applied, and the failed piece
and everything after it are unread.

**Result.** `CleanupResult` reports:

- `cleaned_text`: the input with applied corrections.
- `corrections_made`: applied corrections in input order, each with its `start`
  position in the input.
- `rejections`: counts by the reasons above; `corrections_rejected` is their sum
  and `corrections_proposed` is applied plus rejected, over replies that were
  used.
- `cleaned_chars`: input characters inside a piece whose reply was used.
- `uncleaned_chars`: the rest, including `unread_chars`.
- `unread_chars`: input characters after the last piece whose reply arrived.
- `success`: no error was recorded. `partial`: some text was cleaned and some
  not. `fully_cleaned`: success and every character cleaned.

ADR 0014's `head_cleaned` field is removed; `cleaned_chars > 0` replaces it.

## Consequences

The model can no longer reword or drop text: every change to a transcript is a
listed, located correction that passed the rules above. Output is about the size
of the corrections, so cost and the token cap no longer scale with the
transcript, and long transcripts are cleaned up to the piece bound instead of
only their head.

Some valid fixes are lost. A correction quoted without enough context to be
unique, one larger than the size limits, one that deletes text, or one touching
a mid-word cut is rejected. The counts make this visible, so the prompt and
limits can be tuned from measurements.

The tuned prompt held in operations data must be changed to ask for corrections
only before cleanup is used with it. Until then its replies are reported as
`legacy_response_shape` and change nothing.

Three failure reasons are added to the closed set in ADR 0009:
`truncated_response`, `legacy_response_shape` and `invalid_corrections`.

No production code calls cleanup yet, and nothing is stored, so reverting this
decision is a code revert.
