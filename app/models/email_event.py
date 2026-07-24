from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, String, func
from sqlmodel import Field, SQLModel

# Tracked SendGrid Event Webhook event types (spam/bounce kept for deliverability
# visibility even though they don't feed the open/click rollups on Lead).
EVENT_OPEN = "open"
EVENT_CLICK = "click"
EVENT_DELIVERED = "delivered"
EVENT_BOUNCE = "bounce"
EVENT_DROPPED = "dropped"
EVENT_SPAMREPORT = "spamreport"
EVENT_UNSUBSCRIBE = "unsubscribe"


class EmailEvent(SQLModel, table=True):
    """One SendGrid delivery/engagement event for a campaign email sent to a Lead.

    Correlated back to the lead via `custom_args.lead_id` set at send time (see
    campaign_mailer.send_campaign_email) — events for emails sent before that field
    existed can't be attributed and are dropped at ingest. `sg_event_id` is
    SendGrid's own dedup id: the same event can be POSTed more than once (at-least-
    once delivery), so it's unique here to make ingest idempotent.
    """

    __tablename__ = "email_events"
    __table_args__ = (
        Index("idx_email_events_lead", "lead_id", "occurred_at"),
        Index("idx_email_events_sg_event_id", "sg_event_id", unique=True),
        Index("idx_email_events_trade_lead", "trade_lead_id"),
    )

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    lead_id: int = Field(sa_column=Column(BigInteger, ForeignKey("leads.id"), nullable=False))
    # Set when this event's lead was migrated to the Trade tables (see
    # scripts/transfer_export_launchpad.py) — re-points open/click history at
    # the new trade_leads row without losing the original `lead_id`.
    trade_lead_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("trade_leads.id"), nullable=True),
    )
    email_kind: str | None = None  # "pack" | "viewing" — which email this event is about
    event_type: str = Field(sa_column=Column(String, nullable=False))
    # Resolved document label (e.g. "Corporate Prospectus") for a `click` event whose
    # URL matches a known campaign asset; None for non-click events or unknown URLs.
    material: str | None = None
    url: str | None = None  # raw clicked URL (click events only)
    sg_event_id: str = Field(sa_column=Column(String, nullable=False))
    sg_message_id: str | None = None
    occurred_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
