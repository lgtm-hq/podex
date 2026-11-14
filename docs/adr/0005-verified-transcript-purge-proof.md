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
  artifact identifies the proof. Any read, decryption, shape or hash failure
  refuses the purge before any row is written or object deleted. Decrypted text
  stays inside the verification call; the digest summary never falls back to it.
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

Approved takedowns are not gated by verification: they must remove content even
when it cannot be read. Their digest uses the column description and records its
representation, so an empty takedown proof is labelled `absent` instead of
looking like a hashed source.

Existing empty-text digests are not rewritten. An inventory classifies each as
`retained_artifact`, `source_deleted`, `no_recorded_source` or `repaired`. Only
`retained_artifact` can be repaired, by verifying the artifact and adding a new
digest that carries `repairs_digest_id`; the original row stays as it was.
Deleted input is never reconstructed or certified; the acquisition-time artifact
hash is reported for reference only.

No schema change: representation lives in the existing `metadata_json`.

## Consequences

- Purge is strictly more conservative: transcripts whose artifact is missing,
  corrupt or substituted, or that have no source text, now stay eligible but
  unpurged and return a 409 until an operator resolves them.
- If an artifact object is deleted but its transaction rolls back (a crash
  between object delete and commit), later purges refuse because the object is
  gone. That state needs operator attention rather than a silent proof.
- Purge now reads every artifact before deleting it, which adds one object read
  per artifact.
- Whether takedowns should also verify, and how far to adopt the repair record
  format, are open for the retention owners; changing either should supersede
  this ADR.
