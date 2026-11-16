"""Tests for the SMTP digest delivery adapter."""

from email.message import EmailMessage
from secrets import token_hex

import pytest
from assertpy import assert_that

from podex.services.notification_delivery import SmtpDigestSender

_SMTP_CREDENTIAL = f"synthetic-smtp-{token_hex(8)}"


class _RecordingSmtp:
    """SMTP connection double that records every call made on it."""

    instances: list["_RecordingSmtp"] = []

    def __init__(self, **endpoint: object) -> None:
        self.endpoint = endpoint
        self.calls: list[str] = []
        self.credentials: tuple[str, str] | None = None
        self.messages: list[EmailMessage] = []
        _RecordingSmtp.instances.append(self)

    def __enter__(self) -> "_RecordingSmtp":
        return self

    def __exit__(self, *args: object) -> None:
        self.calls.append("close")

    def starttls(self) -> None:
        """Record TLS negotiation."""
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        """Record the credentials used to authenticate."""
        self.calls.append("login")
        self.credentials = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        """Record the delivered message."""
        self.calls.append("send_message")
        self.messages.append(message)


@pytest.fixture(name="smtp")
def smtp_fixture(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingSmtp]:
    """Replace the SMTP connection class with the recording double."""
    monkeypatch.setattr(_RecordingSmtp, "instances", [])
    monkeypatch.setattr("smtplib.SMTP", _RecordingSmtp)
    return _RecordingSmtp


def _sender(*, username: str = "", starttls: bool = False) -> SmtpDigestSender:
    """Return a digest sender with synthetic SMTP settings."""
    return SmtpDigestSender(
        host="smtp.example.com",
        port=2525,
        from_email="digests@example.com",
        username=username,
        password=_SMTP_CREDENTIAL,
        starttls=starttls,
    )


def test_send_digest_builds_one_text_message(smtp: type[_RecordingSmtp]) -> None:
    """Subject, sender, recipient and body come from the call and settings."""
    _sender().send_digest(
        email="reader@example.com",
        subject="Your Podex digest",
        body_text="Two new episodes mention books you follow.",
    )

    assert_that(smtp.instances).is_length(1)
    connection = smtp.instances[0]
    assert_that(connection.endpoint).is_equal_to(
        {"host": "smtp.example.com", "port": 2525},
    )
    assert_that(connection.calls).is_equal_to(["send_message", "close"])
    assert_that(connection.messages).is_length(1)
    message = connection.messages[0]
    assert_that(message["Subject"]).is_equal_to("Your Podex digest")
    assert_that(message["From"]).is_equal_to("digests@example.com")
    assert_that(message["To"]).is_equal_to("reader@example.com")
    assert_that(message.get_content_type()).is_equal_to("text/plain")
    assert_that(message.is_multipart()).is_false()
    assert_that(message.get_content()).is_equal_to(
        "Two new episodes mention books you follow.\n",
    )


def test_send_digest_negotiates_tls_and_authenticates_when_configured(
    smtp: type[_RecordingSmtp],
) -> None:
    """Configured TLS and credentials are applied before the message is sent."""
    _sender(username="mailer", starttls=True).send_digest(
        email="reader@example.com",
        subject="Digest",
        body_text="Body.",
    )

    connection = smtp.instances[0]
    assert_that(connection.calls).is_equal_to(
        ["starttls", "login", "send_message", "close"],
    )
    assert_that(connection.credentials).is_equal_to(
        ("mailer", _SMTP_CREDENTIAL),
    )
    assert_that(connection.messages[0]["To"]).is_equal_to("reader@example.com")
