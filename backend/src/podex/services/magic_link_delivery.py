"""Email delivery adapter for account sign-in links."""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from podex.config import Settings


class MagicLinkSender(Protocol):
    """Boundary for delivering passwordless sign-in links."""

    def send_magic_link(self, *, email: str, verification_url: str) -> None:
        """Deliver a sign-in URL to one verified destination."""
        ...


@dataclass(frozen=True, slots=True)
class SmtpMagicLinkSender:
    """SMTP-backed sign-in link delivery adapter."""

    host: str
    port: int
    from_email: str
    username: str
    password: str
    starttls: bool

    def send_magic_link(self, *, email: str, verification_url: str) -> None:
        """Send a short-lived passwordless sign-in link by email."""
        message = EmailMessage()
        message["Subject"] = "Sign in to Podex"
        message["From"] = self.from_email
        message["To"] = email
        message.set_content(
            "Use this link to sign in to Podex. It expires shortly and can be used "
            f"only once:\n\n{verification_url}\n",
        )
        with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)


def build_magic_link_sender(*, settings: Settings) -> MagicLinkSender | None:
    """Build SMTP delivery when sign-in email configuration is present."""
    if not settings.auth.smtp_host or not settings.auth.smtp_from_email:
        return None
    return SmtpMagicLinkSender(
        host=settings.auth.smtp_host,
        port=settings.auth.smtp_port,
        from_email=settings.auth.smtp_from_email,
        username=settings.auth.smtp_username,
        password=settings.auth.smtp_password,
        starttls=settings.auth.smtp_starttls,
    )
