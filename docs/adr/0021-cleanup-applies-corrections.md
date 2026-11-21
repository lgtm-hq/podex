<!-- markdownlint-disable MD041 -->

# 0021. Transcript cleanup applies corrections, not the model's text

- Status: proposed
- Date: 2026-11-17
- Tags: ingestion, extraction, transcripts
- Supersedes: parts of
  [ADR 0014](./0014-partial-transcript-cleanup-keeps-tail.md), listed under
  "Changes to ADR 0014" below

## Context

ADR 0014 sends the head of a transcript (up to `max_chars`, 100,000 characters
by default) in one request and asks the model to return all of it in
`cleaned_text`, plus a list of corrections. The model's text then replaces the
head as returned. That has three costs:

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
chosen. The model, the 8,000-token cap, the thinking settings and the
non-streaming request are unchanged; the model is chosen separately, from
measured quality (#929).

## Decision

### Reply

The model returns `{"corrections": [...]}`, each item holding the exact
`original` text, the `corrected` replacement and a short `reason`. It does not
return the transcript. The default prompt asks for this shape, states the limits
below, asks for exact copies including punctuation, spacing, capitalisation and
accents, asks for the JSON object only, and keeps its paragraph saying the
transcript is untrusted data.

A reply is used only when all of these hold, checked in this order:

1. It does not have the old shape. A `"cleaned_text":` key anywhere in the reply
   text, even in JSON cut off at the token cap, is recorded as
   `legacy_response_shape`. Nothing in it is applied and no further piece is
   sent, since every piece would get the same answer.
2. It ended normally: `stop_reason` is `end_turn` or `stop_sequence`. A reply
   that stopped at the token cap (`max_tokens`) is recorded as
   `truncated_response`; any other value, including a missing or non-string one,
   is recorded as `unexpected_stop_reason`. Nothing in such a reply is applied,
   even corrections that parse, because a cut or interrupted answer may have
   lost the corrections paired with them. Reading continues with the next piece.
3. Its text decodes to an object. Text the JSON decoder refuses, including an
   integer longer than the interpreter's digit limit or nesting deeper than its
   recursion limit, is recorded as `invalid_json`; it never raises, and other
   pieces' corrections are kept.
4. Its `corrections` value is a list. Anything else, including a missing key, is
   recorded as `invalid_corrections` and never raises.

A reply with no text block is not an error but cleans nothing.

### Applying

The application applies corrections to the original text. Each accepted
correction is placed at a position in the original, and all accepted corrections
are substituted in one pass at those positions. They are never applied one after
another to changed text, so one correction's replacement can never be matched by
another. Every character outside an applied correction is copied from the input
unchanged, byte for byte. No other path puts model text into the result.

A proposed correction is rejected and counted, by reason, in this order:

| Reason                   | Rule                                                                                                               |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| `over_limit`             | Beyond the first 200 entries of one reply.                                                                         |
| `malformed`              | Not an object, `original` not a non-empty string, `corrected` not a string, or `reason` neither a string nor null. |
| `original_too_long`      | `original` longer than 80 characters.                                                                              |
| `empty_replacement`      | `corrected` empty or only whitespace.                                                                              |
| `unchanged`              | `corrected` equals `original`.                                                                                     |
| `unsafe_replacement`     | `corrected` holds a control (Cc), format (Cf) or surrogate (Cs) character that `original` does not contain.        |
| `oversized_replacement`  | `corrected` more than 40 characters longer than `original`.                                                        |
| `undersized_replacement` | `corrected` shorter than half of `original`.                                                                       |
| `not_found`              | `original` does not occur in the piece the model read.                                                             |
| `ambiguous`              | `original` occurs more than once in that piece (occurrences may overlap, so `aa` occurs twice in `aaa`).           |
| `at_cut`                 | `original` reaches a piece boundary that splits a word, so the model saw only part of that word.                   |
| `not_owned`              | Its start lies in the part of the text a neighbouring piece owns (see below).                                      |
| `over_budget`            | The reply's owned corrections together exceed its change budget; every one of them is rejected.                    |
| `overlap`                | Shares a character with another correction from the same reply. Both are rejected.                                 |
| `superseded`             | Overlaps a correction from a neighbouring piece that starts later (see below).                                     |

The format category covers bidirectional controls, zero-width characters, the
byte order mark and the soft hyphen. A reason has every control, format and
surrogate character removed and is then cut to 200 characters, so a stored
reason never contains them. Corrections that only touch (one ends where the next
starts) both apply.

The limits follow from what a transcription fix is. An original holds the
misheard words plus the few neighbours needed to make it unique, about ten
words, so 80 characters; longer originals rewrite sentences. A replacement may
spell a name out in full (40 characters longer) but does not delete most of what
it replaces (at least half as long). Deleting text is not a transcription fix,
so an empty replacement is refused.

### Repeated originals: require a unique quote

An original must occur exactly once in the piece the model read. The prompt
tells the model to quote enough neighbouring words to make it unique and, for a
mishearing that recurs, to quote each occurrence it wants fixed with its own
neighbours as a separate correction. The alternative, applying a correction at
every occurrence, is simpler and rejects less, but it changes places the model
never judged. A name misheard in one sentence ("Jon" for "John") can be right in
another, and a short original such as `their` occurs all over a transcript.
Rejecting an ambiguous correction loses one fix; applying it everywhere can
silently damage text elsewhere, which is the failure this decision exists to
remove.

### Change budget

Each piece owns a core: neighbouring pieces divide the text they share at its
midpoint, the first piece owns from the start of the transcript and the last
piece to its end, so the cores do not overlap and together cover every piece.
The corrections a reply proposes whose start lies in its core may together cover
at most 15% of the core's characters. Covered characters are the lengths of the
originals. When a reply exceeds this, none of its corrections are applied (they
are counted as `over_budget`, the piece is not counted as cleaned and the run
records `correction_budget_exceeded`); a prefix of them is never applied.

15% is chosen from what speech-to-text cleanup plausibly changes. Word error
rates on conversational audio are a few percent; the names, terms, homophones
and word boundaries cleanup corrects are a part of those; each correction quotes
a few neighbouring words to be unique, roughly tripling the characters it
covers.

### Long transcripts

A transcript longer than `max_chars` is read in consecutive pieces, at most five
per transcript, so at most five requests and about `5 × max_chars` characters of
input (500,000 by default). Each piece is cut as ADR 0014 cuts the head: at the
last whitespace among the final `min(max_chars // 10, 1000)` characters,
otherwise at `max_chars`. The next piece starts at the first whitespace among
the last `min(max_chars // 10, 200)` characters of the previous piece, or
exactly at its end when there is none. Neighbouring pieces therefore share a
short overlap, so an error that straddles a cut is seen whole by the later
piece. A blank piece is not sent.

A limit below 100 characters reads one piece only, as ADR 0014 did; the rest is
unread. A piece shorter than the shortest transcript worth sending (100
characters) is not worth its own request.

Corrections in the overlap are settled by ownership instead of cancelling each
other. A correction is taken only from the piece whose core contains its start;
the same correction proposed by the neighbour is `not_owned`. When owned
corrections from neighbouring pieces still overlap (the earlier one starts in
its piece's core and runs into the next core), the one that starts later wins,
because the overlapping text begins at its start, which its own piece owns; the
other is `superseded`. For example, with `max_chars` 150, a piece ending at
"said alot" that proposes `said alot → said a lot` and the next piece proposing
`alot about → a lot about` no longer cancel: the second applies.

ADR 0014's rule for text that is not cleaned is kept: it is never dropped, it is
returned exactly as it was and it is reported. Text after the last piece read is
counted in `unread_chars` and a warning is logged.

A failed request (provider error, connection error, malformed response) stops
reading. Corrections from earlier pieces are still applied, and the failed piece
and everything after it are unread.

### What is guaranteed

- **Only listed changes.** Every character of `cleaned_text` outside an applied
  correction is the input's character at the same place. Every change is listed
  in `corrections_made` with its `original`, its replacement and its `start`
  position in the input.
- **Per piece.** The corrections accepted from one piece cover at most 15% of
  the characters that piece owns, so at most 15% of the piece.
- **In total.** Cores do not overlap, so applied corrections cover at most 15%
  of `cleaned_chars` (the characters inside pieces whose reply was used), and
  therefore at most 15% of the transcript. This follows from the per-piece
  budget; there is no separate total check.
- **Size of each change.** Each applied correction replaces at most 80
  characters, with a replacement between half that length and 40 characters
  more, containing no control, format or surrogate character the original does
  not contain.

### Result

`CleanupResult` reports:

- `cleaned_text`: the input with applied corrections.
- `corrections_made`: applied corrections in input order, each with its `start`
  position in the input.
- `rejections`: counts by the reasons above; `corrections_rejected` is their sum
  and `corrections_proposed` is applied plus rejected.
- `cleaned_chars`: input characters inside a piece whose reply was used.
- `uncleaned_chars`: the rest, including `unread_chars`.
- `unread_chars`: input characters after the last piece whose reply arrived (or
  that was blank and skipped).
- `success`: no error was recorded. `partial`: some text was cleaned and some
  not. `fully_cleaned`: success and every character cleaned.

### Changes to ADR 0014

ADR 0014 is not edited. This decision replaces these parts of it:

- **The model's text is used as returned.** Replaced: no model text enters the
  result except through an accepted correction.
- **One request per transcript, only the head cleaned.** Replaced: up to five
  pieces are read. The cut rule for each piece is ADR 0014's, unchanged.
- **Blank input is still sent.** Replaced: a blank piece, including input that
  is entirely blank, is not sent, since there is nothing to correct. A blank
  head of non-blank input is skipped and the pieces after it are read, instead
  of skipping the request and returning the whole input.
- **`head_cleaned`.** Removed; `cleaned_chars > 0` replaces it.
- **`uncleaned_chars`.** Was the tail when the head was cleaned, otherwise the
  whole input. Now: input characters not inside a piece whose reply was used, so
  it includes unread text, pieces whose reply was not used and blank pieces.
- **`partial`.** Was "head cleaned and an uncleaned tail follows". Now: some
  text was cleaned and some was not (`cleaned_chars > 0` and
  `uncleaned_chars > 0`).
- **`fully_cleaned`.** Was `success and not partial and head_cleaned`. Now:
  `success`, `cleaned_chars > 0` and `uncleaned_chars == 0`.
- **The truncation warning.** "Transcript truncated from N to M chars" is
  replaced by "Transcript cleanup reads M of N chars in K pieces", logged only
  when text is left unread.

Kept from ADR 0014: the cut rule and its look-back bound, the 100-character
minimum below which nothing is sent, the rule that text which is not cleaned is
kept and reported, and the meaning of `success` (no errors).

## Consequences

The model can no longer reword or drop text beyond the guarantees above: every
change is a listed, located correction that passed the rules, and accepted
corrections cover at most 15% of the text read. Output is about the size of the
corrections, so cost and the token cap no longer scale with the transcript, and
long transcripts are cleaned up to the piece bound instead of only their head.

Some valid fixes are lost: a correction quoted without enough context to be
unique, one larger than the size limits, one that deletes text, one touching a
mid-word cut, one owned by a neighbouring piece that did not propose it, or a
whole reply over its budget. The counts make this visible, so the prompt and
limits can be tuned from measurements.

The tuned prompt held in operations data must be changed to ask for corrections
only before cleanup is used with it. Until then its replies are reported as
`legacy_response_shape`, change nothing, and stop reading after the first piece.

Five failure reasons are added to the closed set in ADR 0009:
`truncated_response`, `legacy_response_shape`, `invalid_corrections`,
`unexpected_stop_reason` and `correction_budget_exceeded`.

No production code calls cleanup yet, and nothing is stored, so reverting this
decision is a code revert.
