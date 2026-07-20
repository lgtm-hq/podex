"""Account and authentication API schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from podex.schemas.media import MediaRead
from podex.schemas.podcast import PodcastRead


class AccountUserRead(BaseModel):
    """Public representation of the signed-in account."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    first_name: str | None
    last_name: str | None
    created_at: datetime
    last_signed_in_at: datetime | None


class AuthMagicLinkRequest(BaseModel):
    """Request delivery of a one-time email sign-in link."""

    email: EmailStr
    redirect_path: str | None = Field(default=None, max_length=300)


class AuthMagicLinkRequestResponse(BaseModel):
    """Acknowledgement that a sign-in link was accepted for delivery."""

    accepted: bool = True


class AuthMagicLinkVerifyRequest(BaseModel):
    """Redeem a one-time sign-in token."""

    token: str = Field(min_length=1, max_length=128)


class AuthSessionRead(BaseModel):
    """Authenticated session summary returned after verification."""

    user: AccountUserRead
    expires_at: datetime


class AuthLogoutResponse(BaseModel):
    """Result of revoking the current browser session."""

    signed_out: bool


class SavedMediaRead(BaseModel):
    """A saved public media record and when it was saved."""

    media: MediaRead
    saved_at: datetime


class SavedMediaListRead(BaseModel):
    """Saved media collection for the current account."""

    items: list[SavedMediaRead]
    total: int


class SavedMediaDeleteResponse(BaseModel):
    """Result of removing a saved media record."""

    deleted: bool


class FollowedPodcastRead(BaseModel):
    """A followed public podcast source and when it was followed."""

    podcast: PodcastRead
    followed_at: datetime


class FollowedPodcastListRead(BaseModel):
    """Followed podcast collection for the current account."""

    items: list[FollowedPodcastRead]
    total: int


class FollowedPodcastDeleteResponse(BaseModel):
    """Result of removing a followed podcast."""

    deleted: bool


class AlertRuleRead(BaseModel):
    """Public representation of one account alert rule."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    target_type: str
    target_id: int
    event_type: str
    enabled: bool
    baseline_count: int
    last_evaluated_at: datetime | None
    created_at: datetime


class AlertRuleListRead(BaseModel):
    """Alert rule collection for the current account."""

    items: list[AlertRuleRead]
    total: int


class AlertRuleCreateRequest(BaseModel):
    """Create an alert rule over a linked public resource."""

    target_type: Literal["media", "podcast"]
    target_id: int
    event_type: Literal["new_mention", "new_episode"]


class AlertRuleUpdateRequest(BaseModel):
    """Pause or resume an alert rule."""

    enabled: bool


class AlertRuleDeleteResponse(BaseModel):
    """Result of removing an alert rule."""

    deleted: bool


class AlertEventRead(BaseModel):
    """A generated alert event and its owning rule."""

    rule: AlertRuleRead
    previous_count: int
    observed_count: int


class AlertEvaluationRead(BaseModel):
    """Result of evaluating the account's alert rules."""

    items: list[AlertEventRead]
    generated: int


class DigestRead(BaseModel):
    """Public representation of a delivered notification digest."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    channel: str
    subject: str
    body_text: str
    event_count: int
    created_at: datetime
    delivered_at: datetime | None


class DigestListRead(BaseModel):
    """Delivered digest collection for the current account."""

    items: list[DigestRead]
    total: int


class DigestSendResponse(BaseModel):
    """Result of an on-demand digest delivery attempt."""

    digest: DigestRead | None = None
    delivered: bool


class PreferenceRead(BaseModel):
    """Notification preferences for the current account."""

    model_config = ConfigDict(from_attributes=True)

    digest_enabled: bool
    digest_frequency: str
    updated_at: datetime


class PreferenceUpdateRequest(BaseModel):
    """Update notification preferences."""

    digest_enabled: bool
    digest_frequency: Literal["immediate", "daily", "weekly"]


class QuotaRead(BaseModel):
    """One monthly feature quota and its current consumption."""

    period: str
    feature: str
    limit: int
    used: int
    remaining: int


class SubscriptionRead(BaseModel):
    """Hosted plan entitlement and current monthly usage."""

    tier: str
    status: str
    paid_features_enforced: bool
    current_period_ends_at: datetime | None
    quotas: list[QuotaRead]


class SubscriptionCheckoutRead(BaseModel):
    """External provider-hosted checkout destination."""

    provider: str
    checkout_url: str
