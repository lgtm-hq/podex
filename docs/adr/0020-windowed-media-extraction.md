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
as if it were complete, and a refused or empty reply counted as a successful
answer with no items.

## Decision

### Windows

Extraction joins the segment texts as before: each segment stripped, joined by
one space. Text of up to `max_chars` characters is sent whole in one request,
unchanged. Longer text is read in consecutive windows of at most `max_chars`
characters (150,000). The model, output token cap (4,000), thinking settings,
non-streaming requests, SDK retry setting, prompt and `<transcript>` wrapping
are unchanged. Each window is wrapped and sent exactly like a whole transcript.
The model is not told that it reads an excerpt; whether it needs to be is left
to the quality measurement in #929.

A window that does not reach the end of the text ends at the last segment
separator between 6,001 and 150,000 characters after its start. Only when no
separator lies in that range is it cut inside a segment: at the last whitespace
within the final 1,000 characters of the range, or else at exactly 150,000
characters. The next window starts after the last separator between 4,000 and
2,001 characters before that end; failing that, after the last whitespace
between 3,001 and 2,001 characters before it; failing that, 2,001 characters
before it.

Consecutive windows therefore share 2,000 to 3,999 characters. Every passage of
up to 2,000 characters that ends before the end of the last window sent lies
whole inside one window: each window starts at least 2,000 characters before the
previous one ends. A sentence naming a work, with the words that identify it, is
a few hundred characters; 2,000 characters is about two minutes of speech. A
window does not always end at least one overlap past the previous end: the last
window ends where the text ends (a 150,001-character text without whitespace is
read as `(0, 150000)` and `(147999, 150001)`).

### Bounds

Each transcript gets at most `max_windows` windows (8 by default). Any two
consecutive windows that are not the last advance the read end by at least
146,002 characters, and the second window ends at least 150,001 characters in.
So eight windows read at least 588,007 characters whatever the segment layout.
Layouts built to approach that floor (alternating 6,001- and 146,000-character
segments) read about 608,000. With segments of ordinary length (a few hundred
characters, cut at separators), eight windows read about 1.18 million characters
(1,185,299 with 99-character segments), roughly 20 hours of speech. Text past
the last window is never sent.

Each window is one logical request of at most 150,000 characters asking for at
most 4,000 output tokens. The SDK client retries a failed request twice by
default (connection errors, timeouts, 408, 409, 429, 5xx, and any response whose
`x-should-retry` header asks for it), so one transcript makes at most 24 HTTP
requests carrying at most 3.6 million characters. At most eight replies are
used. This decision does not change the retry setting.

### Failed windows

A window fails, adds exactly one descriptor to `errors` and contributes no items
when:

- the request fails (`request_failed`, with the SDK class and HTTP status);
- the response is malformed, including a stop reason that is present but not a
  string (`malformed_response`);
- the stop reason is `max_tokens` or `model_context_window_exceeded`
  (`truncated_response`);
- the stop reason is `refusal`, whatever the reply contains
  (`refused_response`);
- the reply has no text, or only whitespace (`empty_response`). The prompt
  requires a JSON array, `[]` when nothing is found, so a reply of `[]` is a
  successful window with no items;
- the reply has no usable JSON array (`missing_json_array`,
  `incomplete_json_array`, `invalid_json`, `expected_json_array`);
- decoding or validating the reply raises `ArithmeticError`, `RecursionError`,
  `TypeError` or `ValueError` (an overflowing number, deep nesting, a value of
  the wrong type), or the client raises one of these for the window
  (`unclassified_failure`, with the exception class).

The new reasons are `FailureReason` members, so the descriptors keep ADR 0009's
grammar. Neither descriptors nor logs carry reply text or a refusal explanation.
The items of the other windows are kept.

After `BadRequestError` (400), `AuthenticationError` (401),
`PermissionDeniedError` (403) or `NotFoundError` (404) no further window is
sent. The key, permissions, model and request parameters are the same for every
window, and windows are of the same size, so every later window would get the
same answer. The windows already answered keep their items. Other request
failures (connection errors, timeouts, 429, 5xx) fail only their window, after
the SDK's retries.

Any other exception propagates unchanged and discards the windows already
answered. Before windowing, every such exception propagated out of the single
request, and these exceptions point at the process or the injected client rather
than at one reply. Catching them all would hide faults such as
`KeyboardInterrupt` or `MemoryError` and would need a broad handler.

### Merging items

Within one reply, the first item of each case-insensitive title is kept, as
before. Across windows, items with the same title after `str.strip` and
`str.lower` are merged into one. The item with the highest confidence is kept,
including its title spelling and type; on a tie, the first found in transcript
order. Creator and year (the only fields an item may lack) are taken from the
kept item when it has them; otherwise from the most confident other item that
has them, first found on a tie. So conflicting non-empty values resolve to the
most confident item's. The merged item keeps the list position where its title
first appeared. A work found in two overlapping windows is reported once. So is
a work named at distant points in the episode, as it was when the whole
transcript fit one request: items carry no timestamp or position, the default
prompt asks for each item once per transcript, and review candidate persistence
finds context from the segments itself.

### Result

`ExtractionResult` states coverage:

- `transcript_chars`: length of the joined text.
- `windows`: requests sent, not counting SDK retries. `failed_windows` is the
  number of descriptors in `errors`.
- `unread_chars`: characters never sent in any request. This is the text past
  the last window sent (because of the window bound, or a failure that ends the
  run), and the whole text when it is below the 100-character minimum.
- `window_limit_reached`: the transcript needed more windows than the bound
  allows. When it is true, a warning gives the window count and the characters
  not read.
- `unextracted_ranges`: ascending, disjoint `(start, end)` offsets into the
  joined text of text inside no window whose reply was used: the unread tail,
  text only failed windows held, and text below the minimum. There are at most
  `max_windows + 1` ranges. `unextracted_chars` is their total length.
- `fully_covered`: no unextracted range. An empty transcript is covered.

`success` means that no window failed: every request sent was answered with a
usable reply. It is false whenever any window failed, including a refusal or an
empty reply. It stays true when the window bound left text unread, or the text
was too short to send: no request failed, and the bound is a cost limit rather
than a failure. Callers that need the whole transcript must check
`fully_covered`. A caller can re-read only the text in `unextracted_ranges`, or
re-run the transcript.

Empty text, and text below the 100-character minimum, returns without a request
whatever the bounds. Bounds that cannot read longer text are rejected with
`ValueError`: `max_windows` below one, or `max_chars` of 8,000 or less when the
text does not fit in one window.

## Consequences

Cost per transcript grows with transcript length up to the bounds above instead
of staying at one request. Transcripts that fit one request cost exactly as
before. Callers that need to know whether a result reflects the whole transcript
must read `fully_covered`, `unextracted_ranges` or `unread_chars`; `success`
alone does not show it. The pipeline stage outcome (planned in #694) should
record these fields; no schema records extraction outcomes today. A failed
window is not retried here beyond the SDK's own retries, and a truncated reply's
complete items are not salvaged.

Reading excerpts loses context a single request would have, and none of the
following is covered by this decision:

- A guest introduced in full early in the episode and named only by first name
  in later windows: a later window may report a second, shorter person entry.
  The two titles differ, so they are not merged.
- A title named before a cut with its creator named more than 2,000 characters
  after it: no window holds both, so the work can lose its creator.
- A reference ("the adaptation") established before a cut and the title first
  named after it: the later window sees the title without what it refers to and
  can report the wrong type.
- Title matching across windows ignores case and outer whitespace only.
  Punctuation, inner whitespace and Unicode forms (for example composed and
  decomposed accents) are compared exactly, so such variants are not merged
  here.

The effect of these limits on extraction quality is to be measured (#929).
Changing the window size, overlap, look-back or bound changes cost per
transcript and should update this record. Reverting is a code revert; nothing is
stored.
