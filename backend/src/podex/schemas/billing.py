"""Pydantic schemas for the billing webhook surface."""

from pydantic import BaseModel


class BillingWebhookAck(BaseModel):
    """Acknowledgement returned to the billing provider's webhook sender."""

    status: str
