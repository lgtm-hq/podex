# 0006. Retention confidence comes from the transcript's own extraction

- Status: proposed
- Date: 2026-11-14
- Tags: retention, extraction, transcripts

## Context

Retention purge deletes the only copy of a transcript's source text. A
transcript that has not opted out of raw retention can become purge-eligible
only when its extraction confidence is at least the policy minimum. A missing
confidence and a low confidence are separate purge blockers.

The two places that evaluate retention read confidence differently:

- The scheduled sweep passed no confidence, so every transcript that had not
  opted out was recorded with the missing-confidence blocker and lost any
  eligibility it had.
- Operator preview and apply used the highest `MentionCandidate.confidence`
  among all candidates for the transcript's episode. That includes candidates
  extracted from a different transcript of the same episode, from earlier
  extraction runs, and rejected candidates. A transcript could therefore become
  purge-eligible on a score from text that was never extracted from it, and the
  next sweep then cleared that eligibility again.

Mention candidates record an episode but no transcript or extraction run, and
candidate persistence matches and updates existing episode candidates across
runs. No stored data shows which transcript or extraction run produced a given
confidence.

## Decision

1. The scheduled sweep, operator preview and operator apply take their
   confidence from one shared definition in `transcript_retention_commands`, so
   they reach the same decision for the same transcript and a sweep cannot undo
   an operator's apply. Today that definition is `UNKNOWN_RETENTION_CONFIDENCE`.
2. Confidence counts only when it comes from a completed extraction generation
   of the transcript being evaluated. It is never inferred from the episode,
   from candidate creation times, or from another transcript.
3. Missing, legacy, failed or ambiguous generation provenance resolves as
   unknown, which keeps the existing missing-confidence blocker. Every candidate
   stored today is legacy (episode-only) provenance, so every transcript
   currently resolves as unknown.
4. Purge thresholds and blockers are unchanged.

Not decided here, and left open for the retention owners:

- How a completed generation's candidate confidences combine into one retention
  confidence, including mixed high and low candidates and a successful
  extraction that finds no candidates. A maximum candidate score is not adopted
  as that rule.
- The schema that records an extraction generation (transcript, source identity,
  completion state and the combined confidence) and how candidates and their
  provenance link to it. The schema depends on the combination rule, so it is
  added with that decision, together with the writers and the change to the
  resolver.

## Consequences

- Until generation records exist, no transcript can become purge-eligible unless
  its source opted out of raw retention. This matches what the scheduled sweep
  already recorded on every transcript it reached. Operator apply can no longer
  make other transcripts eligible.
- No transcript becomes eligible that was not eligible before. Operator preview
  reports `extraction_confidence` as `null` until generation records exist.
- Eligibility recorded before this change from an episode-wide score stays on
  the row until the sweep or an operator apply evaluates it again. Keep
  `PODEX_RETENTION__PURGE_ENABLED` off until one full sweep cycle has run after
  deploying, so that eligibility is cleared before any purge.
- Retention sampling strata still group transcripts by episode-wide candidate
  confidence. They decide sampling exemptions, not confidence, and are outside
  this decision.
- When generation records exist, `UNKNOWN_RETENTION_CONFIDENCE` is replaced by
  one resolver that reads the evaluated transcript's own generation. The sweep
  and the operator commands switch to it in the same change, and the parity
  tests keep them in step.
