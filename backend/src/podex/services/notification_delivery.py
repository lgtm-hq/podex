"""Email delivery adapter for user notification digests."""

from email.message import EmailMessage
from typing import Protocol

from podex.config import Settings

from .smtp_delivery import SmtpDelivery


class DigestSender(Protocol):
    """Delivery boundary for an assembled notification digest."""

    def send_digest(self, *, email: str, subject: str, body_text: str) -> None:
        """Send one account digest email."""


class SmtpDigestSender(SmtpDelivery):
    """Send notification digest mail through configured SMTP."""

    def send_digest(self, *, email: str, subject: str, body_text: str) -> None:
        """Send one text-only digest email."""
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.from_email
        message["To"] = email
        message.set_content(body_text)
        self.send_email(message)


def build_digest_sender(*, settings: Settings) -> DigestSender | None:
    """Build configured digest email delivery when SMTP is available."""
    if not settings.auth.smtp_host or not settings.auth.smtp_from_email:
        return None
    sender: DigestSender = SmtpDigestSender.from_settings(settings)
    return sender
