"""Opaque identifier helpers for the v2 API boundary."""

from enum import StrEnum


class IdentifierPrefix(StrEnum):
    """Supported opaque identifier prefixes."""

    AUDIT_LOG = "audit"
    PODCAST = "pod"
    EPISODE = "ep"
    MEDIA = "med"
    MENTION = "men"
    MENTION_CANDIDATE = "cand"
    REVIEW_ITEM = "rev"
    INGESTION_RUN = "run"
    TRANSCRIPTION_JOB = "job"
    TRANSCRIPT = "trn"
    TRANSCRIPT_ARTIFACT = "artifact"
    TRANSCRIPT_DIGEST = "digest"
    PIPELINE_SCHEDULE = "sched"
    SCHEDULED_WORK = "work"
    TAKEDOWN_REQUEST = "td"
    ACCOUNT_USER = "usr"
    ACCOUNT_ALERT_RULE = "alert"
    ACCOUNT_DIGEST = "mail"


def encode_identifier(*, prefix: IdentifierPrefix, value: int) -> str:
    """Encode an internal integer ID into an opaque boundary ID.

    Args:
        prefix: Boundary prefix.
        value: Internal numeric identifier.

    Returns:
        Encoded opaque identifier.
    """
    return f"{prefix}_{value}"


def decode_identifier(*, prefix: IdentifierPrefix, value: str) -> int:
    """Decode an opaque boundary ID into an internal integer.

    Args:
        prefix: Expected boundary prefix.
        value: Encoded opaque identifier.

    Returns:
        Internal numeric identifier.

    Raises:
        ValueError: If the value is malformed or uses the wrong prefix.
    """
    expected_prefix = f"{prefix}_"
    if not value.startswith(expected_prefix):
        raise ValueError(f"Invalid identifier prefix for {prefix}")

    suffix = value.removeprefix(expected_prefix)
    if not suffix.isdigit():
        raise ValueError(f"Invalid identifier value for {prefix}")

    return int(suffix)


def encode_podcast_id(*, podcast_id: int) -> str:
    """Encode a podcast identifier.

    Args:
        podcast_id: Internal podcast identifier.

    Returns:
        Opaque podcast identifier.
    """
    return encode_identifier(prefix=IdentifierPrefix.PODCAST, value=podcast_id)


def decode_podcast_id(*, podcast_id: str) -> int:
    """Decode a podcast boundary identifier.

    Args:
        podcast_id: Opaque podcast identifier.

    Returns:
        Internal podcast identifier.
    """
    return decode_identifier(prefix=IdentifierPrefix.PODCAST, value=podcast_id)


def encode_account_user_id(*, user_id: int) -> str:
    """Encode an account user identifier.

    Args:
        user_id: Internal account user identifier.

    Returns:
        Opaque account user identifier.
    """
    return encode_identifier(prefix=IdentifierPrefix.ACCOUNT_USER, value=user_id)


def encode_account_alert_rule_id(*, rule_id: int) -> str:
    """Encode an account alert rule identifier."""
    return encode_identifier(prefix=IdentifierPrefix.ACCOUNT_ALERT_RULE, value=rule_id)


def decode_account_alert_rule_id(*, rule_id: str) -> int:
    """Decode an account alert rule identifier."""
    return decode_identifier(prefix=IdentifierPrefix.ACCOUNT_ALERT_RULE, value=rule_id)


def encode_account_digest_id(*, digest_id: int) -> str:
    """Encode an account notification digest identifier."""
    return encode_identifier(prefix=IdentifierPrefix.ACCOUNT_DIGEST, value=digest_id)


def encode_episode_id(*, episode_id: int) -> str:
    """Encode an episode identifier.

    Args:
        episode_id: Internal episode identifier.

    Returns:
        Opaque episode identifier.
    """
    return encode_identifier(prefix=IdentifierPrefix.EPISODE, value=episode_id)


def decode_episode_id(*, episode_id: str) -> int:
    """Decode an episode boundary identifier.

    Args:
        episode_id: Opaque episode identifier.

    Returns:
        Internal episode identifier.
    """
    return decode_identifier(prefix=IdentifierPrefix.EPISODE, value=episode_id)


def encode_media_id(*, media_id: int) -> str:
    """Encode a media identifier.

    Args:
        media_id: Internal media identifier.

    Returns:
        Opaque media identifier.
    """
    return encode_identifier(prefix=IdentifierPrefix.MEDIA, value=media_id)


def encode_mention_id(*, mention_id: int) -> str:
    """Encode a mention identifier.

    Args:
        mention_id: Internal mention identifier.

    Returns:
        Opaque mention identifier.
    """
    return encode_identifier(prefix=IdentifierPrefix.MENTION, value=mention_id)


def decode_mention_id(*, mention_id: str) -> int:
    """Decode a mention boundary identifier.

    Args:
        mention_id: Opaque mention identifier.

    Returns:
        Internal mention identifier.
    """
    return decode_identifier(prefix=IdentifierPrefix.MENTION, value=mention_id)


def encode_ingestion_run_id(*, ingestion_run_id: int) -> str:
    """Encode an ingestion run identifier.

    Args:
        ingestion_run_id: Internal ingestion run identifier.

    Returns:
        Opaque ingestion run identifier.
    """
    return encode_identifier(
        prefix=IdentifierPrefix.INGESTION_RUN,
        value=ingestion_run_id,
    )


def encode_transcription_job_id(*, transcription_job_id: int) -> str:
    """Encode a transcription job identifier.

    Args:
        transcription_job_id: Internal transcription job identifier.

    Returns:
        Opaque transcription job identifier.
    """
    return encode_identifier(
        prefix=IdentifierPrefix.TRANSCRIPTION_JOB,
        value=transcription_job_id,
    )


def encode_transcript_id(*, transcript_id: int) -> str:
    """Encode a transcript identifier."""
    return encode_identifier(prefix=IdentifierPrefix.TRANSCRIPT, value=transcript_id)


def decode_transcript_id(*, transcript_id: str) -> int:
    """Decode a transcript boundary identifier."""
    return decode_identifier(prefix=IdentifierPrefix.TRANSCRIPT, value=transcript_id)


def encode_transcript_digest_id(*, digest_id: int) -> str:
    """Encode a transcript digest identifier."""
    return encode_identifier(prefix=IdentifierPrefix.TRANSCRIPT_DIGEST, value=digest_id)


def encode_transcript_artifact_id(*, artifact_id: int) -> str:
    """Encode a transcript artifact identifier."""
    return encode_identifier(
        prefix=IdentifierPrefix.TRANSCRIPT_ARTIFACT,
        value=artifact_id,
    )


def encode_takedown_request_id(*, takedown_request_id: int) -> str:
    """Encode a takedown request identifier."""
    return encode_identifier(
        prefix=IdentifierPrefix.TAKEDOWN_REQUEST,
        value=takedown_request_id,
    )


def decode_takedown_request_id(*, takedown_request_id: str) -> int:
    """Decode a takedown request boundary identifier."""
    return decode_identifier(
        prefix=IdentifierPrefix.TAKEDOWN_REQUEST,
        value=takedown_request_id,
    )


def encode_mention_candidate_id(*, mention_candidate_id: int) -> str:
    """Encode a mention candidate identifier.

    Args:
        mention_candidate_id: Internal mention candidate identifier.

    Returns:
        Opaque mention candidate identifier.
    """
    return encode_identifier(
        prefix=IdentifierPrefix.MENTION_CANDIDATE,
        value=mention_candidate_id,
    )


def decode_mention_candidate_id(*, mention_candidate_id: str) -> int:
    """Decode a mention candidate boundary identifier.

    Args:
        mention_candidate_id: Opaque mention candidate identifier.

    Returns:
        Internal mention candidate identifier.
    """
    return decode_identifier(
        prefix=IdentifierPrefix.MENTION_CANDIDATE,
        value=mention_candidate_id,
    )


def encode_review_item_id(*, review_item_id: int) -> str:
    """Encode a review item identifier.

    Args:
        review_item_id: Internal review item identifier.

    Returns:
        Opaque review item identifier.
    """
    return encode_identifier(prefix=IdentifierPrefix.REVIEW_ITEM, value=review_item_id)


def decode_review_item_id(*, review_item_id: str) -> int:
    """Decode a review item boundary identifier.

    Args:
        review_item_id: Opaque review item identifier.

    Returns:
        Internal review item identifier.
    """
    return decode_identifier(prefix=IdentifierPrefix.REVIEW_ITEM, value=review_item_id)


def encode_audit_log_id(*, audit_log_id: int) -> str:
    """Encode an audit log identifier.

    Args:
        audit_log_id: Internal audit log identifier.

    Returns:
        Opaque audit log identifier.
    """
    return encode_identifier(prefix=IdentifierPrefix.AUDIT_LOG, value=audit_log_id)


def decode_media_id(*, media_id: str) -> int:
    """Decode a media boundary identifier.

    Args:
        media_id: Opaque media identifier.

    Returns:
        Internal media identifier.
    """
    return decode_identifier(prefix=IdentifierPrefix.MEDIA, value=media_id)


def encode_pipeline_schedule_id(*, schedule_id: int) -> str:
    """Encode a pipeline schedule identifier.

    Args:
        schedule_id: Internal schedule identifier.

    Returns:
        Opaque schedule identifier.
    """
    return encode_identifier(
        prefix=IdentifierPrefix.PIPELINE_SCHEDULE,
        value=schedule_id,
    )


def encode_scheduled_work_id(*, work_item_id: int) -> str:
    """Encode a scheduled work item identifier.

    Args:
        work_item_id: Internal work item identifier.

    Returns:
        Opaque scheduled work identifier.
    """
    return encode_identifier(
        prefix=IdentifierPrefix.SCHEDULED_WORK,
        value=work_item_id,
    )
