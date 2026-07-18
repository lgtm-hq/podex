"""Tests for passwordless account authentication and sessions."""

from datetime import UTC, datetime, timedelta

from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import AccountUser, MagicLinkToken, UserSession
from podex.services.account_auth import (
    authenticate_magic_link,
    get_authenticated_user,
    issue_magic_link,
    revoke_user_session,
)

_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def test_issue_magic_link_creates_user_and_hashed_token(
    db_session: Session,
) -> None:
    """A first sign-in request creates the account and a hashed challenge."""
    issued = issue_magic_link(
        db=db_session,
        email="  Reader@Example.COM ",
        redirect_path="/account",
        ttl_minutes=15,
        now=_NOW,
    )
    db_session.commit()

    user = db_session.execute(select(AccountUser)).scalar_one()
    challenge = db_session.execute(select(MagicLinkToken)).scalar_one()
    assert_that(issued.email).is_equal_to("reader@example.com")
    assert_that(user.email).is_equal_to("reader@example.com")
    assert_that(challenge.token_digest).is_not_equal_to(issued.token)
    assert_that(challenge.redirect_path).is_equal_to("/account")
    assert_that(issued.expires_at).is_equal_to(_NOW + timedelta(minutes=15))


def test_reissue_invalidates_prior_outstanding_challenge(
    db_session: Session,
) -> None:
    """Requesting a second link consumes the earlier unconsumed challenge."""
    first = issue_magic_link(
        db=db_session,
        email="reader@example.com",
        redirect_path=None,
        ttl_minutes=15,
        now=_NOW,
    )
    issue_magic_link(
        db=db_session,
        email="reader@example.com",
        redirect_path=None,
        ttl_minutes=15,
        now=_NOW,
    )
    db_session.commit()

    stale = authenticate_magic_link(
        db=db_session,
        token=first.token,
        session_ttl_days=30,
        now=_NOW,
    )
    assert_that(stale).is_none()


def test_authenticate_magic_link_issues_single_use_session(
    db_session: Session,
) -> None:
    """A valid link signs the user in exactly once."""
    issued = issue_magic_link(
        db=db_session,
        email="reader@example.com",
        redirect_path=None,
        ttl_minutes=15,
        now=_NOW,
    )
    session = authenticate_magic_link(
        db=db_session,
        token=issued.token,
        session_ttl_days=30,
        now=_NOW,
    )
    db_session.commit()

    assert_that(session).is_not_none()
    if session is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(session.user.last_signed_in_at).is_equal_to(
        _NOW.replace(tzinfo=None),
    )
    replay = authenticate_magic_link(
        db=db_session,
        token=issued.token,
        session_ttl_days=30,
        now=_NOW,
    )
    assert_that(replay).is_none()


def test_expired_magic_link_is_rejected(db_session: Session) -> None:
    """A link past its TTL cannot be redeemed."""
    issued = issue_magic_link(
        db=db_session,
        email="reader@example.com",
        redirect_path=None,
        ttl_minutes=15,
        now=_NOW,
    )
    late = authenticate_magic_link(
        db=db_session,
        token=issued.token,
        session_ttl_days=30,
        now=_NOW + timedelta(minutes=16),
    )
    assert_that(late).is_none()


def test_get_authenticated_user_resolves_and_touches_session(
    db_session: Session,
) -> None:
    """An active session token resolves to its user and updates last-seen."""
    issued = issue_magic_link(
        db=db_session,
        email="reader@example.com",
        redirect_path=None,
        ttl_minutes=15,
        now=_NOW,
    )
    session = authenticate_magic_link(
        db=db_session,
        token=issued.token,
        session_ttl_days=30,
        now=_NOW,
    )
    if session is None:  # pragma: no cover - narrowed above
        raise AssertionError
    later = _NOW + timedelta(hours=2)

    user = get_authenticated_user(
        db=db_session,
        session_token=session.token,
        now=later,
    )
    db_session.commit()

    assert_that(user).is_not_none()
    if user is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(user.email).is_equal_to("reader@example.com")
    stored = db_session.execute(select(UserSession)).scalar_one()
    assert_that(stored.last_seen_at).is_equal_to(later.replace(tzinfo=None))
    assert_that(
        get_authenticated_user(db=db_session, session_token=None),
    ).is_none()
    assert_that(
        get_authenticated_user(
            db=db_session,
            session_token="unknown-token",  # nosec B106 - not a credential
            now=later,
        ),
    ).is_none()


def test_expired_or_revoked_session_is_rejected(db_session: Session) -> None:
    """Sessions past expiry or explicitly revoked stop resolving."""
    issued = issue_magic_link(
        db=db_session,
        email="reader@example.com",
        redirect_path=None,
        ttl_minutes=15,
        now=_NOW,
    )
    session = authenticate_magic_link(
        db=db_session,
        token=issued.token,
        session_ttl_days=30,
        now=_NOW,
    )
    if session is None:  # pragma: no cover - narrowed above
        raise AssertionError

    expired = get_authenticated_user(
        db=db_session,
        session_token=session.token,
        now=_NOW + timedelta(days=31),
    )
    assert_that(expired).is_none()

    revoked = revoke_user_session(
        db=db_session,
        session_token=session.token,
        now=_NOW,
    )
    db_session.commit()
    assert_that(revoked).is_true()
    assert_that(
        get_authenticated_user(
            db=db_session,
            session_token=session.token,
            now=_NOW,
        ),
    ).is_none()
    assert_that(
        revoke_user_session(db=db_session, session_token=session.token),
    ).is_false()
    assert_that(revoke_user_session(db=db_session, session_token=None)).is_false()
