<!-- markdownlint-disable MD041 -->

# 0005. Transcript purge proof identifies a verified source

- Status: proposed
- Date: 2026-11-14
- Tags: retention, transcripts, storage

## Context

Purge deletes the only copy of a transcript's source text, so the
`TranscriptDigest` written beforehand is the lasting evidence of what was
processed. The digest hashed `cleaned_text or raw_text or ""`. New acquisitions
store raw text only in an encrypted artifact and leave both columns empty, so
their proof was the SHA-256 of empty text (`e3b0c442…b855`) while the artifact
metadata held the real hash. Purge never read the artifact before deleting it,
so a missing, undecryptable or substituted object was deleted without any check.

## Decision

A retention purge proof hashes the retained source it is about to delete, and
purge refuses unless that source verifies:

- **Encrypted artifacts.** Every unpurged artifact is read and decrypted through
  the store port, its payload shape is validated, and the SHA-256 of its exact
  raw UTF-8 text must equal the hash recorded at acquisition. The newest
  artifact identifies the proof. Raw text that is empty or whitespace-only is
  refused even when its hash matches, because it proves nothing. Any read,
  decryption, shape, empty-text or hash failure refuses the purge before any row
  is written or object deleted.
- **No plaintext escapes verification.** Decryption, validation, encoding and
  hashing run in one helper that returns only a hash or a fixed failure code
  (`unreadable`, `malformed`, `empty`, `mismatch`) and clears its payload
  reference before returning. The error is raised only after that helper has
  returned, so it has no cause or context and no frame on its path holds
  decrypted text. The digest summary never falls back to decrypted text.
- **Legacy columns** (no artifact): cleaned text takes precedence over raw text,
  and empty strings count as absent, exactly as before, so existing digest keys
  replay unchanged. These cannot be verified against stored metadata.
- **No source**: refused. The empty-text hash is no longer recorded as a purge
  proof.
- Every new digest records `source_representation`
  (`encrypted_artifact_raw_text`, `legacy_cleaned_text`, `legacy_raw_text` or
  `absent`) and, for artifacts, `source_artifact_id` in `metadata_json`. The
  digest key format `transcript:{id}:purge:{hash}` is unchanged.
- Purge deletes exactly the artifact set it verified, after the proof row is
  flushed.
- **Operator purge refuses ambiguous existing proofs.** Operator purge used to
  read back the transcript's only digest after replaying the column-hash key,
  and failed (after deletion had begun) whenever that left more than one digest.
  It now returns the proof its own command wrote, and before any read, write or
  delete it refuses exactly the states the old lookup failed on: more than one
  existing digest, or a single digest under any key other than the column-hash
  key (`cleaned_text or raw_text or ""`). So verification never makes a
  transcript purgeable that operator purge previously refused.

Approved takedowns are not gated by verification: they must remove content even
when it cannot be read. Their digest uses the column description and records its
representation, so an empty takedown proof is labelled `absent` instead of
looking like a hashed source.

Existing empty-text digests are not rewritten. An inventory classifies each as
`retained_artifact`, `unattributable_artifact`, `source_deleted`,
`no_recorded_source` or `repaired`. A retained artifact is attributable to the
original proof only if it is the single artifact that was stored and recorded
before the proof was written, is not a re-acquisition, and is the transcript's
only retained artifact. Later, re-acquired or competing artifacts are
`unattributable_artifact` and are never used to backdate proof. Only
`retained_artifact` can be repaired: the artifact is verified and a new digest
is added carrying `repairs_digest_id`, `repairs_purged_at` (the original purge
time, also kept in `purged_at`) and `verified_at` (the repair's own verification
time, also in `generated_at`). The original row stays as it was. Deleted input
is never reconstructed or certified; the hash recorded at acquisition by the
newest artifact that predates the proof is reported for reference only.

No schema change: representation lives in the existing `metadata_json`.

## Consequences

- Purge is strictly more conservative: transcripts whose artifact is missing,
  corrupt or substituted, or that have no source text, now stay eligible but
  unpurged and return a 409 until an operator resolves them.
- Purge is not atomic across the database and object storage. If the process
  dies between object deletion and the database commit, or deleting a later
  object of several fails after an earlier one was deleted, the deletion
  outlives the rolled-back proof. Later purges then refuse because the object is
  gone, and the state needs operator attention. This already happens on main;
  closing it needs a durable deletion-intent record.
- Purge does not serialise concurrent work on the same transcript. The unique
  digest key does not prevent two purges on PostgreSQL from both verifying and
  both deleting, and a takedown can interleave with a purge. This already
  happens on main; closing it needs a per-transcript lock shared by purge and
  takedown.
- Purge now reads every artifact before deleting it, which adds one object read
  per artifact.
- Whether takedowns should also verify, and how far to adopt the repair record
  format, are open for the retention owners; changing either should supersede
  this ADR.
