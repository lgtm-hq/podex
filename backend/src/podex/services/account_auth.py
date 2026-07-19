"""Passwordless account authentication and session persistence."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy.orm import Session

from podex.models import AccountUser, MagicLinkToken, UserSession


@dataclass(frozen=True, slots=True)
class MagicLinkIssueData:
    """One-time credential to deliver to an account email address."""

    email: str
    token: str
    expires_at: datetime
    redirect_path: str | None


@dataclass(frozen=True, slots=True)
class AuthenticatedSessionData:
    """Authenticated user and the newly issued browser session token."""

    user: AccountUser
    token: str
    expires_at: datetime


def issue_magic_link(
    *,
    db: Session,
    email: str,
    redirect_path: str | None,
    ttl_minutes: int,
    now: datetime | None = None,
) -> MagicLinkIssueData:
    """Create a one-time magic-link challenge for a normalized email address."""
    effective_now = now or datetime.now(UTC)
    normalized_email = email.strip().casefold()

    # Account rows are created only on successful verification so that
    # unauthenticated link requests cannot mint unbounded users.
    db.query(MagicLinkToken).filter(
        MagicLinkToken.email == normalized_email,
        MagicLinkToken.consumed_at.is_(None),
    ).update({MagicLinkToken.consumed_at: effective_now}, synchronize_session=False)
    raw_token = token_urlsafe(32)
    expires_at = effective_now + timedelta(minutes=ttl_minutes)
    db.add(
        MagicLinkToken(
            email=normalized_email,
            token_digest=_token_digest(raw_token),
            redirect_path=redirect_path,
            expires_at=expires_at,
        ),
    )
    db.flush()
    return MagicLinkIssueData(
        email=normalized_email,
        token=raw_token,
        expires_at=expires_at,
        redirect_path=redirect_path,
    )


def authenticate_magic_link(
    *,
    db: Session,
    token: str,
    session_ttl_days: int,
    now: datetime | None = None,
) -> AuthenticatedSessionData | None:
    """Consume a valid magic link and issue a revocable browser session."""
    effective_now = now or datetime.now(UTC)
    challenge = (
        db.query(MagicLinkToken)
        .filter(MagicLinkToken.token_digest == _token_digest(token))
        .first()
    )
    if (
        challenge is None
        or challenge.consumed_at is not None
        or _as_utc(challenge.expires_at) <= effective_now
    ):
        return None

    user = db.query(AccountUser).filter(AccountUser.email == challenge.email).first()
    if user is None:
        user = AccountUser(email=challenge.email)
        db.add(user)
        db.flush()
    challenge.user_id = user.id
    raw_session_token = token_urlsafe(32)
    expires_at = effective_now + timedelta(days=session_ttl_days)
    challenge.consumed_at = effective_now
    user.last_signed_in_at = effective_now
    db.add(
        UserSession(
            user_id=user.id,
            token_digest=_token_digest(raw_session_token),
            expires_at=expires_at,
            last_seen_at=effective_now,
        ),
    )
    db.flush()
    return AuthenticatedSessionData(
        user=user,
        token=raw_session_token,
        expires_at=expires_at,
    )


def get_authenticated_user(
    *,
    db: Session,
    session_token: str | None,
    now: datetime | None = None,
) -> AccountUser | None:
    """Resolve an active browser session to its account user."""
    if not session_token:
        return None
    effective_now = now or datetime.now(UTC)
    session = (
        db.query(UserSession)
        .filter(UserSession.token_digest == _token_digest(session_token))
        .first()
    )
    if (
        session is None
        or session.revoked_at is not None
        or _as_utc(session.expires_at) <= effective_now
    ):
        return None
    session.last_seen_at = effective_now
    db.flush()
    return db.query(AccountUser).filter(AccountUser.id == session.user_id).one()


def revoke_user_session(
    *,
    db: Session,
    session_token: str | None,
    now: datetime | None = None,
) -> bool:
    """Revoke an existing browser session token when present."""
    if not session_token:
        return False
    session = (
        db.query(UserSession)
        .filter(UserSession.token_digest == _token_digest(session_token))
        .first()
    )
    if session is None or session.revoked_at is not None:
        return False
    session.revoked_at = now or datetime.now(UTC)
    db.flush()
    return True


def _token_digest(token: str) -> str:
    """Hash an opaque credential before persistent lookup or storage."""
    return sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite-naive or timezone-aware timestamps for comparison."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
