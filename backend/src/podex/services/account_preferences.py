"""Authenticated account preference operations."""

from typing import Literal

from sqlalchemy.orm import Session

from podex.models import AccountPreference

DigestFrequency = Literal["immediate", "daily", "weekly"]


def get_account_preferences(*, db: Session, user_id: int) -> AccountPreference:
    """Load account preferences, establishing sensible defaults when absent."""
    preferences = (
        db.query(AccountPreference).filter(AccountPreference.user_id == user_id).first()
    )
    if preferences is not None:
        return preferences
    preferences = AccountPreference(user_id=user_id)
    db.add(preferences)
    db.flush()
    return preferences


def update_account_preferences(
    *,
    db: Session,
    user_id: int,
    digest_enabled: bool,
    digest_frequency: DigestFrequency,
) -> AccountPreference:
    """Persist digest delivery preferences for an account."""
    preferences = get_account_preferences(db=db, user_id=user_id)
    preferences.digest_enabled = digest_enabled
    preferences.digest_frequency = digest_frequency
    db.flush()
    return preferences
