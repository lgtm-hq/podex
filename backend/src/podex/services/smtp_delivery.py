"""Shared SMTP transport and configuration for account email adapters."""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Self

from podex.config import Settings


@dataclass(frozen=True, slots=True)
class SmtpDelivery:
    """SMTP configuration shared by sign-in and notification delivery."""

    host: str
    port: int
    from_email: str
    username: str
    password: str
    starttls: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """Build the concrete adapter from the common account SMTP settings."""
        return cls(
            host=settings.auth.smtp_host,
            port=settings.auth.smtp_port,
            from_email=settings.auth.smtp_from_email,
            username=settings.auth.smtp_username,
            password=settings.auth.smtp_password,
            starttls=settings.auth.smtp_starttls,
        )

    def send_email(
        self, message: EmailMessage, *, timeout: float | None = None
    ) -> None:
        """Deliver a prepared message using the configured TLS/authentication."""
        # Omit timeout for digest delivery to preserve its existing socket policy.
        connection = (
            smtplib.SMTP(host=self.host, port=self.port)
            if timeout is None
            else smtplib.SMTP(host=self.host, port=self.port, timeout=timeout)
        )
        with connection as smtp:
            if self.starttls:
                smtp.starttls()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)
