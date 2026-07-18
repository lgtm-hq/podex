"""Email delivery adapter for user notification digests."""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from podex.config import Settings


class DigestSender(Protocol):
    """Delivery boundary for an assembled notification digest."""

    def send_digest(self, *, email: str, subject: str, body_text: str) -> None:
        """Send one account digest email."""


@dataclass(frozen=True, slots=True)
class SmtpDigestSender:
    """Send notification digest mail through configured SMTP."""

    host: str
    port: int
    from_email: str
    username: str
    password: str
    starttls: bool

    def send_digest(self, *, email: str, subject: str, body_text: str) -> None:
        """Send one text-only digest email."""
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.from_email
        message["To"] = email
        message.set_content(body_text)
        with smtplib.SMTP(host=self.host, port=self.port) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.username:
                smtp.login(user=self.username, password=self.password)
            smtp.send_message(message)


def build_digest_sender(*, settings: Settings) -> DigestSender | None:
    """Build configured digest email delivery when SMTP is available."""
    if not settings.smtp_host or not settings.smtp_from_email:
        return None
    return SmtpDigestSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        from_email=settings.smtp_from_email,
        username=settings.smtp_username,
        password=settings.smtp_password,
        starttls=settings.smtp_starttls,
    )
