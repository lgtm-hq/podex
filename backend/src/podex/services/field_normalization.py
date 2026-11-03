"""Shared normalization for persisted command and derivative identity fields."""


def normalize_required(*, value: str, field_name: str) -> str:
    """Strip a required field and reject empty values.

    Args:
        value: Raw field value.
        field_name: Field name to report in validation errors.

    Returns:
        The nonempty, stripped value.

    Raises:
        ValueError: If the field contains only whitespace.
    """
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


def normalize_optional(value: str | None) -> str | None:
    """Normalize an optional field without retaining empty strings.

    Args:
        value: Optional raw field value.

    Returns:
        The stripped value, or None for an absent or blank value.
    """
    if value is None:
        return None
    return value.strip() or None


def normalize_source_hash(*, source_text_hash: str) -> str:
    """Validate the existing SHA-256 identity field contract.

    Args:
        source_text_hash: Source text digest supplied by the caller.

    Returns:
        The stripped, 64-character digest.

    Raises:
        ValueError: If the digest is blank or has an incorrect length.
    """
    normalized = normalize_required(
        value=source_text_hash,
        field_name="source_text_hash",
    )
    if len(normalized) != 64:
        raise ValueError("source_text_hash must be a SHA-256 hex digest")
    return normalized
