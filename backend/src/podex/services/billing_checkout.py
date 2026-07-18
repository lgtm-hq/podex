"""Provider boundary for hosted paid-tier checkout."""

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

from podex.config import Settings


@dataclass(frozen=True)
class BillingCheckout:
    """External checkout destination produced by a billing adapter."""

    provider: str
    checkout_url: str


class BillingCheckoutProvider(Protocol):
    """Boundary implemented by the eventual payment-provider adapter."""

    def create_checkout(
        self,
        *,
        email: str,
        account_reference: str,
    ) -> BillingCheckout:
        """Create or link an external checkout session."""


class HostedBillingCheckoutProvider:
    """Config-backed hosted checkout bridge used until provider SDK selection."""

    def __init__(self, *, provider: str, checkout_url: str) -> None:
        self.provider = provider
        self.checkout_url = checkout_url

    def create_checkout(
        self,
        *,
        email: str,
        account_reference: str,
    ) -> BillingCheckout:
        """Build a hosted checkout link with non-secret account context."""
        separator = "&" if "?" in self.checkout_url else "?"
        query = urlencode({"prefilled_email": email, "reference": account_reference})
        return BillingCheckout(
            provider=self.provider,
            checkout_url=f"{self.checkout_url}{separator}{query}",
        )


def build_billing_checkout_provider(
    *,
    settings: Settings,
) -> BillingCheckoutProvider | None:
    """Build the configured provider bridge only when checkout is configured."""
    if not settings.billing_provider_name or not settings.billing_checkout_url:
        return None
    return HostedBillingCheckoutProvider(
        provider=settings.billing_provider_name,
        checkout_url=settings.billing_checkout_url,
    )
